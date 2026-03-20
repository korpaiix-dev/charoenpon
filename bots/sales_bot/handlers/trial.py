"""Trial handler - Sales Bot แพร.

ระบบทดลอง VIP 24 ชม. ฿99
- /trial command หรือ /start trial → เช็คสิทธิ์ + แสดงข้อมูล
- จำกัด 1 ครั้ง / 30 วัน ต่อคน
- deep link: tg://resolve?domain=jarernAD1_bot&start=trial
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy import select, and_
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

from shared.database import get_session
from shared.models import (
    Package,
    PackageTier,
    Payment,
    PaymentStatus,
    Subscription,
    User,
)

logger = logging.getLogger(__name__)

TRIAL_COOLDOWN_DAYS = 30  # จำกัด 1 ครั้ง / 30 วัน

TRIAL_TEXT = (
    "🆕 <b>ทดลอง VIP เจริญพร — 24 ชม.</b>\n\n"
    "ยังไม่เคยลอง VIP? ทดลองก่อนได้!\n"
    "แค่ <b>฿99</b> ดูคลิปเต็มไม่เบลอ 24 ชั่วโมง\n\n"
    "✅ คลิปเต็มไม่เบลอ\n"
    "✅ รวมกว่า 10,000 คลิป\n"
    "✅ ไม่ผูกมัด ไม่ต่ออัตโนมัติ\n\n"
    "⚠️ จำกัด 1 ครั้ง / 30 วัน\n\n"
    "━━━━━━━━━━━━━━━━━━\n"
    "💰 ยอดที่ต้องชำระ: <b>฿99</b>\n\n"
    "📌 <b>วิธีชำระเงิน:</b>\n"
    "1️⃣ สแกน QR PromptPay ด้านล่าง หรือโอน <b>฿99</b>\n"
    "2️⃣ ส่งสลิปโอนเงิน หรือ ลิงก์ซอง TrueMoney\n"
    "3️⃣ รอแอดมินตรวจสอบ\n\n"
    "💳 <b>ช่องทางชำระ:</b>\n"
    "• PromptPay / โอนธนาคาร → ส่งรูปสลิป\n"
    "• TrueMoney Wallet → ส่งลิงก์ gift.truemoney.com"
)

ALREADY_USED_TEXT = (
    "⚠️ คุณเคยทดลอง VIP แล้วค่ะ (ภายใน 30 วัน)\n\n"
    "สมัคร VIP เต็มเลยดีกว่า! คุ้มกว่าเยอะค่ะ 😊\n\n"
    "🥉 <b>VIP 30 วัน — ฿300</b>\n"
    "👙 <b>OnlyFans + VIP 30 วัน — ฿500</b>\n"
    "🥈 <b>GOD MODE 90 วัน — ฿1,299</b>\n"
    "💎 <b>GOD MODE ถาวร — ฿2,499</b>\n\n"
    "กดดูแพ็กเกจด้านล่างเลยค่า 👇"
)

QR_URL = "https://img2.pic.in.th/-2026-03-15-143743.png"


async def _check_trial_eligible(telegram_id: int) -> bool:
    """เช็คว่า user นี้สามารถซื้อ trial ได้มั้ย (1 ครั้ง / 30 วัน)."""
    cutoff = datetime.utcnow() - timedelta(days=TRIAL_COOLDOWN_DAYS)

    async with get_session() as session:
        # หา user
        user_result = await session.execute(
            select(User).where(User.telegram_id == telegram_id)
        )
        user = user_result.scalar_one_or_none()
        if not user:
            return True  # ยังไม่มี user = ยังไม่เคยซื้อ

        # หา trial package
        pkg_result = await session.execute(
            select(Package).where(Package.tier == PackageTier.TIER_99)
        )
        trial_pkg = pkg_result.scalar_one_or_none()
        if not trial_pkg:
            return False  # ไม่มี trial package ในระบบ

        # เช็ค payment ที่ confirmed สำหรับ trial package ภายใน 30 วัน
        payment_result = await session.execute(
            select(Payment).where(
                and_(
                    Payment.user_id == user.id,
                    Payment.package_id == trial_pkg.id,
                    Payment.status == PaymentStatus.CONFIRMED,
                    Payment.created_at >= cutoff,
                )
            )
        )
        existing = payment_result.scalar_one_or_none()
        return existing is None


async def trial_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/trial command — แสดงข้อมูล trial หรือบอกว่าเคยใช้แล้ว."""
    if not update.effective_user or not update.message:
        return

    tg_user = update.effective_user
    eligible = await _check_trial_eligible(tg_user.id)

    if eligible:
        # Set selected tier for payment flow
        context.user_data["selected_tier"] = "99"
        context.user_data["selected_price"] = "99"

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📦 ดูแพ็กเกจอื่น", callback_data="view_packages")],
            [InlineKeyboardButton("🔙 กลับเมนูหลัก", callback_data="back_main")],
        ])

        await update.message.reply_text(
            TRIAL_TEXT,
            parse_mode="HTML",
            reply_markup=keyboard,
        )

        # Send QR code
        try:
            await context.bot.send_photo(
                chat_id=update.message.chat_id,
                photo=QR_URL,
                caption="📱 สแกน QR PromptPay เพื่อโอน <b>฿99</b>\nแล้วส่งสลิปมาที่แชทนี้เลยค่ะ 🙏",
                parse_mode="HTML",
            )
        except Exception as exc:
            logger.warning("Failed to send QR for trial: %s", exc)
    else:
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📦 ดูแพ็กเกจ VIP", callback_data="view_packages")],
            [InlineKeyboardButton("🔙 กลับเมนูหลัก", callback_data="back_main")],
        ])

        await update.message.reply_text(
            ALREADY_USED_TEXT,
            parse_mode="HTML",
            reply_markup=keyboard,
        )


async def trial_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Callback: view_trial — เหมือน /trial แต่เป็น inline button."""
    query = update.callback_query
    if not query:
        return
    await query.answer()

    tg_user = update.effective_user
    if not tg_user:
        return

    eligible = await _check_trial_eligible(tg_user.id)

    if eligible:
        context.user_data["selected_tier"] = "99"
        context.user_data["selected_price"] = "99"

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📦 ดูแพ็กเกจอื่น", callback_data="view_packages")],
            [InlineKeyboardButton("🔙 กลับเมนูหลัก", callback_data="back_main")],
        ])

        await query.edit_message_text(
            TRIAL_TEXT,
            parse_mode="HTML",
            reply_markup=keyboard,
        )

        # Send QR code as separate message
        try:
            await context.bot.send_photo(
                chat_id=query.message.chat_id,
                photo=QR_URL,
                caption="📱 สแกน QR PromptPay เพื่อโอน <b>฿99</b>\nแล้วส่งสลิปมาที่แชทนี้เลยค่ะ 🙏",
                parse_mode="HTML",
            )
        except Exception as exc:
            logger.warning("Failed to send QR for trial callback: %s", exc)
    else:
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📦 ดูแพ็กเกจ VIP", callback_data="view_packages")],
            [InlineKeyboardButton("🔙 กลับเมนูหลัก", callback_data="back_main")],
        ])

        await query.edit_message_text(
            ALREADY_USED_TEXT,
            parse_mode="HTML",
            reply_markup=keyboard,
        )


def get_trial_handlers() -> list:
    """Return all handlers for the trial module."""
    return [
        CommandHandler("trial", trial_command),
        CallbackQueryHandler(trial_callback, pattern="^view_trial$"),
    ]
