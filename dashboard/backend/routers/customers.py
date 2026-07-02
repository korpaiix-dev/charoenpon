"""Customer management router."""
from fastapi import APIRouter, Depends, Request, HTTPException, UploadFile, File, Form
from ..auth.dependencies import get_current_admin, require_role
from ..database import pool
from ..models.schemas import ExtendRequest, UpgradeRequest, KickRequest, BanRequest, DMRequest
from ..services.telegram import send_dm as tg_send_dm, kick_member
from ..config import SALES_BOT_TOKEN
import json
import asyncio
import httpx
import io
from pydantic import BaseModel
from typing import Optional

router = APIRouter(prefix="/api/customers", tags=["customers"])

async def _log(admin_id, action, entity_type, entity_id, details, ip):
    await pool.execute(
        "INSERT INTO dashboard_activity_log (admin_id, action, entity_type, entity_id, details, ip_address) VALUES ($1,$2,$3,$4,$5::jsonb,$6)",
        admin_id, action, entity_type, entity_id, json.dumps(details) if details else None, ip
    )

@router.get("")
async def list_customers(
    page: int = 1, per_page: int = 25, search: str = "", status: str = "all",
    admin=Depends(get_current_admin)
):
    # FIX 2025-05-21 (Phase D-6-business): clamp pagination
    per_page = max(1, min(per_page, 100))
    page = max(1, page)
    offset = (page - 1) * per_page
    conditions = []
    params = []
    idx = 1

    if search:
        conditions.append(f"(u.username ILIKE ${idx} OR u.first_name ILIKE ${idx} OR u.last_name ILIKE ${idx} OR (COALESCE(u.first_name,'') || ' ' || COALESCE(u.last_name,'')) ILIKE ${idx} OR CAST(u.telegram_id AS TEXT) LIKE ${idx})")
        params.append(f"%{search}%")
        idx += 1

    if status == "active":
        conditions.append("EXISTS (SELECT 1 FROM subscriptions s WHERE s.user_id = u.id AND s.status = 'ACTIVE')")
    elif status == "expired":
        conditions.append("NOT EXISTS (SELECT 1 FROM subscriptions s WHERE s.user_id = u.id AND s.status = 'ACTIVE')")
    elif status == "banned":
        conditions.append("u.is_banned = TRUE")

    where = "WHERE " + " AND ".join(conditions) if conditions else ""
    
    total = await pool.fetchval(f"SELECT COUNT(*) FROM users u {where}", *params)
    
    rows = await pool.fetch(f"""
        SELECT u.id, u.telegram_id, u.username, u.first_name, u.last_name, u.is_banned,
               GREATEST(COALESCE(u.total_spent, 0), COALESCE(pay.paid_total, 0)) AS total_spent,
               u.created_at,
               s.status as sub_status, s.end_date, p.name as package_name, p.tier as package_tier
        FROM users u
        LEFT JOIN LATERAL (
            SELECT * FROM subscriptions WHERE user_id = u.id ORDER BY created_at DESC LIMIT 1
        ) s ON TRUE
        LEFT JOIN packages p ON s.package_id = p.id
        LEFT JOIN LATERAL (
            SELECT COALESCE(SUM(amount), 0) AS paid_total
            FROM payments WHERE user_id = u.id AND status = 'CONFIRMED'
        ) pay ON TRUE
        {where}
        ORDER BY u.created_at DESC
        LIMIT {per_page} OFFSET {offset}
    """, *params)

    return {
        "items": [dict(r) for r in rows],
        "total": total,
        "page": page,
        "per_page": per_page,
        "pages": (total + per_page - 1) // per_page,
    }

# ========== BROADCAST (must be before /{user_id} routes) ==========
class BroadcastRequest(BaseModel):
    message: str
    target: str = "all"  # all | active | expired | trial
    parse_mode: Optional[str] = "HTML"



# --- Broadcast helpers ----------------------------------------------------
async def _get_broadcast_users(target: str) -> list[int]:
    """Return list of telegram_id for the given target.
    Always excludes banned and blocked-bot users."""
    base = "FROM users u WHERE NOT u.is_banned AND NOT COALESCE(u.is_blocked_bot, FALSE)"
    if target == "all":
        sql = f"SELECT u.telegram_id {base}"
    elif target == "active":
        sql = f"SELECT u.telegram_id {base} AND EXISTS (SELECT 1 FROM subscriptions s WHERE s.user_id = u.id AND s.status = 'ACTIVE')"
    elif target == "expired":
        sql = f"SELECT u.telegram_id {base} AND EXISTS (SELECT 1 FROM subscriptions s WHERE s.user_id = u.id) AND NOT EXISTS (SELECT 1 FROM subscriptions s WHERE s.user_id = u.id AND s.status = 'ACTIVE')"
    elif target == "trial":
        sql = f"SELECT u.telegram_id {base} AND NOT EXISTS (SELECT 1 FROM subscriptions s WHERE s.user_id = u.id)"
    else:
        return []
    rows = await pool.fetch(sql)
    return [int(r["telegram_id"]) for r in rows if r["telegram_id"]]


async def _get_broadcast_count(target: str) -> int:
    """Cheap COUNT(*) version (no list materialization)."""
    base = "FROM users u WHERE NOT u.is_banned AND NOT COALESCE(u.is_blocked_bot, FALSE)"
    if target == "all":
        sql = f"SELECT COUNT(*) {base}"
    elif target == "active":
        sql = f"SELECT COUNT(*) {base} AND EXISTS (SELECT 1 FROM subscriptions s WHERE s.user_id = u.id AND s.status = 'ACTIVE')"
    elif target == "expired":
        sql = f"SELECT COUNT(*) {base} AND EXISTS (SELECT 1 FROM subscriptions s WHERE s.user_id = u.id) AND NOT EXISTS (SELECT 1 FROM subscriptions s WHERE s.user_id = u.id AND s.status = 'ACTIVE')"
    elif target == "trial":
        sql = f"SELECT COUNT(*) {base} AND NOT EXISTS (SELECT 1 FROM subscriptions s WHERE s.user_id = u.id)"
    else:
        return 0
    return int(await pool.fetchval(sql) or 0)


