"""Marketing analytics router."""
from fastapi import APIRouter, Depends
from ..auth.dependencies import require_role
from ..database import pool

router = APIRouter(prefix="/api/marketing", tags=["marketing"])

@router.get("/kpi")
async def kpi(days: int = 30, admin=Depends(require_role("admin"))):
    row = await pool.fetchrow("""
        SELECT
            COALESCE(SUM(CASE WHEN p.status = 'CONFIRMED' AND p.amount > 0 AND p.created_at >= (NOW() AT TIME ZONE 'Asia/Bangkok')::date - $1 * interval '1 day' THEN p.amount END), 0) as revenue,
            (SELECT COUNT(*) FROM users WHERE created_at >= (NOW() AT TIME ZONE 'Asia/Bangkok')::date - $1 * interval '1 day') as new_members,
            (SELECT COUNT(*) FROM subscriptions WHERE status = 'EXPIRED' AND updated_at >= (NOW() AT TIME ZONE 'Asia/Bangkok')::date - $1 * interval '1 day') as churned,
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
        WHERE p.status = 'CONFIRMED' AND p.created_at >= (NOW() AT TIME ZONE 'Asia/Bangkok')::date - 56
        GROUP BY week_start ORDER BY week_start
    """)
    return [{"week": str(r["week_start"]), "revenue": float(r["revenue"]), "transactions": r["transactions"]} for r in rows]

