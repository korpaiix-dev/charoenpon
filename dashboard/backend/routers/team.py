"""Team management router."""
from fastapi import APIRouter, Depends, Request, HTTPException
from ..auth.dependencies import require_role
from ..database import pool
from ..models.schemas import TeamMemberCreate, TeamMemberUpdate
import bcrypt, json

router = APIRouter(prefix="/api/team", tags=["team"])

async def _log(admin_id, action, entity_type, entity_id, details, ip):
    await pool.execute(
        "INSERT INTO dashboard_activity_log (admin_id, action, entity_type, entity_id, details, ip_address) VALUES ($1,$2,$3,$4,$5::jsonb,$6)",
        admin_id, action, entity_type, entity_id, json.dumps(details) if details else None, ip
    )

@router.get("")
async def list_team(admin=Depends(require_role("admin"))):
    rows = await pool.fetch("""
        SELECT id, telegram_id, username, display_name, role, is_active, last_login_at, created_at
        FROM dashboard_admins ORDER BY 
        CASE role WHEN 'owner' THEN 1 WHEN 'admin' THEN 2 ELSE 3 END, display_name
    """)
    return [dict(r) for r in rows]

@router.post("")
async def add_team_member(req: TeamMemberCreate, request: Request, admin=Depends(require_role("owner"))):
    pw_hash = bcrypt.hashpw(req.password.encode(), bcrypt.gensalt()).decode()
    try:
        row = await pool.fetchrow("""
            INSERT INTO dashboard_admins (telegram_id, display_name, password_hash, role)
            VALUES ($1, $2, $3, $4) RETURNING id
        """, req.telegram_id, req.display_name, pw_hash, req.role)
    except Exception as e:
        if "unique" in str(e).lower():
            raise HTTPException(400, "Telegram ID already exists")
        raise
    
    ip = request.client.host if request.client else None
    await _log(admin["id"], "add_team_member", "admin", row["id"], {"display_name": req.display_name, "role": req.role}, ip)
    return {"ok": True, "id": row["id"]}

@router.put("/{member_id}")
async def update_team_member(member_id: int, req: TeamMemberUpdate, request: Request, admin=Depends(require_role("owner"))):
    updates = []
    params = []
    idx = 1
    for field, val in req.dict(exclude_none=True).items():
        updates.append(f"{field} = ${idx}")
        params.append(val)
        idx += 1
    if not updates:
        raise HTTPException(400, "No fields")
    updates.append("updated_at = NOW()")
    params.append(member_id)
    await pool.execute(f"UPDATE dashboard_admins SET {', '.join(updates)} WHERE id = ${idx}", *params)
    ip = request.client.host if request.client else None
    await _log(admin["id"], "update_team_member", "admin", member_id, req.dict(exclude_none=True), ip)
    return {"ok": True}

@router.delete("/{member_id}")
async def delete_team_member(member_id: int, request: Request, admin=Depends(require_role("owner"))):
    # Can't delete yourself
    if member_id == admin["id"]:
        raise HTTPException(400, "Cannot delete yourself")
    await pool.execute("DELETE FROM dashboard_admins WHERE id = $1", member_id)
    ip = request.client.host if request.client else None
    await _log(admin["id"], "delete_team_member", "admin", member_id, None, ip)
    return {"ok": True}

@router.get("/{member_id}/activity")
async def member_activity(member_id: int, page: int = 1, per_page: int = 50, admin=Depends(require_role("admin"))):
    offset = (page - 1) * per_page
    total = await pool.fetchval("SELECT COUNT(*) FROM dashboard_activity_log WHERE admin_id = $1", member_id)
    rows = await pool.fetch("""
        SELECT * FROM dashboard_activity_log WHERE admin_id = $1
        ORDER BY created_at DESC LIMIT $2 OFFSET $3
    """, member_id, per_page, offset)
    return {"items": [dict(r) for r in rows], "total": total, "page": page}

@router.put("/{member_id}/password-reset")
async def reset_password(member_id: int, request: Request, admin=Depends(require_role("owner"))):
    body = await request.json()
    new_pw = body.get("new_password", "")
    if len(new_pw) < 6:
        raise HTTPException(400, "Password must be at least 6 characters")
    pw_hash = bcrypt.hashpw(new_pw.encode(), bcrypt.gensalt()).decode()
    await pool.execute("UPDATE dashboard_admins SET password_hash = $1, updated_at = NOW() WHERE id = $2", pw_hash, member_id)
    # Revoke all sessions
    await pool.execute("UPDATE dashboard_sessions SET revoked_at = NOW() WHERE admin_id = $1 AND revoked_at IS NULL", member_id)
    ip = request.client.host if request.client else None
    await _log(admin["id"], "reset_password", "admin", member_id, None, ip)
    return {"ok": True}
