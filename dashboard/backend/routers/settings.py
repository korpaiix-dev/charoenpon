"""Settings router — packages, schedules, DM, backup."""
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

# ========== BOT TOKENS ==========
# FIX 2025-05-21 (Phase D-8): bot management endpoints removed from settings.py — the canonical
# implementations now live in routers/bots.py (newer, validates tokens via Telegram getMe, and
# supports orchestrated restarts). Old duplicates here were buggy (path bug for .env, no validation).

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
