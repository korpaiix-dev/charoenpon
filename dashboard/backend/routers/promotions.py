"""Promotions router — Flash Sales, Promo Codes, Scheduled Promotions."""
from fastapi import APIRouter, Depends, Request, HTTPException
from ..auth.dependencies import require_role
from ..database import pool
from ..models.schemas import (FlashSaleCreate, FlashSaleUpdate, PromoCodeCreate, PromoCodeUpdate,
                               ScheduledPromotionCreate, ScheduledPromotionUpdate)
import json
from datetime import datetime

router = APIRouter(tags=["promotions"])

async def _log(admin_id, action, entity_type, entity_id, details, ip):
    await pool.execute(
        "INSERT INTO dashboard_activity_log (admin_id, action, entity_type, entity_id, details, ip_address) VALUES ($1,$2,$3,$4,$5::jsonb,$6)",
        admin_id, action, entity_type, entity_id, json.dumps(details) if details else None, ip
    )

# ========== FLASH SALES ==========
@router.get("/api/flash-sales")
async def list_flash_sales(admin=Depends(require_role("admin"))):
    rows = await pool.fetch("""
        SELECT fs.*, p.name as package_name FROM flash_sales fs
        LEFT JOIN packages p ON fs.package_id = p.id
        ORDER BY fs.created_at DESC
    """)
    return [dict(r) for r in rows]

@router.post("/api/flash-sales")
async def create_flash_sale(req: FlashSaleCreate, request: Request, admin=Depends(require_role("admin"))):
    row = await pool.fetchrow("""
        INSERT INTO flash_sales (name, package_id, flash_price, original_price, total_slots, starts_at, ends_at, is_active)
        VALUES ($1, $2, $3, $4, $5, $6::timestamp, $7::timestamp, TRUE)
        RETURNING id
    """, req.name, req.package_id, req.flash_price, req.original_price, req.total_slots, req.starts_at, req.ends_at)
    
    ip = request.client.host if request.client else None
    await _log(admin["id"], "create_flash_sale", "flash_sale", row["id"], {"name": req.name}, ip)
    return {"ok": True, "id": row["id"]}

@router.put("/api/flash-sales/{sale_id}")
async def update_flash_sale(sale_id: int, req: FlashSaleUpdate, request: Request, admin=Depends(require_role("admin"))):
    updates = []
    params = []
    idx = 1
    for field, val in req.dict(exclude_none=True).items():
        if field in ("starts_at", "ends_at"):
            updates.append(f"{field} = ${idx}::timestamp")
        else:
            updates.append(f"{field} = ${idx}")
        params.append(val)
        idx += 1
    
    if not updates:
        raise HTTPException(400, "No fields to update")
    
    params.append(sale_id)
    await pool.execute(f"UPDATE flash_sales SET {', '.join(updates)} WHERE id = ${idx}", *params)
    
    ip = request.client.host if request.client else None
    await _log(admin["id"], "update_flash_sale", "flash_sale", sale_id, req.dict(exclude_none=True), ip)
    return {"ok": True}

@router.delete("/api/flash-sales/{sale_id}")
async def delete_flash_sale(sale_id: int, request: Request, admin=Depends(require_role("admin"))):
    await pool.execute("DELETE FROM flash_sales WHERE id = $1", sale_id)
    ip = request.client.host if request.client else None
    await _log(admin["id"], "delete_flash_sale", "flash_sale", sale_id, None, ip)
    return {"ok": True}

@router.post("/api/flash-sales/{sale_id}/toggle")
async def toggle_flash_sale(sale_id: int, request: Request, admin=Depends(require_role("admin"))):
    await pool.execute("UPDATE flash_sales SET is_active = NOT is_active WHERE id = $1", sale_id)
    ip = request.client.host if request.client else None
    await _log(admin["id"], "toggle_flash_sale", "flash_sale", sale_id, None, ip)
    return {"ok": True}

# ========== PROMO CODES ==========
@router.get("/api/promo-codes")
async def list_promo_codes(admin=Depends(require_role("admin"))):
    rows = await pool.fetch("""
        SELECT pc.*, p.name as package_name FROM promo_codes pc
        LEFT JOIN packages p ON pc.package_id = p.id
        ORDER BY pc.created_at DESC
    """)
    return [dict(r) for r in rows]

