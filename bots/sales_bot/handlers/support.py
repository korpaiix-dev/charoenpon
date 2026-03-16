"""Support handler - Sales Bot แพร.

ตอบคำถามทั่วไป + ติดต่อแอดมิน
ใช้ AI แพร (gemini-flash-lite) ลงท้าย ค่ะ เสมอ
"""

from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import CommandHandler, ContextTypes, MessageHandler, filters

from shared.api_cost_tracker import call_openrouter

logger = logging.getLogger(__name__)

AI_MODEL = "google/gemini-2.0-flash-lite-001"

PRAE_SYSTEM_PROMPT = """คุณคือ "แพร" ผู้ช่วยฝ่ายขายของบริษัทเจริญพร VIP Telegram System
คุณเป็นผู้หญิง พูดไทย สุภาพ อบอุ่น กระตือรือร้น

กฎสำคัญที่ต้องปฏิบัติเสมอ:
1. ลงท้ายทุกประโยคด้วย "ค่ะ" เสมอ
2. ห้ามรับราคา custom หรือต่อรองราคา — แจ้งว่าราคาตามแพ็กเกจเท่านั้น
3. ห้ามพิมพ์ยอดเงินเอง — อ้างอิงจากแพ็กเกจเท่านั้น (300/500/1,299/2,499 บาท)
4. ห้ามรับปากคืนเงิน — แจ้งว่าติดต่อแอดมินโดยตรง
5. ถ้าไม่แน่ใจ ให้แนะนำติดต่อแอดมิน

แพ็กเกจที่มี:
- 300 บาท/เดือน: ห้อง G300
- 500 บาท/เดือน: ห้อง G300, G500
- 1,299 บาท/เดือน: ห้อง G300, G500, SSS, VGOD
- 2,499 บาท/เดือน: ทุกห้อง (G300, G500, SSS, VGOD, OF, INTER, SERIES)

วิธีชำระ: สลิปโอนเงิน หรือ ลิงก์ซอง TrueMoney
ตอบสั้นกระชับ ไม่เกิน 3-4 ประโยค"""


async def handle_support_text(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle general text messages using AI (แพร persona)."""
    if not update.message or not update.message.text:
        return

    user = update.effective_user
    if not user:
        return

    user_text = update.message.text.strip()

    # Build conversation history from context
    history = context.user_data.get("chat_history", [])
    history.append({"role": "user", "content": user_text})

    # Keep only last 10 messages to control context size
    if len(history) > 10:
        history = history[-10:]

    messages = [
        {"role": "system", "content": PRAE_SYSTEM_PROMPT},
        *history,
    ]

    try:
        response = await call_openrouter(
            model=AI_MODEL,
            messages=messages,
            caller="sales_bot_prae",
            temperature=0.7,
            max_tokens=512,
            metadata={"user_id": user.id, "username": user.username},
        )

        choices = response.get("choices", [])
        if choices:
            reply = choices[0].get("message", {}).get("content", "")
        else:
            reply = ""

        if not reply:
            reply = "ขออภัยค่ะ ระบบมีปัญหาชั่วคราว กรุณาลองใหม่อีกครั้งนะคะ"

        # Ensure reply ends with ค่ะ
        reply = _ensure_ka(reply)

        # Save to history
        history.append({"role": "assistant", "content": reply})
        context.user_data["chat_history"] = history

        await update.message.reply_text(reply)

    except Exception as exc:
        logger.error("AI response error: %s", exc)
        await update.message.reply_text(
            "ขออภัยค่ะ ระบบมีปัญหาชั่วคราว กรุณาลองใหม่อีกครั้ง "
            "หรือพิมพ์ /help เพื่อดูเมนูช่วยเหลือนะคะ"
        )


def _ensure_ka(text: str) -> str:
    """Ensure the response ends with ค่ะ."""
    text = text.rstrip()
    if not text:
        return "ค่ะ"
    # Check if already ends with ค่ะ or common endings
    if text.endswith("ค่ะ") or text.endswith("ค่ะ!") or text.endswith("ค่ะ 😊"):
        return text
    # Remove trailing punctuation then add ค่ะ
    if text[-1] in ".!。":
        text = text[:-1]
    return text + "ค่ะ"


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/help command — show help menu."""
    if not update.message:
        return

    text = (
        "📋 <b>คำสั่งที่ใช้ได้ค่ะ</b>\n\n"
        "/start - เริ่มต้นใช้งาน\n"
        "/packages - ดูแพ็กเกจทั้งหมด\n"
        "/help - แสดงเมนูช่วยเหลือ\n\n"
        "💬 <b>สามารถพิมพ์คำถามได้เลยค่ะ</b>\n"
        "แพรจะช่วยตอบให้นะคะ 😊\n\n"
        "📌 <b>วิธีสมัครแพ็กเกจ:</b>\n"
        "1. พิมพ์ /packages เลือกแพ็กเกจ\n"
        "2. กดสมัคร แล้วชำระเงิน\n"
        "3. ส่งสลิป หรือลิงก์ซอง TrueMoney\n"
        "4. รอระบบตรวจสอบอัตโนมัติค่ะ"
    )

    await update.message.reply_text(text, parse_mode="HTML")


def get_support_handlers() -> list:
    """Return all handlers for the support module."""
    return [
        CommandHandler("help", help_command),
        # Generic text handler — must be LAST in handler list
        MessageHandler(
            filters.TEXT & ~filters.COMMAND & ~filters.Regex(r"gift\.truemoney\.com"),
            handle_support_text,
        ),
    ]
