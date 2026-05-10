"""Dashboard home — summary, charts, stats, alerts, SOS."""
from fastapi import APIRouter, Depends, Request, HTTPException
from ..auth.dependencies import get_current_admin, require_role
from ..database import pool
from ..config import GUARDIAN_BOT_TOKEN, SALES_BOT_TOKEN
import json, logging, httpx
from datetime import date, datetime, timedelta

async def log_admin_action(admin_id, action, target_type="", target_id=0, details=""):
    """Log admin action to dashboard_activity_log table."""
    try:
        await pool.execute(
            "INSERT INTO dashboard_activity_log (admin_id, action, entity_type, entity_id, details) VALUES ($1, $2, $3, $4, $5::jsonb)",
            admin_id, action, target_type, str(target_id), json.dumps({"details": details}, ensure_ascii=False),
        )
    except Exception:
        logger.exception("Failed to write dashboard activity log")

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])

@router.get("/activity-log")
async def activity_log(
    page: int = 1, per_page: int = 30, action: str = "", admin_id: int = 0,
    admin=Depends(get_current_admin)
):
    """Get dashboard activity log with filters."""
    offset = (page - 1) * per_page
    conditions = []
    params = []
    idx = 1
    if action:
        conditions.append(f"al.action = ${idx}")
        params.append(action)
        idx += 1
    if admin_id:
        conditions.append(f"al.admin_id = ${idx}")
        params.append(admin_id)
        idx += 1
    where = "WHERE " + " AND ".join(conditions) if conditions else ""
    total = await pool.fetchval(f"SELECT COUNT(*) FROM dashboard_activity_log al {where}", *params)
    rows = await pool.fetch(f"""
        SELECT al.*, da.display_name as admin_name
        FROM dashboard_activity_log al
        LEFT JOIN dashboard_admins da ON al.admin_id = da.id
        {where}
        ORDER BY al.created_at DESC
        LIMIT {per_page} OFFSET {offset}
    """, *params)
    return {
        "items": [dict(r) for r in rows],
        "total": total,
        "page": page,
        "per_page": per_page,
        "pages": (total + per_page - 1) // per_page,
    }


@router.get("/activity-log/filters")
async def activity_log_filters(admin=Depends(get_current_admin)):
    """Get distinct action types and admins for filter dropdowns."""
    actions = await pool.fetch("SELECT DISTINCT action FROM dashboard_activity_log ORDER BY action")
    admins = await pool.fetch("SELECT DISTINCT al.admin_id, da.display_name FROM dashboard_activity_log al LEFT JOIN dashboard_admins da ON al.admin_id = da.id ORDER BY da.display_name")
    return {
        "actions": [r["action"] for r in actions],
        "admins": [{"id": r["admin_id"], "name": r["display_name"] or str(r["admin_id"])} for r in admins],
    }


@router.get("/summary")
async def summary(request: Request, admin=Depends(get_current_admin)):
    """Revenue summary: today, week, month with comparison."""
    row = await pool.fetchrow("""
        SELECT
            COALESCE(SUM(CASE WHEN p.created_at::date = (NOW() AT TIME ZONE 'Asia/Bangkok')::date THEN p.amount END), 0) as today,
            COALESCE(SUM(CASE WHEN p.created_at::date = (NOW() AT TIME ZONE 'Asia/Bangkok')::date - 1 THEN p.amount END), 0) as yesterday,
            COALESCE(SUM(CASE WHEN p.created_at >= date_trunc('week', (NOW() AT TIME ZONE 'Asia/Bangkok')::date) THEN p.amount END), 0) as week,
            COALESCE(SUM(CASE WHEN p.created_at >= date_trunc('week', (NOW() AT TIME ZONE 'Asia/Bangkok')::date) - interval '7 days'
                              AND p.created_at < date_trunc('week', (NOW() AT TIME ZONE 'Asia/Bangkok')::date) THEN p.amount END), 0) as last_week,
            COALESCE(SUM(CASE WHEN p.created_at >= date_trunc('month', (NOW() AT TIME ZONE 'Asia/Bangkok')::date) THEN p.amount END), 0) as month,
            COALESCE(SUM(CASE WHEN p.created_at >= date_trunc('month', (NOW() AT TIME ZONE 'Asia/Bangkok')::date) - interval '1 month'
                              AND p.created_at < date_trunc('month', (NOW() AT TIME ZONE 'Asia/Bangkok')::date) THEN p.amount END), 0) as last_month
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
        WHERE p.status = 'CONFIRMED' AND p.created_at >= (NOW() AT TIME ZONE 'Asia/Bangkok')::date - $1 * interval '1 day'
        GROUP BY p.created_at::date ORDER BY date
    """, days)
    return [{"date": str(r["date"]), "revenue": float(r["revenue"])} for r in rows]

