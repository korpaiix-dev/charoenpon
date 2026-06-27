"""Workaround DM-forward capture for gacha clips.

Telegram doesn't deliver message updates to bots in new -100**3**xxxx supergroups,
even with privacy=off + admin. So instead admin forwards clips to bot DM and
this handler reads the forward_from_chat metadata to record them.

Usage:
  Admin/owner DMs the bot → forward media from CLIP_A/B/C source group
  → bot replies "✅ บันทึก {source_title} msg_id {N}"
"""
from __future__ import annotations

import logging
import os

from telegram import Update
from telegram.ext import ContextTypes, MessageHandler, filters
from telegram.constants import ParseMode

from sqlalchemy import text as _t
from shared.database import get_session

logger = logging.getLogger(__name__)

# Only these admin telegram_ids are allowed to capture via DM
# B.3 (2026-06-27): Migrated to DB-driven admin perms via shared.admin_perms
# Hardcoded set replaced — kept here only as legacy doc:
# Was: {8502597269 บอสไผ่, 7387557933 wasu}
from shared.admin_perms import is_admin_for_bot as _is_admin_for_bot

class _DBAdminSet:
    """Set-like object that checks admin_bot_permissions (guardian_bot key) live."""
    def __contains__(self, uid: int) -> bool:
        try:
            return _is_admin_for_bot(int(uid), "guardian_bot")
        except Exception:
            return False

ADMIN_IDS = _DBAdminSet()

# Allowed source chat IDs (CLIP groups)
ALLOWED_SOURCES = {
    -1003750777891: "CLIP_A (รางวัล1)",
    -1003965751557: "CLIP_B (รางวัล2)",
    -1003952631536: "CLIP_C (รางวัล3)",
}


def _media_kind(msg) -> str | None:
    if msg.photo: return "photo"
    if msg.video: return "video"
    if msg.animation: return "animation"
    if msg.document: return "document"
    if msg.voice: return "voice"
    if msg.video_note: return "video_note"
    if msg.audio: return "audio"
    return None


async def on_dm_forward(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle forwarded media in DM from admin."""
    msg = update.effective_message
    user = update.effective_user
    if not msg or not user:
        return
    if user.id not in ADMIN_IDS:
        return  # silent ignore non-admin

    # Must be a forwarded message
    fwd_origin = msg.forward_origin
    if not fwd_origin:
        return  # not a forward — ignore

    # Get source chat_id from forward_origin
    src_chat_id = None
    src_message_id = None
    try:
        # MessageOriginChannel or MessageOriginChat
        if hasattr(fwd_origin, "chat") and fwd_origin.chat:
            src_chat_id = fwd_origin.chat.id
        # MessageOriginChannel has message_id attribute
        if hasattr(fwd_origin, "message_id"):
            src_message_id = fwd_origin.message_id
    except Exception as e:
        logger.warning("failed to parse forward_origin: %s", e)
        return

    if src_chat_id is None or src_message_id is None:
        await msg.reply_text(
            "⚠️ ไม่พบ source ของ forward — ส่งจากกลุ่มไหน?",
            parse_mode=ParseMode.HTML,
        )
        return

    if src_chat_id not in ALLOWED_SOURCES:
        await msg.reply_text(
            f"⚠️ chat_id {src_chat_id} ไม่ใช่กลุ่ม CLIP ที่กำหนด\n"
            f"กลุ่มที่อนุญาต: {list(ALLOWED_SOURCES.values())}",
            parse_mode=ParseMode.HTML,
        )
        return

    kind = _media_kind(msg)
    if not kind:
        return  # not media — silent ignore

    # Upsert into DB
    try:
        async with get_session() as s:
            await s.execute(_t("""
                INSERT INTO gacha_prize_messages (source_chat_id, message_id, kind)
                VALUES (:c, :m, :k)
                ON CONFLICT (source_chat_id, message_id) DO NOTHING
                RETURNING id
            """), {"c": src_chat_id, "m": src_message_id, "k": kind})

            # Count current clips for this source
            cnt = await s.execute(_t("""
                SELECT count(*) FROM gacha_prize_messages WHERE source_chat_id = :c
            """), {"c": src_chat_id})
            total = cnt.scalar()
            await s.commit()
        logger.info("DM-captured: src=%s mid=%s kind=%s total_now=%s",
                    src_chat_id, src_message_id, kind, total)

        # Quiet reply (only for first capture per source to avoid spam)
        # Actually always reply briefly so admin knows
        label = ALLOWED_SOURCES[src_chat_id]
        await msg.reply_text(
            f"✅ บันทึก <b>{label}</b> #{src_message_id} ({kind}) — รวม <b>{total}</b> clips",
            parse_mode=ParseMode.HTML,
            disable_notification=True,
        )
    except Exception as e:
        logger.exception("DB write failed: %s", e)
        await msg.reply_text(f"⚠️ DB error: {e}", parse_mode=ParseMode.HTML)


def get_dm_capture_handler():
    """Return MessageHandler that catches forwarded media in private chats."""
    return MessageHandler(
        filters.ChatType.PRIVATE & filters.FORWARDED & (
            filters.PHOTO | filters.VIDEO | filters.ANIMATION |
            filters.Document.ALL | filters.VOICE | filters.AUDIO |
            filters.VIDEO_NOTE
        ),
        on_dm_forward,
    )


__all__ = ["get_dm_capture_handler", "ALLOWED_SOURCES"]
