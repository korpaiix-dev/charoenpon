"""DAY 0 (2026-06-28): Promotions admin endpoints.

CRUD for the promotions table — single source of truth for sales-facing campaigns.
"""
from __future__ import annotations
import logging
import json
from fastapi import APIRouter, Depends, HTTPException, Request

from ..auth.dependencies import require_role
from ..database import pool

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin", tags=["admin-day0-promos"])

def _parse_dt(value):
    """Parse datetime from HTML datetime-local string (e.g. '2026-06-28T01:00').
    Returns datetime or None. Accepts already-datetime values too.
    """
    from datetime import datetime
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        # Try common formats from HTML datetime-local
        for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(s, fmt)
            except ValueError:
                continue
        # Try fromisoformat (Python 3.7+)
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            pass
    return None




@router.get("/day0-promos")
async def list_promotions(
    include_inactive: bool = True,
    _admin=Depends(require_role("admin")),
):
    """List all promotions, newest first."""
    where = "" if include_inactive else "WHERE is_active = TRUE"
    rows = await pool.fetch(f"""
        SELECT id, code, name, is_active,
               package_codes, discount_type, discount_value, valid_hours,
               caption_html, image_path, extra_buttons,
               target_groups, post_times,
               starts_at, ends_at,
               updated_at, updated_by
        FROM promotions {where}
        ORDER BY id DESC
    """)
    return [dict(r) for r in rows]


@router.get("/day0-promos/packages")
async def list_packages_for_promo(_admin=Depends(require_role("admin"))):
    """Return active packages — used for the multi-select picker in promo form."""
    rows = await pool.fetch("""
        SELECT id, name, tier::text AS tier, price, duration_days
        FROM packages WHERE is_active = TRUE
        ORDER BY sort_order DESC, price
    """)
    return [dict(r) for r in rows]




@router.get("/day0-promos/groups")
async def list_groups_for_promo(_admin=Depends(require_role("admin"))):
    """Return active groups grouped by tier — for promo group picker."""
    rows = await pool.fetch("""
        SELECT slug, title, min_tier::text AS tier, is_active
        FROM group_registry
        WHERE is_active = TRUE
        ORDER BY 
          CASE WHEN min_tier::text = 'FREE' THEN 0 ELSE 1 END,
          min_tier::text,
          slug
    """)
    return [dict(r) for r in rows]


@router.get("/day0-promos/{promo_id}")
async def get_promotion_by_id(
    promo_id: int,
    _admin=Depends(require_role("admin")),
):
    """Get one promotion."""
    row = await pool.fetchrow("""
        SELECT * FROM promotions WHERE id = $1
    """, promo_id)
    if not row:
        raise HTTPException(404, "promotion not found")
    return dict(row)


@router.post("/day0-promos")
async def create_promotion(
    payload: dict,
    request: Request,
    admin=Depends(require_role("admin")),
):
    """Create a new promotion.

    Required: code, name
    Optional: everything else
    """
    code = (payload.get("code") or "").strip().lower()
    name = (payload.get("name") or "").strip()
    if not code or not name:
        raise HTTPException(400, "code + name required")

    import re
    if not re.match(r"^[a-z0-9_]+$", code):
        raise HTTPException(400, "code: lowercase letters/digits/underscore only")

    discount_type = payload.get("discount_type") or "none"
    if discount_type not in ("none", "percent", "fixed_off", "fixed_price"):
        raise HTTPException(400, f"invalid discount_type: {discount_type}")

    try:
        row = await pool.fetchrow("""
            INSERT INTO promotions (
                code, name, is_active,
                package_codes, discount_type, discount_value, valid_hours,
                caption_html, image_path, extra_buttons,
                target_groups, post_times,
                starts_at, ends_at,
                updated_by
            ) VALUES (
                $1, $2, $3,
                $4, $5, $6, $7,
                $8, $9, $10,
                $11, $12,
                $13, $14,
                $15
            ) RETURNING id, code
        """,
            code, name, bool(payload.get("is_active", False)),
            payload.get("package_codes") or [],
            discount_type,
            float(payload.get("discount_value") or 0),
            int(payload.get("valid_hours") or 48),
            payload.get("caption_html") or "",
            payload.get("image_path") or "",
            payload.get("extra_buttons") or [],
            payload.get("target_groups") or "all_free",
            payload.get("post_times") or [],
            _parse_dt(payload.get("starts_at")),
            _parse_dt(payload.get("ends_at")),
            int(admin.get("telegram_id") or 0) or None,
        )
    except Exception as exc:
        msg = str(exc)
        if "unique" in msg.lower() or "duplicate" in msg.lower():
            raise HTTPException(409, f"รหัส '{code}' มีอยู่แล้ว")
        logger.exception("create_promotion failed: %s", exc)
        raise HTTPException(500, f"DB error: {msg[:200]}")

    # Clear service cache so bots see the new promo within seconds
    try:
        from shared.promotion_service import clear_cache
        clear_cache()
    except Exception:
        pass

    return {"id": row["id"], "code": row["code"]}


