"""Group management router."""
from fastapi import APIRouter, Depends, Request, HTTPException
from ..auth.dependencies import require_role
from ..database import pool
from ..services.telegram import create_invite_link, get_chat_member_count
from ..models.schemas import GroupCreate, GroupUpdate
import json

router = APIRouter(prefix="/api/groups", tags=["groups"])

async def _log(admin_id, action, entity_type, entity_id, details, ip):
    await pool.execute(
        "INSERT INTO dashboard_activity_log (admin_id, action, entity_type, entity_id, details, ip_address) VALUES ($1,$2,$3,$4,$5::jsonb,$6)",
        admin_id, action, entity_type, entity_id, json.dumps(details) if details else None, ip
    )

@router.get("")
async def list_groups(admin=Depends(require_role("admin"))):
    rows = await pool.fetch("SELECT * FROM group_registry ORDER BY slug")
    return [dict(r) for r in rows]

@router.get("/categorized")
async def list_groups_categorized(admin=Depends(require_role("admin"))):
    """Return groups split into VIP / Free / Chat categories."""
    rows = await pool.fetch("SELECT * FROM group_registry ORDER BY slug")
    vip = []
    free = []
    chat = []
    vip_tiers = {"TIER_300", "TIER_500", "TIER_1299", "TIER_2499", "TIER_99"}
    chat_slugs = {"CHAT", "TALK", "DISCUSS", "พูดคุย"}
    for r in rows:
        d = dict(r)
        slug_upper = (d.get("slug") or "").upper()
        tier = d.get("min_tier") or ""
        # Classify
        if any(cs in slug_upper for cs in chat_slugs) or tier == "FREE_CHAT":
            chat.append(d)
        elif tier == "FREE":
            free.append(d)
        elif tier in vip_tiers:
            vip.append(d)
        else:
            free.append(d)
    return {"vip": vip, "free": free, "chat": chat}

@router.post("")
async def create_group(req: GroupCreate, request: Request, admin=Depends(require_role("admin"))):
    # FIX 2025-05-21 (Phase D-7): use Pydantic GroupCreate (validated) instead of raw request.json()
    row = await pool.fetchrow("""
        INSERT INTO group_registry (slug, chat_id, title, min_tier, is_active, member_count)
        VALUES ($1::groupslug, $2, $3, $4::packagetier, $5, 0)
        RETURNING id
    """, req.slug, req.chat_id, req.title, req.min_tier, req.is_active)
    ip = request.client.host if request.client else None
    await _log(admin["id"], "create_group", "group", row["id"], {"title": req.title}, ip)
    return {"ok": True, "id": row["id"]}

@router.put("/{group_id}")
async def update_group(group_id: int, req: GroupUpdate, request: Request, admin=Depends(require_role("admin"))):
    # FIX 2025-05-21 (Phase D-7): use Pydantic GroupUpdate; only whitelisted fields allowed
    data = req.dict(exclude_none=True)
    updates = []
    params = []
    idx = 1
    if "title" in data:
        updates.append(f"title = ${idx}")
        params.append(data["title"])
        idx += 1
    if "is_active" in data:
        updates.append(f"is_active = ${idx}")
        params.append(data["is_active"])
        idx += 1
    if "min_tier" in data:
        updates.append(f"min_tier = ${idx}::packagetier")
        params.append(data["min_tier"])
        idx += 1

    if not params:
        raise HTTPException(400, "No fields")
    updates.append("updated_at = NOW()")
    params.append(group_id)
    await pool.execute(f"UPDATE group_registry SET {', '.join(updates)} WHERE id = ${idx}", *params)
    ip = request.client.host if request.client else None
    await _log(admin["id"], "update_group", "group", group_id, data, ip)
    return {"ok": True}

@router.delete("/{group_id}")
async def delete_group(group_id: int, request: Request, admin=Depends(require_role("admin"))):
    await pool.execute("DELETE FROM group_registry WHERE id = $1", group_id)
    ip = request.client.host if request.client else None
    await _log(admin["id"], "delete_group", "group", group_id, None, ip)
    return {"ok": True}

@router.get("/{group_id}/members")
async def group_members(group_id: int, admin=Depends(require_role("admin"))):
    group = await pool.fetchrow("SELECT * FROM group_registry WHERE id = $1", group_id)
    if not group:
        raise HTTPException(404, "Group not found")
    
    # FIX 2025-05-21 (Phase D-7): match groups_access as JSONB array exact-element (was LIKE substring,
    # which falsely matched e.g. 'TIER_2499_X' for slug 'TIER_249')
    rows = await pool.fetch("""
        SELECT u.id, u.telegram_id, u.username, u.first_name, s.status, s.end_date, p.name as package_name
        FROM users u
        JOIN subscriptions s ON s.user_id = u.id AND s.status = 'ACTIVE'
        JOIN packages p ON s.package_id = p.id
        WHERE EXISTS (
            SELECT 1 FROM jsonb_array_elements_text(p.groups_access::jsonb) AS slug
            WHERE slug = $1
        )
        ORDER BY u.first_name LIMIT 100
    """, group["slug"])
    return [dict(r) for r in rows]

@router.post("/{group_id}/invite-link")
async def gen_invite_link(group_id: int, request: Request, admin=Depends(require_role("admin"))):
    group = await pool.fetchrow("SELECT * FROM group_registry WHERE id = $1", group_id)
    if not group:
        raise HTTPException(404, "Group not found")
    
    result