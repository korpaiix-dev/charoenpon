"""/ban + /unban admin commands + inline Ban button handler.

Usage:
  /ban <telegram_id> [reason...]
  /ban <telegram_id> remove_blacklist=1     # opt-in: clear blacklist too
  /unban <telegram_id>
  /unban <telegram_id> remove_blacklist=1 unkick=1   # full reverse

Inline buttons attached to slip-review / scam alerts use callback data
  `ban_user:<telegram_id>:<reason_short>`
"""
from __future__ import annotations

import logging
import os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CommandHandler, CallbackQueryHandler, ContextTypes
from shared.admin_perms import is_admin_for_bot

logger = logging.getLogger(__name__)


def _is_admin(user_id: int) -> bool:
    """Migrated to shared.admin_perms (DB-first with env fallback)."""
    return is_admin_for_bot(user_id, "admin_bot")


def build_ban_button(telegram_id: int, reason_short: str = "scam_slip") -> InlineKeyboardMarkup:
    """Reusable: attach to any admin alert that may warrant a ban."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(
            "🔨 แบนถาวร (ครอบทุกระบบ)",
            callback_data=f"ban_user:{telegram_id}:{reason_short[:40]}",
        )],
    ])


async def cmd_ban(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """`/ban <tg> [reason...]`"""
    if not update.effective_user or not _is_admin(update.effective_user.id):
        return
    args = context.args or []
    if not args:
        await update.message.reply_text(
            "ใช้: <code>/ban &lt;telegram_id&gt; [เหตุผล...]</code>\n"
            "ตัวอย่าง: <code>/ban 8755901950 scam_dam_ring</code>",
            parse_mode="HTML",
        )
        return
    try:
        tg_id = int(args[0])
    except ValueError:
        await update.message.reply_text(f"❌ เลข Telegram ID ไม่ถูกต้อง: {args[0]}")
        return

    rest = " ".join(args[1:]).strip() or "manual_ban_by_admin"
    # Parse optional flags
    remove_blacklist = False
    reason_parts = []
    for tok in rest.split():
        if tok.startswith("remove_blacklist=") and tok.endswith("1"):
            remove_blacklist = True
        else:
            reason_parts.append(tok)
    reason = " ".join(reason_parts)

    # Status ping
    try:
        progress = await update.message.reply_text(
            f"🔨 กำลังแบนผู้ใช้ <code>{tg_id}</code>...",
            parse_mode="HTML",
        )
    except Exception:
        progress = None

    from shared.ban_service import ban_user
    result = await ban_user(
        telegram_id=tg_id,
        reason=reason,
        admin_id=update.effective_user.id,
        add_to_blacklist=True,
        kick_from_groups=True,
    )

    msg = result.report_html()
    try:
        if progress:
            await progress.edit_text(msg, parse_mode="HTML")
        else:
            await update.message.reply_text(msg, parse_mode="HTML")
    except Exception as exc:
        logger.warning("ban report edit failed: %s", exc)
        try:
            await update.message.reply_text(msg, parse_mode="HTML")
        except Exception:
            pass


async def cmd_unban(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """`/unban <tg> [remove_blacklist=1] [unkick=1]`"""
    if not update.effective_user or not _is_admin(update.effective_user.id):
        return
    args = context.args or []
    if not args:
        await update.message.reply_text(
            "ใช้: <code>/unban &lt;telegram_id&gt; [remove_blacklist=1] [unkick=1]</code>\n"
            "• <code>remove_blacklist=1</code> = ลบจากบัญชีดำด้วย\n"
            "• <code>unkick=1</code> = ปลดเตะจากกลุ่มด้วย",
            parse_mode="HTML",
        )
        return
    try:
        tg_id = int(args[0])
    except ValueError:
        await update.message.reply_text(f"❌ เลข Telegram ID ไม่ถูกต้อง: {args[0]}")
        return

    flags = set(args[1:])
    remove_bl = "remove_blacklist=1" in flags
    unkick = "unkick=1" in flags

    from shared.ban_service import unban_user
    result = await unban_user(
        telegram_id=tg_id,
        admin_id=update.effective_user.id,
        remove_from_blacklist=remove_bl,
        unkick_from_groups=unkick,
    )

    if not result.success:
        await update.message.reply_text(
            f"❌ ยกเลิกแบนไม่สำเร็จ: {result.error}", parse_mode="HTML"
        )
        return
    from html import escape
    name = escape(result.first_name or "ลูกค้า")
    lines = [
        f"🔓 <b>ยกเลิกแบนเรียบร้อย</b>",
        f"👤 {name} (tg=<code>{tg_id}</code>)",
        f"",
        f"✅ สถานะแบน: ยกเลิก",
        f"✅ รับข้อความได้อีก",
    ]
    if remove_bl:
        lines.append(f"✅ ลบชื่อ + เลขสลิปออกจากบัญชีดำ")
    if unkick:
        lines.append(f"✅ ปลด Guardian ban {len(result.groups_kicked)}/{len(result.groups_kicked)+len(result.groups_failed)} กลุ่ม")
    lines.append(f"")
    lines.append(
        "ℹ️ <i>หมายเหตุ: สมาชิก, ส่วนลด, สิทธิ์กาชา, งาน DM ค้าง ไม่ฟื้นคืนอัตโนมัติ — ต้องคืนเองถ้าจำเป็น</i>"
    )
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


async def cb_ban_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles `ban_user:<tg>:<reason>` callbacks from inline buttons."""
    q = update.callback_query
    if not q or not q.from_user or not _is_admin(q.from_user.id):
        try:
            await q.answer("⛔ ไม่มีสิทธิ์", show_alert=True)
        except Exception:
            pass
        return
    try:
        await q.answer("🔨 Banning...")
    except Exception:
        pass

    parts = (q.data or "").split(":", 2)
    if len(parts) < 2:
        return
    try:
        tg_id = int(parts[1])
    except ValueError:
        return
    reason = parts[2] if len(parts) >= 3 else "scam_via_alert_button"

    from shared.ban_service import ban_user
    result = await ban_user(
        telegram_id=tg_id,
        reason=reason,
        admin_id=q.from_user.id,
        add_to_blacklist=True,
        kick_from_groups=True,
    )

    actor = q.from_user.username or q.from_user.first_name or "admin"
    from html import escape
    marker = f"\n\n{result.report_html()}\n— by @{escape(actor)}"
    try:
        if q.message and q.message.caption is not None:
            await q.edit_message_caption(
                caption=(q.message.caption or "") + marker,
                parse_mode="HTML", reply_markup=None,
            )
        elif q.message and q.message.text is not None:
            await q.edit_message_text(
                text=(q.message.text or "") + marker,
                parse_mode="HTML", reply_markup=None,
            )
    except Exception as exc:
        logger.warning("ban button edit failed: %s", exc)
        # fallback: send a fresh message
        try:
            await context.bot.send_message(
                chat_id=q.message.chat.id,
                text=result.report_html(),
                parse_mode="HTML",
            )
        except Exception:
            pass


def get_ban_handlers():
    return [
        CommandHandler("ban", cmd_ban),
        CommandHandler("unban", cmd_unban),
        CallbackQueryHandler(cb_ban_user, pattern=r"^ban_user:\d+(:.*)?$"),
    ]


__all__ = [
    "cmd_ban",
    "cmd_unban",
    "cb_ban_user",
    "build_ban_button",
    "get_ban_handlers",
]
