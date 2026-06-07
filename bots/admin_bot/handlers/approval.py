# >>> MAY26_COMBO_PROMO <<<  # patched approval.py
"""Approval handlers - อนุมัติ/reject payment และ broadcast."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, func
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from shared.database import get_session
from shared.models import (
    BroadcastLog,
    Package,
    Payment,
    PaymentStatus,
    Subscription,
    SubscriptionStatus,
    User,
)
from shared.endmonth_vip_promo import (
    PROMO_2499_PRICE, PROMO_PRICE, is_endmonth_vip_promo_active,
    PROMO_500_PRICE, PROMO_1299_PRICE, is_may_combo_promo_active,
)
from shared.songkran_promo import get_group_display_title, is_songkran_bonus_slug
from shared.utils import format_datetime_thai, format_thb, log_admin_action
from shared.admin_alert import _admin_group_id

logger = logging.getLogger(__name__)


def _build_manual_invite_alert_keyboard(user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔄 ส่งใหม่", callback_data=f"sos_resend_{user_id}", api_kwargs={"style": "primary"}),
            InlineKeyboardButton("📋 คัดลอกลิงก์", callback_data=f"copy_invites_{user_id}", api_kwargs={"style": "secondary"}),
        ]
    ])


def _admin_ids() -> list[int]:
    """Get admin IDs from main module to avoid circular import."""
    import os
    return [
        int(x.strip())
        for x in os.environ.get("ADMIN_TELEGRAM_IDS", "").split(",")
        if x.strip()
    ]


def _is_admin(user_id: int) -> bool:
    return user_id in _admin_ids()


# ─── Pending Payments ─────────────────────────────────────────────────────────

async def cmd_pending_payments(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """แสดงรายการ payment ที่รออนุมัติ (status=pending)."""
    if not update.effective_user or not _is_admin(update.effective_user.id):
        await update.effective_message.reply_text("⛔ คุณไม่มีสิทธิ์ใช้งาน")
        return

    async with get_session() as session:
        result = await session.execute(
            select(Payment, User, Package)
            .join(User, Payment.user_id == User.id)
            .join(Package, Payment.package_id == Package.id)
            .where(Payment.status == PaymentStatus.PENDING)
            .order_by(Payment.created_at.asc())
        )
        rows = result.all()

    if not rows:
        await update.effective_message.reply_text("✅ ไม่มี payment ที่รออนุมัติ")
        return

    await update.effective_message.reply_text(
        f"💳 <b>Payment รออนุมัติ ({len(rows)} รายการ)</b>",
        parse_mode="HTML",
    )

    for payment, user, package in rows:
        username_display = f"@{user.username}" if user.username else user.first_name or f"ID:{user.telegram_id}"
        text = (
            f"━━━━━━━━━━━━━━━━━\n"
            f"🆔 Payment #{payment.id}\n"
            f"👤 {username_display} (TG: {user.telegram_id})\n"
            f"📦 แพ็กเกจ: {package.name} ({package.tier.value})\n"
            f"💰 จำนวน: {format_thb(payment.amount)}\n"
            f"💳 ช่องทาง: {payment.method.value}\n"
            f"🕐 เวลา: {format_datetime_thai(payment.created_at)}\n"
        )
        if payment.slip_file_id:
            text += f"🖼 สลิป: มี (file_id)\n"
        if payment.transaction_ref:
            text += f"📝 Ref: {payment.transaction_ref}\n"

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ อนุมัติ", callback_data=f"pay_approve:{payment.id}", api_kwargs={"style": "success"}),
                InlineKeyboardButton("❌ ไม่อนุมัติ", callback_data=f"pay_reject:{payment.id}", api_kwargs={"style": "danger"}),
            ]
        ])

        await update.effective_message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)

    logger.info(
        "[%s] [ADMIN_BOT] [VIEW_PENDING] [%s] [%d pending payments]",
        datetime.now(timezone.utc).isoformat(),
        update.effective_user.id,
        len(rows),
    )


# ── Strangler Fig Round 8: approve/reject_payment_callback also moved ──
from bots.admin_bot.handlers.payment_actions import (
    approve_payment_callback,
    reject_payment_callback,
)

async def cmd_pending_broadcasts(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """แสดงรายการ broadcast ที่ยังไม่ส่ง (total_sent=0)."""
    if not update.effective_user or not _is_admin(update.effective_user.id):
        await update.effective_message.reply_text("⛔ คุณไม่มีสิทธิ์ใช้งาน")
        return

    async with get_session() as session:
        result = await session.execute(
            select(BroadcastLog, User)
            .join(User, BroadcastLog.admin_id == User.telegram_id)
            .where(BroadcastLog.total_sent == 0, BroadcastLog.total_failed == 0)
            .order_by(BroadcastLog.created_at.asc())
        )
        rows = result.all()

    if not rows:
        await update.effective_message.reply_text("✅ ไม่มี broadcast ที่รออนุมัติ")
        return

    await update.effective_message.reply_text(
        f"📢 <b>Broadcast รออนุมัติ ({len(rows)} รายการ)</b>",
        parse_mode="HTML",
    )

    for broadcast, creator in rows:
        tier_text = broadcast.target_tier.value if broadcast.target_tier else "ทั้งหมด"
        group_text = broadcast.target_group.value if broadcast.target_group else "ทุกกลุ่ม"
        msg_preview = (broadcast.message_text[:100] + "...") if broadcast.message_text and len(broadcast.message_text) > 100 else (broadcast.message_text or "(ไม่มีข้อความ)")

        text = (
            f"━━━━━━━━━━━━━━━━━\n"
            f"🆔 Broadcast #{broadcast.id}\n"
            f"👤 สร้างโดย: {creator.username or creator.first_name or 'N/A'}\n"
            f"🎯 Tier: {tier_text} | กลุ่ม: {group_text}\n"
            f"📝 ข้อความ:\n{msg_preview}\n"
            f"🕐 เวลา: {format_datetime_thai(broadcast.created_at)}\n"
        )
        if broadcast.media_file_id:
            text += "🖼 มีสื่อแนบ\n"

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ อนุมัติ", callback_data=f"bc_approve:{broadcast.id}", api_kwargs={"style": "success"}),
                InlineKeyboardButton("❌ ไม่อนุมัติ", callback_data=f"bc_reject:{broadcast.id}", api_kwargs={"style": "danger"}),
            ]
        ])

        await update.effective_message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)

    logger.info(
        "[%s] [ADMIN_BOT] [VIEW_BROADCASTS] [%s] [%d pending broadcasts]",
        datetime.now(timezone.utc).isoformat(),
        update.effective_user.id,
        len(rows),
    )


async def approve_broadcast_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """อนุมัติ broadcast — ทำเครื่องหมายว่าพร้อมส่ง."""
    query = update.callback_query
    await query.answer()

    if not query.from_user or not _is_admin(query.from_user.id):
        await query.edit_message_text("⛔ คุณไม่มีสิทธิ์")
        return

    broadcast_id = int(query.data.split(":")[1])

    async with get_session() as session:
        broadcast = await session.get(BroadcastLog, broadcast_id)
        if not broadcast:
            await query.edit_message_text(f"❌ ไม่พบ Broadcast #{broadcast_id}")
            return

        if broadcast.total_sent > 0:
            await query.edit_message_text(f"⚠️ Broadcast #{broadcast_id} ถูกส่งไปแล้ว")
            return

        # Mark as approved by setting total_sent to -1 (signal for sender to pick up)
        # The actual sending will be handled by the broadcast worker
        broadcast.total_sent = -1
        await session.flush()

    await log_admin_action(
        admin_id=query.from_user.id,
        action="approve_broadcast",
        target_type="broadcast",
        target_id=broadcast_id,
        details=f"Approved broadcast #{broadcast_id}",
    )

    await query.edit_message_text(
        f"✅ <b>อนุมัติ Broadcast #{broadcast_id}</b>\n"
        f"📢 ระบบจะเริ่มส่งอัตโนมัติ\n"
        f"👤 อนุมัติโดย: {query.from_user.first_name}",
        parse_mode="HTML",
    )

    logger.info(
        "[%s] [ADMIN_BOT] [APPROVE_BROADCAST] [%s] [broadcast_id=%d]",
        datetime.now(timezone.utc).isoformat(),
        query.from_user.id,
        broadcast_id,
    )


async def reject_broadcast_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """ไม่อนุมัติ broadcast — ลบออก."""
    query = update.callback_query
    await query.answer()

    if not query.from_user or not _is_admin(query.from_user.id):
        await query.edit_message_text("⛔ คุณไม่มีสิทธิ์")
        return

    broadcast_id = int(query.data.split(":")[1])

    async with get_session() as session:
        broadcast = await session.get(BroadcastLog, broadcast_id)
        if not broadcast:
            await query.edit_message_text(f"❌ ไม่พบ Broadcast #{broadcast_id}")
            return

        if broadcast.total_sent > 0:
            await query.edit_message_text(f"⚠️ Broadcast #{broadcast_id} ถูกส่งไปแล้ว")
            return

        # Mark as rejected by setting total_failed to -1
        broadcast.total_failed = -1
        await session.flush()

    await log_admin_action(
        admin_id=query.from_user.id,
        action="reject_broadcast",
        target_type="broadcast",
        target_id=broadcast_id,
        details=f"Rejected broadcast #{broadcast_id}",
    )

    await query.edit_message_text(
        f"❌ <b>ไม่อนุมัติ Broadcast #{broadcast_id}</b>\n"
        f"👤 โดย: {query.from_user.first_name}",
        parse_mode="HTML",
    )

    logger.info(
        "[%s] [ADMIN_BOT] [REJECT_BROADCAST] [%s] [broadcast_id=%d]",
        datetime.now(timezone.utc).isoformat(),
        query.from_user.id,
        broadcast_id,
    )


# ── Strangler Fig Round 6+7: payment-action callbacks moved out ──
# Now live in bots/admin_bot/handlers/payment_actions.py
from bots.admin_bot.handlers.payment_actions import (
    _verify_approve_amount,
    approve_by_price_callback,
    inspect_payment_callback,
    approve_promo_callback,
)

async def reject_user_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """ปฏิเสธสลิป — reject_userid format."""
    query = update.callback_query
    await query.answer()

    if not query.from_user or not _is_admin(query.from_user.id):
        await query.answer("⛔ คุณไม่มีสิทธิ์", show_alert=True)
        return

    target_user_id = int(query.data.split("_")[1])

    import os, telegram as tg
    try:
        sales_bot = tg.Bot(token=os.environ.get("SALES_BOT_TOKEN", ""))
        await sales_bot.initialize()
        await sales_bot.send_message(
            chat_id=target_user_id,
            text="❌ <b>สลิปไม่ผ่านการตรวจสอบค่ะ</b>\nกรุณาส่งสลิปใหม่ หรือติดต่อแอดมิน https://t.me/sperm6969",
            parse_mode="HTML",
        )
    except Exception as exc:
        logger.warning("Failed to notify rejection: %s", exc)

    safe_admin = query.from_user.first_name or "Admin"
    old_caption = query.message.caption or ""
    new_caption = f"{old_caption}\n\n❌ <b>สถานะ: ปฏิเสธ โดย {safe_admin}</b>"
    await _notify_discord_alert(f"❌ ปฏิเสธสลิป", f"👤 TG ID {target_user_id}\n👮 โดย: {safe_admin}", color=0xE74C3C)
    import telegram as tg
    # Look up user for chat button
    try:
        async with get_session() as session:
            _rej_user_result = await session.execute(
                select(User).where(User.telegram_id == target_user_id)
            )
            _rej_db_user = _rej_user_result.scalar_one_or_none()
    except Exception:
        _rej_db_user = None
    post_keyboard = None
    if _rej_db_user and _rej_db_user.username:
        post_keyboard = tg.InlineKeyboardMarkup([[
            tg.InlineKeyboardButton(
                f"💬 @{_rej_db_user.username}",
                url=f"https://t.me/{_rej_db_user.username}",
                api_kwargs={"style": "primary"},
            )
        ]])
    try:
        await query.edit_message_caption(caption=new_caption[:1024], parse_mode="HTML", reply_markup=post_keyboard)
    except Exception as e:
        logger.error("Failed to edit rejection caption: %s", e)


async def ban_user_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """แบนลูกค้า — ban_userid format."""
    query = update.callback_query

    if not query:
        return

    actor_id = query.from_user.id if query.from_user else None
    actor_name = query.from_user.full_name if query.from_user else "-"
    logger.info(
        "Ban callback received: data=%s actor_id=%s actor=%s chat_id=%s message_id=%s",
        query.data,
        actor_id,
        actor_name,
        query.message.chat_id if query.message else None,
        query.message.message_id if query.message else None,
    )

    if not query.from_user or not _is_admin(query.from_user.id):
        logger.warning(
            "Ban callback denied: data=%s actor_id=%s actor=%s allowed_admins=%s",
            query.data,
            actor_id,
            actor_name,
            _admin_ids(),
        )
        await query.answer("⛔ คุณไม่มีสิทธิ์", show_alert=True)
        return

    target_user_id = int(query.data.split("_")[1])
    await query.answer("กำลังแบนลูกค้า...", show_alert=False)

    import os, telegram as tg
    ban_db_user = None
    try:
        # Ban user in DB
        async with get_session() as session:
            user_result = await session.execute(
                select(User).where(User.telegram_id == target_user_id)
            )
            ban_db_user = user_result.scalar_one_or_none()
            if ban_db_user:
                ban_db_user.is_banned = True
            else:
                ban_db_user = User(telegram_id=target_user_id, is_banned=True)
                session.add(ban_db_user)
                await session.flush()
            logger.info("User banned in DB: telegram_id=%s db_id=%s", target_user_id, ban_db_user.id)
    except Exception as exc:
        logger.exception("Failed to ban user in DB: telegram_id=%s error=%s", target_user_id, exc)
        await query.answer("❌ แบนไม่สำเร็จ: DB error", show_alert=True)
        return

    try:
        sales_bot = tg.Bot(token=os.environ.get("SALES_BOT_TOKEN", ""))
        await sales_bot.initialize()
        await sales_bot.send_message(
            chat_id=target_user_id,
            text="🚫 <b>คุณถูกระงับการใช้งานถาวร</b>\nเนื่องจากส่งรูปภาพที่ไม่เหมาะสมหรือสลิปปลอม หากมีข้อสงสัยกรุณาติดต่อแอดมิน",
            parse_mode="HTML",
        )
    except Exception as exc:
        # DM failure must not rollback/undo the DB ban. Users often block the bot.
        logger.warning("User banned in DB, but ban DM failed: telegram_id=%s error=%s", target_user_id, exc)

    safe_admin = query.from_user.first_name or "Admin"
    old_caption = query.message.caption or ""
    new_caption = f"{old_caption}\n\n🚫 <b>สถานะ: แบนถาวร โดย {safe_admin}</b>"
    await _notify_discord_alert(f"🚫 แบนลูกค้า", f"👤 TG ID {target_user_id}\n👮 โดย: {safe_admin}", color=0x992D22)
    import telegram as tg
    post_keyboard = None
    if ban_db_user and ban_db_user.username:
        post_keyboard = tg.InlineKeyboardMarkup([[
            tg.InlineKeyboardButton(
                f"💬 @{ban_db_user.username}",
                url=f"https://t.me/{ban_db_user.username}",
                api_kwargs={"style": "primary"},
            )
        ]])
    try:
        if query.message and query.message.caption is not None:
            await query.edit_message_caption(caption=new_caption[:1024], parse_mode="HTML", reply_markup=post_keyboard)
        else:
            old_text = query.message.text_html if query.message else ""
            new_text = f"{old_text}\n\n🚫 <b>สถานะ: แบนถาวร โดย {safe_admin}</b>"
            await query.edit_message_text(text=new_text[:4096], parse_mode="HTML", reply_markup=post_keyboard)
        logger.info("Ban callback completed: target_user_id=%s actor_id=%s", target_user_id, actor_id)
    except Exception as e:
        logger.error("Failed to edit ban message: %s", e)


# ── Strangler Fig Round 9: SOS callbacks moved out ──
from bots.admin_bot.handlers.sos_actions import (
    sos_resend_callback,
    copy_invites_callback,
    sos_deny_callback,
    sos_ban_callback,
)

async def _notify_discord_alert(title: str, details: str, color: int = 0x3498DB) -> None:
    """[Phase 4 A3] delegated to shared.discord_alert."""
    from shared.discord_alert import notify_discord as _hub
    try:
        # Best-effort: pass any positional/keyword as title + body
        args_str = " | ".join(str(x) for x in locals().values() if isinstance(x, str))[:1000]
        return await _hub("payment", "_notify_discord_alert", args_str, silent_on_error=True)
    except Exception:
        return False

async def chat_user_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """รองรับปุ่มแชทลูกค้าแบบ callback เก่า (chat_user_<id>)."""
    query = update.callback_query
    await query.answer()

    if not query.from_user or not _is_admin(query.from_user.id):
        await query.answer("⛔ คุณไม่มีสิทธิ์", show_alert=True)
        return

    target_user_id = int(query.data.split("_")[2])

    try:
        db_user = None
        async with get_session() as session:
            user_result = await session.execute(
                select(User).where(User.telegram_id == target_user_id)
            )
            db_user = user_result.scalar_one_or_none()

        full_name = " ".join(
            part for part in [
                db_user.first_name if db_user else None,
                db_user.last_name if db_user else None,
            ] if part
        ).strip() or (db_user.first_name if db_user and db_user.first_name else "-")
        username_line = f"@{db_user.username}" if db_user and db_user.username else "-"

        lines = [
            "💬 <b>ข้อมูลลูกค้าสำหรับเปิดแชท</b>",
            f"• ชื่อ-นามสกุล: {full_name}",
            f"• Username: {username_line}",
            f"• TG ID: <code>{target_user_id}</code>",
        ]
        if db_user and db_user.username:
            lines.append(f'• ลิงก์: <a href="https://t.me/{db_user.username}">https://t.me/{db_user.username}</a>')
        else:
            lines.append("• หมายเหตุ: ลูกค้าไม่มี username, Telegram เปิดแชทตรงด้วยปุ่มไม่ได้")
            lines.append("• วิธีใช้: ให้แอดมิน forward ข้อความเก่า, ให้ลูกค้าทักเข้ามาใหม่, หรือคัดลอก TG ID ไปค้นในระบบที่รองรับ")

        await query.message.reply_text(
            text="\n".join(lines),
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
    except Exception as exc:
        logger.error("chat_user callback error: %s", exc)
        await query.answer(f"เปิดข้อมูลลูกค้าไม่ได้: {target_user_id}", show_alert=True)
