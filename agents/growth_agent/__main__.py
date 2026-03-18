import asyncio
import logging
from datetime import datetime
from shared.database import init_db
from shared.utils import TH_TZ
from agents.growth_agent.analyzer import run_weekly_analysis
from agents.growth_agent.ad_tracker import check_all_active_campaigns

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] [GROWTH] %(message)s")

async def main():
    await init_db()
    logging.info("Growth agent started")
    while True:
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

asyncio.run(main())
