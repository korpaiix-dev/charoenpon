"""System Monitor (บิ๊ก) - ตรวจสุขภาพระบบทุก 5 นาที.

Pure Python, ไม่ใช้ AI
ตรวจ: Bot alive? DB OK? CPU<80% RAM<80% Disk<85%? Response<3s?
ผิดปกติ → Discord #alerts
"""

from __future__ import annotations

import asyncio
import logging
import os
import platform
import time
from datetime import datetime, timezone
from typing import Any

import httpx
from sqlalchemy import text

from shared.database import engine

logger = logging.getLogger(__name__)

DISCORD_WEBHOOK_ALERTS: str = os.environ.get("DISCORD_WEBHOOK_ALERTS", "")

CPU_THRESHOLD = 80.0
RAM_THRESHOLD = 80.0
DISK_THRESHOLD = 85.0
RESPONSE_TIME_THRESHOLD = 3.0

CHECK_INTERVAL_SECONDS = 300

BOT_HEALTH_ENDPOINTS: list[str] = [
    os.environ.get("BOT_HEALTH_URL", "http://localhost:8080/health"),
]


async def check_database() -> dict[str, Any]:
    """ตรวจสอบว่า Database เชื่อมต่อได้."""
    start = time.monotonic()
    try:
        async with engine.connect() as conn:
            result = await conn.execute(text("SELECT 1"))
            result.scalar()
        elapsed = time.monotonic() - start
        return {
            "status": "ok",
            "response_time": round(elapsed, 3),
            "slow": elapsed > RESPONSE_TIME_THRESHOLD,
        }
    except Exception as exc:
        elapsed = time.monotonic() - start
        return {
            "status": "error",
            "error": str(exc),
            "response_time": round(elapsed, 3),
            "slow": True,
        }


def check_cpu() -> dict[str, Any]:
    """ตรวจสอบ CPU usage."""
    try:
        import psutil
        cpu_pct = psutil.cpu_percent(interval=1)
        return {
            "status": "ok" if cpu_pct < CPU_THRESHOLD else "warning",
            "usage_percent": cpu_pct,
            "threshold": CPU_THRESHOLD,
            "exceeded": cpu_pct >= CPU_THRESHOLD,
        }
    except ImportError:
        return {
            "status": "unknown",
            "error": "psutil not installed",
            "usage_percent": 0,
            "exceeded": False,
        }


def check_ram() -> dict[str, Any]:
    """ตรวจสอบ RAM usage."""
    try:
        import psutil
        mem = psutil.virtual_memory()
        return {
            "status": "ok" if mem.percent < RAM_THRESHOLD else "warning",
            "usage_percent": mem.percent,
            "total_gb": round(mem.total / (1024**3), 1),
            "available_gb": round(mem.available / (1024**3), 1),
            "threshold": RAM_THRESHOLD,
            "exceeded": mem.percent >= RAM_THRESHOLD,
        }
    except ImportError:
        return {
            "status": "unknown",
            "error": "psutil not installed",
            "usage_percent": 0,
            "exceeded": False,
        }


def check_disk() -> dict[str, Any]:
    """ตรวจสอบ Disk usage."""
    try:
        import psutil
        disk = psutil.disk_usage("/")
        pct = disk.percent
        return {
            "status": "ok" if pct < DISK_THRESHOLD else "warning",
            "usage_percent": pct,
            "total_gb": round(disk.total / (1024**3), 1),
            "free_gb": round(disk.free / (1024**3), 1),
            "threshold": DISK_THRESHOLD,
            "exceeded": pct >= DISK_THRESHOLD,
        }
    except ImportError:
        return {
            "status": "unknown",
            "error": "psutil not installed",
            "usage_percent": 0,
            "exceeded": False,
        }


async def check_bot_health() -> dict[str, Any]:
    """ตรวจสอบว่า Bot ยังทำงานอยู่ผ่าน health endpoint."""
    results = []

    for url in BOT_HEALTH_ENDPOINTS:
        if not url:
            continue

        start = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(url)
                elapsed = time.monotonic() - start

                results.append({
                    "url": url,
                    "status": "ok" if resp.status_code == 200 else "error",
                    "status_code": resp.status_code,
                    "response_time": round(elapsed, 3),
                    "slow": elapsed > RESPONSE_TIME_THRESHOLD,
                })
        except Exception as exc:
            elapsed = time.monotonic() - start
            results.append({
                "url": url,
                "status": "error",
                "error": str(exc),
                "response_time": round(elapsed, 3),
                "slow": True,
            })

    all_ok = all(r["status"] == "ok" for r in results) if results else True

    return {
        "status": "ok" if all_ok else "error",
        "endpoints": results,
    }


