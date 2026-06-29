"""Marketing analytics router."""
from typing import Optional
from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException
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
        "revenue": float(row["revenue"] or 0),
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
    return [{"week": str(r["week_start"]), "revenue": float(r["revenue"] or 0), "transactions": r["transactions"]} for r in rows]

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

@router.get("/roi")
async def marketing_roi(days: int = 30, admin=Depends(require_role("admin"))):
    """Per-marketer ROI summary for last N days.

    Uses the same logic as ``shared.marketing_stats.get_marketer_stats``
    so Discord notify, Dashboard ROI, and Weekly MVP all show the same
    numbers. Key invariants enforced:
      - JOIN payments via user_id (not telegram_id) to avoid cross-link dup
      - DISTINCT p.id in COUNT/SUM (kill double-count for users with
        multiple joins across links)
      - Exclude test users (telegram_id >= 9_000_000_000)
    """
    # Per-link aggregates first — avoids row multiplication that happens
    # when LEFT JOIN payments are summed up at the marketer level.
    link_rows = await pool.fetch("""
        SELECT
            l.id AS link_id,
            l.marketer,
            l.platform,
            l.cost::float AS cost,
            (
                SELECT COUNT(DISTINCT j.user_id)
                FROM marketing_invite_joins j
                JOIN users u ON u.id = j.user_id
                WHERE j.link_id = l.id
                  AND j.user_id IS NOT NULL
                  AND u.telegram_id < 9000000000
                  AND j.joined_at >= now() - ($1::int * INTERVAL '1 day')
            )::int AS joins,
            (
                SELECT COUNT(*) FROM (
                    SELECT DISTINCT p.id
                    FROM marketing_invite_joins j2
                    JOIN users u2 ON u2.id = j2.user_id
                    JOIN payments p ON p.user_id = j2.user_id
                    WHERE j2.link_id = l.id
                      AND j2.user_id IS NOT NULL
                      AND u2.telegram_id < 9000000000
                      AND j2.joined_at >= now() - ($1::int * INTERVAL '1 day')
                      AND p.status = 'CONFIRMED'
                      AND p.amount > 0
                      AND p.created_at >= j2.joined_at
                      AND (p.created_at - j2.joined_at) <= INTERVAL '30 days'
                ) sub
            )::int AS paid,
            (
                SELECT COALESCE(SUM(amount), 0)::float FROM (
                    SELECT DISTINCT p.id, p.amount
                    FROM marketing_invite_joins j2
                    JOIN users u2 ON u2.id = j2.user_id
                    JOIN payments p ON p.user_id = j2.user_id
                    WHERE j2.link_id = l.id
                      AND j2.user_id IS NOT NULL
                      AND u2.telegram_id < 9000000000
                      AND j2.joined_at >= now() - ($1::int * INTERVAL '1 day')
                      AND p.status = 'CONFIRMED'
                      AND p.amount > 0
                      AND p.created_at >= j2.joined_at
                      AND (p.created_at - j2.joined_at) <= INTERVAL '30 days'
                ) sub
            ) AS revenue
        FROM marketing_invite_links l
        WHERE l.is_revoked = false
           OR EXISTS (SELECT 1 FROM marketing_invite_joins jj WHERE jj.link_id = l.id)
    """, days)

    # Aggregate per (marketer, platform). Drop empty rows (no cost / no joins / no revenue).
    from collections import defaultdict
    agg: dict = defaultdict(lambda: {"cost": 0.0, "joins": 0, "paid": 0, "revenue": 0.0})
    for r in link_rows:
        if (r["cost"] or 0) == 0 and (r["joins"] or 0) == 0 and (r["revenue"] or 0) == 0:
            continue
        key = (r["marketer"], r["platform"])
        agg[key]["cost"] += float(r["cost"] or 0)
        agg[key]["joins"] += int(r["joins"] or 0)
        agg[key]["paid"] += int(r["paid"] or 0)
        agg[key]["revenue"] += float(r["revenue"] or 0)

    rows = [
        {
            "marketer": k[0], "platform": k[1],
            "cost": v["cost"], "joins": v["joins"],
            "paid": v["paid"], "revenue": v["revenue"],
            "profit": v["revenue"] - v["cost"],
            "roi_pct": round((v["revenue"] - v["cost"]) / v["cost"] * 100, 1) if v["cost"] > 0 else None,
        }
        for k, v in agg.items()
    ]
    rows.sort(key=lambda r: r["revenue"], reverse=True)
    
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
               l.invite_link, l.name_tag, l.short_code, l.link_type,
               l.cost::float AS cost, l.is_revoked, l.created_at,
               l.cost_updated_at, l.cost_notes,
               (SELECT COUNT(*) FROM marketing_link_clicks c WHERE c.link_id = l.id)::int AS clicks,
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
            "short_code": r["short_code"], "link_type": r["link_type"],
            "short_url": (f"https://telebord.net/r/{r['short_code']}" if r["short_code"] else None),
            "clicks": r["clicks"] or 0,
            "cost": r["cost"] or 0, "is_revoked": r["is_revoked"],
            "joins": r["joins"], "revenue": r["revenue"] or 0,
            "profit": (r["revenue"] or 0) - (r["cost"] or 0),
            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            "cost_updated_at": r["cost_updated_at"].isoformat() if r["cost_updated_at"] else None,
            "cost_notes": r["cost_notes"],
        }
        for r in rows
    ]

