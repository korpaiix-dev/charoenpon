"""/start handler - Sales Bot แพร.

บันทึก user + source, แสดงปุ่มหลัก.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

from shared.database import get_session
from shared.models import Lead, LeadStatus, User

logger = logging.getLogger(__name__)

WELCOME_TEXT = (
    "หวัดดีค่า~ ยินดีต้อนรับสู่ <b>กลุ่ม VIP เจริญพร</b> 🎉\n\n"
    "แพรเองค่า 😊 มีอะไรให้ช่วยบอกได้เลยนะ\n"
    "จะดูแพ็กเกจ จะสมัคร หรือมีคำถามอะไร กดด้านล่างเลยค่า 👇"
)

MAIN_KEYBOARD = InlineKeyboardMarkup(
    [
        [InlineKeyboardButton("⚡ Flash Sale", callback_data="view_flashsale")],
        [InlineKeyboardButton("📦 ดูแพ็กเกจ", callback_data="view_packages")],
        [InlineKeyboardButton("🆓 ห้องฟรี", url="https://t.me/addlist/2xN-ag15W4U2MTNl")],
        [InlineKeyboardButton("👩‍💼 ติดต่อแอดมิน", url="https://t.me/zeinju_bunker")],
    ]
)


def _extract_source(args: list[str]) -> str | None:
    """Extract referral/campaign source from /start deep link."""
    if args and args[0]:
        return args[0]
    return None


async def _handle_comeback_start(update: Update, context: ContextTypes.DEFAULT_TYPE, promo_code: str) -> bool:
    """Handle /start comeback_{code} deep link. Returns True if handled."""
    from bots.sales_bot.comeback_dm import validate_promo_code, mark_promo_responded, _calculate_discounted_price

    promo = await validate_promo_code(promo_code)
    if not promo:
        await update.message.reply_text(
            "❌ โปรโมชั่นนี้หมดอายุหรือไม่ถูกต้องแล้วค่ะ\n\n"
            "กดดูแพ็กเกจราคาปกติได้เลยนะคะ 👇",
            parse_mode="HTML",
            reply_markup=MAIN_KEYBOARD,
        )
        return True

    # Mark as responded
    await mark_promo_responded(promo_code)

    discount_pct = promo["discount_pct"]
    discounted_price = promo["discounted_price"]

    # Store promo in user context for payment
    context.user_data["selected_tier"] = "300"
    context.user_data["selected_price"] = str(discounted_price)
    context.user_data["comeback_promo"] = promo_code
    context.user_data["comeback_discount"] = discount_pct

    text = (
        f"🔥 <b>ยินดีต้อนรับกลับค่ะ!</b>\n\n"
        f"คุณได้รับส่วนลด <b>{discount_pct}%</b> สำหรับแพ็กเกจ VIP 30 วัน\n\n"
        f"💰 ราคาพิเศษ: <b>฿{discounted_price}</b> (จาก ฿300)\n"
        f"⏰ ใช้ได้อีก 48 ชม. เท่านั้น\n\n"
        f"📌 <b>วิธีชำระเงิน:</b>\n"
        f"1️⃣ สแกน QR PromptPay ด้านล่าง หรือโอนเงิน <b>฿{discounted_price}</b>\n"
        f"2️⃣ ส่งสลิปโอนเงิน หรือ ลิงก์ซอง TrueMoney\n"
        f"3️⃣ รอแอดมินตรวจสอบ\n\n"
        f"💳 <b>ช่องทางชำระ:</b>\n"
        f"• PromptPay / โอนธนาคาร → ส่งรูปสลิป\n"
        f"• TrueMoney Wallet → ส่งลิงก์ gift.truemoney.com\n\n"
        f"⚠️ <b>หมายเหตุ:</b> กรุณาโอน <b>฿{discounted_price}</b> บาทเท่านั้นค่ะ"
    )

    keyboard = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📦 ดูแพ็กเกจอื่น", callback_data="view_packages")],
            [InlineKeyboardButton("🔙 กลับเมนูหลัก", callback_data="back_main")],
        ]
    )

    await update.message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)

    # Send QR code
    QR_URL = "https://img2.pic.in.th/-2026-03-15-143743.png"
    try:
        await context.bot.send_photo(
            chat_id=update.message.chat_id,
            photo=QR_URL,
            caption=f"📱 สแกน QR PromptPay เพื่อโอน <b>฿{discounted_price}</b>\nแล้วส่งสลิปมาที่แชทนี้เลยค่ะ 🙏",
            parse_mode="HTML",
        )
    except Exception as exc:
        logger.warning("Failed to send QR for comeback: %s", exc)

    return True


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/start command — register user and show main menu."""
    if not update.effective_user or not update.message:
        return

    tg_user = update.effective_user
    source = _extract_source(context.args or [])

    async with get_session() as session:
        # Upsert user
        result = await session.execute(
            select(User).where(User.telegram_id == tg_user.id)
        )
        user = result.scalar_one_or_none()

        if user is None:
            user = User(
                telegram_id=tg_user.id,
                username=tg_user.username,
                first_name=tg_user.first_name,
                last_name=tg_user.last_name,
            )
            session.add(user)
            await session.flush()
            logger.info(
                "New user registered: %s (tg:%d) source=%s",
                tg_user.username,
                tg_user.id,
                source,
            )
        else:
            # Update profile info
            user.username = tg_user.username
            user.first_name = tg_user.first_name
            user.last_name = tg_user.last_name

        # Create/update lead
        lead_result = await session.execute(
            select(Lead).where(Lead.telegram_id == tg_user.id)
        )
        lead = lead_result.scalar_one_or_none()

        if lead is None:
            lead = Lead(
                user_id=user.id,
                telegram_id=tg_user.id,
                username=tg_user.username,
                source=source,
                status=LeadStatus.NEW,
            )
            session.add(lead)
        elif source and not lead.source:
            lead.source = source

        # Record teaser click if source is a tracking link
        if source and source.startswith("t_"):
            parts = source.split("_")  # t_2300_g5
            if len(parts) == 3:
                try:
                    round_time = parts[1]
                    group_index = int(parts[2].replace("g", ""))
                    from shared.models import TeaserClick
                    click = TeaserClick(
                        user_id=tg_user.id,
                        round_time=round_time,
                        group_index=group_index,
                    )
                    session.add(click)
                    logger.info(
                        "TeaserClick recorded: user=%d round=%s group=%d",
                        tg_user.id, round_time, group_index,
                    )
                except (ValueError, IndexError) as exc:
                    logger.warning("Failed to parse teaser source '%s': %s", source, exc)

    # Handle comeback deep link: /start comeback_{code}
    if source and source.startswith("comeback_"):
        promo_code = source.replace("comeback_", "", 1)
        handled = await _handle_comeback_start(update, context, promo_code)
        if handled:
            return

    # Handle trial deep link: /start trial
    if source == "trial":
        from bots.sales_bot.handlers.trial import trial_command
        await trial_command(update, context)
        return

    # Handle packages deep link: /start packages
    if source == "packages":
        from bots.sales_bot.handlers.packages import view_packages_command
        await view_packages_command(update, context)
        return

    # Build dynamic keyboard — show trial button if eligible
    keyboard_rows = [
        [InlineKeyboardButton("⚡ Flash Sale", callback_data="view_flashsale")],
    ]

    # เช็คสิทธิ์ trial
    try:
        from bots.sales_bot.handlers.trial import _check_trial_eligible
        trial_eligible = await _check_trial_eligible(tg_user.id)
        if trial_eligible:
            keyboard_rows.append(
                [InlineKeyboardButton("🆕 ทดลอง VIP 24 ชม. ฿99", callback_data="view_trial")]
            )
    except Exception:
        pass  # ถ้าเช็คไม่ได้ ไม่แสดงปุ่ม

    keyboard_rows.extend([
        [InlineKeyboardButton("📦 ดูแพ็กเกจ", callback_data="view_packages")],
        [InlineKeyboardButton("🆓 ห้องฟรี", url="https://t.me/addlist/2xN-ag15W4U2MTNl")],
        [InlineKeyboardButton("👩‍💼 ติดต่อแอดมิน", url="https://t.me/zeinju_bunker")],
    ])

    dynamic_keyboard = InlineKeyboardMarkup(keyboard_rows)

    await update.message.reply_text(
        WELCOME_TEXT,
        parse_mode="HTML",
        reply_markup=dynamic_keyboard,
    )


