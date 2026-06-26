"""Feature flag management — Phase A.1 (2026-06-26).

Allows boss/admin to toggle features ON/OFF without redeploy.
Every new Dashboard 2.0 feature must respect a flag here.

Default = all OFF = existing production behavior.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..auth.dependencies import require_role
from ..database import pool

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/feature-flags", tags=["feature-flags"])


class _FlagUpdate(BaseModel):
    enabled: Optional[bool] = None
    scope: Optional[str] = None      # all / admin / canary
    canary_user_ids: Optional[list[int]] = None


async def _log(admin_id: int, action: str, target: str, details: str) -> None:
    try:
        await pool.execute(
            "INSERT INTO admin_logs (admin_id, action, target_type, target_id, details) "
            "VALUES ($1, $2, 'feature_flag', 0, $3)",
            admin_id, action, f"{target}: {details}",
        )
    except Exception as exc:
        logger.warning("audit log failed: %s", exc)


@router.get("")
async def list_flags(admin=Depends(require_role("admin"))):
    """List all known feature flags."""
    rows = await pool.fetch(
        "SELECT flag_key, enabled, scope, canary_user_ids, description, updated_at, updated_by "
        "FROM feature_flags ORDER BY flag_key"
    )
    return [dict(r) for r in rows]


@router.patch("/{flag_key}")
async def update_flag(
    flag_key: str,
    req: _FlagUpdate,
    admin=Depends(require_role("admin")),
):
    """Update one flag. Only owner can flip to ON in scope=all (safety)."""
    row = await pool.fetchrow(
        "SELECT enabled, scope, canary_user_ids FROM feature_flags WHERE flag_key = $1",
        flag_key,
    )
    if not row:
        raise HTTPException(404, f"flag '{flag_key}' not found")

    # Safety check: flipping a flag ON in scope=all requires owner role
    new_enabled = req.enabled if req.enabled is not None else row['enabled']
    new_scope = req.scope if req.scope is not None else row['scope']
    if new_enabled and new_scope == 'all' and admin.get('role') != 'owner':
        raise HTTPException(
            403,
            "Only owner can enable a feature for ALL users. Use scope=admin or canary instead."
        )

    updates = []
    args = []
    idx = 1
    if req.enabled is not None:
        updates.append(f"enabled = ${idx}"); args.append(req.enabled); idx += 1
    if req.scope is not None:
        if req.scope not in ('all', 'admin', 'canary'):
            raise HTTPException(400, "scope must be all/admin/canary")
        updates.append(f"scope = ${idx}"); args.append(req.scope); idx += 1
    if req.canary_user_ids is not None:
        updates.append(f"canary_user_ids = ${idx}"); args.append(req.canary_user_ids); idx += 1

    if not updates:
        raise HTTPException(400, "nothing to update")

    updates.append(f"updated_at = NOW()")
    updates.append(f"updated_by = ${idx}"); args.append(admin['telegram_id']); idx += 1
    args.append(flag_key)

    sql = f"UPDATE feature_flags SET {', '.join(updates)} WHERE flag_key = ${idx}"
    await pool.execute(sql, *args)

    await _log(
        admin['telegram_id'],
        'feature_flag_update',
        flag_key,
        f"enabled={req.enabled} scope={req.scope} canary={req.canary_user_ids}",
    )

    new_row = await pool.fetchrow(
        "SELECT flag_key, enabled, scope, canary_user_ids, description, updated_at, updated_by "
        "FROM feature_flags WHERE flag_key = $1",
        flag_key,
    )
    return {"ok": True, **dict(new_row)}
