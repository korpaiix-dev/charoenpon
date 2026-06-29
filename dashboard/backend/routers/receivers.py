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
               promptpay_number, proxy_last4, qr_url,
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
    cumulative_received: Optional[float] = None  # manual override (rare)
    qr_url: Optional[str] = None


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
    if req.cumulative_received is not None:
        if req.cumulative_received < 0:
            raise HTTPException(400, "cumulative_received must be >= 0")
        sets.append(f"cumulative_received = ${idx}")
        params.append(Decimal(str(req.cumulative_received)))
        idx += 1
    if req.qr_url is not None:
        sets.append(f"qr_url = ${idx}")
        params.append(req.qr_url[:1000] if req.qr_url else None)
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
    if req.cumulative_received is not None:
        changes.append(f"cumulative={req.cumulative_received}")
    if req.qr_url is not None:
        changes.append("qr_url=updated")
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


class ReceiverCreate(BaseModel):
    owner_name: str
    bank_code: str  # e.g. "SCB" "KBANK" "BAY"
    bank_name_th: str  # e.g. "ธนาคารไทยพาณิชย์"
    account_no: str
    name_keyword: str  # for slip OCR matching (substring of owner_name)
    bank_last5: Optional[str] = None
    promptpay_number: Optional[str] = None
    proxy_last4: Optional[str] = None
    qr_url: Optional[str] = None
    weight: int = 1
    alert_threshold: float = 5000
    notes: Optional[str] = None


@router.post("")
async def create_receiver(req: ReceiverCreate, admin=Depends(require_role("admin"))):
    """Create a new receiver account."""
    if req.weight < 0 or req.weight > 100:
        raise HTTPException(400, "weight must be 0-100")
    if req.alert_threshold < 0:
        raise HTTPException(400, "alert_threshold must be >= 0")

    # Derive bank_last5 from account_no if not provided
    last5 = req.bank_last5
    if not last5 and req.account_no:
        digits_only = "".join(c for c in req.account_no if c.isdigit())
        last5 = digits_only[-5:] if len(digits_only) >= 5 else digits_only

    try:
        row = await pool.fetchrow(
            """
            INSERT INTO receiver_accounts
                (owner_name, bank_code, bank_name_th, account_no, name_keyword,
                 bank_last5, promptpay_number, proxy_last4, qr_url,
                 weight, alert_threshold, notes, enabled,
                 cumulative_received, last_alert_at_amount)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, TRUE, 0, 0)
            RETURNING id, owner_name
            """,
            req.owner_name.strip()[:255],
            req.bank_code.strip().upper()[:10],
            req.bank_name_th.strip()[:255],
            req.account_no.strip()[:20],
            req.name_keyword.strip()[:255],
            (last5 or None),
            req.promptpay_number.strip() if req.promptpay_number else None,
            req.proxy_last4.strip() if req.proxy_last4 else None,
            req.qr_url.strip() if req.qr_url else None,
            int(req.weight),
            Decimal(str(req.alert_threshold)),
            req.notes.strip()[:500] if req.notes else None,
        )
    except Exception as exc:
        msg = str(exc)
        if "duplicate key" in msg or "account_no_key" in msg:
            raise HTTPException(409, f"บัญชี {req.account_no} มีอยู่แล้ว")
        logger.exception("create_receiver failed: %s", exc)
        raise HTTPException(500, f"create failed: {msg[:200]}")

    await _log(
        admin["telegram_id"], "receiver_create", row["id"],
        f"owner={row['owner_name']} bank={req.bank_code} acct={req.account_no}"
    )
    return {"ok": True, "id": row["id"], "owner_name": row["owner_name"]}


# QR upload — saves to /root/charoenpon/assets/receiver_qr/ then returns public URL
from fastapi import UploadFile, File
import os, hashlib, time

QR_DIR = "/app/dashboard/frontend/assets/receiver_qr"

@router.post("/upload-qr")
async def upload_qr(file: UploadFile = File(...), admin=Depends(require_role("admin"))):
    """Upload a QR code image; returns a URL the frontend stores in qr_url."""
    ext = (file.filename or "").lower().rsplit(".", 1)[-1] if "." in (file.filename or "") else "png"
    if ext not in ("png", "jpg", "jpeg", "webp"):
        raise HTTPException(400, "รูปต้องเป็น png / jpg / webp")
    data = await file.read()
    if not data:
        raise HTTPException(400, "ไฟล์ว่าง")
    if len(data) > 4 * 1024 * 1024:
        raise HTTPException(400, "ไฟล์ใหญ่เกิน 4MB")

    os.makedirs(QR_DIR, exist_ok=True)
    digest = hashlib.sha256(data).hexdigest()[:16]
    fname = f"qr_{int(time.time())}_{digest}.{ext}"
    fpath = os.path.join(QR_DIR, fname)
    with open(fpath, "wb") as f:
        f.write(data)
    url = f"/assets/receiver_qr/{fname}"
    return {"ok": True, "url": url, "size": len(data)}