async def back_to_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Callback: return to the main menu."""
    query = update.callback_query
    if not query:
        return
    await query.answer()
    await query.edit_message_text(
        WELCOME_TEXT,
        parse_mode="HTML",
        reply_markup=MAIN_KEYBOARD,
    )


async def free_room_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Callback: show free room info."""
    query = update.callback_query
    if not query:
        return
    await query.answer()

    text = (
        "🆓 <b>ห้องฟรี</b>\n\n"
        "เรามีห้องทดลองให้ดูก่อนตัดสินใจค่ะ\n"
        "สามารถเข้าไปดูบรรยากาศและคุณภาพสัญญาณได้เลย\n\n"
        "📌 กดปุ่มด้านล่างเพื่อขอลิงก์เข้าห้องฟรีค่ะ\n\n"
        "หากสนใจอัปเกรดเป็น VIP ทักแพรได้เลยนะคะ 😊"
    )
    keyboard = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📦 ดูแพ็กเกจ VIP", callback_data="view_packages")],
            [InlineKeyboardButton("🔙 กลับเมนูหลัก", callback_data="back_main")],
        ]
    )
    await query.edit_message_text(text, parse_mode="HTML", reply_markup=keyboard)


async def contact_admin_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Callback: show admin contact info."""
    query = update.callback_query
    if not query:
        return
    await query.answer()

    text = (
        "👩‍💼 <b>ติดต่อแอดมิน</b>\n\n"
        "หากมีปัญหาหรือข้อสงสัยที่แพรช่วยไม่ได้\n"
        "สามารถติดต่อแอดมินได้โดยตรงค่ะ\n\n"
        "📩 พิมพ์ข้อความที่ต้องการส่งถึงแอดมิน\n"
        "แพรจะรีบส่งต่อให้นะคะ 😊"
    )
    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("🔙 กลับเมนูหลัก", callback_data="back_main")]]
    )
    await query.edit_message_text(text, parse_mode="HTML", reply_markup=keyboard)


def get_start_handlers() -> list:
    """Return all handlers for the start module."""
    return [
        CommandHandler("start", start_command),
        CallbackQueryHandler(back_to_main_menu, pattern="^back_main$"),
        CallbackQueryHandler(free_room_callback, pattern="^free_room$"),
        CallbackQueryHandler(contact_admin_callback, pattern="^contact_admin$"),
    ]