async def run_health_check() -> dict[str, Any]:
    """รัน health check ทั้งหมด."""
    now = datetime.now(timezone.utc)

    db_result = await check_database()
    bot_result = await check_bot_health()
    cpu_result = check_cpu()
    ram_result = check_ram()
    disk_result = check_disk()

    issues = []

    if db_result["status"] != "ok":
        issues.append(f"Database: {db_result.get('error', 'connection failed')}")
    elif db_result.get("slow"):
        issues.append(f"Database slow: {db_result['response_time']}s")

    if bot_result["status"] != "ok":
        for ep in bot_result.get("endpoints", []):
            if ep["status"] != "ok":
                issues.append(f"Bot ({ep['url']}): {ep.get('error', 'not responding')}")

    if cpu_result.get("exceeded"):
        issues.append(f"CPU high: {cpu_result['usage_percent']}% (>{CPU_THRESHOLD}%)")

    if ram_result.get("exceeded"):
        issues.append(f"RAM high: {ram_result['usage_percent']}% (>{RAM_THRESHOLD}%)")

    if disk_result.get("exceeded"):
        issues.append(f"Disk high: {disk_result['usage_percent']}% (>{DISK_THRESHOLD}%)")

    for ep in bot_result.get("endpoints", []):
        if ep.get("slow") and ep["status"] == "ok":
            issues.append(f"Slow response ({ep['url']}): {ep['response_time']}s")

    overall = "healthy" if not issues else "unhealthy"

    result = {
        "status": overall,
        "timestamp": now.isoformat(),
        "issues": issues,
        "checks": {
            "database": db_result,
            "bot": bot_result,
            "cpu": cpu_result,
            "ram": ram_result,
            "disk": disk_result,
        },
        "platform": {
            "system": platform.system(),
            "python": platform.python_version(),
            "hostname": platform.node(),
        },
    }

    if issues:
        logger.warning("Health check UNHEALTHY: %s", issues)
        await send_alert(result)
    else:
        logger.info("Health check OK")

    return result


def format_health_report(result: dict[str, Any]) -> str:
    """Format health check result เป็น Discord message."""
    status_emoji = "✅" if result["status"] == "healthy" else "🚨"
    checks = result.get("checks", {})

    lines = [
        f"{status_emoji} **System Health Check** — {result.get('timestamp', '-')}",
        "",
    ]

    db = checks.get("database", {})
    db_emoji = "✅" if db.get("status") == "ok" else "❌"
    lines.append(f"{db_emoji} Database: {db.get('status', 'unknown')} ({db.get('response_time', '?')}s)")

    bot = checks.get("bot", {})
    bot_emoji = "✅" if bot.get("status") == "ok" else "❌"
    lines.append(f"{bot_emoji} Bot: {bot.get('status', 'unknown')}")

    cpu = checks.get("cpu", {})
    cpu_emoji = "✅" if not cpu.get("exceeded") else "⚠️"
    lines.append(f"{cpu_emoji} CPU: {cpu.get('usage_percent', '?')}%")

    ram = checks.get("ram", {})
    ram_emoji = "✅" if not ram.get("exceeded") else "⚠️"
    lines.append(f"{ram_emoji} RAM: {ram.get('usage_percent', '?')}% ({ram.get('available_gb', '?')}GB free)")

    disk = checks.get("disk", {})
    disk_emoji = "✅" if not disk.get("exceeded") else "⚠️"
    lines.append(f"{disk_emoji} Disk: {disk.get('usage_percent', '?')}% ({disk.get('free_gb', '?')}GB free)")

    issues = result.get("issues", [])
    if issues:
        lines.append("")
        lines.append("🚨 **Issues:**")
        for issue in issues:
            lines.append(f"  • {issue}")

    return "\n".join(lines)


async def send_alert(result: dict[str, Any]) -> bool:
    """ส่ง alert ไป Discord #alerts เมื่อพบปัญหา."""
    if not DISCORD_WEBHOOK_ALERTS:
        logger.warning("No Discord webhook configured for system alerts")
        return False

    content = format_health_report(result)

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                DISCORD_WEBHOOK_ALERTS,
                json={"content": content},
            )
            resp.raise_for_status()
        logger.info("System alert sent to Discord")
        return True
    except Exception as exc:
        logger.error("Failed to send system alert: %s", exc)
        return False


async def run_monitor_loop() -> None:
    """Main monitor loop - ตรวจทุก 5 นาที."""
    logger.info("System monitor started (interval=%ds)", CHECK_INTERVAL_SECONDS)

    while True:
        try:
            await run_health_check()
        except Exception as exc:
            logger.error("Monitor loop error: %s", exc, exc_info=True)

        await asyncio.sleep(CHECK_INTERVAL_SECONDS)
