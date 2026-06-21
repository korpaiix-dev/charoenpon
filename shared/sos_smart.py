"""Smart SOS alert + Prae AI feedback loop.

Replaces the old "fire alert, hope admin knows what AI did" pattern with:
  - SOS alert fires with PENDING state + [AI Prae: กำลังจัดการ...] placeholder
  - Prae tool runs → calls `update_sos_with_ai_result(...)`
  - Alert message edits itself in place with what AI did
  - Buttons reshuffle to fit the new state

Buttons:
  PENDING (just fired)               → [✅ Resolve]
  AI SUCCESS (link sent)             → [✅ Resolve] [🔨 Ban]
  AI FAIL (no sub / needs admin)     → [🔄 Resend] [❌ Deny] [🔨 Ban] [✅ Resolve]

DB columns added on sos_alerts:
  admin_msg_id BIGINT        — Telegram message_id of the alert
  admin_chat_id BIGINT       — chat where alert was sent
  ai_status VARCHAR(64)      — "active"/"expired"/"never_paid"/"link_sent"/"error"
  ai_detail TEXT             — short human description (50-200 chars)
  ai_acted_at TIMESTAMP      — when Prae completed the action
"""
from __future__ import annotations

import logging
import os
from html import escape

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import text as _t

logger = logging.getLogger(__name__)


# ─── Keyboard builders ──────────────────────────────────────────────────


def _kb_pending(telegram_id: int, username: str | None = None) -> InlineKeyboardMarkup:
    """Initial keyboard — before AI acts."""
    rows = []
    if username:
        rows.append([InlineKeyboardButton("💬 เปิดแชท", url=f"https://t.me/{username}")])
    rows.append([InlineKeyboardButton("✅ จัดการแล้ว", callback_data=f"sos_resolve:{telegram_id}")])
    return InlineKeyboardMarkup(rows)


def _kb_ai_success(telegram_id: int, username: str | None = None) -> InlineKeyboardMarkup:
    """AI helped customer successfully (e.g., sent fresh invite link)."""
    rows = []
    if username:
        rows.append([InlineKeyboardButton("💬 เปิดแชท", url=f"https://t.me/{username}")])
    rows.append([
        InlineKeyboardButton("✅ จัดการแล้ว", callback_data=f"sos_resolve:{telegram_id}"),
        InlineKeyboardButton("🔨 แบนถาวร", callback_data=f"ban_user:{telegram_id}:from_sos"),
    ])
    return InlineKeyboardMarkup(rows)


def _kb_ai_fail(telegram_id: int, username: str | None = None) -> InlineKeyboardMarkup:
    """AI could not help — give admin full control."""
    rows = []
    if username:
        rows.append([InlineKeyboardButton("💬 เปิดแชท", url=f"https://t.me/{username}")])
    rows.append([
        InlineKeyboardButton("🔄 ส่งลิงก์อีกครั้ง", callback_data=f"sos_resend_{telegram_id}"),
        InlineKeyboardButton("❌ ปฏิเสธ", callback_data=f"sos_deny_{telegram_id}"),
    ])
    rows.append([
        InlineKeyboardButton("🔨 แบนถาวร", callback_data=f"ban_user:{telegram_id}:from_sos"),
        InlineKeyboardButton("✅ จัดการแล้ว", callback_data=f"sos_resolve:{telegram_id}"),
    ])
    return InlineKeyboardMarkup(rows)


# ─── AI status → message line + keyboard variant ────────────────────────


def _format_ai_line(status: str, detail: str) -> str:
    """Pretty single-line AI status, e.g. ✅ ส่งลิงก์ ห้องมีคนชัก (หมดอายุ 19/6)."""
    safe = escape(detail or "")
    if status in ("active", "link_sent", "success"):
        return f"🤖 <b>AI Prae:</b> ✅ {safe}"
    if status in ("expired",):
        return f"🤖 <b>AI Prae:</b> ⚠️ {safe}"
    if status in ("never_paid",):
        return f"🤖 <b>AI Prae:</b> ⚠️ {safe}"
    if status in ("error", "fail", "unknown"):
        return f"🤖 <b>AI Prae:</b> ❌ {safe or 'ทำไม่สำเร็จ'}"
    return f"🤖 <b>AI Prae:</b> ℹ️ {safe}"


