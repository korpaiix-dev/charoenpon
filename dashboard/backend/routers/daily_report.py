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


@router.get("/purchases")
async def daily_purchases(period: str = "today", admin=Depends(require_role("admin"))):
    """List of who bought what today/yesterday/this-week — for boss to scan.

    period: today / yesterday / week / month
    """
    BKK = "((NOW() AT TIME ZONE 'UTC') AT TIME ZONE 'Asia/Bangkok')::date"
    BKK_CREATED = "((p.created_at AT TIME ZONE 'UTC') AT TIME ZONE 'Asia/Bangkok')::date"

    if period == "today":
        where = f"{BKK_CREATED} = {BKK}"
    elif period == "yesterday":
        where = f"{BKK_CREATED} = {BKK} - INTERVAL '1 day'"
    elif period == "week":
        where = f"{BKK_CREATED} >= {BKK} - INTERVAL '6 days'"
    elif period == "month":
        where = f"{BKK_CREATED} >= date_trunc('month', {BKK})"
    else:
        where = f"{BKK_CREATED} = {BKK}"

    rows = await pool.fetch(f"""
        SELECT
            p.id, p.amount::float AS amount, p.status::text AS status, p.method::text AS method,
            p.created_at, p.verified_at, p.auto_approved,
            p.sender_name, p.slip_trans_ref,
            pk.id AS package_id, pk.name AS package_name, pk.tier::text AS package_tier,
            pk.price::float AS package_price, pk.duration_days,
            u.id AS user_id, u.telegram_id, u.username, u.first_name, u.last_name,
            u.total_spent::float AS total_spent, u.loyalty_rank,
            u.is_banned, u.is_blocked_bot,
            (SELECT name FROM promotion_campaigns WHERE id = p.promotion_campaign_id) AS promo_name,
            (SELECT COUNT(*) FROM payments p2 WHERE p2.user_id = u.id AND p2.status='CONFIRMED' AND p2.id != p.id) AS past_count
        FROM payments p
        LEFT JOIN users u ON u.id = p.user_id
        LEFT JOIN packages pk ON pk.id = p.package_id
        WHERE p.status IN ('CONFIRMED', 'PENDING', 'REJECTED')
          AND {where}
          AND (u.telegram_id IS NULL OR u.telegram_id < 9000000000)
        ORDER BY p.created_at DESC
    """)

    items = []
    for r in rows:
        pkg_price = float(r["package_price"] or 0)
        amount_paid = float(r["amount"] or 0)
        discount = max(0, pkg_price - amount_paid) if pkg_price > 0 else 0
        past = int(r["past_count"] or 0)
        items.append({
            "id": r["id"],
            "status": r["status"],
            "method": r["method"],
            "amount": amount_paid,
            "auto_approved": r["auto_approved"],
            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            "verified_at": r["verified_at"].isoformat() if r["verified_at"] else None,
            "package_name": r["package_name"],
            "package_tier": r["package_tier"],
            "package_price": pkg_price,
            "duration_days": r["duration_days"],
            "discount": discount,
            "promo_name": r["promo_name"],
            "sender_name": r["sender_name"],
            "trans_ref": r["slip_trans_ref"],
            "customer": {
                "user_id": r["user_id"],
                "telegram_id": r["telegram_id"],
                "username": r["username"],
                "first_name": r["first_name"],
                "last_name": r["last_name"],
                "total_spent": float(r["total_spent"] or 0),
                "loyalty_rank": r["loyalty_rank"],
                "is_banned": r["is_banned"],
                "is_blocked_bot": r["is_blocked_bot"],
                "past_confirmed_count": past,
                "is_returning": past > 0,
            },
        })

    # Summary
    confirmed = [it for it in items if it["status"] == "CONFIRMED" and it["amount"] > 0]
    pending = [it for it in items if it["status"] == "PENDING"]
    rejected = [it for it in items if it["status"] == "REJECTED"]
    total_rev = sum(it["amount"] for it in confirmed)
    unique_buyers = len({it["customer"]["user_id"] for it in confirmed})

    return {
        "period": period,
        "summary": {
            "total_orders": len(items),
            "confirmed": len(confirmed),
            "pending": len(pending),
            "rejected": len(rejected),
            "revenue": total_rev,
            "unique_buyers": unique_buyers,
            "avg_order": total_rev / len(confirmed) if confirmed else 0,
        },
        "items": items,
    }