@router.get("/sales-analytics")
async def sales_analytics(
    period: str = "month",
    date_from: str = "",
    date_to: str = "",
    admin=Depends(get_current_admin),
):
    """Revenue + buyer analytics for historical day/month/custom ranges.

    All grouping uses Asia/Bangkok business date so dashboard numbers match the team day.
    """
    today = await pool.fetchval("SELECT (NOW() AT TIME ZONE 'Asia/Bangkok')::date")
    if period not in {"day", "month", "custom"}:
        period = "month"

    try:
        if period == "day":
            start = datetime.strptime(date_from, "%Y-%m-%d").date() if date_from else today
            end = start
        elif period == "month":
            if date_from:
                first = datetime.strptime(date_from[:7] + "-01", "%Y-%m-%d").date()
            else:
                first = today.replace(day=1)
            next_month = (first.replace(day=28) + timedelta(days=4)).replace(day=1)
            start = first
            end = next_month - timedelta(days=1)
        else:
            start = datetime.strptime(date_from, "%Y-%m-%d").date() if date_from else today.replace(day=1)
            end = datetime.strptime(date_to, "%Y-%m-%d").date() if date_to else today
    except ValueError:
        raise HTTPException(status_code=400, detail="รูปแบบวันที่ไม่ถูกต้อง")

    if end < start:
        start, end = end, start
    if (end - start).days > 366:
        raise HTTPException(status_code=400, detail="เลือกช่วงย้อนหลังได้สูงสุด 366 วันต่อครั้ง")

    if period == "month":
        prev_end = start - timedelta(days=1)
        prev_start = prev_end.replace(day=1)
    else:
        span = (end - start).days + 1
        prev_end = start - timedelta(days=1)
        prev_start = prev_end - timedelta(days=span - 1)

    async def range_summary(a: date, b: date):
        return await pool.fetchrow("""
            WITH confirmed AS (
                SELECT p.user_id, p.amount, (p.created_at AT TIME ZONE 'Asia/Bangkok')::date AS paid_date
                FROM payments p
                WHERE p.status = 'CONFIRMED'
                  AND (p.created_at AT TIME ZONE 'Asia/Bangkok')::date BETWEEN $1 AND $2
            ), first_paid AS (
                SELECT p.user_id, MIN((p.created_at AT TIME ZONE 'Asia/Bangkok')::date) AS first_paid_date
                FROM payments p
                WHERE p.status = 'CONFIRMED'
                GROUP BY p.user_id
            )
            SELECT
                COALESCE(SUM(c.amount), 0) AS revenue,
                COUNT(*) AS orders,
                COUNT(DISTINCT c.user_id) AS buyers,
                COUNT(DISTINCT c.user_id) FILTER (WHERE fp.first_paid_date BETWEEN $1 AND $2) AS new_buyers,
                COALESCE(AVG(c.amount), 0) AS avg_order
            FROM confirmed c
            LEFT JOIN first_paid fp ON fp.user_id = c.user_id
        """, a, b)

    current = await range_summary(start, end)
    previous = await range_summary(prev_start, prev_end)

    chart_rows = await pool.fetch("""
        WITH days AS (
            SELECT generate_series($1::date, $2::date, interval '1 day')::date AS day
        )
        SELECT d.day,
               COALESCE(SUM(p.amount), 0) AS revenue,
               COUNT(p.id) AS orders,
               COUNT(DISTINCT p.user_id) AS buyers
        FROM days d
        LEFT JOIN payments p
          ON p.status = 'CONFIRMED'
         AND (p.created_at AT TIME ZONE 'Asia/Bangkok')::date = d.day
        GROUP BY d.day
        ORDER BY d.day
    """, start, end)

    package_rows = await pool.fetch("""
        SELECT COALESCE(pk.name, 'ไม่ระบุแพ็กเกจ') AS package_name,
               COALESCE(SUM(p.amount), 0) AS revenue,
               COUNT(*) AS orders,
               COUNT(DISTINCT p.user_id) AS buyers
        FROM payments p
        LEFT JOIN packages pk ON pk.id = p.package_id
        WHERE p.status = 'CONFIRMED'
          AND (p.created_at AT TIME ZONE 'Asia/Bangkok')::date BETWEEN $1 AND $2
        GROUP BY COALESCE(pk.name, 'ไม่ระบุแพ็กเกจ')
        ORDER BY revenue DESC
        LIMIT 8
    """, start, end)

    month_rows = await pool.fetch("""
        SELECT date_trunc('month', p.created_at AT TIME ZONE 'Asia/Bangkok')::date AS month,
               COALESCE(SUM(p.amount), 0) AS revenue,
               COUNT(*) AS orders,
               COUNT(DISTINCT p.user_id) AS buyers
        FROM payments p
        WHERE p.status = 'CONFIRMED'
          AND (p.created_at AT TIME ZONE 'Asia/Bangkok')::date >= date_trunc('month', (NOW() AT TIME ZONE 'Asia/Bangkok')::date) - interval '11 months'
        GROUP BY 1
        ORDER BY 1
    """)

    def pct(curr, prev):
        curr = float(curr or 0)
        prev = float(prev or 0)
        if prev == 0:
            return 0
        return round(((curr - prev) / prev) * 100, 1)

    return {
        "period": period,
        "date_from": str(start),
        "date_to": str(end),
        "previous_from": str(prev_start),
        "previous_to": str(prev_end),
        "summary": {
            "revenue": float(current["revenue"] or 0),
            "orders": int(current["orders"] or 0),
            "buyers": int(current["buyers"] or 0),
            "new_buyers": int(current["new_buyers"] or 0),
            "avg_order": float(current["avg_order"] or 0),
            "revenue_change": pct(current["revenue"], previous["revenue"]),
            "buyers_change": pct(current["buyers"], previous["buyers"]),
        },
        "previous": {
            "revenue": float(previous["revenue"] or 0),
            "orders": int(previous["orders"] or 0),
            "buyers": int(previous["buyers"] or 0),
        },
        "chart": [{"date": str(r["day"]), "revenue": float(r["revenue"]), "orders": int(r["orders"]), "buyers": int(r["buyers"])} for r in chart_rows],
        "packages": [{"package_name": r["package_name"], "revenue": float(r["revenue"]), "orders": int(r["orders"]), "buyers": int(r["buyers"])} for r in package_rows],
        "months": [{"month": str(r["month"])[:7], "revenue": float(r["revenue"]), "orders": int(r["orders"]), "buyers": int(r["buyers"])} for r in month_rows],
    }