@router.get("/{user_id}/intents")
async def get_user_intents(user_id: int, admin=Depends(get_current_admin)):
    """List purchase_intents for a customer (pending + history) — Dashboard Customer 360."""
    rows = await pool.fetch("""
        SELECT pi.id, pi.tier, pi.original_price, pi.final_price, pi.promo_id,
               pi.source, pi.created_at, pi.expires_at, pi.consumed_at,
               pi.consumed_payment_id,
               p.code AS promo_code, p.name AS promo_name
        FROM purchase_intents pi
        LEFT JOIN promotions p ON p.id = pi.promo_id
        WHERE pi.user_telegram_id = (SELECT telegram_id FROM users WHERE id = $1)
        ORDER BY pi.created_at DESC
        LIMIT 50
    """, user_id)
    items = []
    for r in rows:
        items.append({
            "id": r["id"],
            "tier": r["tier"],
            "original_price": float(r["original_price"] or 0),
            "final_price": float(r["final_price"] or 0),
            "promo_id": r["promo_id"],
            "promo_code": r["promo_code"],
            "promo_name": r["promo_name"],
            "source": r["source"],
            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            "expires_at": r["expires_at"].isoformat() if r["expires_at"] else None,
            "consumed_at": r["consumed_at"].isoformat() if r["consumed_at"] else None,
            "consumed_payment_id": r["consumed_payment_id"],
        })
    return {"items": items, "total": len(items)}


@router.get("/broadcast/count")
async def broadcast_count(target: str = "all", admin=Depends(require_role("admin"))):
    """Get count of users that would receive the broadcast."""
    count = await _get_broadcast_count(target)
    return {"count": count}


# FIX 2025-05-21 (Phase D-3-business): replaced synchronous HTTP loop with enqueue into
# broadcasts table — picked up by `broadcast_worker` container (uses postgres SKIP LOCKED).
# เดิม: ยิง HTTP 11k คน sync (18 นาที) — timeout, retry แล้วยิงซ้ำ.
# ใหม่: 1 INSERT → return 202-ish ทันที, worker จัดการต่อ.
import base64 as _base64

