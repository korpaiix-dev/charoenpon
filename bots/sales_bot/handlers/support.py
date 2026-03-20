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

    # SOS detection — เข้ากลุ่มไม่ได้ / กลุ่มหาย / กลุ่มบิน
    SOS_KEYWORDS = ["SOS", "sos", "Sos", "เอสโอเอส", "เข้าไม่ได้", "กลุ่มบิน", "กลุ่มหาย", "กดไม่ได้", "ลิงก์หมด", "ลิ้งค์หมด", "ลิ้งหมด", "เข้ากลุ่มไม่ได้", "กลุ่มไม่ขึ้น", "ลิงค์เข้าไม่ได้", "ลิ้งค์เข้าไม่ได้", "เข้ากลุ่ม", "ขอลิ้ง", "ขอลิงก์", "ขอลิงค์"]
    if any(w in user_text for w in SOS_KEYWORDS):
        await update.message.reply_text(
            "📩 รับทราบค่า แพรส่งเรื่องให้แอดมินดูแล้วนะ\n"
            "รอสักครู่นะคะ ถ้านานไป ทักแอดมินได้เลย → https://t.me/zeinju_bunker"
        )
        # Save SOS to database for dashboard
        try:
            import asyncpg, os as _os2
            db_url = _os2.environ.get("DATABASE_URL", "").replace("postgresql+asyncpg://", "postgresql://")
            if db_url:
                _conn = await asyncpg.connect(db_url)
                await _conn.execute(
                    "INSERT INTO sos_alerts (telegram_id, first_name, username, message) VALUES ($1, $2, $3, $4)",
                    user.id, user.first_name, user.username, user_text[:500]
                )
                await _conn.close()
        except Exception as db_exc:
            logger.error("SOS DB insert failed: %s", db_exc)

        # Send SOS to admin group
        try:
            import os, html as _html
            import telegram as tg
            from datetime import datetime, timezone, timedelta
            safe_name = _html.escape(str(user.first_name or "ลูกค้า"))
            now_th = datetime.now(timezone(timedelta(hours=7)))
            ADMIN_GROUP_ID = int(os.environ.get("ADMIN_GROUP_CHAT_ID", "-1003830920430"))
            admin_bot = tg.Bot(token=os.environ.get("ADMIN_BOT_TOKEN", ""))
            
            sos_msg = (
                f"🆘 <b>SOS แจ้งปัญหาการเข้ากลุ่ม</b>\n\n"
                f"👤 <b>ลูกค้า:</b> <a href='tg://user?id={user.id}'>{safe_name}</a> (ID: <code>{user.id}</code>)\n"
                f"🕒 <b>เวลา:</b> {now_th.strftime('%d/%m/%Y %H:%M')}\n"
                f"💬 <b>ข้อความ:</b> {_html.escape(user_text[:200])}"
            )
            keyboard = tg.InlineKeyboardMarkup([
                [tg.InlineKeyboardButton("🔄 ส่งลิงก์ใหม่", callback_data=f"sos_resend_{user.id}")],
                [tg.InlineKeyboardButton(f"💬 แชท ID: {user.id}", callback_data=f"chat_{user.id}")],
            ])
            await admin_bot.send_message(
                chat_id=ADMIN_GROUP_ID,
                text=sos_msg,
                parse_mode="HTML",
                reply_markup=keyboard,
            )
        except Exception as exc:
            logger.error("SOS notification failed: %s", exc)
        return

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
            filters.TEXT & ~filters.COMMAND & ~filters.Regex(r"gift\.truemoney\.com") & filters.ChatType.PRIVATE,
            handle_support_text,
        ),
    ]
