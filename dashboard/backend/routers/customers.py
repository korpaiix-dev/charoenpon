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
        conditions.append(f"(u.username ILIKE ${idx} OR u.first_name ILIKE ${idx} OR CAST(u.telegram_id AS TEXT) LIKE ${idx})")
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


async def _get_broadcast_count(target: str) -> int:
    if target == "active":
        return await pool.fetchval(
            "SELECT COUNT(DISTINCT u.id) FROM users u JOIN subscriptions s ON s.user_id = u.id "
            "WHERE s.status = 'ACTIVE' AND u.telegram_id IS NOT NULL AND u.is_banned = FALSE"
        )
    elif target == "expired":
        return await pool.fetchval(
            "SELECT COUNT(*) FROM users u WHERE u.telegram_id IS NOT NULL AND u.is_banned = FALSE "
            "AND NOT EXISTS (SELECT 1 FROM subscriptions s WHERE s.user_id = u.id AND s.status = 'ACTIVE') "
            "AND EXISTS (SELECT 1 FROM subscriptions s2 WHERE s2.user_id = u.id)"
        )
    elif target == "trial":
        return await pool.fetchval(
            "SELECT COUNT(DISTINCT u.id) FROM users u JOIN subscriptions s ON s.user_id = u.id "
            "JOIN packages p ON s.package_id = p.id "
            "WHERE s.status = 'ACTIVE' AND p.tier = 'TIER_99' AND u.telegram_id IS NOT NULL AND u.is_banned = FALSE"
        )
    else:  # all
        return await pool.fetchval(
            "SELECT COUNT(*) FROM users u WHERE u.telegram_id IS NOT NULL AND u.is_banned = FALSE"
        )


async def _get_broadcast_users(target: str):
    if target == "active":
        return await pool.fetch(
            "SELECT DISTINCT u.telegram_id FROM users u JOIN subscriptions s ON s.user_id = u.id "
            "WHERE s.status = 'ACTIVE' AND u.telegram_id IS NOT NULL AND u.is_banned = FALSE"
        )
    elif target == "expired":
        return await pool.fetch(
            "SELECT u.telegram_id FROM users u WHERE u.telegram_id IS NOT NULL AND u.is_banned = FALSE "
            "AND NOT EXISTS (SELECT 1 FROM subscriptions s WHERE s.user_id = u.id AND s.status = 'ACTIVE') "
            "AND EXISTS (SELECT 1 FROM subscriptions s2 WHERE s2.user_id = u.id)"
        )
    elif target == "trial":
        return await pool.fetch(
            "SELECT DISTINCT u.telegram_id FROM users u JOIN subscriptions s ON s.user_id = u.id "
            "JOIN packages p ON s.package_id = p.id "
            "WHERE s.status = 'ACTIVE' AND p.tier = 'TIER_99' AND u.telegram_id IS NOT NULL AND u.is_banned = FALSE"
        )
    else:  # all
        return await pool.fetch(
            "SELECT telegram_id FROM users WHERE telegram_id IS NOT NULL AND is_banned = FALSE"
        )


@router.get("/broadcast/count")
async def broadcast_count(target: str = "all", admin=Depends(require_role("admin"))):
    """Get count of users that would receive the broadcast."""
    count = await _get_broadcast_count(target)
    return {"count": count}


async def _telegram_api_file(token, method, chat_id, file_bytes, caption, file_type):
    """Send photo/video via Telegram Bot API with file upload."""
    async with httpx.AsyncClient(timeout=30) as client:
        files = {file_type: ("media", io.BytesIO(file_bytes))}
        data = {"chat_id": chat_id, "caption": caption, "parse_mode": "HTML"}
        resp = await client.post(
            f"https://api.telegram.org/bot{token}/{method}",
            data=data,
            files=files,
        )
        return resp.json()


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
    user_rows = await _get_broadcast_users(target)
    user_ids = [int(r["telegram_id"]) for r in user_rows if r["telegram_id"] is not None]
    if not user_ids:
        raise HTTPException(400, "ไม่มีผู้รับ — ยกเลิก broadcast")

    # Enqueue — broadcast_worker container will pick this row up via SELECT ... FOR UPDATE SKIP LOCKED
    broadcast_id = await pool.fetchval("""
        INSERT INTO broadcasts (
            message_text, message_photo_id, target_type, target_value,
            total_count, sent_by, sent_by_username, status, target_user_ids,
            started_at, parse_mode, media_type, media_b64
        )
        VALUES ($1, NULL, $2, $2, $3, $4, $5, 'PENDING', $6::jsonb,
                NOW(), $7, $8, $9)
        RETURNING id
    """,
        message, target, len(user_ids),
        admin["id"], admin.get("display_name") or admin.get("username"),
        json.dumps(user_ids), parse_mode, media_type, media_b64,
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
    total = await pool.fetchval("SELECT COUNT(*) FROM broadcast_log")
    rows = await pool.fetch("""
        SELECT bl.*, u.display_name as admin_name
        FROM broadcast_log bl
        LEFT JOIN dashboard_admins u ON bl.admin_id = u.id
        ORDER BY bl.created_at DESC
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
    rows = await pool.fetch("""
        SELECT s.*, p.name as package_name FROM subscriptions s
        JOIN packages p ON s.package_id = p.id
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
    except:
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
async def dm_customer(user_id: int, req: DMRequest, request: Request, admin=Depends(get_current_admin)):
    user = await pool.fetchrow("SELECT telegram_id FROM users WHERE id = $1", user_id)
    if not user:
        raise HTTPException(404, "User not found")
    
    result = await tg_send_dm(user["telegram_id"], req.message)
    ip = request.client.host if request.client else None
    await _log(admin["id"], "send_dm", "user", user_id, {"message_preview": req.message[:100]}, ip)
    return {"ok": True, "result": result}
