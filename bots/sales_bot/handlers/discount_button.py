"""Discount button + callback — shows up in main menu when user has discount balance.

- Button "💰 ส่วนลดของฉัน ฿X" injected into /start keyboard if balance > 0
- Callback `view_discount` shows usage info + "📦 ดูแพ็กเกจ" CTA
"""
from __future__ import annotations

import logging

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CallbackQueryHandler, ContextTypes

from shared.discount_helper import get_balance, DISCOUNT_CAP

logger = logging.getLogger(__name__)


def _build_caption(balance: float, total_earned: float, total_used: float) -> str:
    lines = [
        "💰 <b>ส่วนลดสะสมของคุณ</b>",
        "━━━━━━━━━━━━━━━",
        "",
        f"💵 <b>ยอดคงเหลือ: ฿{balance:,.0f}</b>",
        f"📥 รวมได้รับ: ฿{total_earned:,.0f}",
        f"📤 ใช้ไปแล้ว: ฿{total_used:,.0f}",
        "",
        "━━━━━━━━━━━━━━━",
        "🎯 <b>วิธีใช้ส่วนลด:</b>",
        "1️⃣ กดปุ่ม <b>📦 ดูแพ็กเกจ</b> ที่เมนูหลัก",
        "2️⃣ เลือกแพ็กเกจที่ต้องการ",
        "3️⃣ ระบบจะ <b>หักส่วนลดให้อัตโนมัติ</b> ✨",
        "",
        "💡 <b>เพดานส่วนลดต่อแพ็กเกจ:</b>",
        "• VIP 399 → ลดได้สูงสุด ฿50",
        "• VIP 999 / GOD 1,299 → ลดได้สูงสุด ฿100",
        "• GOD ถาวร 2,499 → ลดได้สูงสุด ฿200",
        "",
        "━━━━━━━━━━━━━━━",
        "🎰 <b>หาส่วนลดเพิ่ม:</b> หมุนกาชาปอง — ลุ้นได้ ฿50/หมุน",
    ]
    return "\n".join(lines)


def _build_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📦 ดูแพ็กเกจ (ใช้ส่วนลด)", callback_data="view_packages")],
        [InlineKeyboardButton("🎰 เปิดวงล้อกาชาปอง", callback_data="view_gacha_buy")],
        [InlineKeyboardButton("🔙 กลับเมนูหลัก", callback_data="back_main")],
    ])


async def cb_view_discount(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Callback: 'view_discount' from main menu."""
    q = update.callback_query
    await q.answer()
    if not q.from_user:
        return

    # Fetch full row
    from sqlalchemy import text as _t
    from shared.database import get_session
    async with get_session() as s:
        r = await s.execute(
            _t("SELECT balance, total_earned, total_used FROM user_discount_credits WHERE telegram_id = :tg"),
            {"tg": q.from_user.id},
        )
        row = r.fetchone()
    bal = float(row[0]) if row else 0
    earned = float(row[1]) if row else 0
    used = float(row[2]) if row else 0

    if bal <= 0 and earned <= 0:
        msg = (
            "💰 <b>ส่วนลดสะสมของคุณ</b>\n"
            "━━━━━━━━━━━━━━━\n\n"
            "❌ คุณยังไม่มีส่วนลดสะสม\n\n"
            "💡 <b>หมุนกาชาปอง</b> เพื่อลุ้นรางวัล\n"
            "   — มีโอกาสได้ส่วนลด ฿50 ทุกครั้งที่หมุน!"
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🎁 ซื้อสิทธิ์หมุนกาชาปอง", callback_data="view_gacha_buy")],
            [InlineKeyboardButton("🔙 กลับเมนูหลัก", callback_data="back_main")],
        ])
    else:
        msg = _build_caption(bal, earned, used)
        kb = _build_keyboard()

    try:
        await q.edit_message_text(msg, parse_mode="HTML", reply_markup=kb)
    except Exception:
        await q.message.reply_text(msg, parse_mode="HTML", reply_markup=kb)


def get_discount_button_handlers() -> list:
    return [
        CallbackQueryHandler(cb_view_discount, pattern=r"^view_discount$"),
    ]


async def get_balance_for_user(telegram_id: int) -> float:
    """Public helper for start.py to query balance for button display."""
    try:
        bal = await get_balance(telegram_id)
        return float(bal)
    except Exception as e:
        logger.warning("discount balance fetch failed: %s", e)
        return 0.0


__all__ = [
    "cb_view_discount", "get_discount_button_handlers", "get_balance_for_user",
]
