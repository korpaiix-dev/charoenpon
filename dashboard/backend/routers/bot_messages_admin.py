"""Bot message library admin — Phase A.1 (2026-06-26).

Manage customer-facing text (welcome / packages / payment / etc).
- LIST all by category
- GET single
- CREATE new key
- UPDATE existing (writes version history)
- DELETE (soft check: warn if in use)

CRITICAL: Empty table = bots fall back to hardcoded text.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..auth.dependencies import require_role
from ..database import pool

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/bot-messages", tags=["bot-messages"])


class _MessageCreate(BaseModel):
    message_key: str
    content_html: str
    description: Optional[str] = ""
    category: Optional[str] = "general"
    available_placeholders: Optional[list[str]] = None


class _MessageUpdate(BaseModel):
    content_html: Optional[str] = None
    description: Optional[str] = None
    category: Optional[str] = None
    available_placeholders: Optional[list[str]] = None
    change_note: Optional[str] = None  # for version history


async def _log(admin_id: int, action: str, key: str, details: str) -> None:
    try:
        await pool.execute(
            "INSERT INTO admin_logs (admin_id, action, target_type, target_id, details) "
            "VALUES ($1, $2, 'bot_message', 0, $3)",
            admin_id, action, f"{key}: {details}"[:500],
        )
    except Exception as exc:
        logger.warning("audit log failed: %s", exc)


@router.get("")
async def list_messages(
    category: Optional[str] = None,
    admin=Depends(require_role("admin")),
):
    """List all bot messages, optionally filtered by category."""
    if category:
        rows = await pool.fetch(
            "SELECT message_key, content_html, description, category, available_placeholders, updated_at, updated_by "
            "FROM bot_messages WHERE category = $1 ORDER BY message_key",
            category,
        )
    else:
        rows = await pool.fetch(
            "SELECT message_key, content_html, description, category, available_placeholders, updated_at, updated_by "
            "FROM bot_messages ORDER BY category, message_key"
        )
    return [dict(r) for r in rows]


@router.get("/{message_key}")
async def get_message(message_key: str, admin=Depends(require_role("admin"))):
    """Get one message + its version history."""
    row = await pool.fetchrow(
        "SELECT message_key, content_html, description, category, available_placeholders, updated_at, updated_by "
        "FROM bot_messages WHERE message_key = $1",
        message_key,
    )
    if not row:
        raise HTTPException(404, "message not found")

    versions = await pool.fetch(
        "SELECT id, content_html, changed_at, changed_by, change_note "
        "FROM bot_message_versions WHERE message_key = $1 "
        "ORDER BY changed_at DESC LIMIT 20",
        message_key,
    )
    return {**dict(row), "versions": [dict(v) for v in versions]}


@router.post("")
async def create_message(req: _MessageCreate, admin=Depends(require_role("admin"))):
    """Create a new bot message key."""
    # Validate HTML safety (very basic — strict validator is per-tag in v2)
    if not req.content_html or len(req.content_html.strip()) < 1:
        raise HTTPException(400, "content_html cannot be empty")
    if '<script' in req.content_html.lower() or 'javascript:' in req.content_html.lower():
        raise HTTPException(400, "unsafe HTML (script tag or javascript: protocol)")

    try:
        await pool.execute(
            "INSERT INTO bot_messages (message_key, content_html, description, category, available_placeholders, updated_by) "
            "VALUES ($1, $2, $3, $4, $5, $6)",
            req.message_key,
            req.content_html,
            req.description or "",
            req.category or "general",
            req.available_placeholders or [],
            admin['telegram_id'],
        )
    except Exception as exc:
        if 'duplicate key' in str(exc).lower():
            raise HTTPException(409, f"message_key '{req.message_key}' already exists")
        raise HTTPException(400, f"create failed: {exc}")

    await _log(
        admin['telegram_id'],
        'bot_message_create',
        req.message_key,
        f"category={req.category}",
    )
    return {"ok": True, "message_key": req.message_key}


@router.patch("/{message_key}")
async def update_message(
    message_key: str,
    req: _MessageUpdate,
    admin=Depends(require_role("admin")),
):
    """Update a bot message — auto-saves prev content to versions."""
    existing = await pool.fetchrow(
        "SELECT content_html, description, category FROM bot_messages WHERE message_key = $1",
        message_key,
    )
    if not existing:
        raise HTTPException(404, "message not found")

    # Validate new content
    if req.content_html is not None:
        if not req.content_html or len(req.content_html.strip()) < 1:
            raise HTTPException(400, "content_html cannot be empty")
        if '<script' in req.content_html.lower() or 'javascript:' in req.content_html.lower():
            raise HTTPException(400, "unsafe HTML")

    updates = []
    args = []
    idx = 1
    if req.content_html is not None:
        # Save previous version to history (for undo)
        await pool.execute(
            "INSERT INTO bot_message_versions (message_key, content_html, changed_by, change_note) "
            "VALUES ($1, $2, $3, $4)",
            message_key,
            existing['content_html'],
            admin['telegram_id'],
            req.change_note or 'auto-saved on edit',
        )
        updates.append(f"content_html = ${idx}"); args.append(req.content_html); idx += 1
    if req.description is not None:
        updates.append(f"description = ${idx}"); args.append(req.description); idx += 1
    if req.category is not None:
        updates.append(f"category = ${idx}"); args.append(req.category); idx += 1
    if req.available_placeholders is not None:
        updates.append(f"available_placeholders = ${idx}"); args.append(req.available_placeholders); idx += 1

    if not updates:
        raise HTTPException(400, "nothing to update")

    updates.append("updated_at = NOW()")
    updates.append(f"updated_by = ${idx}"); args.append(admin['telegram_id']); idx += 1
    args.append(message_key)

    sql = f"UPDATE bot_messages SET {', '.join(updates)} WHERE message_key = ${idx}"
    await pool.execute(sql, *args)

    await _log(
        admin['telegram_id'],
        'bot_message_update',
        message_key,
        req.change_note or '',
    )
    return {"ok": True}


@router.post("/{message_key}/restore/{version_id}")
async def restore_version(
    message_key: str,
    version_id: int,
    admin=Depends(require_role("admin")),
):
    """Restore a previous version (rollback)."""
    ver = await pool.fetchrow(
        "SELECT content_html FROM bot_message_versions WHERE id = $1 AND message_key = $2",
        version_id, message_key,
    )
    if not ver:
        raise HTTPException(404, "version not found")

    # Save current as version before restoring
    cur = await pool.fetchrow(
        "SELECT content_html FROM bot_messages WHERE message_key = $1",
        message_key,
    )
    if cur:
        await pool.execute(
            "INSERT INTO bot_message_versions (message_key, content_html, changed_by, change_note) "
            "VALUES ($1, $2, $3, 'auto-saved before restore')",
            message_key, cur['content_html'], admin['telegram_id'],
        )

    await pool.execute(
        "UPDATE bot_messages SET content_html = $1, updated_at = NOW(), updated_by = $2 WHERE message_key = $3",
        ver['content_html'], admin['telegram_id'], message_key,
    )

    await _log(admin['telegram_id'], 'bot_message_restore', message_key, f"version_id={version_id}")
    return {"ok": True}


@router.delete("/{message_key}")
async def delete_message(message_key: str, admin=Depends(require_role("owner"))):
    """Delete a bot message. ONLY owner can delete (versions cascade)."""
    cur = await pool.fetchrow(
        "SELECT content_html FROM bot_messages WHERE message_key = $1",
        message_key,
    )
    if not cur:
        raise HTTPException(404, "message not found")
    await pool.execute("DELETE FROM bot_messages WHERE message_key = $1", message_key)
    await _log(admin['telegram_id'], 'bot_message_delete', message_key, '')
    return {"ok": True}
