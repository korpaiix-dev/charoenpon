"""Manager Agent — entry point.

Schedule:
  - Daily Report:   ทุกวัน 09:00 TH (02:00 UTC)
  - Weekly Analysis: ทุกจันทร์ 08:00 TH (01:00 UTC)
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from shared.database import init_db
from agents.manager_agent.reporter import send_daily_report, send_weekly_analysis

TH_TZ = timezone(timedelta(hours=7))

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [MANAGER] %(message)s",
)
logger = logging.getLogger(__name__)


async def main() -> None:
    await init_db()
    logger.info("Manager agent started")

    while True:
        now = datetime.now(TH_TZ)

        # Daily report: 09:00 TH (02:00 UTC)
        if now.hour == 9 and now.minute < 5:
            try:
                await send_daily_report()
            except Exception:
                logger.exception("Daily report failed")
            await asyncio.sleep(300)

        # Weekly analysis: Monday 08:00 TH (01:00 UTC)
        if now.weekday() == 0 and now.hour == 8 and now.minute < 5:
            try:
                await send_weekly_analysis()
            except Exception:
                logger.exception("Weekly analysis failed")
            await asyncio.sleep(300)

        await asyncio.sleep(60)


asyncio.run(main())
