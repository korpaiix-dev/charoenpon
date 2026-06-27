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
async def list_packages(
    show_all: bool = False,
    admin=Depends(require_role("admin")),
):
    """List packages.
    
    show_all=True: รวม inactive ด้วย (สำหรับ admin CRUD)
    show_all=False (default): เฉพาะ active (สำหรับ dropdowns ใน Promo Manager ฯลฯ)
    """
    where = "" if show_all else "WHERE is_active = TRUE"
    rows = await pool.fetch(f"""
        SELECT p.*, 
               (SELECT COUNT(*) FROM subscriptions s 
                WHERE s.package_id = p.id AND s.status = 'ACTIVE' AND s.end_date > NOW()
               ) AS active_subs_count
        FROM packages p {where}
        ORDER BY p.sort_order DESC NULLS LAST, p.price
    """)
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


# ====== Sprint 2.4: Banned lists viewer ======
from fastapi import HTTPException as _HE

@router.get("/banned/summary")
async def banned_summary(admin=Depends(require_role("admin"))):
    """Quick counts across all banned lists."""
    slip_n = await pool.fetchval("SELECT COUNT(*) FROM banned_slips")
    sender_n = await pool.fetchval("SELECT COUNT(*) FROM banned_senders")
    banned_n = await pool.fetchval("SELECT COUNT(*) FROM users WHERE is_banned = TRUE")
    blocked_n = await pool.fetchval("SELECT COUNT(*) FROM users WHERE is_blocked_bot = TRUE")
    return {
        "slips": int(slip_n or 0),
        "senders": int(sender_n or 0),
        "banned_users": int(banned_n or 0),
        "blocked_bots": int(blocked_n or 0),
    }


@router.get("/banned/slips")
async def banned_slips_list(limit: int = 50, offset: int = 0,
                            admin=Depends(require_role("admin"))):
    limit = max(1, min(limit, 200))
    rows = await pool.fetch("""
        SELECT bs.id, bs.slip_trans_ref, bs.slip_hash, bs.source_telegram_id,
               bs.reason, bs.banned_by, bs.created_at,
               u.first_name AS source_first_name
        FROM banned_slips bs
        LEFT JOIN users u ON u.telegram_id = bs.source_telegram_id
        ORDER BY bs.created_at DESC
        LIMIT $1 OFFSET $2
    """, limit, offset)
    total = await pool.fetchval("SELECT COUNT(*) FROM banned_slips")
    return {"items": [dict(r) for r in rows], "total": int(total or 0)}


@router.get("/banned/senders")
async def banned_senders_list(limit: int = 50, offset: int = 0,
                              admin=Depends(require_role("admin"))):
    limit = max(1, min(limit, 200))
    rows = await pool.fetch("""
        SELECT bs.id, bs.sender_name, bs.source_telegram_id, bs.reason,
               bs.banned_by, bs.created_at,
               u.first_name AS source_first_name
        FROM banned_senders bs
        LEFT JOIN users u ON u.telegram_id = bs.source_telegram_id
        ORDER BY bs.created_at DESC
        LIMIT $1 OFFSET $2
    """, limit, offset)
    total = await pool.fetchval("SELECT COUNT(*) FROM banned_senders")
    return {"items": [dict(r) for r in rows], "total": int(total or 0)}


@router.get("/banned/users")
async def banned_users_list(limit: int = 50, offset: int = 0,
                            admin=Depends(require_role("admin"))):
    limit = max(1, min(limit, 200))
    rows = await pool.fetch("""
        SELECT id, telegram_id, username, first_name, last_name, banned_reason,
               banned_at, banned_by, total_spent
        FROM users
        WHERE is_banned = TRUE
        ORDER BY banned_at DESC NULLS LAST
        LIMIT $1 OFFSET $2
    """, limit, offset)
    total = await pool.fetchval("SELECT COUNT(*) FROM users WHERE is_banned = TRUE")
    return {"items": [dict(r) for r in rows], "total": int(total or 0)}


@router.get("/banned/blocked-bots")
async def blocked_bots_list(limit: int = 50, offset: int = 0,
                            admin=Depends(require_role("admin"))):
    limit = max(1, min(limit, 200))
    rows = await pool.fetch("""
        SELECT id, telegram_id, username, first_name, last_name,
               blocked_bot_at, total_spent
        FROM users
        WHERE is_blocked_bot = TRUE
        ORDER BY blocked_bot_at DESC NULLS LAST
        LIMIT $1 OFFSET $2
    """, limit, offset)
    total = await pool.fetchval("SELECT COUNT(*) FROM users WHERE is_blocked_bot = TRUE")
    return {"items": [dict(r) for r in rows], "total": int(total or 0)}


@router.delete("/banned/slips/{bid}")
async def unban_slip(bid: int, admin=Depends(require_role("admin"))):
    row = await pool.fetchrow(
        "DELETE FROM banned_slips WHERE id = $1 RETURNING slip_trans_ref, slip_hash",
        bid,
    )
    if not row:
        raise _HE(404, "banned slip not found")
    try:
        await pool.execute(
            "INSERT INTO admin_logs (admin_id, action, target_type, target_id, details) "
            "VALUES ($1, 'banned_slip_remove', 'banned_slip', $2, $3)",
            admin["telegram_id"], bid,
            f"trans_ref={row['slip_trans_ref']} hash={row['slip_hash']}",
        )
    except Exception:
        pass
    return {"ok": True, "id": bid}


@router.delete("/banned/senders/{bid}")
async def unban_sender(bid: int, admin=Depends(require_role("admin"))):
    row = await pool.fetchrow(
        "DELETE FROM banned_senders WHERE id = $1 RETURNING sender_name",
        bid,
    )
    if not row:
        raise _HE(404, "banned sender not found")
    try:
        await pool.execute(
            "INSERT INTO admin_logs (admin_id, action, target_type, target_id, details) "
            "VALUES ($1, 'banned_sender_remove', 'banned_sender', $2, $3)",
            admin["telegram_id"], bid, f"sender_name={row['sender_name']}",
        )
    except Exception:
        pass
    return {"ok": True, "id": bid}

