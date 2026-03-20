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
        payment.verified_at = datetime.utcnow()

        # Get package for duration
        package = await session.get(Package, payment.package_id)
        duration_days = package.duration_days if package else 30

        # Expire existing active subscriptions (prevent duplicates)
        from datetime import timedelta
        from sqlalchemy import update as sa_update_sub
        await session.execute(
            sa_update_sub(Subscription)
            .where(Subscription.user_id == payment.user_id, Subscription.status == SubscriptionStatus.ACTIVE)
            .values(status=SubscriptionStatus.EXPIRED)
        )

        # Create subscription
        now = datetime.utcnow()
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

            # Mark teaser clicks as converted for this user
            from sqlalchemy import update as sa_update
            from shared.models import TeaserClick
            await session.execute(
                sa_update(TeaserClick)
                .where(TeaserClick.user_id == user.telegram_id, TeaserClick.converted == False)
                .values(converted=True)
            )

        await session.flush()

    # Log admin action
    await log_admin_action(
        admin_id=query.from_user.id,
        action="approve_payment",
        target_type="payment",
        target_id=payment_id,
        details=f"Approved payment #{payment_id}, amount={payment.amount}",
    )

    # Send invite links to customer
    invite_text = ""
    if user:
        try:
            from bots.guardian_bot.group_monitor import generate_invite_links_for_user
            import os
            import telegram as tg
            sales_bot = tg.Bot(token=os.environ.get("SALES_BOT_TOKEN", ""))
            invite_links = await generate_invite_links_for_user(
                sales_bot, user.telegram_id, payment.package_id
            )
            links_list = []
            async with get_session() as session:
                from shared.models import GroupRegistry
                for slug, link in invite_links.items():
                    grp_result = await session.execute(
                        select(GroupRegistry).where(GroupRegistry.slug == slug)
                    )
                    group = grp_result.scalar_one_or_none()
                    title = group.title if group else slug
                    links_list.append(f"• {title}: {link}")
            links_text = "\n".join(links_list) if links_list else "ไม่สามารถสร้างลิงก์ได้"

            await sales_bot.send_message(
                chat_id=user.telegram_id,
                text=(
                    f"✅ <b>ชำระเงินสำเร็จค่ะ!</b>\n\n"
                    f"🔗 <b>ลิงก์เข้ากลุ่ม VIP:</b>\n{links_text}\n\n"
                    f"⚠️ ลิงก์แต่ละลิงก์ใช้ได้ 1 ครั้ง หมดอายุ 24 ชม.\n"
                    f"กรุณากดเข้าร่วมโดยเร็วนะคะ 🙏"
                ),
                parse_mode="HTML",
            )
            invite_text = "\n📩 ส่งลิงก์ให้ลูกค้าแล้ว"

            # ส่ง DM ยินดีต้อนรับ + แนะนำชวนเพื่อน หลัง 3 วินาที
            try:
                import asyncio
                await asyncio.sleep(3)
                await sales_bot.send_message(
                    chat_id=user.telegram_id,
                    text=(
                        '🎉 ยินดีต้อนรับสู่ VIP เจริญพร! 💕\n'
                        '\n'
                        '💡 รู้มั้ย? ชวนเพื่อนมาสมัคร = ได้ VIP ฟรีเพิ่ม!\n'
                        '\n'
                        '🎯 ชวน 1 คน = +7 วัน VIP ฟรี\n'
                        '🎯 ชวน 5 คน = +30 วัน VIP ฟรี!\n'
                        '\n'
                        '━━━━━━━━━━━━━━━━━━\n'
                        '📩 <b>รับลิงก์ชวนเพื่อนเลย 👇</b>\n'
                        '👉 <a href="tg://resolve?domain=NamwarnJarern_bot&start=invite">🎁 กดรับลิงก์ชวนเพื่อน</a>\n'
                        '━━━━━━━━━━━━━━━━━━'
                    ),
                    parse_mode="HTML",
                    disable_web_page_preview=True,
                )
                logger.info("Welcome referral DM sent to %s (admin approval)", user.telegram_id)
            except Exception as exc_w:
                logger.warning("Welcome referral DM failed: %s", exc_w)

        except Exception as exc:
            logger.error("Failed to send invite links: %s", exc)
            invite_text = "\n⚠️ ส่งลิงก์ไม่สำเร็จ"

    package_name = package.name if package else "N/A"
    await query.edit_message_caption(
        caption=(
            f"✅ <b>อนุมัติ Payment #{payment_id}</b>\n"
            f"📦 แพ็กเกจ: {package_name}\n"
            f"💰 จำนวน: {format_thb(payment.amount)}\n"
            f"⏱ ระยะเวลา: {duration_days} วัน\n"
            f"👤 อนุมัติโดย: {query.from_user.first_name}"
            f"{invite_text}"
        ),
        parse_mode="HTML",
    )

    # ── Sync Google Sheets ──
    try:
        from sheets.daily_revenue import DailyRevenueSheet
        from sheets.members import MembersSheet
        from sheets.income_log import IncomeLogSheet
        await DailyRevenueSheet.update()
        from sheets.daily_summary import DailySummarySheet
        await DailySummarySheet.update()
        await IncomeLogSheet.log_payment(payment_id, approved_by=query.from_user.first_name or "Admin")
        if user:
            await MembersSheet.update_member(user.id)
        logger.info("Sheets synced for payment #%d", payment_id)
    except Exception as exc:
        logger.warning("Sheets sync failed for payment #%d: %s", payment_id, exc)
        logger.warning("Sheets sync failed for payment #%d: %s", payment_id, exc)

    # ── Process referral reward ──
    if user:
        try:
            import telegram as tg
            sales_bot = tg.Bot(token=os.environ.get("SALES_BOT_TOKEN", ""))
            from bots.sales_bot.handlers.referral import process_referral_reward
            await process_referral_reward(user.telegram_id, sales_bot)
        except Exception as exc_ref:
            logger.warning("Referral reward failed for payment #%d: %s", payment_id, exc_ref)

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
        payment.verified_at = datetime.utcnow()
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