@router.get("/members-stats")
async def members_stats(admin=Depends(get_current_admin)):
    row = await pool.fetchrow("""
        SELECT
            (SELECT COUNT(*) FROM subscriptions WHERE status = 'ACTIVE') as active,
            (SELECT COUNT(*) FROM subscriptions WHERE status = 'EXPIRED') as expired,
            (SELECT COUNT(*) FROM users WHERE created_at::date = (NOW() AT TIME ZONE 'Asia/Bangkok')::date) as new_today,
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
            (SELECT COUNT(*) FROM comeback_dm_log WHERE sent_at::date = (NOW() AT TIME ZONE 'Asia/Bangkok')::date) as comeback_sent,
            (SELECT COUNT(*) FROM comeback_dm_log WHERE sent_at::date = (NOW() AT TIME ZONE 'Asia/Bangkok')::date AND responded = TRUE) as comeback_respond,
            (SELECT COUNT(*) FROM comeback_dm_log WHERE sent_at::date = (NOW() AT TIME ZONE 'Asia/Bangkok')::date AND purchased = TRUE) as comeback_convert,
            (SELECT COUNT(*) FROM trial_dm_log WHERE sent_at::date = (NOW() AT TIME ZONE 'Asia/Bangkok')::date) as trial_sent,
            (SELECT COUNT(*) FROM trial_dm_log WHERE sent_at::date = (NOW() AT TIME ZONE 'Asia/Bangkok')::date AND clicked = TRUE) as trial_click,
            (SELECT COUNT(*) FROM trial_dm_log WHERE sent_at::date = (NOW() AT TIME ZONE 'Asia/Bangkok')::date AND purchased = TRUE) as trial_convert
    """)
    return dict(row)