@router.patch("/day0-promos/{promo_id}")
async def update_promotion(
    promo_id: int,
    payload: dict,
    request: Request,
    admin=Depends(require_role("admin")),
):
    """Update a promotion — any subset of fields."""
    row = await pool.fetchrow(
        "SELECT id, name FROM promotions WHERE id = $1", promo_id
    )
    if not row:
        raise HTTPException(404, "promotion not found")

    SCALAR_FIELDS = ("name", "is_active", "discount_type", "discount_value",
                     "valid_hours", "caption_html", "image_path",
                     "target_groups", "starts_at", "ends_at")
    JSONB_FIELDS = ("package_codes", "extra_buttons", "post_times")

    updates = []
    args = []
    for fld in SCALAR_FIELDS:
        if fld in payload:
            updates.append(f"{fld}=${len(args)+1}")
            v = payload[fld]
            if fld == "is_active":
                v = bool(v)
            elif fld == "discount_value":
                v = float(v) if v is not None else 0.0
            elif fld == "valid_hours":
                v = int(v) if v else 48
            elif fld == "discount_type":
                if v not in ("none", "percent", "fixed_off", "fixed_price"):
                    raise HTTPException(400, f"invalid discount_type: {v}")
            elif fld in ("starts_at", "ends_at"):
                v = _parse_dt(v)
            args.append(v)

    for fld in JSONB_FIELDS:
        if fld in payload:
            updates.append(f"{fld}=${len(args)+1}")
            args.append(payload[fld] or [])

    if not updates:
        raise HTTPException(400, "no fields to update")

    updates.append("updated_at=NOW()")
    updates.append(f"updated_by=${len(args)+1}")
    args.append(int(admin.get("telegram_id") or 0) or None)
    args.append(promo_id)

    try:
        await pool.execute(
            f"UPDATE promotions SET {', '.join(updates)} WHERE id=${len(args)}",
            *args,
        )
    except Exception as exc:
        logger.exception("update_promotion failed: %s", exc)
        raise HTTPException(500, f"DB error: {str(exc)[:200]}")

    try:
        from shared.promotion_service import clear_cache
        clear_cache()
    except Exception:
        pass

    return {"ok": True, "id": promo_id}


@router.delete("/day0-promos/{promo_id}")
async def delete_promotion(
    promo_id: int,
    _admin=Depends(require_role("admin")),
):
    """Delete a promotion (cascades to promotion_clicks)."""
    row = await pool.fetchrow("SELECT code FROM promotions WHERE id = $1", promo_id)
    if not row:
        raise HTTPException(404, "promotion not found")

    await pool.execute("DELETE FROM promotions WHERE id = $1", promo_id)
    try:
        from shared.promotion_service import clear_cache
        clear_cache()
    except Exception:
        pass
    return {"deleted": True, "code": row["code"]}


@router.post("/day0-promos/upload-image")
async def upload_promo_image(
    request: Request,
    admin=Depends(require_role("admin")),
):
    """Upload a promo image. Saves to /app/assets/uploads/ — same dir as content templates."""
    import os, time, uuid
    form = await request.form()
    file = form.get("file")
    if not file:
        raise HTTPException(400, "no file uploaded")

    upload_dir = "/app/assets/uploads"
    os.makedirs(upload_dir, exist_ok=True)

    ext = ".png"
    fname = getattr(file, "filename", "")
    if "." in fname:
        ext = "." + fname.rsplit(".", 1)[-1].lower()
        if ext not in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
            ext = ".png"
    safe = f"promo_{int(time.time())}_{str(uuid.uuid4())[:8]}{ext}"
    full_path = f"{upload_dir}/{safe}"

    content = await file.read()
    with open(full_path, "wb") as f:
        f.write(content)

    return {
        "path": f"assets/uploads/{safe}",
        "url": f"/assets/uploads/{safe}",
        "filename": safe,
        "size_bytes": len(content),
    }
