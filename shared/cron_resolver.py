"""Cron time resolver — DB-first with hardcoded fallback.

Goal: let admin set bot cron times via Dashboard `bot_schedules` table.
If a job key has a DB row, use it; else use the hardcoded default.

Usage in sales_bot/main.py:
    from shared.cron_resolver import resolve_cron_time
    # old: time(hour=11, minute=0, tzinfo=TH_TZ)
    # new: await resolve_cron_time("exit_survey_daily_1100", default=(11, 0))
"""
from __future__ import annotations

import logging
import os
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


def _conn_str() -> str:
    return os.environ.get("DATABASE_URL", "").replace("postgresql+asyncpg://", "postgresql://")


async def _connect():
    import asyncpg
    return await asyncpg.connect(_conn_str())


async def resolve_cron_time(
    job_key: str,
    default_hour: int = 0,
    default_minute: int = 0,
) -> Tuple[int, int, bool]:
    """Resolve cron hour:minute for a job key.

    Returns (hour, minute, enabled). If DB has no row or query fails,
    returns the provided defaults with enabled=True.
    """
    try:
        conn = await _connect()
        try:
            row = await conn.fetchrow(
                """
                SELECT hour, minute, COALESCE(is_enabled, TRUE) AS enabled
                FROM bot_schedules
                WHERE job_key = $1
                LIMIT 1
                """,
                str(job_key),
            )
        finally:
            await conn.close()
        if row:
            return int(row["hour"]), int(row["minute"]), bool(row["enabled"])
    except Exception as exc:
        logger.warning("cron resolve_cron_time(%s) failed: %s — using defaults", job_key, exc)
    return default_hour, default_minute, True


def resolve_cron_time_sync(
    job_key: str,
    default_hour: int = 0,
    default_minute: int = 0,
) -> Tuple[int, int, bool]:
    """Sync version using psycopg2 — for use at bot startup before event loop."""
    try:
        import psycopg2
        from urllib.parse import urlparse
        dsn = _conn_str()
        if dsn.startswith("postgresql://"):
            p = urlparse(dsn)
            conn = psycopg2.connect(
                host=p.hostname, port=p.port or 5432,
                user=p.username, password=p.password,
                dbname=p.path.lstrip("/"),
            )
            try:
                with conn.cursor() as c:
                    c.execute(
                        "SELECT hour, minute, COALESCE(is_enabled, TRUE) FROM bot_schedules WHERE job_key = %s LIMIT 1",
                        (str(job_key),),
                    )
                    row = c.fetchone()
                    if row:
                        return int(row[0]), int(row[1]), bool(row[2])
            finally:
                conn.close()
    except Exception as exc:
        logger.warning("cron resolve_cron_time_sync(%s) failed: %s", job_key, exc)
    return default_hour, default_minute, True
