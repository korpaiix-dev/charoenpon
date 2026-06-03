"""Content Distributor — listener + scheduler.

Listener: captures media from "คลังกระจายสินค้า" (source group)
Scheduler: distributes content to VIP groups based on tags + tier mapping

Architecture:
- Source: -1004258570047 (คลังกระจายสินค้า)
- Targets: G300/G500/VGOD/INTER/SSS/SERIES/RANDOM/SUMMER (group_registry where min_tier IN VIP tiers)
- Tag → tier-set routing:
    no tag / #vip   → TIER_300+   (all 8 VIP groups)
    #of            → TIER_500+   (7 groups)
    #god           → TIER_1299+  (6 groups)
    #sss/#series/#inter — special routing (specific slugs)
- Method: copyMessage (raw forward, no source attribution)
- Dedup: distribution_log UNIQUE(content_id, target_chat_id)
"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Optional

from sqlalchemy import text
from telegram import Update, Message
from telegram.error import Forbidden, BadRequest, RetryAfter
from telegram.ext import ContextTypes, MessageHandler, filters

from shared.database import get_session

logger = logging.getLogger(__name__)

# ── Tier routing ────────────────────────────────────────────────────────────
# Higher tiers see all lower-tier content.
# tier_rank: G300=1, G500=2, GOD=3 (used to compare)
TAG_TO_MIN_TIER = {
    "vip": "TIER_300",
    "of":  "TIER_500",
    "god": "TIER_1299",
    "sss": "TIER_1299",       # special category — but tier 1299+
    "series": "TIER_1299",
    "inter": "TIER_1299",
}

# Slug-level routing for specialized tags
TAG_TO_SLUGS = {
    "sss":    {"SSS", "RANDOM", "SUMMER"},
    "series": {"SERIES", "RANDOM", "SUMMER"},
    "inter":  {"INTER", "RANDOM", "SUMMER"},
    # generic tier-based tags use tier mapping below
}

TIER_RANK = {"TIER_300": 1, "TIER_500": 2, "TIER_1299": 3, "TIER_2499": 4, "TIER_ADD500": 4}


async def _get_config(key: str, default: Optional[str] = None) -> Optional[str]:
    async with get_session() as s:
        r = await s.execute(text("SELECT value FROM distribution_config WHERE key=:k"), {"k": key})
        row = r.fetchone()
        return row[0] if row else default


def _parse_tags(caption: Optional[str]) -> list[str]:
    """Parse hashtags from caption — returns lowercase list without #."""
    if not caption:
        return []
    return [t.lstrip("#").lower() for t in re.findall(r"#\w+", caption)]


def _resolve_min_tier(tags: list[str]) -> str:
    """Resolve minimum tier based on tags. Default = TIER_300 (#vip)."""
    if not tags:
        return "TIER_300"
    highest = "TIER_300"
    rank = TIER_RANK[highest]
    for tag in tags:
        t = TAG_TO_MIN_TIER.get(tag)
        if t and TIER_RANK[t] > rank:
            highest = t
            rank = TIER_RANK[t]
    return highest


