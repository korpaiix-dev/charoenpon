import asyncio
import logging
from shared.database import init_db

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] [MARKETING] %(message)s")
logger = logging.getLogger(__name__)

async def main():
    await init_db()
    logger.info("Marketing agent ready — awaiting ad approval requests")
    while True:
        try:
            await asyncio.sleep(60)
        except Exception as exc:
            logger.error("Marketing agent loop error: %s", exc)
            await asyncio.sleep(30)

asyncio.run(main())
