"""Promotions router — Flash Sales, Promo Codes, Scheduled Promotions."""
from fastapi import APIRouter, Depends, Request, HTTPException, UploadFile, File
from ..auth.dependencies import require_role
from ..database import pool
from ..models.schemas import (FlashSaleCreate, FlashSaleUpdate, PromoCodeCreate, PromoCodeUpdate,
                               ScheduledPromotionCreate, ScheduledPromotionUpdate, PromotionCampaignCreate, PromotionCampaignUpdate)
import json
import os
import uuid
from pathlib import Path
from datetime import datetime

router = APIRouter(tags=["promotions"])

PROMO_CAMPAIGN_SQL = """
CREATE TABLE IF NOT EXISTS promotion_campaigns (
    id SERIAL PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    package_id INTEGER NOT NULL REFERENCES packages(id),
    normal_price NUMERIC(10,2) NOT NULL,
    promo_price NUMERIC(10,2) NOT NULL,
    starts_at TIMESTAMP NOT NULL,
    ends_at TIMESTAMP NOT NULL,
    bot_badge TEXT NOT NULL DEFAULT '',
    bot_sales_text TEXT NOT NULL DEFAULT '',
    group_caption TEXT NOT NULL DEFAULT '',
    user_broadcast_caption TEXT NOT NULL DEFAULT '',
    target_groups JSONB NOT NULL DEFAULT '[]'::jsonb,
    delivery_channels JSONB NOT NULL DEFAULT '["tracking_only"]'::jsonb,
    image_path TEXT NOT NULL DEFAULT '',
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_by INTEGER REFERENCES dashboard_admins(id),
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_promotion_campaigns_active_window ON promotion_campaigns(is_active, starts_at, ends_at);
ALTER TABLE promotion_campaigns ADD COLUMN IF NOT EXISTS delivery_channels JSONB NOT NULL DEFAULT '["tracking_only"]'::jsonb;
ALTER TABLE payments ADD COLUMN IF NOT EXISTS promotion_campaign_id INTEGER REFERENCES promotion_campaigns(id);
CREATE INDEX IF NOT EXISTS ix_payments_promotion_campaign_id ON payments(promotion_campaign_id);

CREATE OR REPLACE FUNCTION attach_active_promotion_campaign()
RETURNS trigger AS $$
BEGIN
    IF NEW.promotion_campaign_id IS NULL AND NEW.status = 'CONFIRMED' THEN
        SELECT pc.id INTO NEW.promotion_campaign_id
        FROM promotion_campaigns pc
        WHERE pc.is_active = TRUE
          AND pc.package_id = NEW.package_id
          AND NEW.created_at BETWEEN pc.starts_at AND pc.ends_at
          AND NEW.amount = pc.promo_price
        ORDER BY pc.starts_at DESC, pc.id DESC
        LIMIT 1;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_attach_active_promotion_campaign ON payments;
CREATE TRIGGER trg_attach_active_promotion_campaign
BEFORE INSERT OR UPDATE OF status, amount, package_id, created_at, promotion_campaign_id ON payments
FOR EACH ROW EXECUTE FUNCTION attach_active_promotion_campaign();
"""

async def ensure_promo_campaign_tables():
    await pool.execute(PROMO_CAMPAIGN_SQL)



def _to_dt(val):
    """Convert string or datetime to datetime."""
    if isinstance(val, datetime):
        return val
    if isinstance(val, str):
        return datetime.fromisoformat(val)
    raise ValueError(f"Cannot parse datetime from {val!r}")


async def _log(admin_id, action, entity_type, entity_id, details, ip):
    await pool.execute(
        "INSERT INTO dashboard_activity_log (admin_id, action, entity_type, entity_id, details, ip_address) VALUES ($1,$2,$3,$4,$5::jsonb,$6)",
        admin_id, action, entity_type, entity_id, json.dumps(details) if details else None, ip
    )

@router.post("/api/promotion-campaigns/upload-image")
async def upload_promotion_image(file: UploadFile = File(...), admin=Depends(require_role("admin"))):
    """Upload promotion image and return public URL for campaign use."""
    allowed = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}
    ext = allowed.get(file.content_type or "")
    if not ext:
        raise HTTPException(status_code=400, detail="รองรับเฉพาะ JPG, PNG, WEBP")

    data = await file.read()
    if len(data) > 8 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="ไฟล์ใหญ่เกิน 8MB")

    base_dir = Path(__file__).resolve().parents[2] / "frontend" / "assets" / "promotions"
    base_dir.mkdir(parents=True, exist_ok=True)
    safe_name = f"promo-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:8]}{ext}"
    dest = base_dir / safe_name
    dest.write_bytes(data)
    return {"ok": True, "url": f"/assets/promotions/{safe_name}", "path": str(dest)}