# ── Listener (called by guardian-bot) ───────────────────────────────────────
async def capture_storage_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Capture media messages from source group → save to queue."""
    msg = update.message or update.channel_post
    if not msg:
        return

    src = await _get_config("source_chat_id", "0")
    try:
        src_id = int(src or "0")
    except ValueError:
        src_id = 0
    if msg.chat_id != src_id:
        return  # not from storage group

    # Determine media type + file_id
    media_type = None
    file_id = None
    if msg.photo:
        media_type = "photo"
        file_id = msg.photo[-1].file_id
    elif msg.video:
        media_type = "video"
        file_id = msg.video.file_id
    elif msg.animation:
        media_type = "animation"
        file_id = msg.animation.file_id
    elif msg.document:
        media_type = "document"
        file_id = msg.document.file_id
    elif msg.video_note:
        media_type = "video_note"
        file_id = msg.video_note.file_id
    else:
        return  # text-only message — ignore

    caption = msg.caption or ""
    tags = _parse_tags(caption)
    min_tier = _resolve_min_tier(tags)
    media_group_id = msg.media_group_id

    try:
        async with get_session() as s:
            await s.execute(text("""
                INSERT INTO content_distribution_queue
                  (source_chat_id, source_msg_id, media_type, file_id, media_group_id, caption, tags, min_tier)
                VALUES (:cid, :mid, :mt, :fid, :mgid, :cap, :tags, :tier)
                ON CONFLICT (source_chat_id, source_msg_id) DO NOTHING
            """), {
                "cid": msg.chat_id, "mid": msg.message_id, "mt": media_type,
                "fid": file_id, "mgid": media_group_id, "cap": caption,
                "tags": tags, "tier": min_tier,
            })
            await s.commit()
        logger.info("📦 Captured msg %d: type=%s tier=%s tags=%s", msg.message_id, media_type, min_tier, tags)
    except Exception as exc:
        logger.error("Failed to capture msg %d: %s", msg.message_id, exc)


# ── Distributor (scheduled job) ─────────────────────────────────────────────
async def _get_target_groups(min_tier: str, specific_slugs: Optional[set] = None) -> list[tuple[int, str]]:
    """Get target VIP group chat_ids based on minimum tier or specific slugs."""
    async with get_session() as s:
        if specific_slugs:
            placeholders = ",".join(f"'{slug}'" for slug in specific_slugs)
            q = text(f"""
                SELECT chat_id, slug FROM group_registry
                WHERE slug IN ({placeholders}) AND is_active=true
            """)
            r = await s.execute(q)
        else:
            min_rank = TIER_RANK[min_tier]
            # all VIP groups whose tier rank >= min
            valid_tiers = [t for t, r in TIER_RANK.items() if r >= min_rank]
            placeholders = ",".join(f"'{t}'" for t in valid_tiers)
            q = text(f"""
                SELECT chat_id, slug FROM group_registry
                WHERE min_tier IN ({placeholders}) AND is_active=true
                  AND slug NOT IN ('STORAGE')  -- never post back to source
            """)
            r = await s.execute(q)
        return [(row[0], row[1]) for row in r.fetchall()]


def _resolve_target_slugs(tags: list[str], min_tier: str) -> tuple[Optional[set], str]:
    """Decide if tag matches specific slugs OR fall back to tier-based.
    Returns (specific_slugs_set | None, min_tier)."""
    for t in tags:
        if t in TAG_TO_SLUGS:
            return TAG_TO_SLUGS[t], min_tier
    return None, min_tier


async def distribute_pending_content(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Scheduled job — pick K items per group, copyMessage to each."""
    if (await _get_config("enabled", "true")).lower() != "true":
        logger.info("Distributor disabled by config")
        return

    items_per_round = int(await _get_config("items_per_round_per_group", "2"))
    rate_limit = float(await _get_config("rate_limit_seconds", "2"))
    source_chat_id = int(await _get_config("source_chat_id", "0"))

    bot = context.bot
    logger.info("🔄 Distribution round starting — %d items/group", items_per_round)
    import asyncio

    # For each VIP group, pick K items that haven't been posted to it yet
    async with get_session() as s:
        all_targets = await s.execute(text("""
            SELECT chat_id, slug, min_tier FROM group_registry
            WHERE min_tier IN ('TIER_300','TIER_500','TIER_1299','TIER_2499','TIER_ADD500')
              AND is_active=true AND slug <> 'STORAGE'
        """))
        targets = [(r[0], r[1], r[2]) for r in all_targets.fetchall()]

    total_posted = 0
    total_failed = 0
    for chat_id, slug, group_tier in targets:
        # Pick items that match group's tier and haven't been posted to this chat
        group_rank = TIER_RANK[group_tier]
        valid_tier_list = [t for t in TIER_RANK if TIER_RANK[t] <= group_rank]
        async with get_session() as s:
            q = text(f"""
                SELECT q.id, q.media_type, q.file_id, q.caption, q.source_chat_id, q.source_msg_id, q.tags
                FROM content_distribution_queue q
                WHERE q.is_archived=false
                  AND q.min_tier IN ({','.join(f"'{t}'" for t in valid_tier_list)})
                  AND NOT EXISTS (
                    SELECT 1 FROM distribution_log d
                    WHERE d.content_id=q.id AND d.target_chat_id=:cid AND d.success=true
                  )
                ORDER BY RANDOM()
                LIMIT :lim
            """)
            r = await s.execute(q, {"cid": chat_id, "lim": items_per_round})
            items = r.fetchall()

        for item in items:
            content_id, media_type, file_id, caption, src_cid, src_mid, tags = item
            # Specific tag routing: skip group if tag specifies other slugs
            specific_slugs, _ = _resolve_target_slugs(tags or [], group_tier)
            if specific_slugs and slug not in specific_slugs:
                continue  # this content is meant for other slugs only

            try:
                sent = await bot.copy_message(
                    chat_id=chat_id,
                    from_chat_id=src_cid,
                    message_id=src_mid,
                )
                async with get_session() as s:
                    await s.execute(text("""
                        INSERT INTO distribution_log
                          (content_id, target_chat_id, target_slug, success, target_msg_id)
                        VALUES (:cid, :tcid, :slug, true, :tmid)
                        ON CONFLICT (content_id, target_chat_id) DO NOTHING
                    """), {"cid": content_id, "tcid": chat_id, "slug": slug, "tmid": sent.message_id})
                    await s.commit()
                total_posted += 1
                logger.info("  ✓ posted content %d → %s (%s)", content_id, slug, chat_id)
            except RetryAfter as rex:
                wait = rex.retry_after + 1
                logger.warning("  Rate limit; sleeping %ds", wait)
                await asyncio.sleep(wait)
            except (Forbidden, BadRequest) as exc:
                total_failed += 1
                logger.warning("  ✗ failed content %d → %s: %s", content_id, slug, exc)
                async with get_session() as s:
                    await s.execute(text("""
                        INSERT INTO distribution_log
                          (content_id, target_chat_id, target_slug, success, error_msg)
                        VALUES (:cid, :tcid, :slug, false, :err)
                        ON CONFLICT (content_id, target_chat_id) DO NOTHING
                    """), {"cid": content_id, "tcid": chat_id, "slug": slug, "err": str(exc)[:200]})
                    await s.commit()
            except Exception as exc:
                total_failed += 1
                logger.error("  unexpected error content %d → %s: %s", content_id, slug, exc)
            await asyncio.sleep(rate_limit)

    logger.info("🏁 Round done: posted=%d failed=%d", total_posted, total_failed)


# ── Handler factory ─────────────────────────────────────────────────────────
def get_distributor_handlers() -> list:
    """Return MessageHandlers for guardian-bot to register."""
    return [
        # Capture all media from source group (chat filter applied inside handler)
        MessageHandler(
            (filters.PHOTO | filters.VIDEO | filters.ANIMATION | filters.Document.ALL | filters.VIDEO_NOTE)
            & ~filters.ChatType.PRIVATE,
            capture_storage_message,
        ),
    ]
