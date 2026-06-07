"""Single source of truth for Asia/Bangkok timezone handling.

Replaces 39 duplicate `TH_TZ = timezone(timedelta(hours=7))` definitions
across the codebase. All time/date logic should go through this module.

Usage:
    from shared.tz import TH_TZ, now_th, today_bkk, utc_to_bkk, bkk_date_sql

    # naive UTC → Bangkok-aware
    dt_bkk = utc_to_bkk(datetime.utcnow())

    # current Bangkok time
    now = now_th()

    # today's date in BKK (for "this is today's sale" checks)
    if some_date == today_bkk(): ...

    # SQL helper for converting a UTC-stored timestamp column to BKK date
    # WHERE bkk_date_sql("p.created_at") = bkk_date_sql("NOW()")
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

# Bangkok = UTC+7, no DST
TH_TZ = timezone(timedelta(hours=7))


def now_th() -> datetime:
    """Current time in Bangkok timezone (tz-aware)."""
    return datetime.now(TH_TZ)


def today_bkk() -> date:
    """Today's calendar date in Bangkok."""
    return now_th().date()


def utc_to_bkk(dt: datetime) -> datetime:
    """Convert a naive UTC datetime (as stored in DB) to a tz-aware BKK datetime.

    If dt is already tz-aware, just astimezone.
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(TH_TZ)


def bkk_date_sql(col_expr: str) -> str:
    """Render a SQL fragment that converts a UTC-stored timestamp column
    to Bangkok local date. Use inside SQL queries.

    Example:
        f"WHERE {bkk_date_sql('p.created_at')} = {bkk_date_sql('NOW()')}"

    NOTE: Postgres-specific. For `NOW()` (which is timestamptz),
    the inner `AT TIME ZONE 'UTC'` is a no-op but safe.
    """
    return f"(({col_expr}) AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Bangkok')::date"


def bkk_timestamp_sql(col_expr: str) -> str:
    """Like bkk_date_sql but returns timestamp not date."""
    return f"(({col_expr}) AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Bangkok')"


__all__ = [
    "TH_TZ",
    "now_th",
    "today_bkk",
    "utc_to_bkk",
    "bkk_date_sql",
    "bkk_timestamp_sql",
]
