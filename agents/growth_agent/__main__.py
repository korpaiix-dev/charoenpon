import asyncio
import logging
from datetime import datetime
from shared.database import init_db
from shared.utils import TH_TZ
from agents.growth_agent.analyzer import run_weekly_analysis
from agents.growth_agent.ad_tracker import check_all_active_campaigns

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] [GROWTH] %(message)s")
logger = logging.getLogger(__name__)

async def main():
    await init_db()
    logger.info("Growth agent started")
    while True:
        try:
            now = datetime.now(TH_TZ)
            # Weekly analysis: Monday 08:00
            if now.weekday() == 0 and now.hour == 8 and now.minute < 5:
                await run_weekly_analysis()
                await asyncio.sleep(300)
            # Check ad campaigns every 6 hours
            if now.hour % 6 == 0 and now.minute < 5:
                await check_all_active_campaigns()
                await asyncio.sleep(300)
            await asyncio.sleep(60)
        except Exception as exc:
            logger.error("Growth agent loop error: %s", exc)
            await asyncio.sleep(30)

asyncio.run(main())
