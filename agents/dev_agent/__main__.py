import asyncio, sys, logging
logging.basicConfig(level=logging.INFO, format="[%(asctime)s] [DEV] %(message)s")
from shared.database import init_db

async def main():
    await init_db()
    mode = sys.argv[1] if len(sys.argv) > 1 else "monitor"
    if mode == "monitor":
        from agents.dev_agent.monitor import run_monitor_loop
        await run_monitor_loop()
    elif mode == "backup":
        from agents.dev_agent.backup import run_backup_scheduler
        await run_backup_scheduler()

asyncio.run(main())