# ========== PROMOTION CAMPAIGN CENTER ==========
@router.get("/api/promotion-campaigns")
async def list_promotion_campaigns(admin=Depends(require_role("admin"))):
    await ensure_promo_campaign_tables()
    rows = await pool.fetch("""
        SELECT pc.*, p.name AS package_name,
               COUNT(pay.id) FILTER (WHERE pay.status='CONFIRMED') AS orders,
               COUNT(DISTINCT pay.user_id) FILTER (WHERE pay.status='CONFIRMED') AS buyers,
               COALESCE(SUM(pay.amount) FILTER (WHERE pay.status='CONFIRMED'), 0) AS revenue
        FROM promotion_campaigns pc
        LEFT JOIN packages p ON p.id = pc.package_id
        LEFT JOIN payments pay ON pay.promotion_campaign_id = pc.id
        GROUP BY pc.id, p.name
        ORDER BY pc.created_at DESC
    """)
    return [
        {
            **dict(r),
            "normal_price": float(r["normal_price"]) if r["normal_price"] is not None else None,
            "promo_price": float(r["promo_price"]) if r["promo_price"] is not None else None,
            "orders": int(r["orders"] or 0),
            "buyers": int(r["buyers"] or 0),
            "revenue": float(r["revenue"] or 0),
        }
        for r in rows
    ]

@router.post("/api/promotion-campaigns")
async def create_promotion_campaign(req: PromotionCampaignCreate, request: Request, admin=Depends(require_role("admin"))):
    await ensure_promo_campaign_tables()
    admin_id = int(admin["id"])
    row = await pool.fetchrow("""
        INSERT INTO promotion_campaigns
        (name, package_id, normal_price, promo_price, starts_at, ends_at, bot_badge, bot_sales_text,
         group_caption, user_broadcast_caption, target_groups, delivery_channels, image_path, created_by)
        VALUES ($1,$2,$3,$4,$5::timestamp,$6::timestamp,$7,$8,$9,$10,$11::jsonb,$12::jsonb,$13,$14)
        RETURNING id
    """, req.name, req.package_id, req.normal_price, req.promo_price, _to_dt(req.starts_at), _to_dt(req.ends_at),
        req.bot_badge, req.bot_sales_text, req.group_caption, req.user_broadcast_caption,
        json.dumps(req.target_groups), json.dumps(req.delivery_channels), req.image_path, admin_id)
    ip = request.client.host if request.client else None
    await _log(admin_id, "create_promotion_campaign", "promotion_campaign", row["id"], {"name": req.name}, ip)
    return {"ok": True, "id": row["id"]}

# FIX 2025-05-21 (Phase D-5-business): whitelist for promotion_campaigns update
PROMOTION_CAMPAIGN_ALLOWED_UPDATE_FIELDS = {
    "name", "package_id", "normal_price", "promo_price", "starts_at", "ends_at",
    "bot_badge", "bot_sales_text", "group_caption", "user_broadcast_caption",
    "target_groups", "delivery_channels", "image_path", "is_active",
}

@router.put("/api/promotion-campaigns/{campaign_id}")
async def update_promotion_campaign(campaign_id: int, req: PromotionCampaignUpdate, request: Request, admin=Depends(require_role("admin"))):
    await ensure_promo_campaign_tables()
    updates, params, idx = [], [], 1
    for field, val in req.dict(exclude_none=True).items():
        if field not in PROMOTION_CAMPAIGN_ALLOWED_UPDATE_FIELDS:
            continue
        if field in ("starts_at", "ends_at"):
            updates.append(f"{field} = ${idx}::timestamp")
            params.append(_to_dt(val))
        elif field in ("target_groups", "delivery_channels"):
            updates.append(f"{field} = ${idx}::jsonb")
            params.append(json.dumps(val))
        else:
            updates.append(f"{field} = ${idx}")
            params.append(val)
        idx += 1
    if not updates:
        raise HTTPException(400, "No fields to update")
    updates.append("updated_at = NOW()")
    params.append(campaign_id)
    await pool.execute(f"UPDATE promotion_campaigns SET {', '.join(updates)} WHERE id = ${idx}", *params)
    ip = request.client.host if request.client else None
    await _log(int(admin["id"]), "update_promotion_campaign", "promotion_campaign", campaign_id, req.dict(exclude_none=True), ip)
    return {"ok": True}

