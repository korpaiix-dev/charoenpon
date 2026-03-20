"""Dashboard home — summary, charts, stats, alerts, SOS."""
from fastapi import APIRouter, Depends, Request, HTTPException
from ..auth.dependencies import get_current_admin, require_role
from ..database import pool
from ..config import GUARDIAN_BOT_TOKEN, SALES_BOT_TOKEN
import json, logging, httpx

logger = logging.getLogger(__name__)

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
    pending_slips = await pool.fetchval(
        "SELECT COUNT(*) FROM payments WHERE status = 'PENDING' AND created_at >= NOW() - interval '24 hours'"
    )
    expiring_today = await pool.fetchval("""
        SELECT COUNT(*) FROM subscriptions WHERE status = 'ACTIVE' AND end_date::date = CURRENT_DATE
    """)
    sos_count = await pool.fetchval(
        "SELECT COUNT(*) FROM sos_alerts WHERE status = 'PENDING'"
    )
    return {
        "pending_slips": pending_slips,
        "pending_payments": pending_slips,  # alias for notification
        "expiring_today": expiring_today,
        "sos_count": sos_count or 0,
    }


# ── SOS Alerts ──

@router.get("/sos-alerts")
async def sos_alerts(admin=Depends(get_current_admin)):
    """Get all pending SOS alerts."""
    rows = await pool.fetch("""
        SELECT id, telegram_id, first_name, username, message, status, resolved_by, resolved_at, created_at
        FROM sos_alerts
        WHERE status = 'PENDING'
        ORDER BY created_at DESC
        LIMIT 50
    """)
    return [dict(r) for r in rows]


async def _telegram_api(token: str, method: str, payload: dict) -> dict:
    """Call Telegram Bot API."""
    url = f"https://api.telegram.org/bot{token}/{method}"
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(url, json=payload)
        return resp.json()


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

    # Get package group list
    pkg = await pool.fetchrow("SELECT group_list FROM packages WHERE id = $1", sub["package_id"])
    if not pkg or not pkg["group_list"]:
        raise HTTPException(400, "แพ็กเกจไม่มีกลุ่ม")

    # Generate invite links
    invite_links = {}
    links_buttons = []
    for slug in pkg["group_list"]:
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
