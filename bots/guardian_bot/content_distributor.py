"""Content Distributor v3 — Inline button UX + #free tag.

NEW in v3:
- #free tag → routes to all FREE groups (FREE1-15)
- Inline buttons after each capture (multi-select toggle UX)
- No need to type hashtags — กดปุ่มเลือกหมวด
- Auto-confirm after 10 min if no action (default = #vip)
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import text
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import Forbidden, BadRequest, RetryAfter
from telegram.ext import (
    CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters,
)

from shared.database import get_session

logger = logging.getLogger(__name__)

ADMIN_IDS = set(
    int(x) for x in os.environ.get("ADMIN_TELEGRAM_IDS", "").split(",") if x.strip().lstrip("-").isdigit()
)

# ── Tag definitions ─────────────────────────────────────────────────────────
# Order matters for button display
TAGS_ORDER = ["free", "vip", "of", "god", "sss", "series", "inter", "new"]

TAG_DISPLAY = {
    "free":   "📢 FREE",
    "vip":    "💎 VIP",
    "of":     "🔥 OF",
    "god":    "👑 GOD",
    "sss":    "⭐ SSS",
    "series": "📺 SERIES",
    "inter":  "🌍 INTER",
    "new":    "🆕 NEW",
}

TAG_TO_MIN_TIER = {
    "vip": "TIER_300",
    "of":  "TIER_500",
    "god": "TIER_1299",
    "sss": "TIER_1299",
    "series": "TIER_1299",
    "inter": "TIER_1299",
    "new":   "TIER_300",
}

# Tags that route to specific slug set (override tier-based)
TAG_TO_SLUGS = {
    "sss":    {"SSS", "RANDOM", "SUMMER"},
    "series": {"SERIES", "RANDOM", "SUMMER"},
    "inter":  {"INTER", "RANDOM", "SUMMER"},
}

# Special: 'free' tag routes to FREE groups (not VIP)
TIER_RANK = {"TIER_300": 1, "TIER_500": 2, "TIER_1299": 3, "TIER_2499": 4, "TIER_ADD500": 4}
VALID_TAGS = set(TAG_DISPLAY.keys())


async def _get_config(key: str, default: Optional[str] = None) -> Optional[str]:
    async with get_session() as s:
        r = await s.execute(text("SELECT value FROM distribution_config WHERE key=:k"), {"k": key})
        row = r.fetchone()
        return row[0] if row else default


def _parse_tags(caption: Optional[str]) -> list[str]:
    if not caption:
        return []
    return [t.lstrip("#").lower() for t in re.findall(r"#\w+", caption) if t.lstrip("#").lower() in VALID_TAGS]


def _resolve_min_tier(tags: list[str]) -> str:
    if not tags or all(t == "free" for t in tags):
        return "TIER_300"  # FREE doesn't need tier resolution
    highest = "TIER_300"
    rank = TIER_RANK[highest]
    for tag in tags:
        t = TAG_TO_MIN_TIER.get(tag)
        if t and TIER_RANK[t] > rank:
            highest = t
            rank = TIER_RANK[t]
    return highest


def _build_keyboard(queue_id: int, current_tags: list[str]) -> InlineKeyboardMarkup:
    """Build toggle button keyboard. Highlight selected tags."""
    rows = []
    row = []
    for i, tag in enumerate(TAGS_ORDER):
        selected = tag in current_tags
        label = f"✅ {TAG_DISPLAY[tag].split(' ', 1)[1]}" if selected else TAG_DISPLAY[tag]
        row.append(InlineKeyboardButton(label, callback_data=f"cd_{queue_id}_t_{tag}"))
        if len(row) == 3:
            rows.append(row); row = []
    if row:
        rows.append(row)
    # Action row
    rows.append([
        InlineKeyboardButton("✅ ส่งทันที", callback_data=f"cd_{queue_id}_confirm"),
        InlineKeyboardButton("⏭️ ข้าม / ลบ", callback_data=f"cd_{queue_id}_skip"),
    ])
    return InlineKeyboardMarkup(rows)


# ── Capture (with button reply) ─────────────────────────────────────────────
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

    media_type, file_id = None, None
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
    parsed_tags = _parse_tags(caption)
    initial_tags = parsed_tags if parsed_tags else []  # empty by default — boss picks via buttons
    min_tier = _resolve_min_tier(initial_tags) if initial_tags else "TIER_300"

    try:
        async with get_session() as s:
            r = await s.execute(text("""
                INSERT INTO content_distribution_queue
                  (source_chat_id, source_msg_id, media_type, file_id, media_group_id, caption, tags, min_tier, is_archived)
                VALUES (:cid, :mid, :mt, :fid, :mgid, :cap, :tags, :tier, true)
                ON CONFLICT (source_chat_id, source_msg_id) DO NOTHING
                RETURNING id
            """), {
                "cid": msg.chat_id, "mid": msg.message_id, "mt": media_type,
                "fid": file_id, "mgid": msg.media_group_id, "cap": caption,
                "tags": initial_tags, "tier": min_tier,
            })
            row = r.fetchone()
            await s.commit()
        if not row:
            return  # dedup hit — message already captured
        queue_id = row[0]
        logger.info("📦 Captured msg %d → queue %d type=%s", msg.message_id, queue_id, media_type)

        # Reply with selector buttons
        kb = _build_keyboard(queue_id, initial_tags)
        prompt = "📋 <b>เลือกหมวดสำหรับโพสนี้</b>\n(กดได้หลายปุ่ม — toggle เลือก/ยกเลิก)"
        await msg.reply_text(prompt, parse_mode="HTML", reply_markup=kb)
    except Exception as exc:
        logger.error("Failed to capture msg %d: %s", msg.message_id, exc)


# ── Callback handler for buttons ────────────────────────────────────────────
async def handle_cd_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle cd_* callbacks — toggle tag / confirm / skip."""
    query = update.callback_query
    if not query or not query.data:
        return
    if update.effective_user and ADMIN_IDS and update.effective_user.id not in ADMIN_IDS:
        await query.answer("❌ admin เท่านั้น", show_alert=True)
        return

    parts = query.data.split("_", 3)
    if len(parts) < 3 or parts[0] != "cd":
        await query.answer()
        return
    try:
        queue_id = int(parts[1])
    except ValueError:
        await query.answer()
        return
    action = parts[2]

    async with get_session() as s:
        r = await s.execute(text("SELECT tags, is_archived FROM content_distribution_queue WHERE id=:i"), {"i": queue_id})
        row = r.fetchone()
        if not row:
            await query.answer("❌ ไม่พบ content", show_alert=True)
            return
        current_tags = list(row[0] or [])

        if action == "t":
            # Toggle tag
            tag = parts[3] if len(parts) >= 4 else ""
            if tag not in VALID_TAGS:
                await query.answer(); return
            if tag in current_tags:
                current_tags.remove(tag)
            else:
                current_tags.append(tag)
            min_tier = _resolve_min_tier(current_tags)
            await s.execute(text("UPDATE content_distribution_queue SET tags=:t, min_tier=:tr WHERE id=:i"),
                            {"t": current_tags, "tr": min_tier, "i": queue_id})
            await s.commit()
            # Edit keyboard
            kb = _build_keyboard(queue_id, current_tags)
            try:
                await query.edit_message_reply_markup(reply_markup=kb)
            except BadRequest:
                pass
            await query.answer(f"{'+' if tag in current_tags else '−'} {tag}")
            return

        if action == "confirm":
            if not current_tags:
                await query.answer("⚠️ ยังไม่ได้เลือกหมวด — กดอย่างน้อย 1 ปุ่ม", show_alert=True)
                return
            # Set is_archived=false so distributor picks it up
            await s.execute(text("UPDATE content_distribution_queue SET is_archived=false WHERE id=:i"), {"i": queue_id})
            await s.commit()
            await query.edit_message_text(
                f"✅ <b>ยืนยันแล้ว — รอ distribute</b>\n"
                f"หมวด: {', '.join('#'+t for t in current_tags)}\n"
                f"จะ post ภายใน 60 นาทีถัดไป",
                parse_mode="HTML",
            )
            return

        if action == "skip":
            await s.execute(text("DELETE FROM content_distribution_queue WHERE id=:i"), {"i": queue_id})
            await s.commit()
            await query.edit_message_text("⏭️ ลบจาก queue แล้ว — ไม่ post")
            return


