"""Content Distributor v2 — adds Topic-aware routing.

NEW in v2:
- Topic routing: per (chat_id, tag) → message_thread_id
- /set_topic <tag> command: bot picks up current topic when admin uses it
- /unset_topic <tag> command: remove mapping
- /show_topics command: list topic mappings for current chat
- Distributor uses message_thread_id if mapped
"""
from __future__ import annotations

import logging
import os
import re
from typing import Optional

from sqlalchemy import text
from telegram import Update
from telegram.error import Forbidden, BadRequest, RetryAfter
from telegram.ext import (
    CommandHandler, ContextTypes, MessageHandler, filters,
)

from shared.database import get_session

logger = logging.getLogger(__name__)

# Admin telegram_ids that can manage topic routes
ADMIN_IDS = set(
    int(x) for x in os.environ.get("ADMIN_TELEGRAM_IDS", "").split(",") if x.strip().lstrip("-").isdigit()
)

# ── Tier routing (unchanged from v1) ─────────────────────────────────────────
TAG_TO_MIN_TIER = {
    "vip": "TIER_300",
    "of":  "TIER_500",
    "god": "TIER_1299",
    "sss": "TIER_1299",
    "series": "TIER_1299",
    "inter": "TIER_1299",
}

TAG_TO_SLUGS = {
    "sss":    {"SSS", "RANDOM", "SUMMER"},
    "series": {"SERIES", "RANDOM", "SUMMER"},
    "inter":  {"INTER", "RANDOM", "SUMMER"},
}

TIER_RANK = {"TIER_300": 1, "TIER_500": 2, "TIER_1299": 3, "TIER_2499": 4, "TIER_ADD500": 4}

VALID_TAGS = {"vip", "of", "god", "sss", "series", "inter", "new", "rare"}


async def _get_config(key: str, default: Optional[str] = None) -> Optional[str]:
    async with get_session() as s:
        r = await s.execute(text("SELECT value FROM distribution_config WHERE key=:k"), {"k": key})
        row = r.fetchone()
        return row[0] if row else default


def _parse_tags(caption: Optional[str]) -> list[str]:
    if not caption:
        return []
    return [t.lstrip("#").lower() for t in re.findall(r"#\w+", caption)]


def _resolve_min_tier(tags: list[str]) -> str:
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


# ── Capture from storage group ──────────────────────────────────────────────
async def capture_storage_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message or update.channel_post
    if not msg:
        return
    src = await _get_config("source_chat_id", "0")
    try:
        src_id = int(src or "0")
    except ValueError:
        return
    if msg.chat_id != src_id:
        return

    media_type = None
    file_id = None
    if msg.photo:
        media_type, file_id = "photo", msg.photo[-1].file_id
    elif msg.video:
        media_type, file_id = "video", msg.video.file_id
    elif msg.animation:
        media_type, file_id = "animation", msg.animation.file_id
    elif msg.document:
        media_type, file_id = "document", msg.document.file_id
    elif msg.video_note:
        media_type, file_id = "video_note", msg.video_note.file_id
    else:
        return

    caption = msg.caption or ""
    tags = _parse_tags(caption)
    min_tier = _resolve_min_tier(tags)

    try:
        async with get_session() as s:
            await s.execute(text("""
                INSERT INTO content_distribution_queue
                  (source_chat_id, source_msg_id, media_type, file_id, media_group_id, caption, tags, min_tier)
                VALUES (:cid, :mid, :mt, :fid, :mgid, :cap, :tags, :tier)
                ON CONFLICT (source_chat_id, source_msg_id) DO NOTHING
            """), {
                "cid": msg.chat_id, "mid": msg.message_id, "mt": media_type,
                "fid": file_id, "mgid": msg.media_group_id, "cap": caption,
                "tags": tags, "tier": min_tier,
            })
            await s.commit()
        logger.info("📦 Captured msg %d: type=%s tier=%s tags=%s", msg.message_id, media_type, min_tier, tags)
    except Exception as exc:
        logger.error("Failed to capture msg %d: %s", msg.message_id, exc)