@router.get("/funnel")
async def funnel(days: int = 30, admin=Depends(require_role("admin"))):
    # Free group members → Teaser clicks → Trial purchases → VIP/GOD purchases
    free_members = await pool.fetchval("SELECT COUNT(*) FROM users")
    teaser_clicks = await pool.fetchval(
        "SELECT COUNT(*) FROM teaser_clicks WHERE created_at >= (NOW() AT TIME ZONE 'Asia/Bangkok')::date - $1 * interval '1 day'", days)
    trial_purchases = await pool.fetchval("""
        SELECT COUNT(*) FROM payments p JOIN packages pk ON p.package_id = pk.id
        WHERE p.status = 'CONFIRMED' AND pk.tier = 'TIER_99' AND p.created_at >= (NOW() AT TIME ZONE 'Asia/Bangkok')::date - $1 * interval '1 day'
    """, days)
    vip_purchases = await pool.fetchval("""
        SELECT COUNT(*) FROM payments p JOIN packages pk ON p.package_id = pk.id
        WHERE p.status = 'CONFIRMED' AND pk.tier != 'TIER_99' AND p.created_at >= (NOW() AT TIME ZONE 'Asia/Bangkok')::date - $1 * interval '1 day'
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
    # FIX 2025-05-21 (Phase D-6-business): clamp pagination
    per_page = max(1, min(per_page, 100))
    page = max(1, page)
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



@router.get("/roi")
async def marketing_roi(days: int = 30, admin=Depends(require_role("admin"))):
    """Per-marketer ROI summary for last N days."""
    # Per marketer + platform breakdown
    rows = await pool.fetch("""
        WITH joins AS (
            SELECT l.marketer, l.platform, l.id AS link_id, l.cost,
                   j.telegram_id, j.joined_at
            FROM marketing_invite_links l
            LEFT JOIN marketing_invite_joins j ON j.link_id = l.id
                 AND j.joined_at >= now() - ($1 || ' days')::interval
            WHERE l.is_revoked = false OR j.id IS NOT NULL
        ),
        paid AS (
            SELECT j.marketer, j.platform, j.link_id, j.cost,
                   j.telegram_id,
                   (SELECT COALESCE(SUM(p.amount), 0) FROM payments p
                    JOIN users u ON u.id = p.user_id
                    WHERE u.telegram_id = j.telegram_id
                      AND p.status = 'CONFIRMED'
                      AND p.amount > 0
                      AND p.created_at >= j.joined_at
                      AND (p.created_at - j.joined_at) <= interval '30 days') AS rev
            FROM joins j
            WHERE j.telegram_id IS NOT NULL
        ),
        link_revenue AS (
            SELECT link_id, marketer, platform, MAX(cost) AS cost,
                   COUNT(DISTINCT telegram_id) AS joins,
                   COUNT(DISTINCT telegram_id) FILTER (WHERE rev > 0) AS paid_count,
                   SUM(rev) AS revenue
            FROM paid
            GROUP BY link_id, marketer, platform
        )
        SELECT
            marketer,
            platform,
            SUM(cost)::float AS cost,
            SUM(joins)::int AS joins,
            SUM(paid_count)::int AS paid,
            SUM(revenue)::float AS revenue,
            (SUM(revenue) - SUM(cost))::float AS profit,
            CASE WHEN SUM(cost) > 0 THEN ROUND(((SUM(revenue) - SUM(cost)) / SUM(cost) * 100)::numeric, 1)::float ELSE NULL END AS roi_pct
        FROM link_revenue
        GROUP BY marketer, platform
        ORDER BY revenue DESC NULLS LAST
    """, days)
    
    # Also: include links with cost but zero joins (for completeness)
    rows_no_joins = await pool.fetch("""
        SELECT l.marketer, l.platform, l.cost::float AS cost
        FROM marketing_invite_links l
        WHERE l.is_revoked = false
          AND l.cost > 0
          AND NOT EXISTS (SELECT 1 FROM marketing_invite_joins j WHERE j.link_id = l.id)
    """)
    
    breakdown = [
        {
            "marketer": r["marketer"], "platform": r["platform"],
            "cost": r["cost"] or 0,
            "joins": r["joins"] or 0,
            "paid": r["paid"] or 0,
            "revenue": r["revenue"] or 0,
            "profit": r["profit"] or 0,
            "roi_pct": r["roi_pct"],
        }
        for r in rows
    ]
    
    # Compute totals
    total_cost = sum(b["cost"] for b in breakdown) + sum(r["cost"] for r in rows_no_joins)
    total_revenue = sum(b["revenue"] for b in breakdown)
    total_profit = total_revenue - total_cost
    total_joins = sum(b["joins"] for b in breakdown)
    total_paid = sum(b["paid"] for b in breakdown)
    overall_roi = ((total_revenue - total_cost) / total_cost * 100) if total_cost > 0 else None
    
    # Per-marketer aggregate
    by_marketer = {}
    for b in breakdown:
        m = b["marketer"]
        if m not in by_marketer:
            by_marketer[m] = {"marketer": m, "cost": 0, "revenue": 0, "joins": 0, "paid": 0}
        by_marketer[m]["cost"] += b["cost"]
        by_marketer[m]["revenue"] += b["revenue"]
        by_marketer[m]["joins"] += b["joins"]
        by_marketer[m]["paid"] += b["paid"]
    for m_data in by_marketer.values():
        m_data["profit"] = m_data["revenue"] - m_data["cost"]
        m_data["roi_pct"] = round((m_data["revenue"] - m_data["cost"]) / m_data["cost"] * 100, 1) if m_data["cost"] > 0 else None
    
    return {
        "days": days,
        "totals": {
            "cost": total_cost, "revenue": total_revenue, "profit": total_profit,
            "joins": total_joins, "paid": total_paid,
            "roi_pct": round(overall_roi, 1) if overall_roi is not None else None,
        },
        "by_marketer": list(by_marketer.values()),
        "by_platform": breakdown,
    }


@router.get("/links")
async def marketing_links_list(admin=Depends(require_role("admin"))):
    """All marketing links with cost + revenue info."""
    rows = await pool.fetch("""
        SELECT l.id, l.marketer, l.platform, l.group_slug::text AS group_slug,
               l.invite_link, l.name_tag,
               l.cost::float AS cost, l.is_revoked, l.created_at,
               l.cost_updated_at, l.cost_notes,
               (SELECT COUNT(*) FROM marketing_invite_joins j WHERE j.link_id = l.id)::int AS joins,
               (SELECT COALESCE(SUM(p.amount), 0)::float
                FROM marketing_invite_joins j
                JOIN users u ON u.telegram_id = j.telegram_id
                JOIN payments p ON p.user_id = u.id
                WHERE j.link_id = l.id
                  AND p.status = 'CONFIRMED' AND p.amount > 0
                  AND p.created_at >= j.joined_at
                  AND (p.created_at - j.joined_at) <= interval '30 days') AS revenue
        FROM marketing_invite_links l
        ORDER BY l.created_at DESC
    """)
    return [
        {
            "id": r["id"], "marketer": r["marketer"], "platform": r["platform"],
            "group_slug": r["group_slug"],
            "invite_link": r["invite_link"], "name_tag": r["name_tag"],
            "cost": r["cost"] or 0, "is_revoked": r["is_revoked"],
            "joins": r["joins"], "revenue": r["revenue"] or 0,
            "profit": (r["revenue"] or 0) - (r["cost"] or 0),
            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            "cost_updated_at": r["cost_updated_at"].isoformat() if r["cost_updated_at"] else None,
            "cost_notes": r["cost_notes"],
        }
        for r in rows
    ]