async def inspect_payment_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """ดูรายละเอียด payment เพิ่มเติม."""
    query = update.callback_query
    await query.answer()

    if not query.from_user or not _is_admin(query.from_user.id):
        await query.answer("⛔ คุณไม่มีสิทธิ์", show_alert=True)
        return

    payment_id = int(query.data.split(":")[1])

    async with get_session() as session:
        payment = await session.get(Payment, payment_id)
        if not payment:
            await query.answer(f"❌ ไม่พบ Payment #{payment_id}", show_alert=True)
            return

        user = await session.get(User, payment.user_id)
        package = await session.get(Package, payment.package_id)

    info = (
        f"🔍 รายละเอียด #PAY{payment_id}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"สถานะ: {payment.status.value}\n"
        f"ยอด: {format_thb(payment.amount)}\n"
        f"วิธี: {payment.method.value}\n"
    )
    if user:
        info += f"ลูกค้า: @{user.username or user.first_name} (TG: {user.telegram_id})\n"
    if package:
        info += f"แพ็กเกจ: {package.name} ({format_thb(package.price)})\n"
    info += f"สร้างเมื่อ: {str(payment.created_at)[:19]}"

    await query.answer(info[:200], show_alert=True)


async def approve_by_price_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """อนุมัติสลิปโดยเลือกราคา — approve_300_userid format."""
    query = update.callback_query
    await query.answer()

    if not query.from_user or not _is_admin(query.from_user.id):
        await query.answer("⛔ คุณไม่มีสิทธิ์", show_alert=True)
        return

    parts = query.data.split("_")  # approve_300_12345
    price = parts[1]
    target_user_id = int(parts[2])

    import os
    import telegram as tg
    from datetime import timedelta
    from bots.guardian_bot.group_monitor import generate_invite_links_for_user

    try:
        # Find package by price
        async with get_session() as session:
            from shared.models import Package, PackageTier
            tier_map = {"99": "99", "199": "300", "300": "300", "500": "500", "1299": "1299", "2499": "2499"}
            tier = tier_map.get(price)
            if not tier:
                await query.answer(f"❌ ราคา {price} ไม่ถูกต้อง", show_alert=True)
                return

            pkg_result = await session.execute(
                select(Package).where(Package.tier == PackageTier(tier))
            )
            package = pkg_result.scalar_one_or_none()
            if not package:
                await query.answer("❌ ไม่พบแพ็กเกจ", show_alert=True)
                return

            # Find or create user
            user_result = await session.execute(
                select(User).where(User.telegram_id == target_user_id)
            )
            db_user = user_result.scalar_one_or_none()
            if not db_user:
                db_user = User(telegram_id=target_user_id, first_name="ลูกค้า")
                session.add(db_user)
                await session.flush()

            # Check for existing active subscription (prevent duplicates)
            from decimal import Decimal
            existing_sub = await session.execute(
                select(Subscription).where(
                    Subscription.user_id == db_user.id,
                    Subscription.status == SubscriptionStatus.ACTIVE,
                )
            )
            if existing_sub.scalar_one_or_none():
                # Expire existing subscription first
                await session.execute(
                    select(Subscription).where(
                        Subscription.user_id == db_user.id,
                        Subscription.status == SubscriptionStatus.ACTIVE,
                    )
                )
                from sqlalchemy import update as sa_update_sub
                await session.execute(
                    sa_update_sub(Subscription)
                    .where(Subscription.user_id == db_user.id, Subscription.status == SubscriptionStatus.ACTIVE)
                    .values(status=SubscriptionStatus.EXPIRED)
                )

            # Create subscription
            now = datetime.utcnow()
            # Trial 24 ชม.: ใช้ hours=24 แทน days=1
            if package.tier == PackageTier.TIER_99:
                end_date = now + timedelta(hours=24)
            else:
                end_date = now + timedelta(days=package.duration_days)
            subscription = Subscription(
                user_id=db_user.id,
                package_id=package.id,
                status=SubscriptionStatus.ACTIVE,
                start_date=now,
                end_date=end_date,
            )
            session.add(subscription)
            db_user.total_spent = (db_user.total_spent or Decimal("0")) + package.price

            # Mark teaser clicks as converted for this user
            from sqlalchemy import update as sa_update
            from shared.models import TeaserClick
            await session.execute(
                sa_update(TeaserClick)
                .where(TeaserClick.user_id == target_user_id, TeaserClick.converted == False)
                .values(converted=True)
            )

            await session.flush()
            pkg_name = package.name
            duration = package.duration_days
            pkg_id = package.id

        # Flash Sale: increment sold_slots if active
        try:
            from bots.sales_bot.handlers.flash_sale import increment_sold_slot
            if tier == "300":
                success, sold, total = await increment_sold_slot(pkg_id)
                if success:
                    logger.info("Flash sale slot incremented: %d/%d", sold, total)
        except Exception as exc_fs:
            logger.warning("Flash sale slot increment failed (non-critical): %s", exc_fs)

        # Generate invite links using Guardian Bot (must be admin in all VIP groups)
        guardian_bot = tg.Bot(token=os.environ.get("GUARDIAN_BOT_TOKEN", ""))
        sales_bot = tg.Bot(token=os.environ.get("SALES_BOT_TOKEN", ""))
        invite_links = await generate_invite_links_for_user(guardian_bot, target_user_id, pkg_id)

        links_list = []
        async with get_session() as session:
            from shared.models import GroupRegistry
            for slug, link in invite_links.items():
                grp_result = await session.execute(
                    select(GroupRegistry).where(GroupRegistry.slug == slug)
                )
                group = grp_result.scalar_one_or_none()
                title = group.title if group else slug
                links_list.append({"text": f"🚀 {title}", "url": link})

        # Send invite links to customer (2 buttons per row)
        link_buttons = [links_list[i:i+2] for i in range(0, len(links_list), 2)]

        expire_date = (datetime.utcnow() + timedelta(days=duration)).strftime("%d/%m/%Y")
        msg = (
            f"✅ <b>อนุมัติยอด {price} บาท เรียบร้อยค่ะ</b>\n"
            f"📦 แพ็กเกจ: {pkg_name}\n"
            f"📅 หมดอายุ: {expire_date}\n\n"
            f"👇 <b>กดเข้ากลุ่มที่ปุ่มด้านล่างได้เลย</b>\n\n"
            f"🆓 <b>ห้องฟรี:</b> https://t.me/addlist/2xN-ag15W4U2MTNl"
        )
        keyboard = tg.InlineKeyboardMarkup(
            [[tg.InlineKeyboardButton(b["text"], url=b["url"]) for b in row]
             for row in link_buttons]
        )
        await sales_bot.send_message(chat_id=target_user_id, text=msg, parse_mode="HTML", reply_markup=keyboard)

        # Update admin message — keep chat button
        safe_admin = query.from_user.first_name or "Admin"
        old_caption = query.message.caption or ""
        new_caption = f"{old_caption}\n\n✅ <b>สถานะ: อนุมัติ ({price}บ.) โดย {safe_admin}</b>"
        post_keyboard = tg.InlineKeyboardMarkup([
            [tg.InlineKeyboardButton("💬 แชทกับลูกค้า", callback_data=f"chat_user:{target_user_id}")],
        ])
        try:
            await query.edit_message_caption(caption=new_caption[:1024], parse_mode="HTML", reply_markup=post_keyboard)
        except Exception as e:
            logger.error("Failed to edit approval caption: %s", e)

        # Notify Discord
        await _notify_discord_alert(
            f"✅ อนุมัติ {price} บาท",
            f"👤 ลูกค้า: TG ID {target_user_id}\n📦 แพ็กเกจ: {pkg_name}\n👮 โดย: {safe_admin}",
            color=0x2ECC71,
        )

        # ── Create Payment record for tracking ──
        try:
            async with get_session() as session:
                from shared.models import PaymentMethod
                new_payment = Payment(
                    user_id=db_user.id,
                    package_id=pkg_id,
                    amount=package.price if package else Decimal(price),
                    method=PaymentMethod.SLIP,
                    status=PaymentStatus.CONFIRMED,
                    verified_by=query.from_user.id,
                    verified_at=datetime.utcnow(),
                )
                session.add(new_payment)
                await session.flush()
                new_payment_id = new_payment.id
        except Exception as exc_p:
            logger.warning("Failed to create payment record: %s", exc_p)
            new_payment_id = 0

        # ── Sync Google Sheets ──
        try:
            from sheets.daily_revenue import DailyRevenueSheet
            from sheets.members import MembersSheet
            from sheets.income_log import IncomeLogSheet
            await DailyRevenueSheet.update()
            from sheets.daily_summary import DailySummarySheet
            await DailySummarySheet.update()
            await IncomeLogSheet.log_payment(new_payment_id, approved_by=safe_admin)
            await MembersSheet.update_member(db_user.id)
            logger.info("Sheets synced for approve_by_price user %d", target_user_id)
        except Exception as exc_s:
            logger.warning("Sheets sync failed: %s", exc_s)

        # ── Mark comeback promo as purchased (if this user had one) ──
        try:
            from bots.sales_bot.comeback_dm import mark_promo_purchased
            from shared.models import ComebackDmLog
            async with get_session() as session:
                cb_result = await session.execute(
                    select(ComebackDmLog).where(
                        ComebackDmLog.user_id == db_user.id,
                        ComebackDmLog.purchased == False,  # noqa: E712
                    ).order_by(ComebackDmLog.sent_at.desc()).limit(1)
                )
                cb_log = cb_result.scalar_one_or_none()
                if cb_log:
                    cb_log.purchased = True
                    cb_log.responded = True
                    logger.info("Comeback promo %s marked purchased via admin approval", cb_log.promo_code)
        except Exception as exc_cb:
            logger.warning("Comeback promo mark failed (non-critical): %s", exc_cb)

        # ── Process referral reward ──
        try:
            from bots.sales_bot.handlers.referral import process_referral_reward
            await process_referral_reward(target_user_id, sales_bot)
        except Exception as exc_ref:
            logger.warning("Referral reward failed for user %d: %s", target_user_id, exc_ref)

        # ── Welcome referral DM หลัง 3 วินาที ──
        try:
            import asyncio
            await asyncio.sleep(3)
            await sales_bot.send_message(
                chat_id=target_user_id,
                text=(
                    '🎉 ยินดีต้อนรับสู่ VIP เจริญพร! 💕\n'
                    '\n'
                    '💡 รู้มั้ย? ชวนเพื่อนมาสมัคร = ได้ VIP ฟรีเพิ่ม!\n'
                    '\n'
                    '🎯 ชวน 1 คน = +7 วัน VIP ฟรี\n'
                    '🎯 ชวน 5 คน = +30 วัน VIP ฟรี!\n'
                    '\n'
                    '━━━━━━━━━━━━━━━━━━\n'
                    '📩 <b>รับลิงก์ชวนเพื่อนเลย 👇</b>\n'
                    '👉 <a href="tg://resolve?domain=NamwarnJarern_bot&start=invite">🎁 กดรับลิงก์ชวนเพื่อน</a>\n'
                    '━━━━━━━━━━━━━━━━━━'
                ),
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
            logger.info("Welcome referral DM sent to %d (approve_by_price)", target_user_id)
        except Exception as exc_w:
            logger.warning("Welcome referral DM failed for user %d: %s", target_user_id, exc_w)

    except Exception as exc:
        logger.error("approve_by_price error: %s", exc)
        await query.answer(f"❌ Error: {str(exc)[:100]}", show_alert=True)


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
        await sales_bot.send_message(
            chat_id=target_user_id,
            text="❌ <b>สลิปไม่ผ่านการตรวจสอบค่ะ</b>\nกรุณาส่งสลิปใหม่ หรือติดต่อแอดมิน https://t.me/zeinju_bunker",
            parse_mode="HTML",
        )
    except Exception as exc:
        logger.warning("Failed to notify rejection: %s", exc)

    safe_admin = query.from_user.first_name or "Admin"
    old_caption = query.message.caption or ""
    new_caption = f"{old_caption}\n\n❌ <b>สถานะ: ปฏิเสธ โดย {safe_admin}</b>"
    await _notify_discord_alert(f"❌ ปฏิเสธสลิป", f"👤 TG ID {target_user_id}\n👮 โดย: {safe_admin}", color=0xE74C3C)
    import telegram as tg
    post_keyboard = tg.InlineKeyboardMarkup([
        [tg.InlineKeyboardButton("💬 แชทกับลูกค้า", callback_data=f"chat_user:{target_user_id}")],
    ])
    try:
        await query.edit_message_caption(caption=new_caption[:1024], parse_mode="HTML", reply_markup=post_keyboard)
    except Exception as e:
        logger.error("Failed to edit rejection caption: %s", e)


async def ban_user_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """แบนลูกค้า — ban_userid format."""
    query = update.callback_query
    await query.answer()

    if not query.from_user or not _is_admin(query.from_user.id):
        await query.answer("⛔ คุณไม่มีสิทธิ์", show_alert=True)
        return

    target_user_id = int(query.data.split("_")[1])

    import os, telegram as tg
    try:
        # Ban user in DB
        async with get_session() as session:
            user_result = await session.execute(
                select(User).where(User.telegram_id == target_user_id)
            )
            db_user = user_result.scalar_one_or_none()
            if db_user:
                db_user.is_banned = True

        sales_bot = tg.Bot(token=os.environ.get("SALES_BOT_TOKEN", ""))
        await sales_bot.send_message(
            chat_id=target_user_id,
            text="🚫 <b>คุณถูกระงับการใช้งานถาวร</b>\nเนื่องจากส่งรูปภาพที่ไม่เหมาะสมหรือสลิปปลอม หากมีข้อสงสัยกรุณาติดต่อแอดมิน",
            parse_mode="HTML",
        )
    except Exception as exc:
        logger.warning("Failed to ban user: %s", exc)

    safe_admin = query.from_user.first_name or "Admin"
    old_caption = query.message.caption or ""
    new_caption = f"{old_caption}\n\n🚫 <b>สถานะ: แบนถาวร โดย {safe_admin}</b>"
    await _notify_discord_alert(f"🚫 แบนลูกค้า", f"👤 TG ID {target_user_id}\n👮 โดย: {safe_admin}", color=0x992D22)
    import telegram as tg
    post_keyboard = tg.InlineKeyboardMarkup([
        [tg.InlineKeyboardButton("💬 แชทกับลูกค้า", callback_data=f"chat_user:{target_user_id}")],
    ])
    try:
        await query.edit_message_caption(caption=new_caption[:1024], parse_mode="HTML", reply_markup=post_keyboard)
    except Exception as e:
        logger.error("Failed to edit ban caption: %s", e)


async def sos_resend_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """SOS: ส่งลิงก์เข้ากลุ่มใหม่ให้ลูกค้า."""
    query = update.callback_query
    await query.answer()

    if not query.from_user or not _is_admin(query.from_user.id):
        await query.answer("⛔ คุณไม่มีสิทธิ์", show_alert=True)
        return

    target_user_id = int(query.data.split("_")[2])

    import os
    import telegram as tg
    from bots.guardian_bot.group_monitor import generate_invite_links_for_user

    try:
        # Find user's active subscription to determine package
        async with get_session() as session:
            sub_result = await session.execute(
                select(Subscription).where(
                    Subscription.user_id == (
                        select(User.id).where(User.telegram_id == target_user_id).scalar_subquery()
                    ),
                    Subscription.status == SubscriptionStatus.ACTIVE,
                ).order_by(Subscription.end_date.desc())
            )
            sub = sub_result.scalars().first()

        if not sub:
            # เช็ค CSV whitelist — ลูกค้าเก่าอาจไม่มี subscription ในระบบใหม่
            import csv
            csv_path = "/app/data/members2_latest.csv"
            csv_found = False
            csv_status = None
            try:
                with open(csv_path) as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        if row.get("User ID", "").strip() == str(target_user_id):
                            csv_found = True
                            csv_status = (row.get("Status") or "").strip()
                            break
            except Exception:
                pass

            if not csv_found:
                await query.answer("❌ ลูกค้าไม่มี subscription และไม่อยู่ในฐานข้อมูลลูกค้าเก่า", show_alert=True)
                return

            if csv_status == "Expired":
                await query.answer("❌ ลูกค้าหมดอายุแล้ว ไม่สามารถส่งลิ้งค์ได้", show_alert=True)
                return

            # ลูกค้าเก่า — ตรวจสอบว่าเป็นสมาชิกกลุ่มไหนอยู่แล้ว แล้วส่งเฉพาะกลุ่มนั้น
            sub_package_id = "__csv_member__"
        else:
            sub_package_id = sub.package_id

        # Generate invite links using Guardian Bot
        guardian_bot = tg.Bot(token=os.environ.get("GUARDIAN_BOT_TOKEN", ""))
        sales_bot = tg.Bot(token=os.environ.get("SALES_BOT_TOKEN", ""))

        if sub_package_id == "__csv_member__":
            # CSV user: ตรวจสอบ membership จริงในแต่ละกลุ่ม แล้วสร้าง invite เฉพาะกลุ่มที่เป็นสมาชิก
            from bots.guardian_bot.group_monitor import generate_invite_links_for_csv_user
            invite_links = await generate_invite_links_for_csv_user(guardian_bot, target_user_id)
        else:
            invite_links = await generate_invite_links_for_user(guardian_bot, target_user_id, sub_package_id)

        if not invite_links:
            await query.answer("❌ สร้างลิงก์ไม่สำเร็จ", show_alert=True)
            return

        # Build link buttons
        links_list = []
        async with get_session() as session:
            from shared.models import GroupRegistry
            for slug, link in invite_links.items():
                grp_result = await session.execute(
                    select(GroupRegistry).where(GroupRegistry.slug == slug)
                )
                group = grp_result.scalar_one_or_none()
                title = group.title if group else slug
                links_list.append(tg.InlineKeyboardButton(f"🚀 {title}", url=link))

        link_buttons = [links_list[i:i+2] for i in range(0, len(links_list), 2)]
        keyboard = tg.InlineKeyboardMarkup(link_buttons)

        # Send to customer via Sales Bot
        is_sent = True
        try:
            await sales_bot.send_message(
                chat_id=target_user_id,
                text="🔄 <b>ส่งลิงก์เข้ากลุ่มให้ใหม่แล้วค่า</b>\nกดเข้าได้เลยนะ 👇",
                parse_mode="HTML",
                reply_markup=keyboard,
            )
        except Exception:
            is_sent = False

        # Update admin message
        safe_admin = query.from_user.first_name or "Admin"
        old_text = query.message.text or ""
        if is_sent:
            new_text = f"{old_text}\n\n✅ <b>สถานะ: ส่งลิงก์สำรองสำเร็จ โดย {safe_admin}</b>"
            new_keyboard = tg.InlineKeyboardMarkup([
                [tg.InlineKeyboardButton("💬 แชทกับลูกค้า", callback_data=f"chat_user:{target_user_id}")],
            ])
        else:
            new_text = f"{old_text}\n\n❌ <b>ส่งไม่สำเร็จ (ลูกค้าบล็อกบอท)</b>"
            new_keyboard = tg.InlineKeyboardMarkup([
                [tg.InlineKeyboardButton("🔄 ส่งลิงก์ใหม่", callback_data=f"sos_resend_{target_user_id}")],
                [tg.InlineKeyboardButton("💬 แชทกับลูกค้า", callback_data=f"chat_user:{target_user_id}")],
            ])

        try:
            await query.edit_message_text(text=new_text[:4096], parse_mode="HTML", reply_markup=new_keyboard)
        except Exception as e:
            logger.error("Failed to edit SOS resend message: %s", e)

    except Exception as exc:
        logger.error("SOS resend error: %s", exc)
        await query.answer(f"❌ Error: {str(exc)[:100]}", show_alert=True)


async def _notify_discord_alert(title: str, details: str, color: int = 0x3498DB) -> None:
    """Send notification to Discord #alerts as embed."""
    import os, httpx
    from datetime import timezone, timedelta
    discord_token = os.environ.get("DISCORD_BOT_TOKEN", "")
    discord_ch = os.environ.get("DISCORD_CH_ALERTS", "")
    if not discord_token or not discord_ch:
        return
    try:
        now_th = datetime.now(timezone(timedelta(hours=7)))
        embed = {
            "title": title,
            "description": details,
            "color": color,
            "footer": {"text": f"⊙ เจริญพร | {now_th.strftime('%d/%m/%Y %H:%M')}"},
        }
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(
                f"https://discord.com/api/v10/channels/{discord_ch}/messages",
                headers={"Authorization": f"Bot {discord_token}", "Content-Type": "application/json"},
                json={"embeds": [embed]},
            )
    except Exception as exc:
        logger.warning("Discord notify failed: %s", exc)


async def chat_user_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """เปิดแชทกับลูกค้า — ดึง username จาก DB แล้วส่งลิงก์ให้ admin."""
    query = update.callback_query
    await query.answer()

    target_user_id = int(query.data.split(":")[1])

    # ดึงข้อมูล user จาก DB
    async with get_session() as session:
        result = await session.execute(
            select(User).where(User.telegram_id == target_user_id)
        )
        user = result.scalar_one_or_none()

    if user and user.username:
        # มี username → ส่งลิงก์ตรงไปหา profile
        await query.message.reply_text(
            f"💬 <b>แชทกับลูกค้า</b>\n\n"
            f"👤 @{user.username}\n"
            f"👉 <a href=\"https://t.me/{user.username}\">กดที่นี่เพื่อเปิดแชท</a>",
            parse_mode="HTML",
        )
    else:
        # ไม่มี username → แจ้ง admin พร้อม user ID + mention link
        name = (user.first_name if user else None) or f"User {target_user_id}"
        await query.message.reply_text(
            f"💬 <b>แชทกับลูกค้า</b>\n\n"
            f"⚠️ ลูกค้าไม่มี username\n"
            f"👤 ชื่อ: {name}\n"
            f"🆔 Telegram ID: <code>{target_user_id}</code>\n\n"
            f"👉 <a href=\"tg://user?id={target_user_id}\">กดที่นี่เพื่อเปิดแชท</a>\n"
            f"(ใช้ได้เฉพาะบน Telegram app, ไม่รองรับ Desktop บางเวอร์ชัน)",
            parse_mode="HTML",
        )
    logger.info("Admin %s requested chat link for user %s", query.from_user.id, target_user_id)
