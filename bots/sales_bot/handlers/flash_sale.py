"""Flash Sale handler - Sales Bot แพร.

⚡ Flash Friday: VIP 30 วัน ฿199 (ปกติ ฿300) จำกัด 30 slot
ทุกวันศุกร์ 21:00-23:59 เวลาไทย
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from decimal import Decimal

from sqlalchemy import select, update as sa_update
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes

from shared.database import get_session
from shared.models import FlashSale, Package, PackageTier

logger = logging.getLogger(__name__)

TH_TZ = timezone(timedelta(hours=7))


async def _get_active_flash_sale() -> FlashSale | None:
    """Get the currently active flash sale, if any."""
    async with get_session() as session:
        result = await session.execute(
            select(FlashSale).where(FlashSale.is_active == True).order_by(FlashSale.id.desc()).limit(1)  # noqa: E712
        )
        return result.scalar_one_or_none()


async def get_flash_sale_price(package_id: int) -> Decimal | None:
    """Check if there's an active flash sale for a package. Returns flash_price or None."""
    flash = await _get_active_flash_sale()
    if flash and flash.package_id == package_id and flash.sold_slots < flash.total_slots:
        return flash.flash_price
    return None


async def increment_sold_slot(package_id: int) -> tuple[bool, int, int]:
    """Increment sold_slots for active flash sale. Returns (success, sold, total)."""
    async with get_session() as session:
        result = await session.execute(
            select(FlashSale).where(
                FlashSale.is_active == True,  # noqa: E712
                FlashSale.package_id == package_id,
            ).order_by(FlashSale.id.desc()).limit(1)
        )
        flash = result.scalar_one_or_none()
        if not flash:
            return False, 0, 0

        if flash.sold_slots >= flash.total_slots:
            return False, flash.sold_slots, flash.total_slots

        flash.sold_slots += 1
        sold = flash.sold_slots
        total = flash.total_slots
        await session.flush()

    # Check milestones & notify admin
    await _check_flash_sale_milestones(sold, total)

    return True, sold, total


async def _check_flash_sale_milestones(sold: int, total: int) -> None:
    """Send admin notifications at milestones."""
    import os
    import telegram as tg

    ADMIN_GROUP_ID = int(os.environ.get("ADMIN_GROUP_CHAT_ID", "-1003830920430"))
    admin_token = os.environ.get("ADMIN_BOT_TOKEN", "")
    if not admin_token:
        return

    try:
        admin_bot = tg.Bot(token=admin_token)
        await admin_bot.initialize()
        remaining = total - sold

        if sold == total:
            await admin_bot.send_message(
                chat_id=ADMIN_GROUP_ID,
                text=(
                    "🎉🎉🎉 <b>Flash Sale ขายหมดแล้ว!</b>\n\n"
                    f"✅ ขายครบ {total}/{total} slot\n"
                    "💰 ปิดการขาย Flash Sale อัตโนมัติ"
                ),
                parse_mode="HTML",
            )
        elif remaining == 5:
            await admin_bot.send_message(
                chat_id=ADMIN_GROUP_ID,
                text=(
                    f"🔥 <b>Flash Sale เหลืออีก {remaining} slot!</b>\n\n"
                    f"📊 ขายไปแล้ว {sold}/{total} slot\n"
                    "⏰ เร่งโปรโมทได้เลย!"
                ),
                parse_mode="HTML",
            )
    except Exception as exc:
        logger.error("Flash sale milestone notify failed: %s", exc)


async def flashsale_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/flashsale command — show current flash sale."""
    if not update.message:
        return

    flash = await _get_active_flash_sale()
    if not flash:
        await update.message.reply_text(
            "⚡ ยังไม่มี Flash Sale ตอนนี้ค่ะ\n\n"
            "Flash Friday เปิดทุกวันศุกร์ 21:00 - 23:59 น.\n"
            "ติดตามข่าวสารได้ที่กลุ่มฟรีค่ะ 😊\n\n"
            "📦 ดูแพ็กเกจปกติ → /packages"
        )
        return

    await _show_flash_sale(update.message, flash)


async def flashsale_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Callback: show flash sale from inline button."""
    query = update.callback_query
    if not query:
        return
    await query.answer()

    flash = await _get_active_flash_sale()
    if not flash:
        await query.edit_message_text(
            "⚡ Flash Sale หมดแล้วค่ะ หรือยังไม่เปิด\n\n"
            "Flash Friday เปิดทุกวันศุกร์ 21:00 - 23:59 น.\n"
            "📦 ดูแพ็กเกจปกติ → /packages"
        )
        return

    remaining = flash.total_slots - flash.sold_slots
    if remaining <= 0:
        await query.edit_message_text(
            "😱 <b>Flash Sale หมดแล้วค่ะ!</b>\n\n"
            f"ขายครบ {flash.total_slots}/{flash.total_slots} slot แล้ว\n"
            "ไว้ศุกร์หน้ามาใหม่นะคะ 💕\n\n"
            "📦 สนใจแพ็กเกจปกติ → /packages",
            parse_mode="HTML",
        )
        return

    text = _build_flash_sale_text(flash, remaining)
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("⚡ สมัคร Flash Sale ฿199", callback_data="buy_flash")],
        [InlineKeyboardButton("📦 ดูแพ็กเกจอื่น", callback_data="view_packages")],
        [InlineKeyboardButton("🔙 กลับเมนูหลัก", callback_data="back_main")],
    ])
    await query.edit_message_text(text, parse_mode="HTML", reply_markup=keyboard)


