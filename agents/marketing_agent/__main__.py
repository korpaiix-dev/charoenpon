"""เจมส์ — Marketing Agent (เจริญพร)
หน้าที่:
- จัดการเพจ Facebook (auto-post, auto-reply, stats)
- Ad approval requests → Discord
- รายงาน performance

เจมส์ดูแล Facebook เอง ไม่ต้องพึ่งแพนด้า (CEO)
"""

import asyncio
import logging
from datetime import datetime, timezone, timedelta

from shared.database import init_db

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [MARKETING/เจมส์] %(message)s"
)
logger = logging.getLogger(__name__)

ICT = timezone(timedelta(hours=7))


async def main():
    await init_db()
    logger.info("เจมส์ Marketing Agent พร้อมทำงาน 🎯")
    logger.info("ดูแล: Facebook Page + Ad Approval + Performance Report")
    logger.info("FB Manager container: charoenpon-fb-manager (auto-post + auto-reply ทุก 1 นาที)")

    while True:
        try:
            # Log heartbeat ทุก 1 ชม.
            now = datetime.now(ICT)
            if now.minute == 0:
                logger.info(f"💓 Heartbeat — {now.strftime('%H:%M')} ICT")

            await asyncio.sleep(60)
        except Exception as exc:
            logger.error("Marketing agent loop error: %s", exc)
            await asyncio.sleep(30)


asyncio.run(main())
