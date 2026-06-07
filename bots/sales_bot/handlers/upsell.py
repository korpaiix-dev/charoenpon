"""Upsell handler — VIP → GOD MODE upgrade.

- /upgrade command: แสดงแพ็กเกจ GOD MODE
- Auto DM Upsell: ลูกค้า VIP 20+ วัน → DM แนะนำ GOD MODE (1 ครั้ง/คน)
- Schedule: ทุกวัน 15:00 ไทย, 10 คน/วัน
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from telegram import Update
from telegram.ext import (
    CommandHandler,
    ContextTypes,
)

from shared.database import get_session

logger = logging.getLogger(__name__)

from shared.tz import TH_TZ

UPGRADE_TEXT = (
    '😈 พร้อมอัพเกรดยัง?\n'
    '\n'
    'GOD MODE ≠ แค่ VIP+\n'
    'GOD MODE = คุณเลือกเอง\n'
    '\n'
    '✅ เข้าได้ทุกกลุ่ม VIP\n'
    '✅ สิทธิ์พิเศษ Exclusive\n'
    '✅ ไม่มีหมดอายุ (GOD MODE ถาวร)\n'
    '\n'
    'GOD MODE 90 วัน ฿1,299\n'
    'GOD MODE ถาวร ฿2,499\n'
    '\n'
    '━━━━━━━━━━━━━━━━━━\n'
    '📩 <b>อัพเกรดเลย 👇</b>\n'
    '👉 กดปุ่มด้านล่างเพื่อเลือกแพ็กเกจ GOD MODE\n'
    '━━━━━━━━━━━━━━━━━━'
)


async def upgrade_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/upgrade — แสดงแพ็กเกจ GOD MODE พร้อมปุ่มเลือก."""
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("💎 GOD MODE 90 วัน ฿1,299", callback_data="buy_1299")],
        [InlineKeyboardButton("👑 GOD MODE ถาวร ฿2,499", callback_data="buy_2499")],
    ])
    
    msg = update.message or (update.callback_query.message if update.callback_query else None)
    if update.callback_query:
        await update.callback_query.answer()
    
    if msg:
        await msg.reply_text(
            UPGRADE_TEXT,
            parse_mode="HTML",
            disable_web_page_preview=True,
            reply_markup=keyboard,
        )


async def run_upsell_dm_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Scheduled job: ส่ง DM GOD MODE upsell ให้ VIP ที่ใช้มา 20+ วัน.

    - จำกัด 10 คน/วัน
    - 1 ครั้ง/คน (เช็คจาก upsell_dm_log)
    """
    bot = context.bot
    logger.info("Running GOD MODE upsell DM job...")

    try:
        async with get_session() as session:
            # ดึง VIP active ที่ start_date >= 20 วันก่อน และยังไม่เคยได้รับ upsell DM
            cutoff = datetime.utcnow() - timedelta(days=20)
            result = await session.execute(
                text("""
                    SELECT u.telegram_id
                    FROM subscriptions s
                    JOIN users u ON s.user_id = u.id
                    WHERE s.status = 'ACTIVE'
                      AND s.start_date <= :cutoff
                      AND u.telegram_id NOT IN (
                          SELECT user_id FROM upsell_dm_log
                      )
                    ORDER BY s.start_date ASC
                    LIMIT 10
                """),
                {"cutoff": cutoff},
            )
            users = result.fetchall()

        if not users:
            logger.info("No eligible VIP users for GOD MODE upsell DM")
            return

        sent = 0
        for row in users:
            telegram_id = row[0]
            try:
                await bot.send_message(
                    chat_id=telegram_id,
                    text=UPGRADE_TEXT,
                    parse_mode="HTML",
                    disable_web_page_preview=True,
                )
                # Log ว่าส่งแล้ว
                async with get_session() as session:
                    await session.execute(
                        text("INSERT INTO upsell_dm_log (user_id, sent_at) VALUES (:uid, :now)"),
                        {"uid": telegram_id, "now": datetime.utcnow()},
                    )
                sent += 1
                await asyncio.sleep(1)
            except Exception as exc:
                logger.warning("Failed to send upsell DM to %s: %s", telegram_id, exc)

        logger.info("GOD MODE upsell DM sent: %d/%d", sent, len(users))

    except Exception as exc:
        logger.error("GOD MODE upsell DM job failed: %s", exc)


def get_upsell_handlers() -> list:
    """Return handlers for the upsell module."""
    return [
        CommandHandler("upgrade", upgrade_command),
    ]