@router.post("/api/promo-codes")
async def create_promo_code(req: PromoCodeCreate, request: Request, admin=Depends(require_role("admin"))):
    row = await pool.fetchrow("""
        INSERT INTO promo_codes (code, discount_pct, max_uses, package_id, min_amount, expires_at, created_by)
        VALUES ($1, $2, $3, $4, $5, $6::timestamp, $7)
        RETURNING id
    """, req.code.upper(), req.discount_pct, req.max_uses, req.package_id, req.min_amount, req.expires_at, admin["id"])
    
    ip = request.client.host if request.client else None
    await _log(admin["id"], "create_promo_code", "promo_code", row["id"], {"code": req.code}, ip)
    return {"ok": True, "id": row["id"]}

@router.put("/api/promo-codes/{code_id}")
async def update_promo_code(code_id: int, req: PromoCodeUpdate, request: Request, admin=Depends(require_role("admin"))):
    updates = []
    params = []
    idx = 1
    for field, val in req.dict(exclude_none=True).items():
        if field == "expires_at":
            updates.append(f"{field} = ${idx}::timestamp")
        else:
            updates.append(f"{field} = ${idx}")
        params.append(val)
        idx += 1
    if not updates:
        raise HTTPException(400, "No fields to update")
    params.append(code_id)
    await pool.execute(f"UPDATE promo_codes SET {', '.join(updates)} WHERE id = ${idx}", *params)
    ip = request.client.host if request.client else None
    await _log(admin["id"], "update_promo_code", "promo_code", code_id, req.dict(exclude_none=True), ip)
    return {"ok": True}

@router.delete("/api/promo-codes/{code_id}")
async def delete_promo_code(code_id: int, request: Request, admin=Depends(require_role("admin"))):
    await pool.execute("DELETE FROM promo_codes WHERE id = $1", code_id)
    ip = request.client.host if request.client else None
    await _log(admin["id"], "delete_promo_code", "promo_code", code_id, None, ip)
    return {"ok": True}

@router.post("/api/promo-codes/{code_id}/toggle")
async def toggle_promo_code(code_id: int, request: Request, admin=Depends(require_role("admin"))):
    await pool.execute("UPDATE promo_codes SET is_active = NOT is_active WHERE id = $1", code_id)
    ip = request.client.host if request.client else None
    await _log(admin["id"], "toggle_promo_code", "promo_code", code_id, None, ip)
    return {"ok": True}

# ========== SCHEDULED PROMOTIONS ==========
@router.get("/api/scheduled-promotions")
async def list_scheduled(admin=Depends(require_role("admin"))):
    rows = await pool.fetch("SELECT * FROM scheduled_promotions ORDER BY scheduled_at DESC")
    return [dict(r) for r in rows]

@router.post("/api/scheduled-promotions")
async def create_scheduled(req: ScheduledPromotionCreate, request: Request, admin=Depends(require_role("admin"))):
    row = await pool.fetchrow("""
        INSERT INTO scheduled_promotions (name, message_text, target_groups, scheduled_at, repeat_type, created_by)
        VALUES ($1, $2, $3::jsonb, $4::timestamp, $5, $6)
        RETURNING id
    """, req.name, req.message_text, json.dumps(req.target_groups), req.scheduled_at, req.repeat_type, admin["id"])
    ip = request.client.host if request.client else None
    await _log(admin["id"], "create_scheduled_promo", "scheduled_promotion", row["id"], {"name": req.name}, ip)
    return {"ok": True, "id": row["id"]}

@router.put("/api/scheduled-promotions/{promo_id}")
async def update_scheduled(promo_id: int, req: ScheduledPromotionUpdate, request: Request, admin=Depends(require_role("admin"))):
    updates = []
    params = []
    idx = 1
    for field, val in req.dict(exclude_none=True).items():
        if field == "target_groups":
            updates.append(f"{field} = ${idx}::jsonb")
            params.append(json.dumps(val))
        elif field == "scheduled_at":
            updates.append(f"{field} = ${idx}::timestamp")
            params.append(val)
        else:
            updates.append(f"{field} = ${idx}")
            params.append(val)
        idx += 1
    if not updates:
        raise HTTPException(400, "No fields")
    params.append(promo_id)
    await pool.execute(f"UPDATE scheduled_promotions SET {', '.join(updates)} WHERE id = ${idx}", *params)
    ip = request.client.host if request.client else None
    await _log(admin["id"], "update_scheduled_promo", "scheduled_promotion", promo_id, None, ip)
    return {"ok": True}

@router.delete("/api/scheduled-promotions/{promo_id}")
async def delete_scheduled(promo_id: int, request: Request, admin=Depends(require_role("admin"))):
    await pool.execute("DELETE FROM scheduled_promotions WHERE id = $1", promo_id)
    ip = request.client.host if request.client else None
    await _log(admin["id"], "delete_scheduled_promo", "scheduled_promotion", promo_id, None, ip)
    return {"ok": True}
