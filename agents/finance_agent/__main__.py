import asyncio
import logging
from datetime import datetime, date
from shared.database import init_db
from shared.utils import TH_TZ
from agents.finance_agent.reports import run_daily_routine, run_weekly_routine

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] [FINANCE] %(message)s")
logger = logging.getLogger(__name__)


# Idempotency: track last run date (in-memory + check)
_last_daily_run: date | None = None
_last_weekly_run: date | None = None


async def main():
    await init_db()
    global _last_daily_run, _last_weekly_run
    logger.info("Finance scheduler started")
    while True:
        try:
            now = datetime.now(TH_TZ)
            today = now.date()

            # Daily routine: 23:00 BKK (window 23:00–23:04 — runs only once per day)
            if (
                now.hour == 23
                and now.minute < 5
                and _last_daily_run != today
            ):
                _last_daily_run = today
                logger.info("Running daily routine for %s", today)
                try:
                    await run_daily_routine()
                    logger.info("Daily routine completed for %s", today)
                except Exception:
                    logger.exception("daily routine failed")
                await asyncio.sleep(300)  # skip recheck for 5 min
                continue

            # Weekly routine: Monday 07:00 BKK
            if (
                now.weekday() == 0
                and now.hour == 7
                and now.minute < 5
                and _last_weekly_run != today
            ):
                _last_weekly_run = today
                logger.info("Running weekly routine for %s", today)
                try:
                    await run_weekly_routine()
                    logger.info("Weekly routine completed for %s", today)
                except Exception:
                    logger.exception("weekly routine failed")
                await asyncio.sleep(300)
                continue

            await asyncio.sleep(60)
        except Exception:
            logger.exception("main loop error (continuing)")
            await asyncio.sleep(60)


asyncio.run(main())