def _kb_for_status(status: str, telegram_id: int, username: str | None) -> InlineKeyboardMarkup:
    if status in ("active", "link_sent", "success"):
        return _kb_ai_success(telegram_id, username)
    # All non-success → give admin full control
    return _kb_ai_fail(telegram_id, username)


# ─── Public API ─────────────────────────────────────────────────────────


async def update_sos_with_ai_result(
    telegram_id: int,
    status: str,
    detail: str = "",
) -> bool:
    """Edit the most-recent PENDING SOS alert for this user with AI outcome.

    Called by prae_tools.handle_group_access_issue after it figures out what
    to do for the customer.

    Returns True if an alert was found + edited, False otherwise.
    """
    from shared.database import get_session

    # 1. find the latest PENDING SOS for this user
    try:
        async with get_session() as s:
            r = await s.execute(_t("""
                SELECT id, admin_msg_id, admin_chat_id, username
                FROM sos_alerts
                WHERE telegram_id = :tg
                  AND status = 'PENDING'
                  AND admin_msg_id IS NOT NULL
                ORDER BY created_at DESC
                LIMIT 1
            """), {"tg": telegram_id})
            row = r.fetchone()
            if not row:
                logger.info("update_sos_with_ai_result: no PENDING alert found for tg=%s", telegram_id)
                return False
            sos_id = row.id
            msg_id = row.admin_msg_id
            chat_id = row.admin_chat_id
            username = row.username
    except Exception as exc:
        logger.warning("update_sos_with_ai_result lookup failed: %s", exc)
        return False

    if not msg_id or not chat_id:
        return False

    # 2. mark ai_status in DB first (idempotent — survives even if Telegram edit fails)
    try:
        async with get_session() as s:
            await s.execute(_t("""
                UPDATE sos_alerts
                   SET ai_status = :st, ai_detail = :dt, ai_acted_at = NOW()
                 WHERE id = :sid
            """), {"st": status[:64], "dt": (detail or "")[:1000], "sid": sos_id})
            await s.commit()
    except Exception as exc:
        logger.warning("update_sos_with_ai_result DB update failed: %s", exc)

    # 3. edit the Telegram alert in place
    try:
        from telegram import Bot
        admin_tok = os.environ.get("ADMIN_BOT_TOKEN") or os.environ.get("SALES_BOT_TOKEN", "")
        if not admin_tok:
            return False
        b = Bot(token=admin_tok)
        await b.initialize()
        try:
            # Get current text
            ai_line = _format_ai_line(status, detail)
            new_kb = _kb_for_status(status, telegram_id, username)

            # We don't know the exact original text without re-fetching, so we
            # rebuild it from DB. (Simpler than carrying original text through.)
            async with get_session() as s2:
                r = await s2.execute(_t("""
                    SELECT telegram_id, first_name, username, message
                    FROM sos_alerts WHERE id = :sid
                """), {"sid": sos_id})
                row2 = r.fetchone()
            if not row2:
                return False

            user_label = escape(row2.first_name or row2.username or f"tg:{row2.telegram_id}")
            user_at = ' @' + escape(row2.username) if row2.username else ''
            user_text = escape(row2.message or "")[:200]
            new_text = (
                f"🚨 <b>SOS</b> — ลูกค้าทักว่าเข้ากลุ่มไม่ได้\n"
                f"👤 <b>{user_label}</b>{user_at}\n"
                f"🆔 <code>{row2.telegram_id}</code>\n"
                f"💬 <i>{user_text}</i>\n"
                f"\n──────────\n"
                f"{ai_line}\n"
                f"──────────"
            )

            await b.edit_message_text(
                chat_id=chat_id,
                message_id=msg_id,
                text=new_text,
                parse_mode="HTML",
                reply_markup=new_kb,
                disable_web_page_preview=True,
            )
            logger.info("SOS alert updated with AI result: sos_id=%s status=%s", sos_id, status)
            return True
        finally:
            try: await b.shutdown()
            except Exception: pass
    except Exception as exc:
        logger.warning("update_sos_with_ai_result edit failed: %s", exc)
        return False


__all__ = [
    "update_sos_with_ai_result",
    "_kb_pending",
    "_kb_ai_success",
    "_kb_ai_fail",
]
