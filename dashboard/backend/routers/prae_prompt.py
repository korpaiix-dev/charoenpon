"""Prae prompt editor — read/edit/version system prompt."""
from __future__ import annotations

import os
from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException

from ..auth.dependencies import require_role, get_current_admin
from ..database import pool

router = APIRouter(prefix="/prae-prompt", tags=["prae-prompt"])

PROMPT_FILE = "/app/data/prae_prompt_override.txt"


@router.get("/active")
async def get_active_prompt(admin=Depends(require_role("admin"))):
    """Get currently-active prompt (file override or default)."""
    file_content = None
    if os.path.exists(PROMPT_FILE):
        try:
            with open(PROMPT_FILE, "r", encoding="utf-8") as f:
                file_content = f.read()
        except Exception:
            pass

    # Default SYSTEM_PROMPT from prae_team_engine
    default = ""
    try:
        from shared.prae_team_engine import SYSTEM_PROMPT
        default = SYSTEM_PROMPT
    except Exception:
        pass

    return {
        "active_source": "file" if file_content else "constant",
        "content": file_content or default,
        "default": default,
        "override_exists": file_content is not None,
        "char_count": len(file_content or default),
    }


@router.get("/versions")
async def list_versions(admin=Depends(require_role("admin"))):
    """List all saved versions."""
    rows = await pool.fetch("""
        SELECT id, name, version, is_active, notes, created_by, created_at,
               LENGTH(content) AS char_count
        FROM ai_prompts
        WHERE name = 'prae'
        ORDER BY version DESC
        LIMIT 50
    """)
    return [dict(r) for r in rows]


@router.get("/versions/{vid}")
async def get_version(vid: int, admin=Depends(require_role("admin"))):
    row = await pool.fetchrow("SELECT * FROM ai_prompts WHERE id = $1", vid)
    if not row:
        raise HTTPException(404, "not found")
    return dict(row)


class _SavePromptReq(BaseModel):
    content: str
    notes: str = ""
    activate: bool = True


@router.post("/save")
async def save_prompt(req: _SavePromptReq, admin=Depends(get_current_admin)):
    """Save new prompt version + optionally activate (write to file)."""
    if not req.content.strip():
        raise HTTPException(400, "content required")

    # Find next version number
    latest = await pool.fetchval(
        "SELECT COALESCE(MAX(version), 0) FROM ai_prompts WHERE name = 'prae'"
    )
    new_version = (latest or 0) + 1

    # If activate, deactivate previous + write file
    if req.activate:
        await pool.execute("UPDATE ai_prompts SET is_active = FALSE WHERE name = 'prae'")
        # Write override file
        os.makedirs(os.path.dirname(PROMPT_FILE), exist_ok=True)
        with open(PROMPT_FILE, "w", encoding="utf-8") as f:
            f.write(req.content)

    row = await pool.fetchrow("""
        INSERT INTO ai_prompts (name, version, content, is_active, notes, created_by)
        VALUES ('prae', $1, $2, $3, $4, $5)
        RETURNING id, version
    """, new_version, req.content, req.activate, req.notes[:500] if req.notes else None,
        admin["telegram_id"])

    try:
        await pool.execute(
            "INSERT INTO admin_logs (admin_id, action, target_type, target_id, details) "
            "VALUES ($1, 'prae_prompt_save', 'prompt', $2, $3)",
            admin["telegram_id"], row["id"],
            f"version={new_version} active={req.activate} chars={len(req.content)}"
        )
    except Exception:
        pass

    return {"ok": True, "id": row["id"], "version": new_version, "active": req.activate}


@router.post("/activate/{vid}")
async def activate_version(vid: int, admin=Depends(get_current_admin)):
    """Activate an existing version."""
    row = await pool.fetchrow("SELECT content, version FROM ai_prompts WHERE id = $1", vid)
    if not row:
        raise HTTPException(404, "not found")
    await pool.execute("UPDATE ai_prompts SET is_active = FALSE WHERE name = 'prae'")
    await pool.execute("UPDATE ai_prompts SET is_active = TRUE WHERE id = $1", vid)
    os.makedirs(os.path.dirname(PROMPT_FILE), exist_ok=True)
    with open(PROMPT_FILE, "w", encoding="utf-8") as f:
        f.write(row["content"])
    try:
        await pool.execute(
            "INSERT INTO admin_logs (admin_id, action, target_type, target_id, details) "
            "VALUES ($1, 'prae_prompt_activate', 'prompt', $2, $3)",
            admin["telegram_id"], vid, f"version={row['version']}"
        )
    except Exception:
        pass
    return {"ok": True, "id": vid, "version": row["version"]}


@router.delete("/override")
async def remove_override(admin=Depends(get_current_admin)):
    """Reset to constant default (delete override file)."""
    if os.path.exists(PROMPT_FILE):
        os.remove(PROMPT_FILE)
    await pool.execute("UPDATE ai_prompts SET is_active = FALSE WHERE name = 'prae'")
    try:
        await pool.execute(
            "INSERT INTO admin_logs (admin_id, action, target_type, target_id, details) "
            "VALUES ($1, 'prae_prompt_reset', 'prompt', 0, $2)",
            admin["telegram_id"], "reset to constant default"
        )
    except Exception:
        pass
    return {"ok": True, "reset": True}
