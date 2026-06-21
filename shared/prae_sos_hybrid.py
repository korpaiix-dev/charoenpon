"""SOS Hybrid handler — runs BEFORE prae v2.

If user message matches SOS keywords:
1. Rate-limit check (1 SOS per user per 6 hours)
2. INSERT sos_alerts
3. Send admin group notification with action buttons
4. Then continue to prae v2 (which will reply with /getlink guidance)
"""
from __future__ import annotations

import logging
import os
import time

logger = logging.getLogger(__name__)


# SOS keywords (กว้างกว่า v1 เพื่อจับ pattern ลูกค้าจริง)
SOS_KEYWORDS = [
    # Plain
    "SOS", "sos", "Sos", "เอสโอเอส",
    # เข้ากลุ่มไม่ได้
    "เข้าไม่ได้", "เข้าไม่ได", "เข้ากลุ่มไม่ได้", "กลุ่มไม่ขึ้น",
    "กดไม่ได้", "กลุ่มบิน", "กลุ่มหาย",
    # ลิงก์
    "ลิงก์หมด", "ลิ้งค์หมด", "ลิ้งหมด",
    "ลิงค์เข้าไม่ได้", "ลิ้งค์เข้าไม่ได้", "ลิงค์หมดอายุ",
    "ลิงก์หมดอายุ", "ลิ้งหมดอายุ",
    "ขอลิ้ง", "ขอลิงก์",
]


def is_sos(text: str) -> bool:
    return any(kw in text for kw in SOS_KEYWORDS)


async def trigger_sos_workflow(
    telegram_id: int,
    user_text: str,
    first_name: str | None,
    username: str | None,
    context,
) -> bool:
    """Trigger SOS workflow — return True if triggered (caller may still want to send AI reply).

    Skips if user has SOS within last 6 hours (avoid spam).
    """
    # Rate limit per user
    last_sos = context.user_data.get("last_sos_time", 0)
    if time.time() - last_sos < 21600:  # 6h
        logger.info("SOS rate-limited: tg=%s", telegram_id)
        return False
    context.user_data["last_sos_time"] = time.time()

    # 1. INSERT sos_alerts
    try:
        import asyncpg
        db_url = (os.environ.get("DATABASE_URL", "")
                  .replace("postgresql+asyncpg://", "postgresql://"))
        if db_url:
            conn = await asyncpg.connect(db_url)
            try:
                await conn.execute(
                    "INSERT INTO sos_alerts (telegram_id, first_name, username, message) "
                    "VALUES ($1, $2, $3, $4)",
                    telegram_id, first_name or "", username or "", user_text[:500],
                )
            finally:
                await conn.close()
            logger.info("SOS recorded: tg=%s msg=%r", telegram_id, user_text[:60])
    except Exception as e:
        logger.exception("SOS INSERT failed: %s", e)

    # 2. Notify admin group (use existing admin alert infra)
    try:
        from shared.admin_alert import _admin_group_id
        admin_chat = _admin_group_id()
        if admin_chat:
            from telegram import Bot, InlineKeyboardMarkup, InlineKeyboardButton
            admin_tok = os.environ.get("ADMIN_BOT_TOKEN") or os.environ.get("SALES_BOT_TOKEN", "")
            b = Bot(token=admin_tok)
            await b.initialize()
            try:
                user_label = first_name or username or f"tg:{telegram_id}"
                msg = (
                    f"🚨 <b>SOS</b> — ลูกค้าทักว่าเข้ากลุ่มไม่ได้\n"
                    f"👤 <b>{user_label}</b>"
                    f"{' @' + username if username else ''}\n"
                    f"🆔 <code>{telegram_id}</code>\n"
                    f"💬 <i>{user_text[:200]}</i>"
                )
                # FIX 2026-06-17: "tg://user?id=X" causes Button_user_invalid when bot has no contact
                _btn_rows = []
                if username:
                    _btn_rows.append([InlineKeyboardButton("\U0001F4AC \u0e40\u0e1b\u0e34\u0e14\u0e41\u0e0a\u0e17", url=f"https://t.me/{username}")])
                _btn_rows.append([InlineKeyboardButton("\u2705 Resolve", callback_data=f"sos_resolve:{telegram_id}")])
                kb = InlineKeyboardMarkup(_btn_rows)
                _sent_msg = await b.send_message(
                    chat_id=admin_chat, text=msg, parse_mode="HTML",
                    reply_markup=kb, disable_web_page_preview=True,
                )
                # Save message_id so AI tools can edit this same message later
                try:
                    import asyncpg as _asy
                    _db_url2 = os.environ.get("DATABASE_URL", "")
                    if _sent_msg and _db_url2:
                        _c = await _asy.connect(_db_url2)
                        try:
                            await _c.execute(
                                "UPDATE sos_alerts SET admin_msg_id = $1, admin_chat_id = $2 "
                                "WHERE id = (SELECT id FROM sos_alerts WHERE telegram_id = $3 "
                                "ORDER BY created_at DESC LIMIT 1)",
                                int(_sent_msg.message_id), int(admin_chat), telegram_id,
                            )
                        finally:
                            await _c.close()
                except Exception as _exc:
                    logger.warning("SOS msg_id save failed: %s", _exc)
            finally:
                await b.shutdown()
            logger.info("SOS admin notified: tg=%s", telegram_id)
    except Exception as e:
        logger.warning("SOS admin notify failed: %s", e)

    return True


__all__ = ["SOS_KEYWORDS", "is_sos", "trigger_sos_workflow"]