@router.get("/content-stats")
async def content_stats(admin=Depends(require_role("admin"))):
    row = await pool.fetchrow("""
        SELECT
            (SELECT COUNT(*) FROM teaser_clicks WHERE created_at::date = (NOW() AT TIME ZONE 'Asia/Bangkok')::date) as teaser_clicks_today,
            (SELECT COUNT(*) FROM content_queue WHERE is_used = FALSE) as queue_remaining,
            (SELECT COUNT(*) FROM content_schedule WHERE is_sent = TRUE AND sent_at::date = (NOW() AT TIME ZONE 'Asia/Bangkok')::date) as teasers_sent_today
    """)
    return dict(row)

@router.get("/alerts")
async def alerts(admin=Depends(get_current_admin)):
    pending_slips = await pool.fetchval(
        "SELECT COUNT(*) FROM payments WHERE status = 'PENDING' AND created_at >= NOW() - interval '24 hours'"
    )
    expiring_today = await pool.fetchval("""
        SELECT COUNT(*) FROM subscriptions WHERE status = 'ACTIVE' AND end_date::date = (NOW() AT TIME ZONE 'Asia/Bangkok')::date
    """)
    sos_count = await pool.fetchval(
        "SELECT COUNT(*) FROM sos_alerts WHERE status = 'PENDING'"
    )

    # ── ดึงสลิปใหม่ล่าสุด (สำหรับ notification พร้อมรูป) ──
    new_slip_rows = await pool.fetch("""
        SELECT p.id, p.amount, p.slip_file_id, p.created_at,
               u.first_name, u.username, u.telegram_id,
               pk.name as package_name
        FROM payments p
        LEFT JOIN users u ON p.user_id = u.id
        LEFT JOIN packages pk ON p.package_id = pk.id
        WHERE p.status = 'PENDING' AND p.created_at >= NOW() - interval '24 hours'
        ORDER BY p.created_at DESC
        LIMIT 5
    """)
    new_slips = [dict(r) for r in new_slip_rows]

    # ── ตรวจยอดผิดปกติ (ยอดไม่ตรงแพ็กเกจ / ซ้ำ) ──
    anomaly_rows = await pool.fetch("""
        SELECT p.id, p.amount, p.created_at,
               u.first_name, u.username,
               pk.name as package_name, pk.price as expected_price,
               CASE
                   WHEN p.amount < pk.price * 0.9 THEN 'ยอดน้อยกว่าแพ็กเกจ'
                   WHEN p.amount > pk.price * 1.5 THEN 'ยอดมากผิดปกติ'
               END as reason
        FROM payments p
        LEFT JOIN users u ON p.user_id = u.id
        LEFT JOIN packages pk ON p.package_id = pk.id
        WHERE p.status = 'PENDING'
          AND p.created_at >= NOW() - interval '24 hours'
          AND pk.price IS NOT NULL
          AND (p.amount < pk.price * 0.9 OR p.amount > pk.price * 1.5)
        ORDER BY p.created_at DESC
        LIMIT 10
    """)
    anomalies = [dict(r) for r in anomaly_rows]

    return {
        "pending_slips": pending_slips,
        "pending_payments": pending_slips,
        "expiring_today": expiring_today,
        "sos_count": sos_count or 0,
        "new_slips": new_slips,
        "anomaly_count": len(anomalies),
        "anomalies": anomalies,
    }


# ── SOS Alerts ──

