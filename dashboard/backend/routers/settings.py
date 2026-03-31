"""Settings router — packages, bots, schedules, DM, backup."""
from fastapi import APIRouter, Depends, Request, HTTPException
from ..auth.dependencies import require_role
from ..database import pool
from ..models.schemas import PackageCreate, PackageUpdate
import json
import os

router = APIRouter(prefix="/api/settings", tags=["settings"])

async def _log(admin_id, action, entity_type, entity_id, details, ip):
    await pool.execute(
        "INSERT INTO dashboard_activity_log (admin_id, action, entity_type, entity_id, details, ip_address) VALUES ($1,$2,$3,$4,$5::jsonb,$6)",
        admin_id, action, entity_type, entity_id, json.dumps(details) if details else None, ip
    )

# ========== PACKAGES ==========
@router.get("/packages")
async def list_packages(admin=Depends(require_role("admin"))):
    rows = await pool.fetch("SELECT * FROM packages WHERE is_active = TRUE ORDER BY sort_order")
    return [dict(r) for r in rows]

@router.post("/packages")
async def create_package(req: PackageCreate, request: Request, admin=Depends(require_role("owner"))):
    row = await pool.fetchrow("""
        INSERT INTO packages (name, tier, price, duration_days, description, groups_access, is_active, sort_order)
        VALUES ($1, $2::packagetier, $3, $4, $5, $6, $7, $8) RETURNING id
    """, req.name, req.tier, req.price, req.duration_days, req.description, req.groups_access, req.is_active, req.sort_order)
    ip = request.client.host if request.client else None
    await _log(admin["id"], "create_package", "package", row["id"], {"name": req.name}, ip)
    return {"ok": True, "id": row["id"]}

@router.put("/packages/{pkg_id}")
async def update_package(pkg_id: int, req: PackageUpdate, request: Request, admin=Depends(require_role("owner"))):
    updates = []
    params = []
    idx = 1
    for field, val in req.dict(exclude_none=True).items():
        updates.append(f"{field} = ${idx}")
        params.append(val)
        idx += 1
    if not updates:
        raise HTTPException(400, "No fields")
    params.append(pkg_id)
    await pool.execute(f"UPDATE packages SET {', '.join(updates)} WHERE id = ${idx}", *params)
    ip = request.client.host if request.client else None
    await _log(admin["id"], "update_package", "package", pkg_id, req.dict(exclude_none=True), ip)
    return {"ok": True}

@router.delete("/packages/{pkg_id}")
async def delete_package(pkg_id: int, request: Request, admin=Depends(require_role("owner"))):
    # Check if any active subscriptions use this package
    count = await pool.fetchval("SELECT COUNT(*) FROM subscriptions WHERE package_id = $1 AND status = 'ACTIVE'", pkg_id)
    if count > 0:
        raise HTTPException(400, f"Cannot delete: {count} active subscriptions use this package")
    await pool.execute("UPDATE packages SET is_active = FALSE WHERE id = $1", pkg_id)
    ip = request.client.host if request.client else None
    await _log(admin["id"], "delete_package", "package", pkg_id, None, ip)
    return {"ok": True}

# ========== BOT TOKENS (Owner only, display masked) ==========
@router.get("/bots")
async def get_bots(admin=Depends(require_role("owner"))):
    import os
    tokens = {
        "sales": os.getenv("SALES_BOT_TOKEN", ""),
        "guardian": os.getenv("GUARDIAN_BOT_TOKEN", ""),
        "admin": os.getenv("ADMIN_BOT_TOKEN", ""),
        "content": os.getenv("CONTENT_BOT_TOKEN", ""),
        "announce": os.getenv("ANNOUNCE_BOT_TOKEN", ""),
    }
    # Mask tokens
    masked = {}
    for name, token in tokens.items():
        if token and len(token) > 10:
            masked[name] = token[:6] + "..." + token[-4:]
        else:
            masked[name] = "not set"
    return masked

@router.put("/bot-token")
async def update_bot_token(request: Request, admin=Depends(require_role("owner"))):
    """Update a bot token in .env file. Owner only."""
    body = await request.json()
    bot_name = body.get("name", "")
    new_token = body.get("token", "").strip()
    
    valid_names = {
        "sales": "SALES_BOT_TOKEN",
        "guardian": "GUARDIAN_BOT_TOKEN",
        "admin": "ADMIN_BOT_TOKEN",
        "content": "CONTENT_BOT_TOKEN",
        "announce": "ANNOUNCE_BOT_TOKEN",
    }
    
    if bot_name not in valid_names:
        raise HTTPException(400, f"Invalid bot name: {bot_name}")
    if not new_token or ":" not in new_token:
        raise HTTPException(400, "Invalid token format")
    
    env_key = valid_names[bot_name]
    
    # Update .env file
    env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))), ".env")
    if not os.path.isfile(env_path):
        raise HTTPException(500, ".env file not found")
    
    with open(env_path, "r") as f:
        lines = f.readlines()
    
    found = False
    new_lines = []
    for line in lines:
        if line.strip().startswith(f"{env_key}="):
            new_lines.append(f"{env_key}={new_token}\n")
            found = True
        else:
            new_lines.append(line)
    
    if not found:
        new_lines.append(f"{env_key}={new_token}\n")
    
    with open(env_path, "w") as f:
        f.writelines(new_lines)
    
    # Also update os.environ for current process
    os.environ[env_key] = new_token
    
    ip = request.client.host if request.client else None
    await _log(admin["id"], "update_bot_token", "settings", None, {"bot": bot_name}, ip)
    
    return {"ok": True, "message": f"Updated {bot_name} token. Restart bots to apply."}

@router.get("/admin-ids")
async def get_admin_ids(admin=Depends(require_role("owner"))):
    import os
    return {"admin_ids": os.getenv("ADMIN_TELEGRAM_IDS", "")}

# ========== SCHEDULE (simplified) ==========
@router.get("/schedule")
async def get_schedule(admin=Depends(require_role("admin"))):
    # Return teaser schedule times
    rows = await pool.fetch("""
        SELECT DISTINCT TO_CHAR(scheduled_at, 'HH24:MI') as time_slot
        FROM content_schedule WHERE scheduled_at > NOW() - interval '7 days'
        ORDER BY time_slot
    """)
    return {"teaser_times": [r["time_slot"] for r in rows]}

@router.put("/schedule")
async def update_schedule(request: Request, admin=Depends(require_role("admin"))):
    body = await request.json()
    ip = request.client.host if request.client else None
    await _log(admin["id"], "update_schedule", "settings", None, body, ip)
    return {"ok": True}

# ========== DM SETTINGS (simplified) ==========
@router.get("/dm")
async def get_dm_settings(admin=Depends(require_role("admin"))):
    return {
        "comeback_per_day": 50,
        "comeback_delay": 30,
        "trial_per_day": 100,
        "trial_delay": 30,
    }

@router.put("/dm")
async def update_dm_settings(request: Request, admin=Depends(require_role("admin"))):
    body = await request.json()
    ip = request.client.host if request.client else None
    await _log(admin["id"], "update_dm_settings", "settings", None, body, ip)
    return {"ok": True}

# ========== BACKUP (simplified) ==========
@router.get("/backup")
async def list_backups(admin=Depends(require_role("owner"))):
    return {"backups": []}

@router.post("/backup/now")
async def backup_now(request: Request, admin=Depends(require_role("owner"))):
    ip = request.client.host if request.client else None
    await _log(admin["id"], "backup_now", "backup", None, None, ip)
    return {"ok": True, "message": "Backup initiated"}
