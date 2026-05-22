"""Team management router."""
from fastapi import APIRouter, Depends, Request, HTTPException
from ..auth.dependencies import require_role
from ..database import pool
from ..models.schemas import TeamMemberCreate, TeamMemberUpdate, PasswordReset
import bcrypt, json
import asyncpg

# FIX 2025-05-21 (Phase D-6): roles allowed via API. 'owner' is intentionally excluded —
# owner promotion must be done out-of-band (DB) to prevent privilege escalation via API.
ALLOWED_ROLES_CREATE = {"super_admin", "admin", "moderator"}

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
    # FIX 2025-05-21 (Phase D-6): validate role + min password length; use typed exception for unique violation
    if req.role not in ALLOWED_ROLES_CREATE:
        raise HTTPException(400, f"Role must be one of {sorted(ALLOWED_ROLES_CREATE)}")
    if len(req.password) < 10:
        raise HTTPException(400, "Password must be at least 10 characters")
    pw_hash = bcrypt.hashpw(req.password.encode(), bcrypt.gensalt()).decode()
    try:
        row = await pool.fetchrow("""
            INSERT INTO dashboard_admins (telegram_id, display_name, password_hash, role)
            VALUES ($1, $2, $3, $4) RETURNING id
        """, req.telegram_id, req.display_name, pw_hash, req.role)
    except asyncpg.UniqueViolationError:
        raise HTTPException(400, "Telegram ID already exists")

    ip = request.client.host if request.client else None
    await _log(admin["id"], "add_team_member", "admin", row["id"], {"display_name": req.display_name, "role": req.role}, ip)
    return {"ok": True, "id": row["id"]}

@router.put("/{member_id}")
async def update_team_member(member_id: int, req: TeamMemberUpdate, request: Request, admin=Depends(require_role("owner"))):
    data = req.dict(exclude_none=True)

    # FIX 2025-05-21 (Phase D-6): protect last owner from demote/disable; restrict role change
    if "role" in data or data.get("is_active") is False:
        target = await pool.fetchrow("SELECT role FROM dashboard_admins WHERE id=$1", member_id)
        if target and target["role"] == "owner":
            owners = await pool.fetchval(
                "SELECT COUNT(*) FROM dashboard_admins WHERE role='owner' AND is_active=TRUE"
            )
            if owners <= 1:
                raise HTTPException(400, "Cannot demote/disable the last owner")
        if "role" in data and data["role"] not in ALLOWED_ROLES_CREATE:
            raise HTTPException(400, f"Cannot change role to {data['role']}")

    updates = []
    params = []
    idx = 1
    for field, val in data.items():
        updates.append(f"{field} = ${idx}")
        params.append(val)
        idx += 1
    if not updates:
        raise HTTPException(400, "No fields")
    updates.append("updated_at = NOW()")
    params.append(member_id)
    await pool.execute(f"UPDATE dashboard_admins SET {', '.join(updates)} WHERE id = ${idx}", *params)
    ip = request.client.host if request.client else None
    await _log(admin["id"], "update_team_member", "admin", member_id, data, ip)
    return {"ok": True}

@router.delete("/{member_id}")
async def delete_team_member(member_id: int, request: Request, admin=Depends(require_role("owner"))):
    # Can't delete yourself
    if member_id == admin["id"]:
        raise HTTPException(400, "Cannot delete yourself")
    # FIX 2025-05-21 (Phase D-6): protect last active owner from deletion
    target = await pool.fetchrow("SELECT role FROM dashboard_admins WHERE id=$1", member_id)
    if target and target["role"] == "owner":
        owners = await pool.fetchval(
            "SELECT COUNT(*) FROM dashboard_admins WHERE role='owner' AND is_active=TRUE"
        )
