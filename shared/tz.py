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


def th_day_start_utc(ref: datetime | None = None) -> datetime:
    """Start (00:00) of the Thai calendar day containing `ref`, as a NAIVE-UTC datetime.

    THE single source for "today 00:00 Thai" when comparing against naive-UTC DB
    timestamps (Payment.created_at etc). Every revenue/report/day-boundary MUST use
    this so the UTC-vs-Thai off-by-7h bug can never recur.

    `ref` may be None (=> now), a naive-UTC datetime, or a tz-aware datetime.
    Idempotent on values already equal to a TH-midnight boundary.
    """
    r = datetime.utcnow() if ref is None else ref
    if r.tzinfo is not None:
        r = r.astimezone(timezone.utc).replace(tzinfo=None)
    th = r + timedelta(hours=7)                       # naive-UTC -> Thai wall-clock
    th_midnight = th.replace(hour=0, minute=0, second=0, microsecond=0)
    return th_midnight - timedelta(hours=7)           # Thai wall-clock -> naive-UTC


def th_day_bounds(ref: datetime | None = None) -> tuple[datetime, datetime]:
    """(start, end) naive-UTC of the Thai calendar day containing `ref` (end = next 00:00)."""
    start = th_day_start_utc(ref)
    return start, start + timedelta(days=1)


def th_month_start_utc(ref: datetime | None = None) -> datetime:
    """Start (day 1, 00:00) of the Thai calendar month containing `ref`, as NAIVE-UTC."""
    r = datetime.utcnow() if ref is None else ref
    if r.tzinfo is not None:
        r = r.astimezone(timezone.utc).replace(tzinfo=None)
    th = (r + timedelta(hours=7)).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return th - timedelta(hours=7)


__all__ = [
    "TH_TZ",
    "now_th",
    "today_bkk",
    "utc_to_bkk",
    "bkk_date_sql",
    "bkk_timestamp_sql",
    "th_day_start_utc",
    "th_day_bounds",
    "th_month_start_utc",
]