@router.post("/api/promotion-campaigns/{campaign_id}/toggle")
async def toggle_promotion_campaign(campaign_id: int, request: Request, admin=Depends(require_role("admin"))):
    await ensure_promo_campaign_tables()
    await pool.execute("UPDATE promotion_campaigns SET is_active = NOT is_active, updated_at = NOW() WHERE id = $1", campaign_id)
    ip = request.client.host if request.client else None
    await _log(int(admin["id"]), "toggle_promotion_campaign", "promotion_campaign", campaign_id, None, ip)
    return {"ok": True}

@router.delete("/api/promotion-campaigns/{campaign_id}")
async def delete_promotion_campaign(campaign_id: int, request: Request, admin=Depends(require_role("admin"))):
    await ensure_promo_campaign_tables()
    await pool.execute("DELETE FROM promotion_campaigns WHERE id = $1", campaign_id)
    ip = request.client.host if request.client else None
    await _log(int(admin["id"]), "delete_promotion_campaign", "promotion_campaign", campaign_id, None, ip)
    return {"ok": True}

# ========== PROMO STATS SUMMARY ==========
@router.get("/api/promo-stats")
async def promo_stats(admin=Depends(require_role("admin"))):
    """Summary stats for promotions page."""
    row = await pool.fetchrow("""
        SELECT
            (SELECT COUNT(*) FROM promo_code_usage) as codes_used,
            (SELECT COALESCE(SUM(discount_amount), 0) FROM promo_code_usage) as total_discount,
            (SELECT COALESCE(SUM(sold_slots), 0) FROM flash_sales) as flash_sold,
            (SELECT COALESCE(SUM(sold_slots * flash_price), 0) FROM flash_sales WHERE sold_slots > 0) as flash_revenue
    """)
    return {
        "codes_used": row["codes_used"],
        "total_discount": float(row["total_discount"] or 0),
        "flash_sold": row["flash_sold"],
        "flash_revenue": float(row["flash_revenue"] or 0),
    }


@router.get("/api/promo-performance")
async def promo_performance(admin=Depends(require_role("admin"))):
    """Detailed promotion performance from tracked promo tables."""
    flash_rows = await pool.fetch("""
        SELECT fs.id, fs.name, fs.package_id, p.name AS package_name,
               fs.flash_price, fs.original_price, fs.total_slots, fs.sold_slots,
               (fs.sold_slots * fs.flash_price) AS revenue,
               (fs.sold_slots * GREATEST(fs.original_price - fs.flash_price, 0)) AS discount_saved,
               fs.starts_at, fs.ends_at, fs.is_active, fs.created_at
        FROM flash_sales fs
        LEFT JOIN packages p ON p.id = fs.package_id
        ORDER BY fs.starts_at DESC, fs.id DESC
    """)
    code_rows = await pool.fetch("""
        SELECT pc.id, pc.code, pc.discount_pct, pc.max_uses, pc.used_count,
               pc.package_id, pk.name AS package_name, pc.is_active, pc.expires_at, pc.created_at,
               COUNT(pcu.id) AS tracked_uses,
               COUNT(DISTINCT pcu.user_id) AS buyers,
               COALESCE(SUM(p.amount) FILTER (WHERE p.status = 'CONFIRMED'), 0) AS revenue,
               COALESCE(SUM(pcu.discount_amount), 0) AS discount_total
        FROM promo_codes pc
        LEFT JOIN packages pk ON pk.id = pc.package_id
        LEFT JOIN promo_code_usage pcu ON pcu.promo_code_id = pc.id
        LEFT JOIN payments p ON p.id = pcu.payment_id
        GROUP BY pc.id, pk.name
        ORDER BY pc.created_at DESC
    """)
    scheduled_rows = await pool.fetch("""
        SELECT id, name, repeat_type, is_active, is_sent, sent_at, scheduled_at, created_at
        FROM scheduled_promotions
        ORDER BY scheduled_at DESC, id DESC
    """)

    flash_total_revenue = sum(float(r["revenue"] or 0) for r in flash_rows)
    flash_total_sold = sum(int(r["sold_slots"] or 0) for r in flash_rows)
    code_total_revenue = sum(float(r["revenue"] or 0) for r in code_rows)
    code_total_buyers = sum(int(r["buyers"] or 0) for r in code_rows)

    return {
        "summary": {
            "flash_sold": flash_total_sold,
            "flash_revenue": flash_total_revenue,
            "promo_code_buyers": code_total_buyers,
            "promo_code_revenue": code_total_revenue,
            "scheduled_sent": sum(1 for r in scheduled_rows if r["is_sent"]),
        },
        "flash_sales": [
            {
                "id": r["id"], "name": r["name"], "package_name": r["package_name"],
                "flash_price": float(r["flash_price"] or 0), "original_price": float(r["original_price"] or 0),
                "sold_slots": int(r["sold_slots"] or 0), "total_slots": int(r["total_slots"] or 0),
                "revenue": float(r["revenue"] or 0), "discount_saved": float(r["discount_saved"] or 0),
                "starts_at": str(r["starts_at"]), "ends_at": str(r["ends_at"]), "is_active": r["is_active"],
            }
            for r in flash_rows
        ],
        "promo_codes": [
            {
                "id": r["id"], "code": r["code"], "discount_pct": r["discount_pct"],
                "package_name": r["package_name"], "used_count": int(r["used_count"] or 0),
                "tracked_uses": int(r["tracked_uses"] or 0), "buyers": int(r["buyers"] or 0),
                "revenue": float(r["revenue"] or 0), "discount_total": float(r["discount_total"] or 0),
                "max_uses": int(r["max_uses"] or 0), "is_active": r["is_active"], "expires_at": str(r["expires_at"]),
            }
            for r in code_rows
        ],
        "scheduled_promotions": [dict(r) for r in scheduled_rows],
    }

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
    """, req.name, req.package_id, req.flash_price, req.original_price, req.total_slots, _to_dt(req.starts_at), _to_dt(req.ends_at))

    ip = request.client.host if request.client else None
    await _log(admin["id"], "create_flash_sale", "flash_sale", row["id"], {"name": req.name}, ip)
    return {"ok": True, "id": row["id"]}

# FIX 2025-05-21 (Phase D-5-business): whitelist for flash_sales update
FLASH_SALE_ALLOWED_UPDATE_FIELDS = {"name", "flash_price", "total_slots", "starts_at", "ends_at", "is_active"}

@router.put("/api/flash-sales/{sale_id}")
async def update_flash_sale(sale_id: int, req: FlashSaleUpdate, request: Request, admin=Depends(require_role("admin"))):
    updates = []
    params = []
    idx = 1
    for field, val in req.dict(exclude_none=True).items():
        if field not in FLASH_SALE_ALLOWED_UPDATE_FIELDS:
            continue
        if field in ("starts_at", "ends_at"):
            updates.append(f"{field} = ${idx}::timestamp")
            params.append(_to_dt(val))
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
    """, req.code.upper(), req.discount_pct, req.max_uses, req.package_id, req.min_amount, _to_dt(req.expires_at), admin["id"])

    ip = request.client.host if request.client else None
    await _log(admin["id"], "create_promo_code", "promo_code", row["id"], {"code": req.code}, ip)
    return {"ok": True, "id": row["id"]}

