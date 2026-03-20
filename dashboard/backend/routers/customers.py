"""Customer management router."""
from fastapi import APIRouter, Depends, Request, HTTPException
from ..auth.dependencies import get_current_admin, require_role
from ..database import pool
from ..models.schemas import ExtendRequest, UpgradeRequest, KickRequest, BanRequest, DMRequest
from ..services.telegram import send_dm as tg_send_dm, kick_member
import json

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
        SELECT u.id, u.telegram_id, u.username, u.first_name, u.last_name, u.is_banned, u.total_spent, u.created_at,
               s.status as sub_status, s.end_date, p.name as package_name, p.tier as package_tier
        FROM users u
        LEFT JOIN LATERAL (
            SELECT * FROM subscriptions WHERE user_id = u.id ORDER BY created_at DESC LIMIT 1
        ) s ON TRUE
        LEFT JOIN packages p ON s.package_id = p.id
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
    
    await pool.execute(
        "UPDATE subscriptions SET end_date = end_date + ($1 || ' days')::interval, updated_at = NOW() WHERE id = $2",
        str(req.days), sub["id"]
    )
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
    
    results = []
    for gid in req.group_ids:
        group = await pool.fetchrow("SELECT chat_id, title FROM group_registry WHERE id = $1", gid)
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
