import asyncio
from agents.content_agent.scheduler import run_scheduler_loop
asyncio.run(run_scheduler_loop(bot=None))
