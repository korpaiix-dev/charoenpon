"""Marketing analytics router."""
from fastapi import APIRouter, Depends
from ..auth.dependencies import require_role
from ..database import pool

router = APIRouter(prefix="/api/marketing", tags=["marketing"])

@router.get("/kpi")
async def kpi(days: int = 30, admin=Depends(require_role("admin"))):
    row = await pool.fetchrow("""
        SELECT
            COALESCE(SUM(CASE WHEN p.status = 'CONFIRMED' AND p.created_at >= CURRENT_DATE - $1::int THEN p.amount END), 0) as revenue,
            (SELECT COUNT(*) FROM users WHERE created_at >= CURRENT_DATE - $1::int) as new_members,
            (SELECT COUNT(*) FROM subscriptions WHERE status = 'EXPIRED' AND updated_at >= CURRENT_DATE - $1::int) as churned,
            (SELECT COUNT(*) FROM subscriptions WHERE status = 'ACTIVE') as active_members
        FROM payments p
    """, days)
    
    active = row["active_members"] or 1
    return {
        "revenue": float(row["revenue"]),
        "new_members": row["new_members"],
        "churned": row["churned"],
        "active_members": active,
        "churn_rate": round((row["churned"] / active) * 100, 1) if active else 0,
    }

@router.get("/weekly-comparison")
async def weekly_comparison(admin=Depends(require_role("admin"))):
    rows = await pool.fetch("""
        SELECT 
            date_trunc('week', p.created_at)::date as week_start,
            SUM(p.amount) as revenue,
            COUNT(*) as transactions
        FROM payments p
        WHERE p.status = 'CONFIRMED' AND p.created_at >= CURRENT_DATE - 56
        GROUP BY week_start ORDER BY week_start
    """)
    return [{"week": str(r["week_start"]), "revenue": float(r["revenue"]), "transactions": r["transactions"]} for r in rows]

@router.get("/funnel")
async def funnel(days: int = 30, admin=Depends(require_role("admin"))):
    # Free group members → Teaser clicks → Trial purchases → VIP/GOD purchases
    free_members = await pool.fetchval("SELECT COUNT(*) FROM users")
    teaser_clicks = await pool.fetchval(
        "SELECT COUNT(*) FROM teaser_clicks WHERE created_at >= CURRENT_DATE - $1::int", days)
    trial_purchases = await pool.fetchval("""
        SELECT COUNT(*) FROM payments p JOIN packages pk ON p.package_id = pk.id
        WHERE p.status = 'CONFIRMED' AND pk.tier = 'TIER_99' AND p.created_at >= CURRENT_DATE - $1::int
    """, days)
    vip_purchases = await pool.fetchval("""
        SELECT COUNT(*) FROM payments p JOIN packages pk ON p.package_id = pk.id
        WHERE p.status = 'CONFIRMED' AND pk.tier != 'TIER_99' AND p.created_at >= CURRENT_DATE - $1::int
    """, days)
    
    return {
        "free_members": free_members,
        "teaser_clicks": teaser_clicks,
        "trial_purchases": trial_purchases,
        "vip_purchases": vip_purchases,
    }

@router.get("/ai-insights")
async def ai_insights(admin=Depends(require_role("admin"))):
    # Get latest report with insights
    row = await pool.fetchrow("""
        SELECT ai_insights, report_date FROM marketing_daily_reports
        WHERE ai_insights IS NOT NULL ORDER BY report_date DESC LIMIT 1
    """)
    if row:
        return {"insights": row["ai_insights"], "date": str(row["report_date"])}
    return {"insights": "ยังไม่มีข้อมูล AI Insights — ระบบจะสร้างอัตโนมัติเมื่อมี data เพียงพอ", "date": None}

@router.get("/daily-reports")
async def daily_reports(page: int = 1, per_page: int = 30, admin=Depends(require_role("admin"))):
    offset = (page - 1) * per_page
    total = await pool.fetchval("SELECT COUNT(*) FROM marketing_daily_reports")
    rows = await pool.fetch("""
        SELECT * FROM marketing_daily_reports ORDER BY report_date DESC
        LIMIT $1 OFFSET $2
    """, per_page, offset)
    return {"items": [dict(r) for r in rows], "total": total, "page": page}

@router.get("/daily-reports/{date}")
async def daily_report_detail(date: str, admin=Depends(require_role("admin"))):
    row = await pool.fetchrow("SELECT * FROM marketing_daily_reports WHERE report_date = $1::date", date)
    if not row:
        return {"error": "No report for this date"}
    return dict(row)
