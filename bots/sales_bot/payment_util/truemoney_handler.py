"""TrueMoney link handler extracted from handlers/payment.py (Round E final).
Handles incoming TrueMoney gift link messages from customers, verifies + redeems
via Slip2Go-equivalent, and routes to admin or auto-approves.
"""
from __future__ import annotations

import asyncio
import html as _html
import logging
import os
import re
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional

from sqlalchemy import select, update as _upd
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from shared.database import get_session
from shared.models import (
    User, Payment, PaymentStatus, PaymentMethod,
    Subscription, SubscriptionStatus,
    Package, PackageTier, GroupRegistry,
)
from bots.sales_bot.payment_util.utils import _resolve_tier
from shared.tz import TH_TZ
from shared.utils import (
    check_duplicate_slip,
    compute_slip_hash,
    format_thb,
    log_admin_action,
)
from shared.endmonth_vip_promo import (
    is_endmonth_vip_promo_active,
    is_may_combo_promo_active,
    is_lucky_6_active,
)
from shared.admin_alert import _admin_group_id
from shared.pricing import effective_price as _hub_effective_price, TIER_PRICES as _HUB_TIER_PRICES

from bots.sales_bot.payment_util.utils import _notify_discord
from bots.sales_bot.payment_util.promo_helpers import TRUEMONEY_PATTERN
from bots.sales_bot.payment_util.ai_helpers import _ai_screen_image, _ai_read_slip
from bots.sales_bot.payment_util.promo_helpers import _verify_truemoney_link, _get_active_promo_for_user
from bots.sales_bot.payment_util.approve import _approve_payment

logger = logging.getLogger(__name__)

# Re-export helpers that may be called as bare names inside the function body
async def _get_effective_price(tier: str, context_user_data: dict) -> Decimal:
    """Local effective_price wrapper preserving comeback logic."""
    base_price = _HUB_TIER_PRICES.get(tier, Decimal('0'))
    comeback_promo = context_user_data.get('comeback_promo')
    if comeback_promo:
        from bots.sales_bot.comeback_dm import validate_promo_code
        promo = await validate_promo_code(comeback_promo)
        if promo:
            discount_pct = promo['discount_pct']
            return Decimal(str(int(base_price * (100 - discount_pct) / 100)))
    return _hub_effective_price(tier, context_user_data)