@router.get("/sos-alerts")
async def sos_alerts(admin=Depends(get_current_admin)):
    """Get all pending SOS alerts + check subscription status."""
    rows = await pool.fetch("""
        SELECT sa.id, sa.telegram_id, sa.first_name, sa.username, sa.message, sa.status, sa.resolved_by, sa.resolved_at, sa.created_at,
               EXISTS(
                   SELECT 1 FROM subscriptions s
                   JOIN users u ON s.user_id = u.id
                   WHERE u.telegram_id = sa.telegram_id AND s.status = 'ACTIVE'
               ) as has_active_sub
        FROM sos_alerts sa
        WHERE sa.status = 'PENDING'
        ORDER BY sa.created_at DESC
        LIMIT 50
    """)
    return [dict(r) for r in rows]


@router.get("/sos-history")
async def sos_history(status: str = "all", page: int = 1, per_page: int = 25, admin=Depends(get_current_admin)):
    """Get SOS alerts history (all statuses)."""
    offset = (page - 1) * per_page
    conditions = []
    params = []
    idx = 1
    if status != "all":
        conditions.append(f"status = ${idx}")
        params.append(status.upper())
        idx += 1
    where = "WHERE " + " AND ".join(conditions) if conditions else ""
    total = await pool.fetchval(f"SELECT COUNT(*) FROM sos_alerts {where}", *params)
    rows = await pool.fetch(f"""
        SELECT id, telegram_id, first_name, username, message, status, resolved_by, resolved_at, created_at
        FROM sos_alerts {where}
        ORDER BY created_at DESC
        LIMIT {per_page} OFFSET {offset}
    """, *params)
    return {
        "items": [dict(r) for r in rows],
        "total": total,
        "page": page,
        "per_page": per_page,
        "pages": (total + per_page - 1) // per_page,
    }


@router.post("/sos/batch-resolve")
async def sos_batch_resolve(request: Request, admin=Depends(get_current_admin)):
    """Resolve all pending SOS alerts at once."""
    admin_name = admin.get("display_name", "Dashboard")
    result = await pool.execute("""
        UPDATE sos_alerts SET status = 'RESOLVED', resolved_by = $1, resolved_at = NOW()
        WHERE status = 'PENDING'
    """, admin_name)
    count = int(result.split()[-1]) if result else 0

    ip = request.client.host if request.client else None
    await pool.execute(
        "INSERT INTO dashboard_activity_log (admin_id, action, entity_type, entity_id, details, ip_address) VALUES ($1,$2,$3,$4,$5::jsonb,$6)",
        admin["id"], "batch_resolve_sos", "sos_alert", None,
        json.dumps({"resolved_count": count}), ip
    )
    return {"ok": True, "resolved_count": count}


async def _telegram_api(token: str, method: str, payload: dict) -> dict:
    """Call Telegram Bot API."""
    url = f"https://api.telegram.org/bot{token}/{method}"
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(url, json=payload)
        return resp.json()


@router.post("/sos/{telegram_id}/contact")
async def sos_contact_customer(telegram_id: int, body: dict, admin=Depends(get_current_admin)):
    """ส่งข้อความหาลูกค้า SOS (กรณีไม่มี subscription)."""
    message = body.get("message", "")
    if not message:
        raise HTTPException(400, "กรุณาใส่ข้อความ")

    if not SALES_BOT_TOKEN:
        raise HTTPException(500, "Sales Bot Token ไม่ได้ตั้งค่า")

    dm_sent = False
    try:
        result = await _telegram_api(SALES_BOT_TOKEN, "sendMessage", {
            "chat_id": telegram_id,
            "text": message,
            "parse_mode": "HTML",
        })
        dm_sent = result.get("ok", False)
    except Exception as exc:
        logger.error("SOS contact DM failed: %s", exc)

    if not dm_sent:
        raise HTTPException(500, "ส่ง DM ไม่ได้ (ลูกค้าอาจบล็อกบอท)")

    # Log
    admin_name = admin.get("display_name", "Dashboard")
    await log_admin_action(
        admin_id=admin.get("telegram_id", 0),
        action="sos_contact_customer",
        target_type="sos",
        target_id=telegram_id,
        details=f"sent message to {telegram_id}",
    )

    return {"success": True, "dm_sent": dm_sent}


