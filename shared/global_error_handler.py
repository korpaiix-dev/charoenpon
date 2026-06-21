"""Global error handler for Telegram Application.

Catches ALL unhandled exceptions in handlers (commands, callbacks, messages)
and does 3 things:

1. Log full traceback
2. Send admin alert with user info + traceback (so we KNOW immediately)
3. DM the customer with a friendly message so they aren't ghosted

This prevents the silent-crash pattern that lost us 7 Tier-100 customers.
"""
from __future__ import annotations

import html
import logging
import traceback as _tb

from telegram import Update
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)

# Throttle: don't spam the admin group with the same error
_ERROR_THROTTLE_CACHE: dict[str, float] = {}
_ERROR_THROTTLE_WINDOW = 60.0  # seconds


def _should_alert(err_key: str) -> bool:
    import time
    now = time.time()
    last = _ERROR_THROTTLE_CACHE.get(err_key, 0)
    if now - last < _ERROR_THROTTLE_WINDOW:
        return False
    _ERROR_THROTTLE_CACHE[err_key] = now
    # Purge old entries
    if len(_ERROR_THROTTLE_CACHE) > 200:
        cutoff = now - _ERROR_THROTTLE_WINDOW * 5
        for k in list(_ERROR_THROTTLE_CACHE.keys()):
            if _ERROR_THROTTLE_CACHE[k] < cutoff:
                del _ERROR_THROTTLE_CACHE[k]
    return True



async def _mark_user_blocked_bot(telegram_id: int | None, reason: str) -> None:
    """Set users.is_blocked_bot=TRUE so DM jobs skip this user forever."""
    if not telegram_id:
        return
    try:
        from shared.database import get_session
        from sqlalchemy import text as _t
        async with get_session() as s:
            await s.execute(_t(
                "UPDATE users SET is_blocked_bot = TRUE, blocked_bot_at = NOW() "
                "WHERE telegram_id = :tg AND is_blocked_bot IS NOT TRUE"
            ), {"tg": telegram_id})
            await s.commit()
        logger.info("global_error_handler: marked is_blocked_bot tg=%s reason=%s",
                    telegram_id, reason)
    except Exception as exc:
        logger.warning("mark is_blocked_bot failed tg=%s: %s", telegram_id, exc)


async def global_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Application-level error handler — called automatically on uncaught exceptions."""
    err = context.error
    if err is None:
        return

    # Extract user info early (need tg_id for blocked-bot marking)
    _tg_id_early = None
    try:
        if isinstance(update, Update) and update.effective_user:
            _tg_id_early = update.effective_user.id
    except Exception:
        pass

    _err_str = str(err)
    _err_type = type(err).__name__

    # ─── CATEGORY 1: Stale UI (old buttons/messages) — silent ───
    _stale_ui_patterns = (
        "Query is too old",
        "query id is invalid",
        "response timeout expired",
        "Message is not modified",
        "message to edit not found",
        "message to delete not found",
    )
    if any(p in _err_str for p in _stale_ui_patterns):
        logger.info("stale UI (no alert): %s: %s", _err_type, _err_str[:120])
        return

    # ─── CATEGORY 2: Customer blocked the bot / deactivated — silent + mark DB ───
    from telegram.error import Forbidden as _Forbidden, BadRequest as _BadRequest
    _customer_state_patterns = (
        "bot was blocked by the user",
        "user is deactivated",
        "Chat not found",
        "bot was kicked",
        "bot can\'t initiate conversation",
        "Forbidden: bot can\'t initiate",
    )
    if isinstance(err, _Forbidden) or any(p in _err_str for p in _customer_state_patterns):
        try:
            await _mark_user_blocked_bot(_tg_id_early, _err_str[:80])
        except Exception:
            pass
        logger.info("customer-state (no alert): tg=%s %s: %s",
                    _tg_id_early, _err_type, _err_str[:120])
        return

    # ─── CATEGORY 3: Transient network errors — silent (PTB auto-retries) ───
    _TRANSIENT_TYPES = (
        "NetworkError", "TimedOut", "ReadError", "ConnectError",
        "WriteError", "PoolTimeout", "ReadTimeout", "ConnectTimeout",
        "RemoteProtocolError", "ConnectionResetError", "RetryAfter",
    )
    if _err_type in _TRANSIENT_TYPES or "ReadError" in _err_str or "Timed out" in _err_str:
        logger.warning("transient network (no alert): %s: %s", _err_type, _err_str[:120])
        return

    # Extract user info from update
    tg_id = None
    user_name = "unknown"
    chat_id = None
    update_type = "unknown"

    try:
        if isinstance(update, Update):
            if update.effective_user:
                tg_id = update.effective_user.id
                user_name = update.effective_user.first_name or update.effective_user.username or "user"
            if update.effective_chat:
                chat_id = update.effective_chat.id
            if update.message:
                if update.message.photo:
                    update_type = "photo"
                elif update.message.text:
                    update_type = f"text:{update.message.text[:30]}"
                else:
                    update_type = "message"
            elif update.callback_query:
                update_type = f"callback:{update.callback_query.data}"
    except Exception:
        pass

    err_type = type(err).__name__
    err_msg = str(err)[:200]

    logger.error(
        "GLOBAL_ERROR_HANDLER: tg=%s update=%s err=%s msg=%s",
        tg_id, update_type, err_type, err_msg,
    )

    # 1. Log full traceback
    tb_text = "".join(_tb.format_exception(type(err), err, err.__traceback__))
    logger.error("Full traceback:\n%s", tb_text)

    # 2. Notify customer (so they aren't ghosted)
    if isinstance(update, Update) and update.effective_chat and update_type == "photo":
        # Photo upload (likely a slip) — important to acknowledge
        try:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=(
                    "⚠️ <b>ระบบขัดข้องชั่วคราว</b>\n\n"
                    "ทีมแอดมินได้รับแจ้งเตือนแล้ว และจะตรวจสอบสลิปของคุณภายใน 10 นาทีค่ะ 🙏\n"
                    "หากเร่งด่วน กรุณาทักแอดมินที่ /support"
                ),
                parse_mode="HTML",
            )
        except Exception:
            pass

    # 3. Alert admin group (throttled)
    err_key = f"{err_type}:{err_msg[:50]}"
    if _should_alert(err_key):
        try:
            from shared.admin_alert import notify_admin_report
            tg_safe = html.escape(str(user_name))
            tb_short = html.escape(tb_text[-1500:])  # last 1.5KB of traceback
            alert_msg = (
                "🚨 <b>UNHANDLED EXCEPTION</b>\n"
                "━━━━━━━━━━━━━━\n"
                f"👤 User: {tg_safe} (<code>{tg_id or '?'}</code>)\n"
                f"📍 Update: <code>{html.escape(update_type)}</code>\n"
                f"❌ Error: <b>{err_type}</b>\n"
                f"💬 Message: <code>{html.escape(err_msg)}</code>\n"
                f"\n<pre>{tb_short}</pre>"
            )
            await notify_admin_report(alert_msg, parse_mode="HTML")
        except Exception as exc:
            logger.warning("admin alert (global error) failed: %s", exc)


__all__ = ["global_error_handler"]
