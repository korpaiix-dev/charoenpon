"""Dashboard home — summary, charts, stats, alerts."""
from fastapi import APIRouter, Depends, Request
from ..auth.dependencies import get_current_admin, require_role
from ..database import pool
import json

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])

@router.get("/summary")
async def summary(request: Request, admin=Depends(get_current_admin)):
    """Revenue summary: today, week, month with comparison."""
    row = await pool.fetchrow("""
        SELECT
            COALESCE(SUM(CASE WHEN p.created_at::date = CURRENT_DATE THEN p.amount END), 0) as today,
            COALESCE(SUM(CASE WHEN p.created_at::date = CURRENT_DATE - 1 THEN p.amount END), 0) as yesterday,
            COALESCE(SUM(CASE WHEN p.created_at >= date_trunc('week', CURRENT_DATE) THEN p.amount END), 0) as week,
            COALESCE(SUM(CASE WHEN p.created_at >= date_trunc('week', CURRENT_DATE) - interval '7 days'
                              AND p.created_at < date_trunc('week', CURRENT_DATE) THEN p.amount END), 0) as last_week,
            COALESCE(SUM(CASE WHEN p.created_at >= date_trunc('month', CURRENT_DATE) THEN p.amount END), 0) as month,
            COALESCE(SUM(CASE WHEN p.created_at >= date_trunc('month', CURRENT_DATE) - interval '1 month'
                              AND p.created_at < date_trunc('month', CURRENT_DATE) THEN p.amount END), 0) as last_month
        FROM payments p WHERE p.status = 'CONFIRMED'
    """)
    def pct(curr, prev):
        if prev == 0: return 0
        return round(((curr - prev) / prev) * 100, 1)
    
    return {
        "today": float(row["today"]),
        "today_change": pct(row["today"], row["yesterday"]),
        "week": float(row["week"]),
        "week_change": pct(row["week"], row["last_week"]),
        "month": float(row["month"]),
        "month_change": pct(row["month"], row["last_month"]),
    }

@router.get("/revenue-chart")
async def revenue_chart(days: int = 30, admin=Depends(get_current_admin)):
    rows = await pool.fetch("""
        SELECT p.created_at::date as date, COALESCE(SUM(p.amount), 0) as revenue
        FROM payments p
        WHERE p.status = 'CONFIRMED' AND p.created_at >= CURRENT_DATE - $1 * interval '1 day'
        GROUP BY p.created_at::date ORDER BY date
    """, days)
    return [{"date": str(r["date"]), "revenue": float(r["revenue"])} for r in rows]

@router.get("/members-stats")
async def members_stats(admin=Depends(get_current_admin)):
    row = await pool.fetchrow("""
        SELECT
            (SELECT COUNT(*) FROM subscriptions WHERE status = 'ACTIVE') as active,
            (SELECT COUNT(*) FROM subscriptions WHERE status = 'EXPIRED') as expired,
            (SELECT COUNT(*) FROM users WHERE created_at::date = CURRENT_DATE) as new_today,
            (SELECT COUNT(*) FROM users) as total_users
    """)
    return dict(row)

@router.get("/flash-sale-status")
async def flash_sale_status(admin=Depends(get_current_admin)):
    row = await pool.fetchrow("""
        SELECT * FROM flash_sales 
        WHERE is_active = TRUE AND ends_at > NOW()
        ORDER BY ends_at ASC LIMIT 1
    """)
    if not row:
        return {"active": False}
    return {
        "active": True,
        "name": row["name"],
        "sold_slots": row["sold_slots"],
        "total_slots": row["total_slots"],
        "ends_at": str(row["ends_at"]),
        "flash_price": float(row["flash_price"]),
    }

@router.get("/dm-stats")
async def dm_stats(admin=Depends(require_role("admin"))):
    row = await pool.fetchrow("""
        SELECT
            (SELECT COUNT(*) FROM comeback_dm_log WHERE sent_at::date = CURRENT_DATE) as comeback_sent,
            (SELECT COUNT(*) FROM comeback_dm_log WHERE sent_at::date = CURRENT_DATE AND responded = TRUE) as comeback_respond,
            (SELECT COUNT(*) FROM comeback_dm_log WHERE sent_at::date = CURRENT_DATE AND purchased = TRUE) as comeback_convert,
            (SELECT COUNT(*) FROM trial_dm_log WHERE sent_at::date = CURRENT_DATE) as trial_sent,
            (SELECT COUNT(*) FROM trial_dm_log WHERE sent_at::date = CURRENT_DATE AND clicked = TRUE) as trial_click,
            (SELECT COUNT(*) FROM trial_dm_log WHERE sent_at::date = CURRENT_DATE AND purchased = TRUE) as trial_convert
    """)
    return dict(row)

@router.get("/content-stats")
async def content_stats(admin=Depends(require_role("admin"))):
    row = await pool.fetchrow("""
        SELECT
            (SELECT COUNT(*) FROM teaser_clicks WHERE created_at::date = CURRENT_DATE) as teaser_clicks_today,
            (SELECT COUNT(*) FROM content_queue WHERE is_used = FALSE) as queue_remaining,
            (SELECT COUNT(*) FROM content_schedule WHERE is_sent = TRUE AND sent_at::date = CURRENT_DATE) as teasers_sent_today
    """)
    return dict(row)

@router.get("/alerts")
async def alerts(admin=Depends(get_current_admin)):
    pending_slips = await pool.fetchval("SELECT COUNT(*) FROM payments WHERE status = 'PENDING'")
    expiring_today = await pool.fetchval("""
        SELECT COUNT(*) FROM subscriptions WHERE status = 'ACTIVE' AND end_date::date = CURRENT_DATE
    """)
    return {
        "pending_slips": pending_slips,
        "expiring_today": expiring_today,
    }