@router.post("/broadcast")
async def broadcast_message(
    request: Request,
    message: str = Form(...),
    target: str = Form("all"),
    parse_mode: str = Form("HTML"),
    media: UploadFile | None = File(None),
    buttons: str | None = Form(None, description="JSON array of {text,url}"),
    admin=Depends(require_role("admin")),
):
    """Enqueue a broadcast into the `broadcasts` queue — picked up by broadcast_worker."""
    if not message.strip():
        raise HTTPException(400, "Message cannot be empty")

    # Validate target — must match _get_broadcast_users() keys
    if target not in {"all", "active", "expired", "trial"}:
        raise HTTPException(400, f"Invalid target: {target}")

    # Optional media — read chunked, validate size, store as base64 (worker decodes)
    media_b64 = None
    media_type = None
    if media is not None:
        media_bytes = b""
        try:
            chunk = await media.read(1024 * 1024)
            while chunk:
                media_bytes += chunk
                if len(media_bytes) > 20 * 1024 * 1024:
                    raise HTTPException(400, "ไฟล์ใหญ่เกิน 20MB")
                chunk = await media.read(1024 * 1024)
        finally:
            await media.close()
        if media_bytes:
            ct = (media.content_type or "").lower()
            if ct.startswith("image/"):
                media_type = "photo"
            elif ct.startswith("video/"):
                media_type = "video"
            else:
                raise HTTPException(400, f"ไม่รองรับไฟล์ประเภท {ct} (รองรับ image/*, video/*)")
            media_b64 = _base64.b64encode(media_bytes).decode("ascii")

    # Resolve target user IDs (set) — re-use existing helper
    user_ids = await _get_broadcast_users(target)
    # helper already returns list[int] of telegram_ids — no further mapping needed
    if not user_ids:
        raise HTTPException(400, "ไม่มีผู้รับ — ยกเลิก broadcast")

    # Parse inline buttons (optional) — JSON array of {text, url}
    inline_buttons_json = None
    if buttons:
        try:
            btn_list = json.loads(buttons)
            if isinstance(btn_list, list) and btn_list:
                clean = []
                for b in btn_list[:10]:
                    txt = (b.get("text") or "").strip()[:64]
                    url = (b.get("url") or "").strip()[:256]
                    if txt and url and (url.startswith("http://") or url.startswith("https://") or url.startswith("tg://")):
                        clean.append({"text": txt, "url": url})
                if clean:
                    inline_buttons_json = json.dumps(clean)
        except Exception:
            pass

    # Enqueue — broadcast_worker container will pick this row up via SELECT ... FOR UPDATE SKIP LOCKED
    broadcast_id = await pool.fetchval("""
        INSERT INTO broadcasts (
            message_text, message_photo_id, target_type, target_value,
            total_count, sent_by, sent_by_username, status, target_user_ids,
            started_at, parse_mode, media_type, media_b64, inline_buttons
        )
        VALUES ($1, NULL, $2, $2, $3, $4, $5, 'PENDING', $6::jsonb,
                NOW(), $7, $8, $9, $10::jsonb)
        RETURNING id
    """,
        message, target, len(user_ids),
        admin["id"], admin.get("display_name") or admin.get("username"),
        json.dumps(user_ids), parse_mode, media_type, media_b64,
        inline_buttons_json,
    )

    ip = request.client.host if request.client else None
    await _log(
        admin["id"], "broadcast_enqueue", "broadcast", broadcast_id,
        {
            "target": target, "total": len(user_ids),
            "message_preview": message[:100],
            "media_type": media_type,
        },
        ip,
    )

    return {
        "ok": True,
        "queued": True,
        "broadcast_id": broadcast_id,
        "total": len(user_ids),
        "eta_minutes": max(1, len(user_ids) // 1200),
    }


# ========== BROADCAST HISTORY ==========
@router.get("/broadcast/history")
async def broadcast_history(page: int = 1, per_page: int = 25, admin=Depends(get_current_admin)):
    """Get broadcast history from broadcast_log table."""
    # FIX 2025-05-21 (Phase D-6-business): clamp pagination
    per_page = max(1, min(per_page, 100))
    page = max(1, page)
    offset = (page - 1) * per_page
    # FIX: read from broadcasts (the real table all writers use); broadcast_log has 0 writers
    total = await pool.fetchval("SELECT COUNT(*) FROM broadcasts")
    rows = await pool.fetch("""
        SELECT b.*, b.started_at AS created_at, b.sent_by_username AS admin_name
        FROM broadcasts b
        ORDER BY b.started_at DESC
        LIMIT $1 OFFSET $2
    """, per_page, offset)
    return {
        "items": [dict(r) for r in rows],
        "total": total,
        "page": page,
        "per_page": per_page,
        "pages": (total + per_page - 1) // per_page,
    }


# ========== CUSTOMER DETAIL ==========
@router.get("/{user_id}")
async def get_customer(user_id: int, admin=Depends(get_current_admin)):
    row = await pool.fetchrow("SELECT * FROM users WHERE id = $1", user_id)
    if not row:
        raise HTTPException(404, "User not found")
    
    sub = await pool.fetchrow("""
        SELECT s.*, p.name as package_name, p.tier 
        FROM subscriptions s JOIN packages p ON s.package_id = p.id
        WHERE s.user_id = $1 AND s.status = 'ACTIVE' LIMIT 1
    """, user_id)
    
    return {
        "user": dict(row),
        "subscription": dict(sub) if sub else None,
    }

@router.get("/{user_id}/payments")
async def customer_payments(user_id: int, admin=Depends(get_current_admin)):
    rows = await pool.fetch("""
        SELECT p.*, pk.name as package_name FROM payments p
        JOIN packages pk ON p.package_id = pk.id
        WHERE p.user_id = $1 ORDER BY p.created_at DESC LIMIT 50
    """, user_id)
    return [dict(r) for r in rows]

@router.get("/{user_id}/subscriptions")
async def customer_subscriptions(user_id: int, admin=Depends(get_current_admin)):
    """DAY 0: returns subscriptions with promotion + payment info (if any)."""
    rows = await pool.fetch("""
        SELECT s.*, p.name AS package_name, p.tier::text AS tier,
               pay.amount AS amount_paid,
               promo.code AS promo_code, promo.name AS promo_name,
               promo.discount_type AS promo_discount_type,
               promo.discount_value AS promo_discount_value
        FROM subscriptions s
        JOIN packages p ON s.package_id = p.id
        LEFT JOIN payments pay ON pay.id = s.payment_id
        LEFT JOIN promotion_clicks pc ON pc.consumed_payment_id = s.payment_id
        LEFT JOIN promotions promo ON promo.id = pc.promotion_id
        WHERE s.user_id = $1 ORDER BY s.created_at DESC LIMIT 50
    """, user_id)
    return [dict(r) for r in rows]

@router.get("/{user_id}/groups")
async def customer_groups(user_id: int, admin=Depends(get_current_admin)):
    # Get user's active subscription tier, then match groups
    sub = await pool.fetchrow("""
        SELECT p.groups_access FROM subscriptions s
        JOIN packages p ON s.package_id = p.id
        WHERE s.user_id = $1 AND s.status = 'ACTIVE' LIMIT 1
    """, user_id)
    if not sub:
        return []
    
    try:
        group_slugs = json.loads(sub["groups_access"]) if isinstance(sub["groups_access"], str) else sub["groups_access"]
    except Exception:
        group_slugs = []
    
    if not group_slugs:
        return []
    
    # Use ANY array for enum type
    rows = await pool.fetch("""
        SELECT * FROM group_registry WHERE slug = ANY($1::groupslug[]) AND is_active = TRUE
    """, group_slugs)
    return [dict(r) for r in rows]

@router.post("/{user_id}/extend")
async def extend_subscription(user_id: int, req: ExtendRequest, request: Request, admin=Depends(require_role("admin"))):
    sub = await pool.fetchrow(
        "SELECT * FROM subscriptions WHERE user_id = $1 AND status = 'ACTIVE' ORDER BY end_date DESC LIMIT 1", user_id
    )
    if not sub:
        raise HTTPException(400, "No active subscription")
    
    # FIX 2025-05-21 (Phase D-4-business): ขยายจาก GREATEST(end_date, NOW()) — ป้องกัน sub ที่ end_date หมดอายุไปแล้ว
    # ถูกขยายจากอดีต (ทำให้ลูกค้าได้วันน้อยกว่าที่จ่าย)
    await pool.execute("""
        UPDATE subscriptions
           SET end_date = GREATEST(end_date, NOW()) + ($1 || ' days')::interval,
               updated_at = NOW()
         WHERE id = $2
    """, str(req.days), sub["id"])
    ip = request.client.host if request.client else None
    await _log(admin["id"], "extend_subscription", "user", user_id, {"days": req.days}, ip)
    return {"ok": True, "message": f"Extended {req.days} days"}

@router.post("/{user_id}/upgrade")
async def upgrade_subscription(user_id: int, req: UpgradeRequest, request: Request, admin=Depends(require_role("admin"))):
    pkg = await pool.fetchrow("SELECT * FROM packages WHERE id = $1", req.package_id)
    if not pkg:
        raise HTTPException(400, "Package not found")
    
    sub = await pool.fetchrow(
        "SELECT * FROM subscriptions WHERE user_id = $1 AND status = 'ACTIVE' LIMIT 1", user_id
    )
    if sub:
        await pool.execute("UPDATE subscriptions SET status = 'CANCELLED', updated_at = NOW() WHERE id = $1", sub["id"])
    
    await pool.execute("""
        INSERT INTO subscriptions (user_id, package_id, status, start_date, end_date, auto_renew)
        VALUES ($1, $2, 'ACTIVE', NOW(), NOW() + ($3 || ' days')::interval, FALSE)
    """, user_id, req.package_id, str(pkg["duration_days"]))
    
    ip = request.client.host if request.client else None
    await _log(admin["id"], "upgrade_subscription", "user", user_id, {"package_id": req.package_id, "package": pkg["name"]}, ip)
    return {"ok": True, "message": f"Upgraded to {pkg['name']}"}

@router.post("/{user_id}/kick")
async def kick_user(user_id: int, req: KickRequest, request: Request, admin=Depends(require_role("admin"))):
    user = await pool.fetchrow("SELECT telegram_id FROM users WHERE id = $1", user_id)
    if not user:
        raise HTTPException(404, "User not found")
    
    # FIX 2025-05-21 (Phase D-10-business): fetch all groups in one query (was N+1)
    results = []
    if req.group_ids:
        groups = await pool.fetch(
            "SELECT id, chat_id, title FROM group_registry WHERE id = ANY($1::int[])",
            req.group_ids,
        )
        groups_by_id = {g["id"]: g for g in groups}
        for gid in req.group_ids:
            group = groups_by_id.get(gid)
            if group:
                result = await kick_member(group["chat_id"], user["telegram_id"])
                results.append({"group": group["title"], "result": result})
    
    ip = request.client.host if request.client else None
    await _log(admin["id"], "kick_user", "user", user_id, {"groups": req.group_ids}, ip)
    return {"ok": True, "results": results}

@router.post("/{user_id}/ban")
async def ban_user(user_id: int, req: BanRequest, request: Request, admin=Depends(require_role("admin"))):
    await pool.execute("UPDATE users SET is_banned = TRUE, updated_at = NOW() WHERE id = $1", user_id)
    
    # Cancel active subs
    await pool.execute("UPDATE subscriptions SET status = 'CANCELLED', updated_at = NOW() WHERE user_id = $1 AND status = 'ACTIVE'", user_id)
    
    # Kick from all groups
    user = await pool.fetchrow("SELECT telegram_id FROM users WHERE id = $1", user_id)
    groups = await pool.fetch("SELECT chat_id FROM group_registry WHERE is_active = TRUE")
    for g in groups:
        await kick_member(g["chat_id"], user["telegram_id"])
    
    ip = request.client.host if request.client else None
    await _log(admin["id"], "ban_user", "user", user_id, {"reason": req.reason}, ip)
    return {"ok": True}

@router.post("/{user_id}/unban")
async def unban_user(user_id: int, request: Request, admin=Depends(require_role("admin"))):
    await pool.execute("UPDATE users SET is_banned = FALSE, updated_at = NOW() WHERE id = $1", user_id)
    ip = request.client.host if request.client else None
    await _log(admin["id"], "unban_user", "user", user_id, None, ip)
    return {"ok": True}

@router.post("/{user_id}/dm")
async def dm_customer(user_id: int, req: DMRequest, request: Request, admin=Depends(require_role("admin"))):
    user = await pool.fetchrow("SELECT telegram_id FROM users WHERE id = $1", user_id)
    if not user:
        raise HTTPException(404, "User not found")
    
    result = await tg_send_dm(user["telegram_id"], req.message)
    ip = request.client.host if request.client else None
    await _log(admin["id"], "send_dm", "user", user_id, {"message_preview": req.message[:100]}, ip)
    return {"ok": True, "result": result}

@router.get("/{user_id}/timeline")
async def get_customer_timeline(user_id: int, admin=Depends(require_role("moderator"))):
    """Unified customer timeline — all events in one chronological list.

    Sources:
      - payments (PENDING / CONFIRMED / REJECTED)
      - subscriptions (created)
      - admin_logs (rank_up, kick_expired, ban, unban, manual approves)
      - marketing_invite_joins (attribution event)
      - sos_alerts
      - gachapon_pulls (top-tier rewards)
    """
    # First confirm user exists + get telegram_id
    user_row = await pool.fetchrow(
        "SELECT id, telegram_id, first_name, last_name, username FROM users WHERE id = $1",
        user_id,
    )
    if not user_row:
        raise HTTPException(404, "user not found")
    tg_id = user_row["telegram_id"]

    events = []

    # Payments
    pay_rows = await pool.fetch("""
        SELECT p.id, p.amount, p.status::text AS status, p.method::text AS method,
               p.created_at, p.verified_at, p.auto_approved, p.reject_reason,
               pk.name AS package_name
        FROM payments p
        LEFT JOIN packages pk ON pk.id = p.package_id
        WHERE p.user_id = $1
        ORDER BY p.created_at DESC
        LIMIT 50
    """, user_id)
    for r in pay_rows:
        if r["status"] == "CONFIRMED":
            events.append({
                "type": "payment_confirmed",
                "icon": "💰",
                "color": "success",
                "at": r["verified_at"].isoformat() if r["verified_at"] else r["created_at"].isoformat(),
                "title": f"จ่ายเงิน ฿{float(r['amount'] or 0):,.0f}",
                "subtitle": f"{r['package_name'] or '?'} · {r['method']}{' (auto)' if r['auto_approved'] else ' (manual)'}",
                "ref_id": r["id"],
                "ref_type": "payment",
            })
        elif r["status"] == "REJECTED":
            events.append({
                "type": "payment_rejected",
                "icon": "❌",
                "color": "error",
                "at": r["created_at"].isoformat(),
                "title": f"Payment ถูก reject ฿{float(r['amount'] or 0):,.0f}",
                "subtitle": (r["reject_reason"] or "no reason")[:120],
                "ref_id": r["id"],
                "ref_type": "payment",
            })
        else:  # PENDING/HOLD
            events.append({
                "type": "payment_pending",
                "icon": "⏳",
                "color": "warning",
                "at": r["created_at"].isoformat(),
                "title": f"ส่งสลิป ฿{float(r['amount'] or 0):,.0f} รออนุมัติ",
                "subtitle": r["package_name"] or "?",
                "ref_id": r["id"],
                "ref_type": "payment",
            })

    # Subscriptions
    sub_rows = await pool.fetch("""
        SELECT s.id, s.status::text AS status, s.created_at, s.start_date, s.end_date,
               pk.name AS package_name, pk.tier::text AS tier
        FROM subscriptions s
        LEFT JOIN packages pk ON pk.id = s.package_id
        WHERE s.user_id = $1
        ORDER BY s.created_at DESC
        LIMIT 20
    """, user_id)
    for r in sub_rows:
        events.append({
            "type": "subscription_created",
            "icon": "📋",
            "color": "info",
            "at": r["created_at"].isoformat(),
            "title": f"เริ่มสมาชิก {r['package_name'] or '?'}",
            "subtitle": f"{r['tier'] or ''} · ถึง {r['end_date'].strftime('%d %b %Y')}" if r["end_date"] else r["tier"],
            "ref_id": r["id"],
            "ref_type": "subscription",
        })

    # Admin logs (rank up, kick, ban, manual approve etc) by tg_id OR by user.id
    log_rows = await pool.fetch("""
        SELECT id, admin_id, action, target_type, target_id, details, created_at
        FROM admin_logs
        WHERE (target_id = $1 OR target_id = $2)
        ORDER BY created_at DESC
        LIMIT 50
    """, user_id, tg_id)
    for r in log_rows:
        action = r["action"]
        details = r["details"] or ""
        # Skip self-payment events (they show in payments section)
        if action in ("payment_approved_backfill_admin", "approve_by_price"):
            continue
        # Pretty labels
        if action == "loyalty_rank_up_v2":
            events.append({
                "type": "loyalty_rank_up",
                "icon": "🏆",
                "color": "success",
                "at": r["created_at"].isoformat(),
                "title": "เลื่อนยศ Loyalty",
                "subtitle": (details if isinstance(details, str) else str(details))[:120],
                "ref_id": r["id"],
            })
        elif action == "kick_expired":
            events.append({
                "type": "kicked",
                "icon": "🚪",
                "color": "warning",
                "at": r["created_at"].isoformat(),
                "title": "ถูกเตะจากกลุ่ม (sub expired)",
                "subtitle": (details if isinstance(details, str) else str(details))[:120],
                "ref_id": r["id"],
            })
        elif action == "gacha_reward_delivered":
            events.append({
                "type": "gacha_reward",
                "icon": "🎰",
                "color": "success",
                "at": r["created_at"].isoformat(),
                "title": "ได้รางวัลจากกาชา",
                "subtitle": (details if isinstance(details, str) else str(details))[:120],
                "ref_id": r["id"],
            })
        elif action == "create_one_time_invite":
            events.append({
                "type": "invite_created",
                "icon": "🔗",
                "color": "info",
                "at": r["created_at"].isoformat(),
                "title": "สร้างลิ้งเข้ากลุ่ม",
                "subtitle": (details if isinstance(details, str) else str(details))[:120],
                "ref_id": r["id"],
            })
        elif "ban" in action:
            events.append({
                "type": action,
                "icon": "🚫" if "unban" not in action else "🔓",
                "color": "error" if "unban" not in action else "info",
                "at": r["created_at"].isoformat(),
                "title": "ถูกแบน" if "unban" not in action else "ปลดแบน",
                "subtitle": (details if isinstance(details, str) else str(details))[:120],
                "ref_id": r["id"],
            })
        else:
            events.append({
                "type": action,
                "icon": "📝",
                "color": "default",
                "at": r["created_at"].isoformat(),
                "title": action.replace("_", " ").title(),
                "subtitle": (details if isinstance(details, str) else str(details))[:120],
                "ref_id": r["id"],
            })

    # Marketing attribution (first event when user joined via mkt_ link)
    mkt_rows = await pool.fetch("""
        SELECT j.id, j.joined_at, l.marketer, l.platform, l.link_type, l.group_slug::text AS group_slug
        FROM marketing_invite_joins j
        LEFT JOIN marketing_invite_links l ON l.id = j.link_id
        WHERE j.telegram_id = $1
    """, tg_id)
    for r in mkt_rows:
        events.append({
            "type": "marketing_attribution",
            "icon": "🎯",
            "color": "info",
            "at": r["joined_at"].isoformat(),
            "title": f"เข้าระบบผ่าน {r['marketer']} / {r['platform']}",
            "subtitle": f"link_type={r['link_type']} group={r['group_slug']}",
            "ref_id": r["id"],
        })

    # SOS alerts
    sos_rows = await pool.fetch("""
        SELECT id, message, status, ai_status, ai_detail, created_at, resolved_at, resolved_by
        FROM sos_alerts
        WHERE telegram_id = $1
        ORDER BY created_at DESC
        LIMIT 20
    """, tg_id)
    for r in sos_rows:
        events.append({
            "type": "sos_alert",
            "icon": "🆘",
            "color": "error" if r["status"] == "PENDING" else "default",
            "at": r["created_at"].isoformat(),
            "title": f"SOS ticket ({r['status']})",
            "subtitle": (r["message"] or "")[:120],
            "ref_id": r["id"],
        })

    # User registration as oldest event
    events.append({
        "type": "registered",
        "icon": "🆕",
        "color": "info",
        "at": user_row["telegram_id"] and (await pool.fetchval("SELECT created_at FROM users WHERE id=$1", user_id)).isoformat(),
        "title": "สมัครเข้าระบบ",
        "subtitle": f"ชื่อ: {user_row['first_name'] or '?'} {user_row['last_name'] or ''}",
    })

    # Sort newest first
    events.sort(key=lambda e: e["at"], reverse=True)

    return {
        "user_id": user_id,
        "telegram_id": tg_id,
        "total_events": len(events),
        "events": events,
    }


# ====== Sprint 2.6: Subscription manipulation ======
from pydantic import BaseModel as _BM2

class _SubCancelReq(_BM2):
    reason: str = ''
    refund_kept_days: bool = False

@router.post('/{user_id}/cancel-sub')
async def cancel_subscription(user_id: int, req: _SubCancelReq, admin=Depends(require_role("admin"))):
    """Cancel active subscription. Optionally keep used days (no refund) or full revoke."""
    row = await pool.fetchrow(
        "UPDATE subscriptions SET status='EXPIRED', end_date=NOW(), updated_at=NOW() "
        "WHERE user_id=$1 AND status='ACTIVE' RETURNING id, package_id",
        user_id,
    )
    if not row:
        raise HTTPException(404, 'no active subscription found')
    try:
        await pool.execute(
            "INSERT INTO admin_logs (admin_id, action, target_type, target_id, details) "
            "VALUES ($1, 'subscription_cancel', 'subscription', $2, $3)",
            admin['telegram_id'], row['id'],
            f"user_id={user_id} reason={req.reason[:200]} kept_days={req.refund_kept_days}"
        )
    except Exception:
        pass
    return {'ok': True, 'subscription_id': row['id']}


@router.post('/{user_id}/reactivate-sub')
async def reactivate_subscription(user_id: int, admin=Depends(require_role("admin"))):
    """Reactivate latest EXPIRED sub if end_date >= NOW() (revert cancel)."""
    row = await pool.fetchrow(
        """UPDATE subscriptions SET status='ACTIVE', updated_at=NOW()
            WHERE id = (
                SELECT id FROM subscriptions
                WHERE user_id=$1 AND status='EXPIRED'
                ORDER BY updated_at DESC LIMIT 1
            )
            AND end_date > NOW()
            RETURNING id, end_date""",
        user_id,
    )
    if not row:
        raise HTTPException(400, 'no recent EXPIRED sub with future end_date to reactivate')
    try:
        await pool.execute(
            "INSERT INTO admin_logs (admin_id, action, target_type, target_id, details) "
            "VALUES ($1, 'subscription_reactivate', 'subscription', $2, $3)",
            admin['telegram_id'], row['id'],
            f"user_id={user_id} end_date={row['end_date']}"
        )
    except Exception:
        pass
    return {'ok': True, 'subscription_id': row['id']}


class _GiftSubReq(_BM2):
    package_id: int
    days: int = 30
    reason: str = ''

@router.post('/{user_id}/gift-sub')
async def gift_subscription(user_id: int, req: _GiftSubReq, admin=Depends(require_role("admin"))):
    """Grant a free subscription (no payment_id) — used for compensation / gift.

    FIX 2026-06-29 (#445): trigger downstream — เติม credits / invite links / DM
    เดิม: แค่ INSERT subscription + admin_log → ลูกค้าไม่ได้รับอะไรเลย
    """
    import os, json as _j, logging as _logging
    _logger = _logging.getLogger(__name__)

    if req.days < 1 or req.days > 365:
        raise HTTPException(400, 'days must be 1-365')
    # Verify package exists + get tier
    pk = await pool.fetchrow('SELECT id, name, tier::text AS tier FROM packages WHERE id = $1', req.package_id)
    if not pk:
        raise HTTPException(404, 'package not found')
    tier_enum = (pk['tier'] or '').replace('TIER_', '')  # e.g. "GACHA_10" or "300"

    # Get user telegram_id
    user = await pool.fetchrow('SELECT id, telegram_id, first_name FROM users WHERE id = $1', user_id)
    if not user:
        raise HTTPException(404, 'user not found')

    # Create sub starting now
    row = await pool.fetchrow(
        """INSERT INTO subscriptions (user_id, package_id, status, start_date, end_date, auto_renew)
            VALUES ($1, $2, 'ACTIVE', NOW(), NOW() + ($3 || ' days')::interval, false)
            RETURNING id, end_date""",
        user_id, req.package_id, str(req.days),
    )
    try:
        await pool.execute(
            "INSERT INTO admin_logs (admin_id, action, target_type, target_id, details) "
            "VALUES ($1, 'subscription_gift', 'subscription', $2, $3)",
            admin['telegram_id'], row['id'],
            f"user_id={user_id} package={pk['name']} days={req.days} reason={req.reason[:200]}"
        )
    except Exception:
        pass

    # ─── Downstream actions (NEW) ───
    gacha_added = 0
    invite_links = {}
    dm_sent = False

    # 1. GACHA package → add credits
    _GACHA_SPINS = {"GACHA_1": 1, "GACHA_3": 3, "GACHA_10": 10}
    if tier_enum in _GACHA_SPINS and user['telegram_id']:
        try:
            spins = _GACHA_SPINS[tier_enum]
            await pool.execute(
                """INSERT INTO gachapon_credits (user_id, telegram_id, credits, total_purchased)
                   VALUES ($1, $2, $3, $3)
                   ON CONFLICT (user_id) DO UPDATE SET
                     credits = gachapon_credits.credits + $3,
                     total_purchased = gachapon_credits.total_purchased + $3,
                     updated_at = NOW()""",
                user_id, user['telegram_id'], spins,
            )
            gacha_added = spins
            _logger.info(f"gift_sub: added {spins} gacha credits to user {user_id}")
        except Exception as e:
            _logger.warning(f"gift_sub: failed to add gacha credits: {e}")

    # 2. VIP package → generate invite links
    if tier_enum not in _GACHA_SPINS and user['telegram_id']:
        try:
            from .payments import _generate_invite_links
            invite_links = await _generate_invite_links(
                req.package_id, user['telegram_id'], return_titles=True
            )
            _logger.info(f"gift_sub: generated {len(invite_links)} invite links for user {user_id}")
        except Exception as e:
            _logger.warning(f"gift_sub: failed to generate invite links: {e}")

    # 3. DM customer notification (via sales bot)
    if user['telegram_id']:
        try:
            sales_token = os.environ.get('SALES_BOT_TOKEN') or os.environ.get('BOT_TOKEN')
            if sales_token:
                import httpx
                first_name = user['first_name'] or 'ลูกค้า'
                lines = [
                    f"🎁 <b>คุณได้รับของขวัญจากทีมงาน!</b>",
                    "",
                    f"📦 แพ็คเกจ: <b>{pk['name']}</b>",
                    f"⏰ ระยะเวลา: <b>{req.days} วัน</b>",
                ]
                if gacha_added > 0:
                    lines.append(f"🎰 สิทธิ์หมุนกาชา: <b>+{gacha_added} ครั้ง</b>")
                if req.reason:
                    lines.append(f"📝 หมายเหตุ: <i>{req.reason[:200]}</i>")
                if invite_links:
                    lines.append("")
                    lines.append("🔗 <b>ลิงก์เข้าห้อง VIP:</b>")
                    for slug, info in invite_links.items():
                        if isinstance(info, dict):
                            url = info.get('url', '')
                            title = info.get('title', slug)
                        else:
                            url = info
                            title = slug
                        if url:
                            lines.append(f'• <a href="{url}">{title}</a>')
                lines.append("")
                lines.append("ขอบคุณที่ใช้บริการเจริญพรนะคะ 🙏")
                msg = "\n".join(lines)
                async with httpx.AsyncClient(timeout=20) as cli:
                    r = await cli.post(
                        f"https://api.telegram.org/bot{sales_token}/sendMessage",
                        json={
                            "chat_id": user['telegram_id'],
                            "text": msg,
                            "parse_mode": "HTML",
                            "disable_web_page_preview": True,
                        }
                    )
                    if r.status_code == 200:
                        dm_sent = True
                    else:
                        _logger.warning(f"gift_sub: DM failed status={r.status_code} body={r.text[:200]}")
        except Exception as e:
            _logger.warning(f"gift_sub: DM exception: {e}")

    return {
        'ok': True,
        'subscription_id': row['id'],
        'end_date': row['end_date'].isoformat(),
        'gacha_credits_added': gacha_added,
        'invite_links_count': len(invite_links),
        'dm_sent': dm_sent,
    }


# ====== Customer 360 Sprint 2026-06-26: regen links + group memberships ======

class _RegenLinkReq(BaseModel):
    slugs: list[str] | None = None  # None = all eligible
    message: str | None = None  # None = default


@router.post("/{user_id}/regen-links")
async def regen_invite_links(user_id: int, request: Request, req: _RegenLinkReq | None = None,
                              admin=Depends(require_role("admin"))):
    """Regenerate invite links for customer's active subscription + DM the customer.

    Use case: customer's old link expired / didn't click in time. Send fresh links.

    Optional body: slugs (which groups), message (custom DM text).
    """
    import os, httpx, json
    from .payments import _generate_invite_links, _telegram_api
    from ..database import pool as _pool

    # Get customer info + active sub
    user = await pool.fetchrow("""
        SELECT u.id, u.telegram_id, u.first_name
        FROM users u WHERE u.id = $1
    """, user_id)
    if not user:
        raise HTTPException(404, "Customer not found")
    if not user["telegram_id"]:
        raise HTTPException(400, "Customer has no telegram_id")

    sub = await pool.fetchrow("""
        SELECT s.id, s.package_id, s.end_date, p.name AS package_name
        FROM subscriptions s JOIN packages p ON p.id = s.package_id
        WHERE s.user_id = $1 AND s.status = 'ACTIVE'
        ORDER BY s.end_date DESC LIMIT 1
    """, user_id)
    if not sub:
        raise HTTPException(400, "Customer has no active subscription")

    # Generate links
    invite_links_full = await _generate_invite_links(
        sub["package_id"], user["telegram_id"], return_titles=True
    )
    if not invite_links_full:
        raise HTTPException(500, "Failed to generate any invite links")

    # Filter by selected slugs if provided
    if req and req.slugs:
        invite_links_full = {s: v for s, v in invite_links_full.items() if s in req.slugs}
        if not invite_links_full:
            raise HTTPException(400, "No matching groups in selection")

    # Build DM message
    links_list = []
    for slug, info in invite_links_full.items():
        links_list.append({"text": f"🚀 {info['title']}", "url": info["url"]})

    end_date_str = sub["end_date"].strftime("%d/%m/%Y") if sub["end_date"] else "—"
    msg = (
        f"🔄 <b>ลิงก์เข้ากลุ่มใหม่</b>\n"
        f"📦 แพ็กเกจ: {sub['package_name']}\n"
        f"📅 หมดอายุ: {end_date_str}\n\n"
        f"👇 กดปุ่มด้านล่างเข้ากลุ่ม"
    )
    keyboard_rows = []
    for i in range(0, len(links_list), 2):
        row = [{"text": b["text"], "url": b["url"]} for b in links_list[i:i+2]]
        keyboard_rows.append(row)
    payload = {
        "chat_id": user["telegram_id"],
        "text": msg,
        "parse_mode": "HTML",
    }
    if keyboard_rows:
        payload["reply_markup"] = {"inline_keyboard": keyboard_rows}

    SALES_BOT_TOKEN = os.environ.get("SALES_BOT_TOKEN") or os.environ.get("NAMWAN_TOKEN")
    dm_ok = False
    dm_error = None
    if SALES_BOT_TOKEN:
        try:
            result = await _telegram_api(SALES_BOT_TOKEN, "sendMessage", payload)
            dm_ok = bool(result.get("ok"))
            if not dm_ok:
                dm_error = result.get("description", "unknown")
        except Exception as exc:
            dm_error = str(exc)[:200]

    # Audit log
    ip = request.client.host if request.client else None
    await _log(admin["id"], "regen_invite_links", "customer", user_id,
               {"package_id": sub["package_id"], "links": len(invite_links_full), "dm_sent": dm_ok}, ip)

    return {
        "ok": True,
        "links": [{"slug": s, "url": info["url"], "title": info["title"]} for s, info in invite_links_full.items()],
        "dm_sent": dm_ok,
        "dm_error": dm_error,
    }


@router.get("/{user_id}/group-memberships")
async def customer_group_memberships(user_id: int, admin=Depends(get_current_admin)):
    """Check which groups (VIP + Free) the customer is currently in via Telegram getChatMember.

    Categorizes:
    - vip_in: VIP groups they belong to
    - vip_should: VIP groups they should be in based on subscription
    - vip_missing: should be in but not
    - free_in: Free groups they belong to
    - other_in: Other groups (chat/announce/etc)
    """
    import os, asyncio, httpx, json

    user = await pool.fetchrow(
        "SELECT id, telegram_id, first_name FROM users WHERE id = $1", user_id
    )
    if not user:
        raise HTTPException(404, "Customer not found")
    if not user["telegram_id"]:
        raise HTTPException(400, "Customer has no telegram_id")

    # Get all groups in registry
    groups = await pool.fetch("""
        SELECT slug::text AS slug, chat_id, title, min_tier::text AS min_tier
        FROM group_registry WHERE is_active = TRUE
        ORDER BY min_tier, slug
    """)

    # Active sub determines which VIP groups customer SHOULD have
    sub = await pool.fetchrow("""
        SELECT p.groups_access FROM subscriptions s
        JOIN packages p ON s.package_id = p.id
        WHERE s.user_id = $1 AND s.status = 'ACTIVE' LIMIT 1
    """, user_id)
    should_have_slugs = set()
    if sub and sub["groups_access"]:
        try:
            raw = sub["groups_access"]
            if isinstance(raw, str):
                if raw.startswith("["):
                    should_have_slugs = set(json.loads(raw))
                else:
                    should_have_slugs = set(s.strip().strip('\"') for s in raw.split(",") if s.strip())
            else:
                should_have_slugs = set(raw)
        except Exception:
            should_have_slugs = set()

    GUARDIAN_BOT_TOKEN = os.environ.get("GUARDIAN_BOT_TOKEN")
    if not GUARDIAN_BOT_TOKEN:
        raise HTTPException(500, "GUARDIAN_BOT_TOKEN not configured")

    # Check membership in each group concurrently
    async def check_one(grp):
        slug = grp["slug"]
        chat_id = grp["chat_id"]
        url = f"https://api.telegram.org/bot{GUARDIAN_BOT_TOKEN}/getChatMember"
        try:
            async with httpx.AsyncClient(timeout=10.0) as cli:
                r = await cli.get(url, params={"chat_id": chat_id, "user_id": user["telegram_id"]})
                data = r.json()
                if data.get("ok"):
                    status = data["result"].get("status", "unknown")
                    is_member = status in ("creator", "administrator", "member", "restricted")
                    return {**dict(grp), "status": status, "is_member": is_member, "error": None}
                else:
                    return {**dict(grp), "status": None, "is_member": False, "error": data.get("description", "unknown")}
        except Exception as exc:
            return {**dict(grp), "status": None, "is_member": False, "error": str(exc)[:120]}

    results = await asyncio.gather(*[check_one(g) for g in groups])

    # Categorize
    vip_in = [r for r in results if r["min_tier"] != "FREE" and r["is_member"]]
    vip_not_in = [r for r in results if r["min_tier"] != "FREE" and not r["is_member"] and not r["error"]]
    free_in = [r for r in results if r["min_tier"] == "FREE" and r["is_member"]]
    errors = [r for r in results if r["error"]]

    # Which VIP groups should they have access to?
    should_groups = [g for g in groups if g["slug"] in should_have_slugs]
    in_slugs = {r["slug"] for r in vip_in}
    missing_vip = [g for g in should_groups if g["slug"] not in in_slugs]

    return {
        "customer": dict(user),
        "vip_in": vip_in,
        "vip_not_in": vip_not_in,
        "vip_should_have": should_have_slugs and list(should_have_slugs),
        "vip_missing": [dict(g) for g in missing_vip],
        "free_in": free_in,
        "errors": errors,
        "total_groups_in": len(vip_in) + len(free_in),
    }


@router.get("/{user_id}/regen-link-options")
async def regen_link_options(user_id: int, admin=Depends(get_current_admin)):
    """Return eligible groups + default message template for regen-link modal."""
    import json

    user = await pool.fetchrow(
        "SELECT u.id, u.telegram_id, u.first_name FROM users u WHERE u.id = $1", user_id
    )
    if not user:
        raise HTTPException(404, "Customer not found")

    sub = await pool.fetchrow("""
        SELECT s.id, s.package_id, s.end_date,
               p.name AS package_name, p.groups_access
        FROM subscriptions s JOIN packages p ON p.id = s.package_id
        WHERE s.user_id = $1 AND s.status = 'ACTIVE'
        ORDER BY s.end_date DESC LIMIT 1
    """, user_id)
    if not sub:
        raise HTTPException(400, "Customer has no active subscription")

    # Parse groups_access
    raw = sub["groups_access"]
    if isinstance(raw, str):
        if raw.startswith("["):
            try:
                group_slugs = json.loads(raw)
            except Exception:
                group_slugs = [g.strip().strip('\"') for g in raw.split(",") if g.strip()]
        else:
            group_slugs = [g.strip().strip('\"') for g in raw.split(",") if g.strip()]
    else:
        group_slugs = list(raw) if raw else []

    # Get group details
    groups = []
    if group_slugs:
        rows = await pool.fetch("""
            SELECT slug::text AS slug, chat_id, title, min_tier::text AS min_tier
            FROM group_registry WHERE slug = ANY($1::groupslug[]) AND is_active = TRUE
            ORDER BY slug
        """, group_slugs)
        groups = [dict(r) for r in rows]

    end_date_str = sub["end_date"].strftime("%d/%m/%Y") if sub["end_date"] else "—"
    default_msg = (
        f"🔄 <b>ลิงก์เข้ากลุ่มใหม่</b>\n"
        f"📦 แพ็กเกจ: {sub['package_name']}\n"
        f"📅 หมดอายุ: {end_date_str}\n\n"
        f"👇 กดปุ่มด้านล่างเข้ากลุ่ม"
    )

    return {
        "customer": dict(user),
        "subscription": {
            "package_id": sub["package_id"],
            "package_name": sub["package_name"],
            "end_date": sub["end_date"].isoformat() if sub["end_date"] else None,
        },
        "groups": groups,
        "default_message": default_msg,
    }


@router.post("/admin/payments/{payment_id}/force-approve")
async def force_approve_payment(payment_id: int, admin=Depends(require_role("admin"))):
    """Force approve a PENDING payment without slip verify (emergency tool — logged)."""
    from sqlalchemy import select
    from shared.payment_approval import apply_payment_approval, ApprovalInput, ApprovalSource
    from shared.models import Payment as _P
    from decimal import Decimal

    # Read payment
    row = await pool.fetchrow("""
        SELECT id, user_id, amount, status, package_id,
               (SELECT telegram_id FROM users WHERE id = payments.user_id) AS tg_id,
               (SELECT tier::text FROM packages WHERE id = payments.package_id) AS tier
        FROM payments WHERE id = $1
    """, payment_id)
    if not row:
        raise HTTPException(404, "payment not found")
    if row["status"] != "PENDING":
        raise HTTPException(400, f"payment status is {row['status']}, must be PENDING")

    # Apply approval
    result = await apply_payment_approval(ApprovalInput(
        user_id=row["user_id"],
        telegram_id=row["tg_id"],
        source=ApprovalSource.ADMIN_BY_PID,
        amount_paid=Decimal(str(row["amount"])),
        explicit_package_id=row["package_id"],
        admin_id=admin["id"],
        method="SLIP",
        skip_dup_check=True,
        skip_sender_ring=True,
    ))
    if not result.success:
        raise HTTPException(500, f"approval failed: {result.error}")

    return {
        "ok": True,
        "payment_id": payment_id,
        "approved_by": admin["id"],
        "subscription_id": getattr(result, "subscription_id", None),
    }


@router.post("/admin/payments/{payment_id}/reset")
async def reset_payment(payment_id: int, admin=Depends(require_role("admin"))):
    """Reset a stuck payment back to PENDING (emergency tool — logged)."""
    row = await pool.fetchrow("SELECT id, status FROM payments WHERE id = $1", payment_id)
    if not row:
        raise HTTPException(404, "payment not found")
    if row["status"] == "PENDING":
        return {"ok": True, "note": "already pending"}
    await pool.execute(
        "UPDATE payments SET status = 'PENDING' WHERE id = $1",
        payment_id,
    )
    await _log(
        admin["id"], "force_reset_payment", "payment", payment_id,
        {"from_status": row["status"], "to_status": "PENDING"}, None,
    )
    return {"ok": True, "payment_id": payment_id, "from_status": row["status"]}



@router.get("/admin/orphan-subs")
async def list_orphan_subs(days: int = 7, admin=Depends(require_role("admin"))):
    """List ACTIVE subscriptions with NULL payment_id — data integrity audit."""
    from shared.orphan_subs_watchdog import find_orphan_subs
    items = await find_orphan_subs(within_days=days)
    total = sum(float(r.get("price") or 0) for r in items)
    return {
        "items": items,
        "count": len(items),
        "total_revenue_missing": total,
        "scope_days": days,
    }
