"""Unified daily report endpoint — single source of truth.
Returns same data shape used by:
- Discord daily_report_task (08:00 BKK)
- Shell daily_sales_summary.sh (23:59 BKK)
- Dashboard on-demand view (any time)
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from ..auth.dependencies import require_role
from ..database import pool

router = APIRouter(prefix='/daily-report', tags=['daily-report'])


@router.get('/today')
async def daily_report_today(admin=Depends(require_role('admin'))):
    """All-in-one daily report for today (BKK timezone)."""
    BKK = "((NOW() AT TIME ZONE 'UTC') AT TIME ZONE 'Asia/Bangkok')::date"
    BKK_CREATED = "((p.created_at AT TIME ZONE 'UTC') AT TIME ZONE 'Asia/Bangkok')::date"

    # Revenue + count today
    today = await pool.fetchrow(f"""
        SELECT
            COUNT(*) FILTER (WHERE p.status='CONFIRMED' AND p.amount > 0 AND u.telegram_id < 9000000000) AS orders,
            COALESCE(SUM(p.amount) FILTER (WHERE p.status='CONFIRMED' AND p.amount > 0 AND u.telegram_id < 9000000000), 0)::float AS revenue,
            COUNT(*) FILTER (WHERE p.status='PENDING') AS pending,
            COUNT(*) FILTER (WHERE p.status='REJECTED') AS rejected
        FROM payments p
        LEFT JOIN users u ON u.id = p.user_id
        WHERE {BKK_CREATED} = {BKK}
    """)

    # Yesterday for comparison
    yest = await pool.fetchrow(f"""
        SELECT
            COUNT(*) FILTER (WHERE p.status='CONFIRMED' AND p.amount > 0 AND u.telegram_id < 9000000000) AS orders,
            COALESCE(SUM(p.amount) FILTER (WHERE p.status='CONFIRMED' AND p.amount > 0 AND u.telegram_id < 9000000000), 0)::float AS revenue
        FROM payments p
        LEFT JOIN users u ON u.id = p.user_id
        WHERE {BKK_CREATED} = {BKK} - INTERVAL '1 day'
    """)

    # Active subscriptions
    subs = await pool.fetchrow("""
        SELECT
            COUNT(*) FILTER (WHERE status='ACTIVE') AS active,
            COUNT(*) FILTER (WHERE status='ACTIVE' AND end_date <= NOW() + INTERVAL '7 days') AS expiring_7d,
            COUNT(*) FILTER (WHERE status='ACTIVE' AND end_date <= NOW() + INTERVAL '1 day') AS expiring_24h
        FROM subscriptions
    """)

    # Top packages today
    top_pkgs = await pool.fetch(f"""
        SELECT pk.name, COUNT(*) AS sold, SUM(p.amount)::float AS revenue
        FROM payments p
        JOIN packages pk ON pk.id = p.package_id
        LEFT JOIN users u ON u.id = p.user_id
        WHERE p.status='CONFIRMED' AND p.amount > 0
          AND u.telegram_id < 9000000000
          AND {BKK_CREATED} = {BKK}
        GROUP BY pk.name
        ORDER BY revenue DESC
        LIMIT 5
    """)

    # New users today
    new_users = await pool.fetchval("""
        SELECT COUNT(*) FROM users
        WHERE telegram_id < 9000000000
          AND ((created_at AT TIME ZONE 'UTC') AT TIME ZONE 'Asia/Bangkok')::date
              = ((NOW() AT TIME ZONE 'UTC') AT TIME ZONE 'Asia/Bangkok')::date
    """)

    # SOS open
    sos_open = await pool.fetchval("""
        SELECT COUNT(*) FROM sos_alerts WHERE status IN ('OPEN','IN_PROGRESS')
    """)

    diff_revenue = today['revenue'] - (yest['revenue'] or 0)
    diff_pct = (diff_revenue / yest['revenue'] * 100) if (yest['revenue'] or 0) > 0 else 0

    return {
        'date_bkk': str((await pool.fetchval(f'SELECT {BKK}'))),
        'today': {
            'orders': today['orders'],
            'revenue': today['revenue'],
            'pending': today['pending'],
            'rejected': today['rejected'],
        },
        'yesterday': {
            'orders': yest['orders'] or 0,
            'revenue': yest['revenue'] or 0,
        },
        'diff_revenue': diff_revenue,
        'diff_pct': diff_pct,
        'subscriptions': dict(subs),
        'top_packages': [dict(r) for r in top_pkgs],
        'new_users': int(new_users or 0),
        'sos_open': int(sos_open or 0),
    }
