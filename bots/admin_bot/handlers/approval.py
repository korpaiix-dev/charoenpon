"""Approval handlers - อนุมัติ/reject payment และ broadcast."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

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
from shared.utils import format_datetime_thai, format_thb, log_admin_action

logger = logging.getLogger(__name__)


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
                InlineKeyboardButton("✅ อนุมัติ", callback_data=f"pay_approve:{payment.id}"),
                InlineKeyboardButton("❌ ไม่อนุมัติ", callback_data=f"pay_reject:{payment.id}"),
            ]
        ])

        await update.effective_message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)

    logger.info(
        "[%s] [ADMIN_BOT] [VIEW_PENDING] [%s] [%d pending payments]",
        datetime.now(timezone.utc).isoformat(),
        update.effective_user.id,
        len(rows),
    )


async def approve_payment_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """อนุมัติ payment — สร้าง subscription ให้สมาชิก."""
    query = update.callback_query
    await query.answer()

    if not query.from_user or not _is_admin(query.from_user.id):
        await query.edit_message_text("⛔ คุณไม่มีสิทธิ์")
        return

    payment_id = int(query.data.split(":")[1])

    async with get_session() as session:
        payment = await session.get(Payment, payment_id)
        if not payment:
            await query.edit_message_text(f"❌ ไม่พบ Payment #{payment_id}")
            return

        if payment.status != PaymentStatus.PENDING:
            await query.edit_message_text(
                f"⚠️ Payment #{payment_id} สถานะเป็น {payment.status.value} แล้ว"
            )
            return

        # Update payment status
        payment.status = PaymentStatus.CONFIRMED
        payment.verified_by = query.from_user.id
        payment.verified_at = datetime.now(timezone.utc)

        # Get package for duration
        package = await session.get(Package, payment.package_id)
        duration_days = package.duration_days if package else 30

        # Create subscription
        now = datetime.now(timezone.utc)
        from datetime import timedelta
        subscription = Subscription(
            user_id=payment.user_id,
            package_id=payment.package_id,
            status=SubscriptionStatus.ACTIVE,
            start_date=now,
            end_date=now + timedelta(days=duration_days),
            payment_id=payment.id,
        )
        session.add(subscription)

        # Update user total spent
        user = await session.get(User, payment.user_id)
        if user:
            user.total_spent = user.total_spent + payment.amount

        await session.flush()

    # Log admin action
    await log_admin_action(
        admin_id=query.from_user.id,
        action="approve_payment",
        target_type="payment",
        target_id=payment_id,
        details=f"Approved payment #{payment_id}, amount={payment.amount}",
    )

    package_name = package.name if package else "N/A"
    await query.edit_message_text(
        f"✅ <b>อนุมัติ Payment #{payment_id}</b>\n"
        f"📦 แพ็กเกจ: {package_name}\n"
        f"💰 จำนวน: {format_thb(payment.amount)}\n"
        f"⏱ ระยะเวลา: {duration_days} วัน\n"
        f"👤 อนุมัติโดย: {query.from_user.first_name}",
        parse_mode="HTML",
    )

    logger.info(
        "[%s] [ADMIN_BOT] [APPROVE_PAYMENT] [%s] [payment_id=%d amount=%s]",
        datetime.now(timezone.utc).isoformat(),
        query.from_user.id,
        payment_id,
        payment.amount,
    )


async def reject_payment_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """ไม่อนุมัติ payment."""
    query = update.callback_query
    await query.answer()

    if not query.from_user or not _is_admin(query.from_user.id):
        await query.edit_message_text("⛔ คุณไม่มีสิทธิ์")
        return

    payment_id = int(query.data.split(":")[1])

    async with get_session() as session:
        payment = await session.get(Payment, payment_id)
        if not payment:
            await query.edit_message_text(f"❌ ไม่พบ Payment #{payment_id}")
            return

        if payment.status != PaymentStatus.PENDING:
            await query.edit_message_text(
                f"⚠️ Payment #{payment_id} สถานะเป็น {payment.status.value} แล้ว"
            )
            return

        payment.status = PaymentStatus.REJECTED
        payment.verified_by = query.from_user.id
        payment.verified_at = datetime.now(timezone.utc)
        payment.reject_reason = "ไม่อนุมัติโดยแอดมิน"

        await session.flush()

    await log_admin_action(
        admin_id=query.from_user.id,
        action="reject_payment",
        target_type="payment",
        target_id=payment_id,
        details=f"Rejected payment #{payment_id}",
    )

    await query.edit_message_text(
        f"❌ <b>ไม่อนุมัติ Payment #{payment_id}</b>\n"
        f"👤 โดย: {query.from_user.first_name}",
        parse_mode="HTML",
    )

    logger.info(
        "[%s] [ADMIN_BOT] [REJECT_PAYMENT] [%s] [payment_id=%d]",
        datetime.now(timezone.utc).isoformat(),
        query.from_user.id,
        payment_id,
    )


# ─── Pending Broadcasts ──────────────────────────────────────────────────────

async def cmd_pending_broadcasts(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """แสดงรายการ broadcast ที่ยังไม่ส่ง (total_sent=0)."""
    if not update.effective_user or not _is_admin(update.effective_user.id):
        await update.effective_message.reply_text("⛔ คุณไม่มีสิทธิ์ใช้งาน")
        return

    async with get_session() as session:
        result = await session.execute(
            select(BroadcastLog, User)
            .join(User, BroadcastLog.admin_id == User.id)
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
                InlineKeyboardButton("✅ อนุมัติ", callback_data=f"bc_approve:{broadcast.id}"),
                InlineKeyboardButton("❌ ไม่อนุมัติ", callback_data=f"bc_reject:{broadcast.id}"),
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