# ── Topic management commands (from v2) ─────────────────────────────────────
async def cmd_set_topic(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    if not msg or not update.effective_user:
        return
    if update.effective_user.id not in ADMIN_IDS:
        await msg.reply_text("❌ admin เท่านั้น")
        return
    args = context.args or []
    if len(args) != 1 or args[0].lstrip("#").lower() not in VALID_TAGS:
        await msg.reply_text(f"❌ ใช้: /set_topic <tag>\nValid: {', '.join(sorted(VALID_TAGS))}")
        return
    tag = args[0].lstrip("#").lower()
    topic_id = msg.message_thread_id
    if not topic_id:
        await msg.reply_text("❌ คำสั่งนี้ต้องใช้ใน topic ของกลุ่ม Forum mode")
        return
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
        await msg.reply_text(f"✅ Mapping saved: #{tag} → topic id={topic_id}")
    except Exception as exc:
        await msg.reply_text(f"❌ Error: {exc}")


async def cmd_show_topics(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    if not msg or not update.effective_user or update.effective_user.id not in ADMIN_IDS:
        return
    async with get_session() as s:
        r = await s.execute(text("SELECT tag, topic_id, topic_name FROM group_topic_routes WHERE chat_id=:c ORDER BY tag"),
                            {"c": msg.chat_id})
        rows = r.fetchall()
    if not rows:
        await msg.reply_text("ℹ️ ยังไม่มี topic mapping")
        return
    lines = [f"📌 <b>Topics — {msg.chat.title}</b>\n"]
    for tag, tid, tname in rows:
        lines.append(f"  #{tag} → {tname} (id={tid})")
    await msg.reply_text("\n".join(lines), parse_mode="HTML")


# ── Distributor (with FREE routing) ─────────────────────────────────────────
async def _get_topic_for(chat_id: int, tags: list[str]) -> Optional[int]:
    if not tags:
        return None
    async with get_session() as s:
        for tag in tags:
            r = await s.execute(text("SELECT topic_id FROM group_topic_routes WHERE chat_id=:c AND tag=:t"),
                                {"c": chat_id, "t": tag})
            row = r.fetchone()
            if row:
                return int(row[0])
    return None


async def _get_target_groups_for_content(tags: list[str]) -> list[tuple[int, str, str]]:
    """Return list of (chat_id, slug, min_tier) groups to post for given tags.

    Routing priority:
    - 'free' → all FREE groups (only)
    - Specific tag (sss/series/inter) → specific slugs
    - Otherwise → all VIP groups whose tier ≥ resolved min_tier
    """
    if not tags:
        return []
    targets: dict[int, tuple[int, str, str]] = {}  # dedup by chat_id

    async with get_session() as s:
        # 1) #free → all FREE
        if "free" in tags:
            r = await s.execute(text("""
                SELECT chat_id, slug, min_tier FROM group_registry
                WHERE min_tier='FREE' AND is_active=true
            """))
            for row in r.fetchall():
                targets[row[0]] = (row[0], row[1], row[2])

        # 2) Tier-based VIP groups for non-free tags
        non_free_tags = [t for t in tags if t != "free"]
        if non_free_tags:
            # Determine slug filter
            specific_slugs = set()
            tier_based = False
            for t in non_free_tags:
                if t in TAG_TO_SLUGS:
                    specific_slugs.update(TAG_TO_SLUGS[t])
                else:
                    tier_based = True
            min_tier = _resolve_min_tier(non_free_tags)
            min_rank = TIER_RANK.get(min_tier, 1)

            valid_tiers = [t for t in TIER_RANK if TIER_RANK[t] >= min_rank]
            placeholders = ",".join(f"'{t}'" for t in valid_tiers)
            slug_filter = ""
            if specific_slugs and not tier_based:
                slugs_ph = ",".join(f"'{s}'" for s in specific_slugs)
                slug_filter = f" AND slug IN ({slugs_ph})"
            elif specific_slugs and tier_based:
                # combine — union
                slugs_ph = ",".join(f"'{s}'" for s in specific_slugs)
                slug_filter = f" AND (slug IN ({slugs_ph}) OR min_tier IN ({placeholders}))"

            if slug_filter:
                q = text(f"""
                    SELECT chat_id, slug, min_tier FROM group_registry
                    WHERE is_active=true AND slug<>'STORAGE' {slug_filter}
                """)
            else:
                q = text(f"""
                    SELECT chat_id, slug, min_tier FROM group_registry
                    WHERE is_active=true AND slug<>'STORAGE' AND min_tier IN ({placeholders})
                """)
            r = await s.execute(q)
            for row in r.fetchall():
                targets[row[0]] = (row[0], row[1], row[2])

    return list(targets.values())


async def distribute_pending_content(context: ContextTypes.DEFAULT_TYPE) -> None:
    if (await _get_config("enabled", "true")).lower() != "true":
        return

    items_per_round = int(await _get_config("items_per_round_per_group", "2"))
    rate_limit = float(await _get_config("rate_limit_seconds", "2"))
    bot = context.bot

    logger.info("🔄 Distribution round (v3 free+button)")

    # Auto-confirm pending items older than 10 minutes (default tag = vip)
    cutoff = datetime.utcnow() - timedelta(minutes=10)
    async with get_session() as s:
        await s.execute(text("""
            UPDATE content_distribution_queue
            SET is_archived=false, tags=ARRAY['vip'], min_tier='TIER_300'
            WHERE is_archived=true AND captured_at<:c
              AND (tags IS NULL OR cardinality(tags)=0)
        """), {"c": cutoff})
        await s.commit()

    # Pick items that are NOT archived (confirmed)
    async with get_session() as s:
        r = await s.execute(text("""
            SELECT id, media_type, file_id, source_chat_id, source_msg_id, tags
            FROM content_distribution_queue
            WHERE is_archived=false
            ORDER BY RANDOM()
            LIMIT 100
        """))
        items = r.fetchall()

    total_posted = total_failed = 0
    for item in items:
        content_id, media_type, file_id, src_cid, src_mid, tags = item
        tags = list(tags or [])
        if not tags:
            continue
        targets = await _get_target_groups_for_content(tags)
        for chat_id, slug, group_tier in targets:
            # Check dedup: already posted to this group
            async with get_session() as s:
                r2 = await s.execute(text("""
                    SELECT 1 FROM distribution_log
                    WHERE content_id=:c AND target_chat_id=:t AND success=true
                """), {"c": content_id, "t": chat_id})
                if r2.fetchone():
                    continue
            # Get topic
            topic_id = await _get_topic_for(chat_id, tags)
            try:
                send_kwargs = {"chat_id": chat_id, "from_chat_id": src_cid, "message_id": src_mid}
                if topic_id is not None:
                    send_kwargs["message_thread_id"] = topic_id
                sent = await bot.copy_message(**send_kwargs)
                async with get_session() as s:
                    await s.execute(text("""
                        INSERT INTO distribution_log (content_id, target_chat_id, target_slug, success, target_msg_id)
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
                async with get_session() as s:
                    await s.execute(text("""
                        INSERT INTO distribution_log (content_id, target_chat_id, target_slug, success, error_msg)
                        VALUES (:cid, :tcid, :slug, false, :err)
                        ON CONFLICT (content_id, target_chat_id) DO NOTHING
                    """), {"cid": content_id, "tcid": chat_id, "slug": slug, "err": str(exc)[:200]})
                    await s.commit()
                logger.warning("  ✗ content %d → %s: %s", content_id, slug, exc)
            except Exception as exc:
                total_failed += 1
                logger.error("  unexpected: %s", exc)
            await asyncio.sleep(rate_limit)

    logger.info("🏁 Round done posted=%d failed=%d", total_posted, total_failed)


def get_distributor_handlers() -> list:
    return [
        CommandHandler("set_topic", cmd_set_topic),
        CommandHandler("show_topics", cmd_show_topics),
        CallbackQueryHandler(handle_cd_callback, pattern=r"^cd_"),
        MessageHandler(
            (filters.PHOTO | filters.VIDEO | filters.ANIMATION | filters.Document.ALL | filters.VIDEO_NOTE)
            & ~filters.ChatType.PRIVATE,
            capture_storage_message,
        ),
    ]
