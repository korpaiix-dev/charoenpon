"""Promo Manager — Phase B.1 (2026-06-27).

CRUD for promo_config table.
Allows admin to manage Comeback DM / Quick Buy / Gacha Discount / Group Bot settings.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..auth.dependencies import require_role
from ..database import pool

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/promo-manager", tags=["promo-manager"])


class _ConfigUpdate(BaseModel):
    value_json: Any  # any JSON-serializable value


async def _log(admin_id: int, action: str, key: str, details: str) -> None:
    try:
        await pool.execute(
            "INSERT INTO admin_logs (admin_id, action, target_type, target_id, details) "
            "VALUES ($1, $2, 'promo_config', 0, $3)",
            admin_id, action, f"{key}: {details}"[:500],
        )
    except Exception as exc:
        logger.warning("audit log failed: %s", exc)


@router.get("")
async def list_all_configs(
    category: Optional[str] = None,
    admin=Depends(require_role("admin")),
):
    """List all promo configs, optionally filtered by category."""
    if category:
        rows = await pool.fetch(
            "SELECT config_key, value_json, description, category, updated_at, updated_by "
            "FROM promo_config WHERE category = $1 ORDER BY config_key",
            category,
        )
    else:
        rows = await pool.fetch(
            "SELECT config_key, value_json, description, category, updated_at, updated_by "
            "FROM promo_config ORDER BY category, config_key"
        )
    return [dict(r) for r in rows]


@router.get("/{config_key}")
async def get_config(config_key: str, admin=Depends(require_role("admin"))):
    """Get one config."""
    row = await pool.fetchrow(
        "SELECT config_key, value_json, description, category, updated_at, updated_by "
        "FROM promo_config WHERE config_key = $1",
        config_key,
    )
    if not row:
        raise HTTPException(404, "config not found")
    return dict(row)


@router.patch("/{config_key}")
async def update_config(
    config_key: str,
    req: _ConfigUpdate,
    admin=Depends(require_role("admin")),
):
    """Update one config value."""
    existing = await pool.fetchrow(
        "SELECT value_json, category FROM promo_config WHERE config_key = $1",
        config_key,
    )
    if not existing:
        raise HTTPException(404, "config not found")

    # Validate value type matches existing (basic check)
    new_value = req.value_json
    if new_value is None:
        raise HTTPException(400, "value_json cannot be null")

    # Strict type guard: if existing was scalar int, new should be int. If dict, new should be dict.
    # This prevents accidentally changing comeback_r1_discount_pct from 30 → "30" (string).
    old_value = existing['value_json']
    if isinstance(old_value, (int, float)) and not isinstance(new_value, (int, float)):
        raise HTTPException(400, f"value must be number (got {type(new_value).__name__})")
    if isinstance(old_value, bool) and not isinstance(new_value, bool):
        raise HTTPException(400, "value must be boolean")
    if isinstance(old_value, str) and not isinstance(new_value, str):
        raise HTTPException(400, "value must be string")
    if isinstance(old_value, dict) and not isinstance(new_value, dict):
        raise HTTPException(400, "value must be JSON object")

    # Range validation for known % keys
    if 'discount_pct' in config_key and isinstance(new_value, (int, float)):
        if not (0 <= new_value <= 99):
            raise HTTPException(400, "discount % must be 0-99")
    if 'days' in config_key and isinstance(new_value, (int, float)):
        if not (0 <= new_value <= 365):
            raise HTTPException(400, "days must be 0-365")

    await pool.execute(
        "UPDATE promo_config SET value_json = $1, updated_at = NOW(), updated_by = $2 WHERE config_key = $3",
        json.dumps(new_value), admin['telegram_id'], config_key,
    )

    await _log(
        admin['telegram_id'],
        'promo_config_update',
        config_key,
        f"{old_value} → {new_value}",
    )

    return {"ok": True, "config_key": config_key, "value_json": new_value}


@router.post("/cache-clear")
async def cache_clear(admin=Depends(require_role("admin"))):
    """Force-clear in-memory cache so bot reads new values immediately."""
    try:
        from shared.promo_config import clear_cache
        clear_cache()
    except Exception:
        pass
    return {"ok": True}
