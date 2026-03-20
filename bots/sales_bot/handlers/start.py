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

    await update.message.reply_text(
        WELCOME_TEXT,
        parse_mode="HTML",
        reply_markup=MAIN_KEYBOARD,
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
