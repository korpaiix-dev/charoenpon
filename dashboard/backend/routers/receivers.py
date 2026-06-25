"""Receivers admin endpoints — bank accounts that receive payments.

Mirrors the Telegram /receivers command into the web dashboard.
Read + write — uses asyncpg pool (same as other routers).
"""
from __future__ import annotations

import logging
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..auth.dependencies import require_role
from ..database import pool

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/receivers", tags=["receivers"])


async def _log(admin_id: int, action: str, target_id: int, details: str) -> None:
    """Insert audit log row (best-effort — failure does not block action)."""
    try:
        await pool.execute(
            "INSERT INTO admin_logs (admin_id, action, target_type, target_id, details) "
            "VALUES ($1, $2, $3, $4, $5)",
            admin_id, action, "receiver", target_id, details,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("audit log failed: %s", exc)


@router.get("")
async def list_receivers(admin=Depends(require_role("admin"))):
    """List all receiver accounts (enabled first)."""
    rows = await pool.fetch("""
        SELECT id, owner_name, bank_name_th, bank_code, account_no, bank_last5,
               promptpay_number, proxy_last4,
               weight, enabled, cumulative_received, alert_threshold, last_alert_at_amount,
               notes, created_at, updated_at
        FROM receiver_accounts
        ORDER BY enabled DESC, id ASC
    """)
    return {
        "items": [dict(r) for r in rows],
        "total": len(rows),
    }


@router.post("/{rid}/reset")
async def reset_receiver(rid: int, admin=Depends(require_role("admin"))):
    """Reset cumulative_received + last_alert_at_amount to 0 (after withdraw)."""
    row = await pool.fetchrow(
        "UPDATE receiver_accounts "
        "SET cumulative_received = 0, last_alert_at_amount = 0, updated_at = NOW() "
        "WHERE id = $1 RETURNING id, owner_name",
        rid,
    )
    if not row:
        raise HTTPException(404, "receiver not found")
    await _log(
        admin["telegram_id"], "receiver_reset", rid,
        f"owner={row['owner_name']}"
    )
    return {"ok": True, "id": rid, "owner": row["owner_name"]}


class ReceiverUpdate(BaseModel):
    enabled: Optional[bool] = None
    weight: Optional[int] = None
    alert_threshold: Optional[float] = None
    notes: Optional[str] = None


@router.patch("/{rid}")
async def update_receiver(rid: int, req: ReceiverUpdate, admin=Depends(require_role("admin"))):
    """Update mutable fields: enabled / weight / alert_threshold / notes."""
    sets = []
    params = []
    idx = 1
    if req.enabled is not None:
        sets.append(f"enabled = ${idx}")
        params.append(req.enabled)
        idx += 1
    if req.weight is not None:
        if req.weight < 0 or req.weight > 100:
            raise HTTPException(400, "weight must be 0-100")
        sets.append(f"weight = ${idx}")
        params.append(int(req.weight))
        idx += 1
    if req.alert_threshold is not None:
        if req.alert_threshold < 0:
            raise HTTPException(400, "alert_threshold must be >= 0")
        sets.append(f"alert_threshold = ${idx}")
        params.append(Decimal(str(req.alert_threshold)))
        idx += 1
    if req.notes is not None:
        sets.append(f"notes = ${idx}")
        params.append(req.notes[:500])
        idx += 1
    if not sets:
        raise HTTPException(400, "no fields to update")

    sets.append("updated_at = NOW()")
    params.append(rid)
    set_clause = ", ".join(sets)
    sql = f"UPDATE receiver_accounts SET {set_clause} WHERE id = ${idx} RETURNING id, owner_name"
    row = await pool.fetchrow(sql, *params)
    if not row:
        raise HTTPException(404, "receiver not found")

    changes = []
    if req.enabled is not None:
        changes.append(f"enabled={req.enabled}")
    if req.weight is not None:
        changes.append(f"weight={req.weight}")
    if req.alert_threshold is not None:
        changes.append(f"threshold={req.alert_threshold}")
    if req.notes is not None:
        changes.append("notes=updated")
    details = f"owner={row['owner_name']} " + " ".join(changes)
    await _log(admin["telegram_id"], "receiver_update", rid, details)
    return {"ok": True, "id": rid, "changes": changes}


@router.get("/{rid}/sender-history")
async def receiver_sender_history(rid: int, limit: int = 20,
                                  admin=Depends(require_role("admin"))):
    """Recent CONFIRMED payments matched to this receiver — audit who paid in here."""
    limit = max(1, min(limit, 100))
    rcv = await pool.fetchrow(
        "SELECT account_no, bank_last5 FROM receiver_accounts WHERE id = $1",
        rid,
    )
    if not rcv:
        raise HTTPException(404, "receiver not found")
    last5 = rcv["bank_last5"]
    acct = rcv["account_no"]

    rows = await pool.fetch("""
        SELECT p.id, p.amount, p.created_at, p.sender_name, p.sender_bank_name,
               p.sender_bank_account, p.slip_trans_ref, u.telegram_id, u.first_name
        FROM payments p
        LEFT JOIN users u ON u.id = p.user_id
        WHERE p.status = 'CONFIRMED'
          AND (
            ($1::text IS NOT NULL AND p.sender_bank_account LIKE '%' || $1 || '%')
            OR p.sender_bank_account = $2
          )
        ORDER BY p.created_at DESC
        LIMIT $3
    """, last5, acct, limit)
    return {"items": [dict(r) for r in rows]}