# ====== Sprint 2.3: Link CRUD endpoints ======

class _LinkCostUpdate(BaseModel):
    cost: Optional[float] = None
    cost_notes: Optional[str] = None


@router.patch("/links/{link_id}")
async def update_marketing_link(link_id: int, req: _LinkCostUpdate,
                                admin=Depends(require_role("admin"))):
    """Update cost / cost_notes for a marketing link."""
    sets = []
    params: list = []
    idx = 1
    if req.cost is not None:
        if req.cost < 0:
            raise HTTPException(400, "cost must be >= 0")
        sets.append(f"cost = ${idx}::numeric")
        params.append(req.cost)
        idx += 1
    if req.cost_notes is not None:
        sets.append(f"cost_notes = ${idx}")
        params.append(req.cost_notes[:500])
        idx += 1
    if not sets:
        raise HTTPException(400, "no fields to update")
    sets.append("cost_updated_at = NOW()")
    params.append(link_id)
    sql = f"UPDATE marketing_invite_links SET {', '.join(sets)} WHERE id = ${idx} RETURNING id, marketer, platform"
    row = await pool.fetchrow(sql, *params)
    if not row:
        raise HTTPException(404, "link not found")
    try:
        await pool.execute(
            "INSERT INTO admin_logs (admin_id, action, target_type, target_id, details) "
            "VALUES ($1, 'marketing_link_update', 'marketing_link', $2, $3)",
            admin["telegram_id"], link_id,
            f"marketer={row['marketer']} platform={row['platform']} cost={req.cost}",
        )
    except Exception as _re_exc:
        logger.warning("marketing revoke: %s", _re_exc)
    return {"ok": True, "id": link_id}


@router.post("/links/{link_id}/revoke")
async def revoke_marketing_link(link_id: int, admin=Depends(require_role("admin"))):
    """Mark link as revoked. Group invites also revoked via Telegram API."""
    row = await pool.fetchrow(
        "SELECT id, marketer, platform, link_type, invite_link, group_chat_id, is_revoked "
        "FROM marketing_invite_links WHERE id = $1",
        link_id,
    )
    if not row:
        raise HTTPException(404, "link not found")
    if row["is_revoked"]:
        return {"ok": True, "already_revoked": True}

    tg_revoke_result = None
    if row["link_type"] == "group_invite" and row["invite_link"] and row["group_chat_id"]:
        try:
            import os, httpx
            token = os.getenv("GUARDIAN_BOT_TOKEN", "")
            if token:
                async with httpx.AsyncClient(timeout=10) as cx:
                    r = await cx.post(
                        f"https://api.telegram.org/bot{token}/revokeChatInviteLink",
                        data={"chat_id": row["group_chat_id"], "invite_link": row["invite_link"]},
                    )
                    tg_revoke_result = bool(r.json().get("ok", False))
        except Exception:
            pass

    await pool.execute(
        "UPDATE marketing_invite_links SET is_revoked = TRUE WHERE id = $1",
        link_id,
    )
    try:
        await pool.execute(
            "INSERT INTO admin_logs (admin_id, action, target_type, target_id, details) "
            "VALUES ($1, 'marketing_link_revoke', 'marketing_link', $2, $3)",
            admin["telegram_id"], link_id,
            f"marketer={row['marketer']} platform={row['platform']} tg_api={tg_revoke_result}",
        )
    except Exception as _re_exc:
        logger.warning("marketing revoke: %s", _re_exc)
    return {"ok": True, "id": link_id, "tg_revoked": tg_revoke_result}




@router.get("/heatmap")
async def marketing_heatmap(days: int = 30, admin=Depends(require_role("admin"))):
    """Conversion heatmap: 7 days × 24 hours grid.

    Cells = number of joins via marketing links.
    Returns: { grid: [[0,1,2,...], [...], ...], totals: {day: N}, peak: {dow,hour,count} }
    """
    rows = await pool.fetch(
        f"""
        SELECT
            EXTRACT(DOW FROM joined_at AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Bangkok')::int AS dow,
            EXTRACT(HOUR FROM joined_at AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Bangkok')::int AS hr,
            COUNT(*) AS joins
        FROM marketing_invite_joins
        WHERE joined_at > NOW() - INTERVAL '{int(days)} days'
        GROUP BY dow, hr
        """
    )

    # Build 7×24 grid (dow 0=Sunday)
    grid = [[0]*24 for _ in range(7)]
    day_totals = [0]*7
    peak = {"dow": 0, "hour": 0, "count": 0}
    total = 0
    for r in rows:
        d = int(r['dow']); h = int(r['hr']); c = int(r['joins'])
        grid[d][h] = c
        day_totals[d] += c
        total += c
        if c > peak['count']:
            peak = {"dow": d, "hour": h, "count": c}

    day_labels = ['อาทิตย์','จันทร์','อังคาร','พุธ','พฤหัส','ศุกร์','เสาร์']
    return {
        "days": days,
        "grid": grid,
        "day_totals": day_totals,
        "day_labels": day_labels,
        "peak": peak,
        "total": total,
    }
