"""Summer Fest DM — แจ้งลูกค้า GOD MODE 2499 เก่าว่ามีกลุ่มใหม่

เฉพาะลูกค้า TIER_2499 ที่ subscription ACTIVE
สร้าง 1 เม.ย. 2569 — run ครั้งเดียว
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone

from telegram import Bot
from telegram.error import Forbidden, BadRequest

logger = logging.getLogger(__name__)

TH_TZ = timezone(timedelta(hours=7))
SALES_BOT_TOKEN = os.environ.get("SALES_BOT_TOKEN", "")

DM_TEXT = (
    "🌊 <b>Summer Fest — กลุ่มใหม่ล่าสุด!</b>\n"
    "\n"
    "สวัสดีค่ะ ลูกค้า GOD MODE 💎\n"
    "\n"
    "เราเปิดกลุ่มใหม่ <b>Summer Fest</b> 🔥\n"
    "รวมงานพิเศษ 4 หมวดแรร์:\n"
    "• งานแรร์90\n"
    "• สาวอ้วน\n"
    "• เลสเบี้ยน\n"
    "• สาวน้อยตกน้ำ (สงกรานต์) 💦\n"
    "\n"
    "━━━━━━━━━━━━━━━━━━\n"
    "💰 <b>จ่ายเพิ่มเพียง ฿500</b> เข้ากลุ่ม Summer Fest ถาวร!\n"
    "(สำหรับลูกค้า GOD MODE เก่าเท่านั้น)\n"
    "━━━━━━━━━━━━━━━━━━\n"
    "\n"
    "📩 สนใจ พิมพ์ <b>/summer</b> หรือทักแชทนี้ได้เลยค่ะ 😊"
)


async def send_summer_fest_dm():
    """ส่ง DM แจ้งลูกค้า GOD MODE 2499 เก่า."""
    from shared.database import get_session
    from sqlalchemy import text

    bot = Bot(token=SALES_BOT_TOKEN)

    async with get_session() as session:
        result = await session.execute(text("""
            SELECT DISTINCT u.telegram_id, u.first_name
            FROM subscriptions s
            JOIN packages p ON s.package_id = p.id
            JOIN users u ON s.user_id = u.id
            WHERE p.tier = 'TIER_2499'
            AND s.status = 'ACTIVE'
            AND u.telegram_id IS NOT NULL
        """))
        users = result.fetchall()

    logger.info("Found %d GOD MODE 2499 users to DM", len(users))

    sent = 0
    failed = 0
    blocked = 0

    for tg_id, first_name in users:
        try:
            await bot.send_message(
                chat_id=tg_id,
                text=DM_TEXT,
                parse_mode="HTML",
            )
            sent += 1
            # Rate limit: 1 msg per second
            await asyncio.sleep(1)
        except Forbidden:
            blocked += 1
            logger.debug("User %s blocked the bot", tg_id)
        except BadRequest as e:
            failed += 1
            logger.warning("Failed to DM %s: %s", tg_id, e)
        except Exception as e:
            failed += 1
            logger.error("Unexpected error DM %s: %s", tg_id, e)

    summary = f"Summer Fest DM: sent={sent}, blocked={blocked}, failed={failed}, total={len(users)}"
    logger.info(summary)

    # แจ้งแอดมิน
    admin_group = int(os.environ.get("ADMIN_GROUP_CHAT_ID", "-1003830920430"))
    try:
        await bot.send_message(
            chat_id=admin_group,
            text=f"📊 <b>Summer Fest DM Report</b>\n\n{summary}",
            parse_mode="HTML",
        )
    except Exception:
        pass

    return {"sent": sent, "blocked": blocked, "failed": failed}


if __name__ == "__main__":
    asyncio.run(send_summer_fest_dm())
