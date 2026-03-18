import asyncio
import logging
from datetime import datetime
from shared.database import init_db
from shared.utils import TH_TZ
from agents.finance_agent.reports import run_daily_routine, run_weekly_routine
logging.basicConfig(level=logging.INFO, format="[%(asctime)s] [FINANCE] %(message)s")
async def main():
    await init_db()
    while True:
        now = datetime.now(TH_TZ)
        if now.hour == 23 and now.minute < 5:
            await run_daily_routine()
            await asyncio.sleep(300)
        if now.weekday() == 0 and now.hour == 7 and now.minute < 5:
            await run_weekly_routine()
            await asyncio.sleep(300)
        await asyncio.sleep(60)
asyncio.run(main())