async def handle_truemoney_link(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle TrueMoney gift link."""
    if not update.message or not update.message.text:
        return

    user = update.effective_user
    if not user:
        return

    text = update.message.text.strip()
    match = TRUEMONEY_PATTERN.search(text)
    if not match:
        return

    link = match.group(0)

    # Check selected package
    selected_tier = context.user_data.get("selected_tier")
    if not selected_tier:
        await update.message.reply_text(
            "กรุณาเลือกแพ็กเกจก่อนส่งลิงก์ซองนะคะ 📦\n"
            "พิมพ์ /packages เพื่อดูแพ็กเกจค่ะ",
        )
        return

    expected_price = await _get_effective_price(selected_tier, context.user_data)
    if not expected_price:
        await update.message.reply_text("แพ็กเกจไม่ถูกต้องค่ะ กรุณาเลือกใหม่นะคะ")
        return

    await update.message.reply_text("🔍 กำลังตรวจสอบซอง TrueMoney ค่ะ กรุณารอสักครู่...")

    # Check duplicate
    dup = await check_duplicate_slip(link)
    if dup:
        await update.message.reply_text(
            "❌ ลิงก์ซองนี้เคยใช้แล้วค่ะ กรุณาส่งลิงก์ใหม่นะคะ"
        )
        await log_admin_action(
            admin_id=0,
            action="payment_reject_duplicate_truemoney",
            target_type="user",
            target_id=user.id,
            details=f"Duplicate TrueMoney link: {link}",
        )
        return

    # Verify TrueMoney
    tm_result = await _verify_truemoney_link(link)
    slip_hash = compute_slip_hash(link)

    # Get user and package from DB
    async with get_session() as session:
        from shared.models import User as UserModel

        user_result = await session.execute(
            select(UserModel).where(UserModel.telegram_id == user.id)
        )
        db_user = user_result.scalar_one_or_none()
        if not db_user:
            db_user = UserModel(
                telegram_id=user.id,
                username=user.username,
                first_name=user.first_name,
            )
            session.add(db_user)
            await session.flush()

        pkg_result = await session.execute(
            select(Package).where(Package.tier == _resolve_tier(selected_tier))
        )
        package = pkg_result.scalar_one_or_none()
        if not package:
            await update.message.reply_text("ไม่พบแพ็กเกจในระบบค่ะ ติดต่อแอดมิน @sperm6969นะคะ")
            return

        # Duplicate payment guard: same user + same amount within 120 seconds (extended 2026-06-21 — redeem can take 90s+)
        dedup_cutoff = datetime.utcnow() - timedelta(seconds=120)
        dup_check = await session.execute(
            select(Payment).where(
                Payment.user_id == db_user.id,
                Payment.amount == expected_price,
                Payment.method == PaymentMethod.TRUEWALLET,
                Payment.created_at >= dedup_cutoff,
            )
        )
        if dup_check.scalar_one_or_none():
            logger.warning("Duplicate TRUEWALLET payment skipped: user_id=%s amount=%s", db_user.id, expected_price)
            await update.message.reply_text("⚠️ คุณเพิ่งส่งลิงก์ยอดนี้ไปแล้วค่ะ กรุณารอแอดมินตรวจสอบ 🙏")
            return

        payment = Payment(
            user_id=db_user.id,
            package_id=package.id,
            amount=expected_price,
            method=PaymentMethod.TRUEWALLET,
            status=PaymentStatus.PENDING,
            slip_url=link,
            slip_hash=slip_hash,
            transaction_ref=tm_result.get("voucher_id", ""),
        )
        session.add(payment)
        await session.flush()
        payment_id = payment.id

    # Decision
    reasons: list[str] = []

    tm_error = tm_result.get("error", "")

    # Handle specific errors
    if tm_error == "own_voucher":
        await update.message.reply_text("❌ ซองนี้เป็นของร้านเอง (เติมไม่ได้ค่ะ)")
        return
    elif tm_error == "wallet_not_found":
        await update.message.reply_text("❌ เบอร์วอลเล็ทร้านผิด ติดต่อแอดมินค่ะ @sperm6969")
        return
    elif tm_error in ("forbidden", "timeout"):
        await update.message.reply_text("⚠️ บอทรับซองไม่ได้ ส่งให้แอดมินกดรับเองนะคะ")
        # Send fallback to admin group
        try:
            import telegram as tg
            import html as _html
            safe_name = _html.escape(str(user.first_name or "ลูกค้า"))
            ADMIN_GROUP_ID = int(os.environ.get("ADMIN_GROUP_CHAT_ID", ""))
            admin_bot = tg.Bot(token=os.environ.get("ADMIN_BOT_TOKEN", ""))
            await admin_bot.initialize()
            keyboard = tg.InlineKeyboardMarkup([
                [
                    # TIER_99 button removed 2026-06-01
                    tg.InlineKeyboardButton("⚡ 199 (Flash)", callback_data=f"approve_199_{user.id}", api_kwargs={"style": "success"}),
                    tg.InlineKeyboardButton("🔥 200 (VIP โปร)", callback_data=f"approve_200_{user.id}", api_kwargs={"style": "success"}) if is_endmonth_vip_promo_active() else tg.InlineKeyboardButton("✅ 300 (VIP)", callback_data=f"approve_300_{user.id}", api_kwargs={"style": "success"}),
                ],
                [
                    tg.InlineKeyboardButton("🔥 349 (OF โปร)", callback_data=f"approve_349_{user.id}", api_kwargs={"style": "success"}) if is_may_combo_promo_active() else tg.InlineKeyboardButton("✅ 500 (OF)", callback_data=f"approve_500_{user.id}", api_kwargs={"style": "success"}),
                    tg.InlineKeyboardButton("🔥 999 (3M โปร)", callback_data=f"approve_999_{user.id}", api_kwargs={"style": "success"}) if is_may_combo_promo_active() else tg.InlineKeyboardButton("✅ 1299 (3M)", callback_data=f"approve_1299_{user.id}", api_kwargs={"style": "success"}),
                ],
                [
                    tg.InlineKeyboardButton("💎 2000 (GOD โปร)", callback_data=f"approve_2000_{user.id}", api_kwargs={"style": "success"}) if is_endmonth_vip_promo_active() else tg.InlineKeyboardButton("✅ 2499 (GOD)", callback_data=f"approve_2499_{user.id}", api_kwargs={"style": "success"}),
                    tg.InlineKeyboardButton("🌊 500 (Summer)", callback_data=f"approve_ADD500_{user.id}", api_kwargs={"style": "success"}),
                ],
                *([
                    [
                        tg.InlineKeyboardButton("🍀 166 (Lucky VIP)", callback_data=f"approve_166_{user.id}", api_kwargs={"style": "success"}),
                        tg.InlineKeyboardButton("🍀 266 (Lucky OF)",  callback_data=f"approve_266_{user.id}", api_kwargs={"style": "success"}),
                    ],
                    [
                        tg.InlineKeyboardButton("🍀 666 (Lucky GOD3M)", callback_data=f"approve_666_{user.id}", api_kwargs={"style": "success"}),
                        tg.InlineKeyboardButton("🍀 2266 (Lucky ถาวร)", callback_data=f"approve_2266_{user.id}", api_kwargs={"style": "success"}),
                    ],
                ] if is_lucky_6_active() else []),
                *([
                    [
                        tg.InlineKeyboardButton("🍀 166 (Lucky VIP)", callback_data=f"approve_166_{user.id}", api_kwargs={"style": "success"}),
                        tg.InlineKeyboardButton("🍀 266 (Lucky OF)",  callback_data=f"approve_266_{user.id}", api_kwargs={"style": "success"}),
                    ],
                    [
                        tg.InlineKeyboardButton("🍀 666 (Lucky GOD3M)", callback_data=f"approve_666_{user.id}", api_kwargs={"style": "success"}),
                        tg.InlineKeyboardButton("🍀 2266 (Lucky ถาวร)", callback_data=f"approve_2266_{user.id}", api_kwargs={"style": "success"}),
                    ],
                ] if is_lucky_6_active() else []),
                [
                    tg.InlineKeyboardButton("❌ ซองเสีย", callback_data=f"reject_{user.id}", api_kwargs={"style": "danger"}),
                ],
                [tg.InlineKeyboardButton(f"💬 @{user.username}", url=f"https://t.me/{user.username}", api_kwargs={"style": "primary"}) if user.username else tg.InlineKeyboardButton(f"💬 ID: {user.id}", url=f"tg://user?id={user.id}", api_kwargs={"style": "primary"})],
            ])
            await admin_bot.send_message(
                chat_id=ADMIN_GROUP_ID,
                text=(
                    f"🆘 <b>บอทเติมเองไม่ได้ (Timeout/Error)</b>\n"
                    f"👤 ลูกค้า: {safe_name} (ID: <code>{user.id}</code>)\n"
                    f"🔗 <b>ลิ้งค์:</b> {link}\n\n"
                    f"👇 <b>แอดมินกดรับเอง แล้วมากดปุ่มยอดเงิน:</b>"
                ),
                parse_mode="HTML",
                reply_markup=keyboard,
            )
        except Exception as exc:
            logger.error("Failed to send TM fallback: %s", exc)
        return

    if not tm_result["valid"]:
        reasons.append("ไม่สามารถยืนยันซอง TrueMoney ได้")
    elif tm_result["amount"] is not None:
        # Accept BOTH promo price (expected) and tier base price.
        # Some customers pay full price even when promo is active.
        tier_base_map = {
            "300": Decimal("300"), "500": Decimal("500"),
            "1299": Decimal("1299"), "2499": Decimal("2499"),
        }
        base_price = tier_base_map.get(str(selected_tier), expected_price)
        acceptable_amounts = {expected_price, base_price}
        # If envelope matches base price -> auto-bump expected_price + payment.amount
        # so DB logs reflect what customer actually paid.
        if abs(tm_result["amount"] - base_price) <= Decimal("1") and base_price != expected_price:
            expected_price = base_price
            try:
                payment.amount = base_price
            except Exception:
                pass
        elif not any(abs(tm_result["amount"] - amt) <= Decimal("1") for amt in acceptable_amounts):
            reasons.append(
                f"ยอดไม่ตรง: ซอง {format_thb(tm_result['amount'])} "
                f"แต่ต้องการ {format_thb(expected_price)} "
                f"(หรือ {format_thb(base_price)} ราคาเต็ม)"
            )

    if not reasons and tm_result["valid"]:
        # APPROVED
        invite_links_raw = await _approve_payment(payment, user.id, context.bot, source="truemoney")

        # Flash Sale: increment sold_slots if active
        if context.user_data.get("flash_sale_id") and selected_tier == "300":
            try:
                from bots.sales_bot.handlers.flash_sale import increment_sold_slot
                success_fs, sold_fs, total_fs = await increment_sold_slot(payment.package_id)
                if success_fs:
                    logger.info("Flash sale slot incremented (TrueMoney): %d/%d", sold_fs, total_fs)
            except Exception as exc_fs:
                logger.warning("Flash sale slot increment failed: %s", exc_fs)

        # คำนวณวันหมดอายุ
        async with get_session() as session:
            pkg_result = await session.execute(
                select(Package).where(Package.id == payment.package_id)
            )
            pkg = pkg_result.scalar_one()
            expire_date = (datetime.utcnow() + timedelta(days=pkg.duration_days)).strftime("%d/%m/%Y")
            pkg_name = pkg.name

        # สร้าง inline buttons สำหรับ invite links
        import telegram as tg
        import html as _html
        link_buttons = []
        for link_line in invite_links_raw:
            # format: "• title: https://..."
            parts = link_line.split(": ", 1)
            if len(parts) == 2:
                title = parts[0].replace("• ", "").strip()
                url = parts[1].strip()
                link_buttons.append(tg.InlineKeyboardButton(f"🚀 {title}", url=url))

        # จัดปุ่ม 2 คอลัมน์
        button_rows = [link_buttons[i:i+2] for i in range(0, len(link_buttons), 2)]
        keyboard = tg.InlineKeyboardMarkup(button_rows) if button_rows else None

        await update.message.reply_text(
            f"🟢 <b>อนุมัติยอด {selected_tier} บาท เรียบร้อยค่ะ</b>\n"
            f"แพ็กเกจ: {pkg_name}\n"
            f"📅 หมดอายุ: {expire_date}\n\n"
            f"👆 <b>กดเข้ากลุ่มที่ปุ่มด้านล่างได้เลย</b>\n\n"
            f"🆓 <b>ห้องฟรี:</b> https://t.me/addlist/2xN-ag15W4U2MTNl",
            parse_mode="HTML",
            reply_markup=keyboard,
        )

        # แจ้งเตือนกลุ่มแอดมิน
        try:
            import html as _html
            safe_name = _html.escape(str(user.first_name or "ลูกค้า"))
            ADMIN_GROUP_ID = int(os.environ.get("ADMIN_GROUP_CHAT_ID", ""))
            admin_bot = tg.Bot(token=os.environ.get("ADMIN_BOT_TOKEN", ""))
            await admin_bot.initialize()
            links_count = len(invite_links_raw)
            admin_keyboard = tg.InlineKeyboardMarkup([
                [tg.InlineKeyboardButton(f"💬 @{user.username}", url=f"https://t.me/{user.username}", api_kwargs={"style": "primary"}) if user.username else tg.InlineKeyboardButton(f"💬 ID: {user.id}", url=f"tg://user?id={user.id}", api_kwargs={"style": "primary"})],
            ])
            await admin_bot.send_message(
                chat_id=ADMIN_GROUP_ID,
                text=(
                    f"✅ <b>TrueMoney อนุมัติอัตโนมัติ</b>\n\n"
                    f"👤 ลูกค้า: {safe_name} (ID: <code>{user.id}</code>)\n"
                    f"💰 ยอด: {format_thb(expected_price)}\n"
                    f"📦 แพ็กเกจ: {pkg_name}\n"
                    f"🔗 ส่งลิงก์: {links_count} กลุ่ม\n"
                    f"🏦 Voucher: <code>{tm_result.get('voucher_id', 'N/A')}</code>"
                ),
                parse_mode="HTML",
                reply_markup=admin_keyboard,
            )
        except Exception as exc:
            logger.warning("Failed to notify admin group (TM approve): %s", exc)

        await log_admin_action(
            admin_id=0,
            action="payment_approved_truemoney",
            target_type="payment",
            target_id=payment_id,
            details=f"user_tg={user.id} tier={selected_tier} voucher={tm_result.get('voucher_id', '')}",
        )

        await _notify_discord(
            "✅ Payment Approved (TrueMoney)",
            f"User: @{user.username or user.id}\n"
            f"Package: {selected_tier} THB\n"
            f"Voucher: {tm_result.get('voucher_id', 'N/A')}",
        )

        # ── Sync Google Sheets ──
        try:
            from sheets.daily_revenue import DailyRevenueSheet
            from sheets.members import MembersSheet
            from sheets.income_log import IncomeLogSheet
            await DailyRevenueSheet.update()
            from sheets.daily_summary import DailySummarySheet
            await DailySummarySheet.update()
            await IncomeLogSheet.log_payment(payment_id, approved_by="ระบบอัตโนมัติ")
            await MembersSheet.update_member(db_user.id)
            logger.info("Sheets synced for TrueMoney payment user_tg=%d", user.id)
        except Exception as exc_s:
            logger.warning("Sheets sync failed: %s", exc_s)

        # Mark comeback promo as purchased if applicable
        comeback_promo = context.user_data.get("comeback_promo")
        if comeback_promo:
            try:
                from bots.sales_bot.comeback_dm import mark_promo_purchased
                await mark_promo_purchased(comeback_promo)
                logger.info("Comeback promo %s marked as purchased", comeback_promo)
            except Exception as exc_cb:
                logger.warning("Failed to mark comeback promo: %s", exc_cb)

        # Process referral reward if this user was referred
        try:
            from bots.sales_bot.handlers.referral import process_referral_reward
            await process_referral_reward(user.id, context.bot)
        except Exception as exc_ref:
            logger.warning("Referral reward processing failed: %s", exc_ref)

        # ส่ง DM แนะนำชวนเพื่อน หลังจากส่งลิงก์เข้ากลุ่ม 3 วินาที
        try:
            await asyncio.sleep(3)
            await _send_welcome_referral_dm(context.bot, user.id)
        except Exception as exc_w:
            logger.warning("Welcome referral DM failed (TrueMoney): %s", exc_w)

        context.user_data.pop("selected_tier", None)
        context.user_data.pop("selected_price", None)
        context.user_data.pop("comeback_promo", None)
        context.user_data.pop("comeback_discount", None)

    elif tm_result["valid"] and tm_result["amount"] is None:
        # HOLD — valid link but can't read amount
        await update.message.reply_text(
            "⏳ <b>ซองอยู่ระหว่างตรวจสอบค่ะ</b>\n\n"
            "แอดมินจะตรวจสอบและแจ้งผลให้เร็วที่สุดค่ะ\n"
            f"หมายเลขอ้างอิง: #PAY{payment_id}",
            parse_mode="HTML",
        )

        await log_admin_action(
            admin_id=0,
            action="payment_hold_truemoney",
            target_type="payment",
            target_id=payment_id,
            details=f"user_tg={user.id} reason=Cannot verify amount",
        )

        await _notify_discord(
            "⏳ Payment On Hold (TrueMoney)",
            f"User: @{user.username or user.id}\n"
            f"Package: {selected_tier} THB\n"
            f"Payment ID: {payment_id}\n"
            f"Reason: Cannot verify amount",
        )

    else:
        # REJECTED
        async with get_session() as session:
            result = await session.execute(
                select(Payment).where(Payment.id == payment_id)
            )
            p = result.scalar_one()
            p.status = PaymentStatus.REJECTED
            p.reject_reason = "; ".join(reasons)

        reasons_text = "\n".join(f"• {r}" for r in reasons)
        await update.message.reply_text(
            f"❌ <b>ซอง TrueMoney ไม่ผ่านการตรวจสอบค่ะ</b>\n\n"
            f"<b>เหตุผล:</b>\n{reasons_text}\n\n"
            f"กรุณาส่งลิงก์ใหม่ที่ถูกต้อง หรือติดต่อแอดมิน @sperm6969ค่ะ\n"
            f"หมายเลขอ้างอิง: #PAY{payment_id}",
            parse_mode="HTML",
        )

        await log_admin_action(
            admin_id=0,
            action="payment_rejected_truemoney",
            target_type="payment",
            target_id=payment_id,
            details=f"user_tg={user.id} reasons={'; '.join(reasons)}",
        )

        await _notify_discord(
            "❌ Payment Rejected (TrueMoney)",
            f"User: @{user.username or user.id}\n"
            f"Package: {selected_tier} THB\n"
            f"Reasons: {'; '.join(reasons)}",
        )

        # แจ้ง Telegram Admin Group ด้วย — ให้แอดมินเข้าไปเช็คได้
        try:
            import telegram as tg
            import html as _html
            safe_name = _html.escape(str(user.first_name or "ลูกค้า"))
            ADMIN_GROUP_ID = int(os.environ.get("ADMIN_GROUP_CHAT_ID", ""))
            admin_bot = tg.Bot(token=os.environ.get("ADMIN_BOT_TOKEN", ""))
            await admin_bot.initialize()
            reasons_tg = "\n".join(f"• {r}" for r in reasons)
            keyboard = tg.InlineKeyboardMarkup([
                [
                    tg.InlineKeyboardButton("🔥 200 (VIP โปร)", callback_data=f"approve_200_{user.id}", api_kwargs={"style": "success"}) if is_endmonth_vip_promo_active() else tg.InlineKeyboardButton("✅ 300 (VIP)", callback_data=f"approve_300_{user.id}", api_kwargs={"style": "success"}),
                    tg.InlineKeyboardButton("🔥 349 (OF โปร)", callback_data=f"approve_349_{user.id}", api_kwargs={"style": "success"}) if is_may_combo_promo_active() else tg.InlineKeyboardButton("✅ 500 (OF)", callback_data=f"approve_500_{user.id}", api_kwargs={"style": "success"}),
                ],
                [
                    tg.InlineKeyboardButton("🔥 999 (3M โปร)", callback_data=f"approve_999_{user.id}", api_kwargs={"style": "success"}) if is_may_combo_promo_active() else tg.InlineKeyboardButton("✅ 1299 (3M)", callback_data=f"approve_1299_{user.id}", api_kwargs={"style": "success"}),
                    tg.InlineKeyboardButton("💎 2000 (GOD โปร)", callback_data=f"approve_2000_{user.id}", api_kwargs={"style": "success"}) if is_endmonth_vip_promo_active() else tg.InlineKeyboardButton("✅ 2499 (GOD)", callback_data=f"approve_2499_{user.id}", api_kwargs={"style": "success"}),
                ],
                [
                    tg.InlineKeyboardButton("🌊 500 (Summer)", callback_data=f"approve_ADD500_{user.id}", api_kwargs={"style": "success"}),
                ],
                *([
                    [
                        tg.InlineKeyboardButton("🍀 166 (Lucky VIP)", callback_data=f"approve_166_{user.id}", api_kwargs={"style": "success"}),
                        tg.InlineKeyboardButton("🍀 266 (Lucky OF)",  callback_data=f"approve_266_{user.id}", api_kwargs={"style": "success"}),
                    ],
                    [
                        tg.InlineKeyboardButton("🍀 666 (Lucky GOD3M)", callback_data=f"approve_666_{user.id}", api_kwargs={"style": "success"}),
                        tg.InlineKeyboardButton("🍀 2266 (Lucky ถาวร)", callback_data=f"approve_2266_{user.id}", api_kwargs={"style": "success"}),
                    ],
                ] if is_lucky_6_active() else []),
                [tg.InlineKeyboardButton(f"💬 @{user.username}", url=f"https://t.me/{user.username}", api_kwargs={"style": "primary"}) if user.username else tg.InlineKeyboardButton(f"💬 ID: {user.id}", url=f"tg://user?id={user.id}", api_kwargs={"style": "primary"})],
            ])
            await admin_bot.send_message(
                chat_id=ADMIN_GROUP_ID,
                text=(
                    f"❌ <b>Payment Rejected (TrueMoney)</b>\n\n"
                    f"👤 ลูกค้า: {safe_name}\n"
                    f"🆔 TG ID: <code>{user.id}</code>\n"
                    f"📦 แพ็กเกจ: {selected_tier} THB\n"
                    f"🔗 ลิงก์: {link}\n"
                    f"📝 #PAY{payment_id}\n\n"
                    f"<b>เหตุผล:</b>\n{reasons_tg}\n\n"
                    f"⚠️ แอดมินตรวจสอบและกดอนุมัติ manual ได้ที่ปุ่มด้านล่าง"
                ),
                parse_mode="HTML",
                reply_markup=keyboard,
            )
        except Exception as exc:
            logger.warning("Failed to notify TG admin group (TM reject): %s", exc)
