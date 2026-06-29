"""Single source of truth สำหรับ marketing stats — 3 consumers ใช้เหมือนกัน.

Background:
    ก่อนหน้านี้ 3 ที่ (Discord notify, Dashboard ROI, Weekly MVP) คำนวณสถิติ marketer
    ต่างกัน เพราะ:
      - JOIN payments → users ผ่าน telegram_id ทำให้ cross-link dup
      - บางที่ filter test users (telegram_id >= 9_000_000_000) บางที่ไม่
      - คิด window ต่างกัน (calendar month vs rolling 30d vs 7d+14d)

    โมดูลนี้รวมทั้งหมดมาเป็น API เดียว: ``get_marketer_stats(marketer, ...)``
    ส่ง parameter ที่ต่างกันสำหรับแต่ละ consumer.

Invariants (สำคัญมาก):
    1. JOIN payments p ON p.user_id = j.user_id  (NOT telegram_id — กัน dup)
    2. DISTINCT p.id ใน COUNT/SUM  (กัน double-count ถ้าลูกค้ามี joins หลาย link)
    3. WHERE j.user_id IS NOT NULL  (ลูกค้าต้องเคย register ใน users table)
    4. exclude_test_users: WHERE u.telegram_id < 9_000_000_000  (default True)
    5. WHERE p.created_at >= j.joined_at  (payment ต้องหลัง join)
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import text as sql_text

from shared.database import get_session


# Test-user threshold — สอดคล้องกับ Panda Monitor + Excel reports
_TEST_USER_TG_FLOOR = 9_000_000_000


@dataclass
class MarketerStats:
    """Aggregate stats for a single marketer over a defined window.

    Attributes:
        marketer:      e.g. "Pai" | "Ivy" | "Wasu"
        joins:         distinct users (j.user_id) that joined via this marketer's links
        clicks:        clicks on short URLs (/r/{code}) for this marketer's links
        conversions:   distinct payments (p.id) attributed to this marketer
        revenue_thb:   SUM of attributed payment amounts (THB)
        window_desc:   Thai friendly window label (e.g. "30 วันที่ผ่านมา")
        platform:      filter applied (None = all platforms)
    """
    marketer: str
    joins: int
    clicks: int
    conversions: int
    revenue_thb: float
    window_desc: str
    platform: Optional[str] = None


def _format_window_desc(
    join_window: Optional[timedelta],
    payment_window: Optional[timedelta],
    period_start: Optional[datetime],
    period_end: Optional[datetime],
) -> str:
    """Render a Thai-friendly window description."""
    if period_start is not None:
        # Calendar month (Discord notify) — period_start = month_floor_bkk
        month_th = [
            "ม.ค.", "ก.พ.", "มี.ค.", "เม.ย.", "พ.ค.", "มิ.ย.",
            "ก.ค.", "ส.ค.", "ก.ย.", "ต.ค.", "พ.ย.", "ธ.ค.",
        ]
        bkk = period_start + timedelta(hours=7)
        m = month_th[bkk.month - 1]
        y = bkk.year + 543 if bkk.year < 2400 else bkk.year
        return f"เดือน {m} {y % 100:02d}"
    if join_window is not None:
        d = int(join_window.total_seconds() // 86400)
        if d == 7:
            return "7 วันที่ผ่านมา"
        if d == 30:
            return "30 วันที่ผ่านมา"
        return f"{d} วันที่ผ่านมา"
    return "ตั้งแต่เริ่ม (lifetime)"


async def get_marketer_stats(
    marketer: str,
    *,
    join_window: Optional[timedelta] = None,
    payment_window: Optional[timedelta] = None,
    period_start: Optional[datetime] = None,
    period_end: Optional[datetime] = None,
    exclude_test_users: bool = True,
    platform: Optional[str] = None,
) -> MarketerStats:
    """Compute aggregate stats for a single marketer.

    Args:
        marketer:           name in marketing_invite_links.marketer
        join_window:        if set, only count joins where joined_at >= now() - window
        payment_window:     if set, only count payments where
                            (p.created_at - j.joined_at) <= payment_window
                            (i.e. cap how long after join we attribute revenue)
        period_start:       absolute floor on p.created_at (e.g. calendar month start)
        period_end:         absolute ceiling on p.created_at
        exclude_test_users: filter out telegram_id >= 9_000_000_000 (default True)
        platform:           filter to a specific platform (e.g. "telegram")
    """
    # Build WHERE clauses
    join_filters = ["l.marketer = :marketer", "j.user_id IS NOT NULL"]
    pay_filters = [
        "p.status = 'CONFIRMED'",
        "p.amount > 0",
        "p.created_at >= j.joined_at",
    ]
    params: dict = {"marketer": marketer}

    if exclude_test_users:
        join_filters.append(f"u.telegram_id < {_TEST_USER_TG_FLOOR}")

    if platform is not None:
        join_filters.append("l.platform = :platform")
        params["platform"] = platform

    if join_window is not None:
        join_filters.append("j.joined_at >= :join_floor")
        params["join_floor"] = datetime.utcnow() - join_window

    if payment_window is not None:
        pay_filters.append("(p.created_at - j.joined_at) <= :pay_cap")
        params["pay_cap"] = payment_window

    if period_start is not None:
        # Naive comparison — payments.created_at is timestamp without tz (UTC)
        # period_start should be a UTC-equivalent datetime
        pay_filters.append("p.created_at >= :period_start")
        params["period_start"] = period_start

    if period_end is not None:
        pay_filters.append("p.created_at < :period_end")
        params["period_end"] = period_end

    join_where = " AND ".join(join_filters)
    pay_where = " AND ".join(pay_filters)

    # Joins: distinct users that joined via this marketer's links
    joins_sql = sql_text(f"""
        SELECT COUNT(DISTINCT j.user_id) AS joins
        FROM marketing_invite_joins j
        JOIN marketing_invite_links l ON l.id = j.link_id
        JOIN users u ON u.id = j.user_id
        WHERE {join_where}
    """)

    # Clicks: per marketer (clicks are not user-bound, so we just count link clicks)
    click_filters = ["l.marketer = :marketer"]
    if platform is not None:
        click_filters.append("l.platform = :platform")
    if join_window is not None:
        click_filters.append("c.clicked_at >= :join_floor")
    clicks_sql = sql_text(f"""
        SELECT COUNT(*) AS clicks
        FROM marketing_link_clicks c
        JOIN marketing_invite_links l ON l.id = c.link_id
        WHERE {' AND '.join(click_filters)}
    """)

    # Conversions + revenue: DISTINCT p.id to avoid cross-link dup
    # Use subquery so the DISTINCT happens before aggregation.
    conv_sql = sql_text(f"""
        SELECT COUNT(*) AS conversions,
               COALESCE(SUM(amount), 0)::float AS revenue
        FROM (
            SELECT DISTINCT p.id, p.amount
            FROM marketing_invite_joins j
            JOIN marketing_invite_links l ON l.id = j.link_id
            JOIN users u ON u.id = j.user_id
            JOIN payments p ON p.user_id = j.user_id
            WHERE {join_where}
              AND {pay_where}
        ) sub
    """)

    async with get_session() as s:
        joins_count = int((await s.execute(joins_sql, params)).scalar() or 0)
        clicks_count = int((await s.execute(clicks_sql, params)).scalar() or 0)
        row = (await s.execute(conv_sql, params)).first()
        conv = int(row.conversions or 0) if row else 0
        rev = float(row.revenue or 0) if row else 0.0

    return MarketerStats(
        marketer=marketer,
        joins=joins_count,
        clicks=clicks_count,
        conversions=conv,
        revenue_thb=rev,
        window_desc=_format_window_desc(
            join_window, payment_window, period_start, period_end
        ),
        platform=platform,
    )


# ---------------------------------------------------------------------------
# Convenience presets — one per consumer use-case
# ---------------------------------------------------------------------------

def _calendar_month_start_bkk_utc() -> datetime:
    """Return UTC datetime for start of current calendar month in Asia/Bangkok.

    BKK is UTC+7, so 1st of month 00:00 BKK = previous day 17:00 UTC.
    """
    now_utc = datetime.utcnow()
    now_bkk = now_utc + timedelta(hours=7)
    month_start_bkk = datetime(now_bkk.year, now_bkk.month, 1)
    return month_start_bkk - timedelta(hours=7)


async def stats_this_calendar_month_bkk(
    marketer: str, *, exclude_test_users: bool = True
) -> MarketerStats:
    """For Discord notify (STEP 21.5) — calendar month BKK so the leaderboard
    resets on the 1st of each month."""
    return await get_marketer_stats(
        marketer,
        period_start=_calendar_month_start_bkk_utc(),
        exclude_test_users=exclude_test_users,
    )


async def stats_rolling_30d(
    marketer: str, *, payment_window: Optional[timedelta] = timedelta(days=30),
    exclude_test_users: bool = True, platform: Optional[str] = None,
) -> MarketerStats:
    """For Dashboard ROI — rolling 30 days. Caller can override payment_window
    or pass None to disable the cap entirely."""
    return await get_marketer_stats(
        marketer,
        join_window=timedelta(days=30),
        payment_window=payment_window,
        exclude_test_users=exclude_test_users,
        platform=platform,
    )


async def stats_weekly_mvp(
    marketer: str, *, exclude_test_users: bool = True
) -> MarketerStats:
    """For Weekly MVP — joined within 7 days, paid within 14 days of join."""
    return await get_marketer_stats(
        marketer,
        join_window=timedelta(days=7),
        payment_window=timedelta(days=14),
        exclude_test_users=exclude_test_users,
    )


async def stats_lifetime(
    marketer: str, *, exclude_test_users: bool = True
) -> MarketerStats:
    """Lifetime totals (no window)."""
    return await get_marketer_stats(
        marketer, exclude_test_users=exclude_test_users
    )


async def list_active_marketers(exclude_test_users: bool = True) -> list[str]:
    """All marketer names that have at least one (non-revoked) link."""
    async with get_session() as s:
        rows = (await s.execute(sql_text(
            "SELECT DISTINCT marketer FROM marketing_invite_links "
            "WHERE is_revoked = false ORDER BY marketer"
        ))).fetchall()
    return [r[0] for r in rows]


__all__ = [
    "MarketerStats",
    "get_marketer_stats",
    "stats_this_calendar_month_bkk",
    "stats_rolling_30d",
    "stats_weekly_mvp",
    "stats_lifetime",
    "list_active_marketers",
]