# ── Topic management commands ───────────────────────────────────────────────
async def cmd_set_topic(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/set_topic <tag> — map current topic to tag (admin only)."""
    msg = update.message
    if not msg or not update.effective_user:
        return
    if update.effective_user.id not in ADMIN_IDS:
        await msg.reply_text("❌ admin เท่านั้น")
        return
    args = context.args or []
    if len(args) != 1 or args[0].lstrip("#").lower() not in VALID_TAGS:
        await msg.reply_text(
            f"❌ ใช้: /set_topic <tag>\nValid tags: {', '.join(sorted(VALID_TAGS))}"
        )
        return
    tag = args[0].lstrip("#").lower()
    topic_id = msg.message_thread_id
    if not topic_id:
        await msg.reply_text("❌ คำสั่งนี้ต้องใช้ใน topic ของกลุ่ม (กลุ่ม Forum mode) — กดเข้า topic ที่ต้องการก่อน")
        return

    topic_name = None
    if msg.reply_to_message and getattr(msg.reply_to_message, "forum_topic_created", None):
        topic_name = msg.reply_to_message.forum_topic_created.name
    if not topic_name:
        topic_name = f"topic_{topic_id}"

    try:
        async with get_session() as s:
            await s.execute(text("""
                INSERT INTO group_topic_routes (chat_id, tag, topic_id, topic_name, set_by)
                VALUES (:c, :t, :tid, :tn, :u)
                ON CONFLICT (chat_id, tag) DO UPDATE
                SET topic_id=EXCLUDED.topic_id, topic_name=EXCLUDED.topic_name,
                    set_by=EXCLUDED.set_by, updated_at=NOW()
            """), {"c": msg.chat_id, "t": tag, "tid": topic_id, "tn": topic_name, "u": update.effective_user.id})
            await s.commit()
        await msg.reply_text(
            f"✅ Mapping saved!\n"
            f"กลุ่ม: {msg.chat.title}\n"
            f"Topic: {topic_name} (id={topic_id})\n"
            f"Tag: #{tag}\n\n"
            f"Content ที่มี #{tag} จะถูก post ใน topic นี้"
        )
    except Exception as exc:
        await msg.reply_text(f"❌ Error: {exc}")
        logger.error("set_topic failed: %s", exc)


async def cmd_unset_topic(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/unset_topic <tag> — remove mapping for current group + tag."""
    msg = update.message
    if not msg or not update.effective_user:
        return
    if update.effective_user.id not in ADMIN_IDS:
        return
    args = context.args or []
    if len(args) != 1:
        await msg.reply_text("❌ ใช้: /unset_topic <tag>")
        return
    tag = args[0].lstrip("#").lower()
    try:
        async with get_session() as s:
            r = await s.execute(text("""
                DELETE FROM group_topic_routes WHERE chat_id=:c AND tag=:t RETURNING topic_name
            """), {"c": msg.chat_id, "t": tag})
            row = r.fetchone()
            await s.commit()
        if row:
            await msg.reply_text(f"✅ ลบ mapping #{tag} → {row[0]}")
        else:
            await msg.reply_text(f"⚠️ ไม่พบ mapping #{tag} ในกลุ่มนี้")
    except Exception as exc:
        await msg.reply_text(f"❌ Error: {exc}")


async def cmd_show_topics(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/show_topics — list topic mappings for current chat."""
    msg = update.message
    if not msg or not update.effective_user:
        return
    if update.effective_user.id not in ADMIN_IDS:
        return
    try:
        async with get_session() as s:
            r = await s.execute(text("""
                SELECT tag, topic_id, topic_name FROM group_topic_routes
                WHERE chat_id=:c ORDER BY tag
            """), {"c": msg.chat_id})
            rows = r.fetchall()
        if not rows:
            await msg.reply_text("ℹ️ ยังไม่มี topic mapping ในกลุ่มนี้\nใช้ /set_topic <tag> ใน topic ที่ต้องการ map")
            return
        lines = [f"📌 <b>Topic Mappings — {msg.chat.title}</b>\n"]
        for tag, tid, tname in rows:
            lines.append(f"  #{tag} → {tname} (id={tid})")
        await msg.reply_text("\n".join(lines), parse_mode="HTML")
    except Exception as exc:
        await msg.reply_text(f"❌ Error: {exc}")


# ── Distributor (with topic-aware routing) ───────────────────────────────────
async def _get_topic_for(chat_id: int, tags: list[str]) -> Optional[int]:
    """Find topic_id for (chat_id, tag) — priority: most-specific tag wins."""
    if not tags:
        tags = ["vip"]  # default
    async with get_session() as s:
        for tag in tags:  # try each tag in order
            r = await s.execute(text("""
                SELECT topic_id FROM group_topic_routes WHERE chat_id=:c AND tag=:t
            """), {"c": chat_id, "t": tag})
            row = r.fetchone()
            if row:
                return int(row[0])
    return None  # post to general topic


async def distribute_pending_content(context: ContextTypes.DEFAULT_TYPE) -> None:
    if (await _get_config("enabled", "true")).lower() != "true":
        return

    items_per_round = int(await _get_config("items_per_round_per_group", "2"))
    rate_limit = float(await _get_config("rate_limit_seconds", "2"))
    bot = context.bot

    import asyncio
    logger.info("🔄 Distribution round (topic-aware)")

    async with get_session() as s:
        all_targets = await s.execute(text("""
            SELECT chat_id, slug, min_tier FROM group_registry
            WHERE min_tier IN ('TIER_300','TIER_500','TIER_1299','TIER_2499','TIER_ADD500')
              AND is_active=true AND slug <> 'STORAGE'
        """))
        targets = [(r[0], r[1], r[2]) for r in all_targets.fetchall()]

    total_posted = total_failed = 0
    for chat_id, slug, group_tier in targets:
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
            # Specific tag → only certain slugs?
            specific_slugs = None
            for t in (tags or []):
                if t in TAG_TO_SLUGS:
                    specific_slugs = TAG_TO_SLUGS[t]
                    break
            if specific_slugs and slug not in specific_slugs:
                continue

            # Resolve topic for this chat + tags
            topic_id = await _get_topic_for(chat_id, tags or [])

            try:
                send_kwargs = {"chat_id": chat_id, "from_chat_id": src_cid, "message_id": src_mid}
                if topic_id is not None:
                    send_kwargs["message_thread_id"] = topic_id
                sent = await bot.copy_message(**send_kwargs)
                async with get_session() as s:
                    await s.execute(text("""
                        INSERT INTO distribution_log
                          (content_id, target_chat_id, target_slug, success, target_msg_id)
                        VALUES (:cid, :tcid, :slug, true, :tmid)
                        ON CONFLICT (content_id, target_chat_id) DO NOTHING
                    """), {"cid": content_id, "tcid": chat_id, "slug": slug, "tmid": sent.message_id})
                    await s.commit()
                total_posted += 1
                logger.info("  ✓ content %d → %s topic=%s", content_id, slug, topic_id or "general")
            except RetryAfter as rex:
                await asyncio.sleep(rex.retry_after + 1)
            except (Forbidden, BadRequest) as exc:
                total_failed += 1
                logger.warning("  ✗ content %d → %s: %s", content_id, slug, exc)
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
                logger.error("  unexpected: %s", exc)
            await asyncio.sleep(rate_limit)

    logger.info("🏁 Round done posted=%d failed=%d", total_posted, total_failed)


def get_distributor_handlers() -> list:
    """Handlers for guardian-bot to register."""
    return [
        # Topic management commands
        CommandHandler("set_topic", cmd_set_topic),
        CommandHandler("unset_topic", cmd_unset_topic),
        CommandHandler("show_topics", cmd_show_topics),
        # Capture all media (chat filter applied inside handler)
        MessageHandler(
            (filters.PHOTO | filters.VIDEO | filters.ANIMATION | filters.Document.ALL | filters.VIDEO_NOTE)
            & ~filters.ChatType.PRIVATE,
            capture_storage_message,
        ),
    ]