async def buy_flash_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Callback: user wants to buy flash sale."""
    query = update.callback_query
    if not query:
        return
    await query.answer()

    flash = await _get_active_flash_sale()
    if not flash:
        await query.edit_message_text("⚡ Flash Sale หมดแล้วหรือยังไม่เปิดค่ะ")
        return

    remaining = flash.total_slots - flash.sold_slots
    if remaining <= 0:
        await query.edit_message_text(
            "😱 <b>หมดแล้วค่ะ!</b> ขายครบ {0}/{0} slot\n"
            "ไว้ศุกร์หน้ามาใหม่นะคะ 💕".format(flash.total_slots),
            parse_mode="HTML",
        )
        return

    # Set flash sale tier in user context — uses tier 300 (VIP 30 วัน) but at flash price
    context.user_data["selected_tier"] = "300"
    context.user_data["selected_price"] = "199"
    context.user_data["flash_sale_id"] = flash.id

    import os
    QR_URL = "https://img2.pic.in.th/-2026-03-15-143743.png"

    text = (
        f"⚡ <b>Flash Sale — VIP 30 วัน</b>\n\n"
        f"💰 ราคาพิเศษ: <b>฿199</b> <s>฿300</s>\n"
        f"📊 เหลือ: <b>{remaining}/{flash.total_slots}</b> slot\n\n"
        f"📌 <b>วิธีชำระเงิน:</b>\n"
        f"1️⃣ สแกน QR PromptPay ด้านล่าง โอน <b>199 บาท</b>\n"
        f"2️⃣ ส่งสลิปโอนเงิน หรือ ลิงก์ซอง TrueMoney\n"
        f"3️⃣ รอแอดมินตรวจสอบ\n\n"
        f"⚠️ <b>โอน 199 บาทเท่านั้นค่ะ (ไม่ใช่ 300)</b>"
    )

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("⚡ ดู Flash Sale", callback_data="view_flashsale")],
        [InlineKeyboardButton("📦 ดูแพ็กเกจอื่น", callback_data="view_packages")],
    ])

    await query.edit_message_text(text, parse_mode="HTML", reply_markup=keyboard)

    # Send QR
    try:
        await context.bot.send_photo(
            chat_id=query.message.chat_id,
            photo=QR_URL,
            caption="📱 สแกน QR PromptPay เพื่อโอน <b>199 บาท</b>\nแล้วส่งสลิปมาที่แชทนี้เลยค่ะ ⚡",
            parse_mode="HTML",
        )
    except Exception as exc:
        logger.warning("Failed to send QR: %s", exc)


async def _show_flash_sale(message, flash: FlashSale) -> None:
    """Send flash sale info to user."""
    remaining = flash.total_slots - flash.sold_slots

    if remaining <= 0:
        await message.reply_text(
            "😱 <b>Flash Sale หมดแล้วค่ะ!</b>\n\n"
            f"ขายครบ {flash.total_slots}/{flash.total_slots} slot แล้ว\n"
            "ไว้ศุกร์หน้ามาใหม่นะคะ 💕\n\n"
            "📦 สนใจแพ็กเกจปกติ → /packages",
            parse_mode="HTML",
        )
        return

    text = _build_flash_sale_text(flash, remaining)
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("⚡ สมัคร Flash Sale ฿199", callback_data="buy_flash")],
        [InlineKeyboardButton("📦 ดูแพ็กเกจอื่น", callback_data="view_packages")],
        [InlineKeyboardButton("🔙 กลับเมนูหลัก", callback_data="back_main")],
    ])
    await message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)


def _build_flash_sale_text(flash: FlashSale, remaining: int) -> str:
    """Build flash sale display text."""
    # Progress bar
    sold = flash.sold_slots
    total = flash.total_slots
    filled = int((sold / total) * 10)
    bar = "🟥" * filled + "⬜" * (10 - filled)

    return (
        "⚡⚡⚡ <b>FLASH FRIDAY</b> ⚡⚡⚡\n\n"
        f"📦 <b>{flash.name}</b>\n"
        f"💰 ราคาพิเศษ: <b>฿{flash.flash_price:.0f}</b> <s>฿{flash.original_price:.0f}</s>\n"
        f"🔥 ลดไป: <b>฿{flash.original_price - flash.flash_price:.0f}</b> ({((flash.original_price - flash.flash_price) / flash.original_price * 100):.0f}%)\n\n"
        f"📊 <b>เหลือ {remaining}/{total} slot</b>\n"
        f"{bar}\n\n"
        "✅ คลิปเต็มไม่เบลอ ทุกวัน\n"
        "✅ Exclusive set ก่อนใคร\n"
        "✅ รวมกว่า 10,000 คลิป\n\n"
        f"⏰ เปิดขาย: <b>ศุกร์ 21:00 - 23:59</b>\n"
        "เมื่อหมดก็หมด ไม่มีรอบสอง! 🔥"
    )


def get_flash_sale_handlers() -> list:
    """Return all handlers for the flash sale module."""
    return [
        CommandHandler("flashsale", flashsale_command),
        CallbackQueryHandler(flashsale_callback, pattern="^view_flashsale$"),
        CallbackQueryHandler(buy_flash_callback, pattern="^buy_flash$"),
    ]
