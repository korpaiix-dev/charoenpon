"""Gacha clip-pack listener — record message IDs from CLIP source groups.

Whenever boss/admin posts media (photo/video/animation/document/voice) into
one of the CLIP_A/B/C source groups, this handler upserts (source_chat_id,
message_id) into gacha_prize_messages so the prize worker can forward them
to winners later.
"""
from __future__ import annotations

import logging
from typing import Set

from telegram import Update
from telegram.ext import ContextTypes, MessageHandler, filters

from sqlalchemy import text as _t
from shared.database import get_session

logger = logging.getLogger(__name__)

# Cache of clip source chat IDs (loaded once at startup, refreshable)
_CLIP_CHAT_IDS: Set[int] = set()
_CACHE_LOADED = False


async def _load_clip_chat_ids() -> None:
    """Load CLIP source_chat_id values from gachapon_prizes."""
    global _CLIP_CHAT_IDS, _CACHE_LOADED
    async with get_session() as s:
        r = await s.execute(_t("""
            SELECT DISTINCT source_chat_id FROM gachapon_prizes
            WHERE type = 'clip_pack' AND source_chat_id IS NOT NULL
        """))
        _CLIP_CHAT_IDS = {int(row[0]) for row in r.all()}
    _CACHE_LOADED = True
    logger.info("Gacha clip source chats loaded: %s", _CLIP_CHAT_IDS)


def _is_media_message(msg) -> str | None:
    """Return media kind if message contains relevant media, else None."""
    if msg.photo:
        return "photo"
    if msg.video:
        return "video"
    if msg.animation:
        return "animation"
    if msg.document:
        return "document"
    if msg.voice:
        return "voice"
    if msg.video_note:
        return "video_note"
    if msg.audio:
        return "audio"
    return None


async def on_clip_group_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Capture media posts in CLIP source groups."""
    global _CACHE_LOADED
    if not _CACHE_LOADED:
        try:
            await _load_clip_chat_ids()
        except Exception as e:
            logger.warning("Failed to load clip chats: %s", e)
            return

    msg = update.effective_message
    if not msg or not msg.chat:
        return

    chat_id = msg.chat.id
    if chat_id not in _CLIP_CHAT_IDS:
        return

    kind = _is_media_message(msg)
    if not kind:
        return  # text-only / system events ignored

    # Upsert into gacha_prize_messages
    try:
        async with get_session() as s:
            await s.execute(_t("""
                INSERT INTO gacha_prize_messages
                    (source_chat_id, message_id, kind)
                VALUES (:c, :m, :k)
                ON CONFLICT (source_chat_id, message_id) DO NOTHING
            """), {"c": chat_id, "m": msg.message_id, "k": kind})
            await s.commit()
        logger.info("Captured gacha clip: chat=%s mid=%s kind=%s",
                    chat_id, msg.message_id, kind)
    except Exception as e:
        logger.exception("Failed to capture clip message: %s", e)


def get_gacha_clip_listener_handler():
    """Return a MessageHandler that catches media in any group chat.
    The handler filters internally by chat_id to keep config low-touch.
    """
    return MessageHandler(
        filters.ChatType.GROUPS & (
            filters.PHOTO | filters.VIDEO | filters.ANIMATION |
            filters.Document.ALL | filters.VOICE | filters.AUDIO |
            filters.VIDEO_NOTE
        ),
        on_clip_group_message,
    )


__all__ = ["get_gacha_clip_listener_handler", "_load_clip_chat_ids"]