# FIX 2025-05-21 (Phase D-5-business): whitelist field names + Pydantic-validated values
PROMO_CODE_ALLOWED_UPDATE_FIELDS = {"discount_pct", "max_uses", "is_active", "expires_at"}

@router.put("/api/promo-codes/{code_id}")
async def update_promo_code(code_id: int, req: PromoCodeUpdate, request: Request, admin=Depends(require_role("admin"))):
    updates = []
    params = []
    idx = 1
    for field, val in req.dict(exclude_none=True).items():
        # FIX 2025-05-21 (Phase D-5-business): drop fields ที่ไม่ได้ whitelist (กัน user ใส่ code/created_by ผ่าน body)
        if field not in PROMO_CODE_ALLOWED_UPDATE_FIELDS:
            continue
        if field == "expires_at":
            updates.append(f"{field} = ${idx}::timestamp")
            params.append(_to_dt(val))
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
    """, req.name, req.message_text, json.dumps(req.target_groups), _to_dt(req.scheduled_at), req.repeat_type, admin["id"])
    ip = request.client.host if request.client else None
    await _log(admin["id"], "create_scheduled_promo", "scheduled_promotion", row["id"], {"name": req.name}, ip)
    return {"ok": True, "id": row["id"]}

# FIX 2025-05-21 (Phase D-5-business): whitelist for scheduled_promotions update
SCHEDULED_PROMO_ALLOWED_UPDATE_FIELDS = {
    "name", "message_text", "target_groups", "scheduled_at", "repeat_type", "is_active",
}

@router.put("/api/scheduled-promotions/{promo_id}")
async def update_scheduled(promo_id: int, req: ScheduledPromotionUpdate, request: Request, admin=Depends(require_role("admin"))):
    updates = []
    params = []
    idx = 1
    for field, val in req.dict(exclude_none=True).items():
        if field not in SCHEDULED_PROMO_ALLOWED_UPDATE_FIELDS:
            continue
        if field == "target_groups":
            updates.append(f"{field} = ${idx}::jsonb")
            params.append(json.dumps(val))
        elif field == "scheduled_at":
            updates.append(f"{field} = ${idx}::timestamp")
            params.append(_to_dt(val))
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
