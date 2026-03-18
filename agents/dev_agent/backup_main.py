import asyncio
from shared.database import init_db
from agents.dev_agent.backup import run_backup_scheduler
async def main():
    await init_db()
    await run_backup_scheduler()
asyncio.run(main())
