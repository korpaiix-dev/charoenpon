import asyncio
from shared.database import init_db
from agents.dev_agent.monitor import run_monitor_loop
async def main():
    await init_db()
    await run_monitor_loop()
asyncio.run(main())
