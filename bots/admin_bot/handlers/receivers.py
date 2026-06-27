"""Admin /receivers - view + reset receiver account balances."""
from __future__ import annotations

import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CommandHandler, CallbackQueryHandler, ContextTypes
from shared.admin_perms import is_admin_for_bot

logger = logging.getLogger(__name__)


def _is_admin(user_id: int) -> bool:
    """Migrated to shared.admin_perms (DB-first with env fallback)."""
    return is_admin_for_bot(user_id, "admin_bot")


async def _fetch_accounts():
    from shared.database import get_session
    from sqlalchemy import text as _t
    async with get_session() as s:
        r = await s.execute(_t(
            "SELECT id, owner_name, bank_name_th, account_no, bank_last5, "
            "promptpay_number, weight, cumulative_received, alert_threshold, "
            "last_alert_at_amount, enabled, notes "
            "FROM receiver_accounts ORDER BY id"
        ))
        rows = r.fetchall()
    return [dict(row._mapping) for row in rows]


def _format_account(acc) -> str:
    cum = float(acc.get("cumulative_received") or 0)
    th = float(acc.get("alert_threshold") or 5000)
    pct = (cum / th * 100) if th > 0 else 0
    bar = "GREEN" if pct < 60 else ("YELLOW" if pct < 90 else "RED")
    on = "ON" if acc.get("enabled") else "OFF"
    notes = acc.get("notes") or ""
    lines = [
        f"[{on}] <b>#{acc['id']} {acc['owner_name']}</b>",
        f"   bank: {acc['bank_name_th']} <code>{acc.get('account_no','-')}</code>",
        f"   promptpay: <code>{acc.get('promptpay_number','-')}</code>",
        f"   [{bar}] cum: <b>{cum:,.0f} THB</b> / {th:,.0f} ({pct:.0f}%)",
        f"   weight: {acc.get('weight', 1)}",
    ]
    if notes:
        lines.append(f"   note: {notes}")
    return "\n".join(lines)


def _build_keyboard(accounts):
    rows = []
    for a in accounts:
        if a.get("enabled"):
            label = f"Reset #{a['id']} ({a['owner_name'][:20]})"
            rows.append([InlineKeyboardButton(label, callback_data=f"recv_reset:{a['id']}")])
    if not rows:
        return None
    return InlineKeyboardMarkup(rows)


def _format_list(accounts) -> str:
    text = "<b>Receiver Accounts</b>\n\n"
    text += "\n\n".join(_format_account(a) for a in accounts)
    text += f"\n\n<i>{len(accounts)} accounts | /receivers reset &lt;id&gt;</i>"
    return text


async def cmd_receivers(update, context):
    if not _is_admin(update.effective_user.id):
        return
    args = context.args or []
    if args and args[0].lower() == "reset" and len(args) >= 2:
        return await _reset_handler(update, context, args[1])
    accounts = await _fetch_accounts()
    if not accounts:
        await update.message.reply_text("no receiver_accounts")
        return
    await update.message.reply_text(
        _format_list(accounts), parse_mode="HTML",
        reply_markup=_build_keyboard(accounts)
    )


async def _reset_handler(update, context, account_id):
    try:
        acc_id = int(account_id)
    except Exception:
        await update.message.reply_text(f"bad id: {account_id}")
        return
    from shared.receiver_pool import reset_account
    from shared.database import get_session
    from sqlalchemy import text as _t
    ok = await reset_account(acc_id)
    if not ok:
        await update.message.reply_text(f"not found: {acc_id}")
        return
    async with get_session() as s:
        r = await s.execute(_t("SELECT owner_name FROM receiver_accounts WHERE id=:id"), {"id": acc_id})
        row = r.fetchone()
    owner = row[0] if row else f"id={acc_id}"
    await update.message.reply_text(
        f"OK reset #{acc_id} ({owner}) cumulative = 0", parse_mode="HTML"
    )
    try:
        from shared.utils import log_admin_action
        await log_admin_action(
            admin_id=update.effective_user.id,
            action="receiver_reset",
            target_type="receiver_account",
            target_id=acc_id,
            details=f"owner={owner}"
        )
    except Exception as e:
        logger.warning("audit log failed: %s", e)


async def cb_recv_reset(update, context):
    q = update.callback_query
    if not q or not q.from_user:
        return
    if not _is_admin(q.from_user.id):
        try: await q.answer("forbidden", show_alert=True)
        except Exception: pass
        return
    try: await q.answer("Resetting...")
    except Exception: pass
    try:
        acc_id = int((q.data or "").split(":")[1])
    except Exception:
        return
    from shared.receiver_pool import reset_account
    ok = await reset_account(acc_id)
    if not ok:
        try: await q.answer("reset failed", show_alert=True)
        except Exception: pass
        return
    try:
        from shared.utils import log_admin_action
        await log_admin_action(
            admin_id=q.from_user.id,
            action="receiver_reset",
            target_type="receiver_account",
            target_id=acc_id,
            details="inline button"
        )
    except Exception as e:
        logger.warning("audit log failed: %s", e)
    accounts = await _fetch_accounts()
    text = _format_list(accounts)
    actor = q.from_user.username or q.from_user.first_name or "admin"
    text += f"\n\n<i>OK Reset #{acc_id} by @{actor}</i>"
    try:
        await q.edit_message_text(text, parse_mode="HTML", reply_markup=_build_keyboard(accounts))
    except Exception as e:
        logger.warning("edit failed: %s", e)


def get_receivers_handlers():
    return [
        CommandHandler("receivers", cmd_receivers),
        CallbackQueryHandler(cb_recv_reset, pattern=r"^recv_reset:\d+$"),
    ]


__all__ = ["cmd_receivers", "cb_recv_reset", "get_receivers_handlers"]
