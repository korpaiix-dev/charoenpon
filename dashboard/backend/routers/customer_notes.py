"""Customer notes + rejection reasons — Phase A.2 (2026-06-27)."""
from __future__ import annotations
import logging
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from ..auth.dependencies import require_role
from ..database import pool

logger = logging.getLogger(__name__)
router = APIRouter(tags=["customer-notes"])


class _NoteCreate(BaseModel):
    content: str
    is_pinned: Optional[bool] = False


class _NoteUpdate(BaseModel):
    content: Optional[str] = None
    is_pinned: Optional[bool] = None


@router.get("/customers/{user_id}/notes")
async def list_notes(user_id: int, admin=Depends(require_role("admin"))):
    rows = await pool.fetch(
        "SELECT id, content, is_pinned, created_at, created_by, updated_at "
        "FROM customer_notes WHERE user_id = $1 "
        "ORDER BY is_pinned DESC, created_at DESC",
        user_id,
    )
    return [dict(r) for r in rows]


@router.post("/customers/{user_id}/notes")
async def create_note(user_id: int, req: _NoteCreate, admin=Depends(require_role("admin"))):
    if not req.content or len(req.content.strip()) < 1:
        raise HTTPException(400, "content cannot be empty")
    if len(req.content) > 2000:
        raise HTTPException(400, "content too long (max 2000)")
    row = await pool.fetchrow(
        "INSERT INTO customer_notes (user_id, content, is_pinned, created_by) "
        "VALUES ($1, $2, $3, $4) RETURNING id",
        user_id, req.content.strip(), bool(req.is_pinned), admin['telegram_id'],
    )
    try:
        await pool.execute(
            "INSERT INTO admin_logs (admin_id, action, target_type, target_id, details) "
            "VALUES ($1, 'note_create', 'user', $2, $3)",
            admin['telegram_id'], user_id, req.content[:200],
        )
    except Exception:
        pass
    return {"ok": True, "id": row['id']}


@router.patch("/customers/{user_id}/notes/{note_id}")
async def update_note(user_id: int, note_id: int, req: _NoteUpdate, admin=Depends(require_role("admin"))):
    existing = await pool.fetchrow(
        "SELECT content, is_pinned FROM customer_notes WHERE id = $1 AND user_id = $2",
        note_id, user_id,
    )
    if not existing:
        raise HTTPException(404, "note not found")
    updates = []
    args = []
    idx = 1
    if req.content is not None:
        if len(req.content) > 2000:
            raise HTTPException(400, "content too long")
        updates.append(f"content = ${idx}"); args.append(req.content.strip()); idx += 1
    if req.is_pinned is not None:
        updates.append(f"is_pinned = ${idx}"); args.append(bool(req.is_pinned)); idx += 1
    if not updates:
        raise HTTPException(400, "nothing to update")
    updates.append("updated_at = NOW()")
    args.extend([note_id, user_id])
    sql = f"UPDATE customer_notes SET {', '.join(updates)} WHERE id = ${idx} AND user_id = ${idx+1}"
    await pool.execute(sql, *args)
    return {"ok": True}


@router.delete("/customers/{user_id}/notes/{note_id}")
async def delete_note(user_id: int, note_id: int, admin=Depends(require_role("admin"))):
    res = await pool.execute(
        "DELETE FROM customer_notes WHERE id = $1 AND user_id = $2",
        note_id, user_id,
    )
    try:
        await pool.execute(
            "INSERT INTO admin_logs (admin_id, action, target_type, target_id, details) "
            "VALUES ($1, 'note_delete', 'user', $2, $3)",
            admin['telegram_id'], user_id, f"note_id={note_id}",
        )
    except Exception:
        pass
    return {"ok": True}


@router.get("/rejection-reasons")
async def list_reasons(admin=Depends(require_role("admin"))):
    rows = await pool.fetch(
        "SELECT id, label, customer_message, sort_order, enabled "
        "FROM rejection_reasons WHERE enabled = TRUE ORDER BY sort_order"
    )
    return [dict(r) for r in rows]
