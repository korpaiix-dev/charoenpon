"""Standalone runner — ทดสอบ marketing daily report.

Usage:
    cd /root/charoenpon
    python -m agents.marketing_analyzer
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

from agents.marketing_analyzer.daily_report import run_daily_marketing_report

logging.basicConfig(
    format="[%(asctime)s] [MARKETING] [%(levelname)s] %(message)s",
    level=logging.INFO,
    datefmt="%Y-%m-%d %H:%M:%S",
)


async def main() -> None:
    print("🚀 Running Marketing Daily Report...")
    await run_daily_marketing_report()
    print("✅ Done!")


if __name__ == "__main__":
    asyncio.run(main())