@router.post("/sos/{telegram_id}/resolve")
async def sos_resolve_manual(telegram_id: int, admin=Depends(get_current_admin)):
    """จบเคส SOS manually (mark as resolved)."""
    admin_name = admin.get("display_name", "Dashboard")
    result = await pool.execute("""
        UPDATE sos_alerts SET status = 'RESOLVED', resolved_by = $1, resolved_at = NOW()
        WHERE telegram_id = $2 AND status = 'PENDING'
    """, admin_name, telegram_id)

    await log_admin_action(
        admin_id=admin.get("telegram_id", 0),
        action="sos_resolve_manual",
        target_type="sos",
        target_id=telegram_id,
        details=f"manually resolved SOS for {telegram_id}",
    )

    return {"success": True}


@router.post("/sos/{telegram_id}/resend-links")
async def sos_resend_links(telegram_id: int, admin=Depends(get_current_admin)):
    """Generate new invite links and send to customer via DM."""
    # Find user
    user = await pool.fetchrow(
        "SELECT id, telegram_id, first_name FROM users WHERE telegram_id = $1", telegram_id
    )
    if not user:
        raise HTTPException(404, "ไม่พบลูกค้าในระบบ")

    # Find active subscription
    sub = await pool.fetchrow("""
        SELECT s.package_id FROM subscriptions s
        WHERE s.user_id = $1 AND s.status = 'ACTIVE'
        ORDER BY s.end_date DESC LIMIT 1
    """, user["id"])

    if not sub:
        raise HTTPException(400, "ลูกค้าไม่มี subscription ที่ active อยู่")

    if not GUARDIAN_BOT_TOKEN:
        raise HTTPException(500, "Guardian Bot Token ไม่ได้ตั้งค่า")

    # Get package groups_access
    pkg = await pool.fetchrow("SELECT groups_access FROM packages WHERE id = $1", sub["package_id"])
    if not pkg or not pkg["groups_access"]:
        raise HTTPException(400, "แพ็กเกจไม่มีกลุ่ม")

    # Parse groups_access
    import json as _json
    raw = pkg["groups_access"].strip()
    if raw.startswith("["):
        try:
            _group_slugs = _json.loads(raw)
        except Exception:
            _group_slugs = [g.strip().strip('"') for g in raw.split(",") if g.strip()]
    else:
        _group_slugs = [g.strip().strip('"') for g in raw.split(",") if g.strip()]

    # Generate invite links
    invite_links = {}
    links_buttons = []
    for slug in _group_slugs:
        grp = await pool.fetchrow(
            "SELECT chat_id, title FROM group_registry WHERE slug = $1", slug
        )
        if not grp or not grp["chat_id"]:
            continue
        try:
            result = await _telegram_api(GUARDIAN_BOT_TOKEN, "createChatInviteLink", {
                "chat_id": grp["chat_id"],
                "member_limit": 1,
                "name": f"SOS resend dashboard",
            })
            if result.get("ok"):
                link = result["result"]["invite_link"]
                invite_links[slug] = link
                links_buttons.append([{"text": f"🚀 {grp['title']}", "url": link}])
        except Exception as exc:
            logger.warning("SOS invite link error for %s: %s", slug, exc)

    if not invite_links:
        raise HTTPException(500, "สร้างลิงก์ไม่สำเร็จ")

    # Send DM via Sales Bot
    dm_sent = False
    if SALES_BOT_TOKEN:
        try:
            result = await _telegram_api(SALES_BOT_TOKEN, "sendMessage", {
                "chat_id": telegram_id,
                "text": "🔄 <b>ส่งลิงก์เข้ากลุ่มให้ใหม่แล้วค่า</b>\nกดเข้าได้เลยนะ 👇",
                "parse_mode": "HTML",
                "reply_markup": {"inline_keyboard": links_buttons},
            })
            dm_sent = result.get("ok", False)
        except Exception as exc:
            logger.error("SOS DM send failed: %s", exc)

    # Mark SOS as resolved
    admin_name = admin.get("display_name", "Dashboard")
    await pool.execute("""
        UPDATE sos_alerts SET status = 'RESOLVED', resolved_by = $1, resolved_at = NOW()
        WHERE telegram_id = $2 AND status = 'PENDING'
    """, admin_name, telegram_id)

    return {
        "success": True,
        "dm_sent": dm_sent,
        "links_count": len(invite_links),
        "groups": list(invite_links.keys()),
    }
