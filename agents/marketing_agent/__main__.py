import asyncio
import logging
from shared.database import init_db
logging.basicConfig(level=logging.INFO, format="[%(asctime)s] [MARKETING] %(message)s")
async def main():
    await init_db()
    logging.info("Marketing agent ready — awaiting ad approval requests")
    while True:
        await asyncio.sleep(60)
asyncio.run(main())
