"""Group broadcast — let admin post to multiple groups from dashboard.

Endpoints:
  GET  /api/group-broadcast/groups        — list available groups for picker
  POST /api/group-broadcast/preview       — render HTML preview (no send)
  POST /api/group-broadcast/send          — actually send to selected groups
  GET  /api/group-broadcast/history       — recent broadcasts log
"""
from __future__ import annotations

import asyncio
import io
import logging
import os
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from ..auth.dependencies import require_role
from ..database import pool

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/group-broadcast", tags=["group-broadcast"])


# Use Guardian Bot for posting to groups (it's admin in all FREE/VIP groups)
GUARDIAN_BOT_TOKEN = os.environ.get("GUARDIAN_BOT_TOKEN") or os.environ.get("NAMWAN_TOKEN", "")
CONTENT_BOT_TOKEN = os.environ.get("CONTENT_BOT_TOKEN", "")


async def _ensure_history_table():
    """Create group_broadcasts table if not exists."""
    await pool.execute("""
        CREATE TABLE IF NOT EXISTS group_broadcasts (
            id SERIAL PRIMARY KEY,
            admin_id INTEGER,
            admin_name VARCHAR(255),
            target_slugs JSONB NOT NULL,
            message_html TEXT NOT NULL,
            has_image BOOLEAN NOT NULL DEFAULT FALSE,
            sent_count INTEGER NOT NULL DEFAULT 0,
            failed_count INTEGER NOT NULL DEFAULT 0,
            errors JSONB,
            sent_at TIMESTAMP NOT NULL DEFAULT NOW()
        )
    """)


@router.get("/groups")
async def list_broadcast_groups(admin=Depends(require_role("admin"))):
    """Return all active groups for picker, categorized."""
    rows = await pool.fetch("""
        SELECT slug::text AS slug, chat_id, title, min_tier::text AS min_tier
        FROM group_registry WHERE is_active = TRUE
        ORDER BY min_tier, id
    """)

    free = [dict(r) for r in rows if r["min_tier"] == "FREE"]
    vip = [dict(r) for r in rows if r["min_tier"] != "FREE"]

    return {"free": free, "vip": vip, "total": len(free) + len(vip)}


@router.post("/send")
async def send_broadcast(
    slugs: str = Form(..., description="JSON array of slugs"),
    message: str = Form(...),
    parse_mode: str = Form("HTML"),
    disable_preview: bool = Form(False),
    image: Optional[UploadFile] = File(None),
    buttons: Optional[str] = Form(None, description="JSON array of {text,url}"),
    admin=Depends(require_role("admin")),
):
    """Send broadcast message (with optional image) to selected groups."""
    import json as _json

    try:
        target_slugs = _json.loads(slugs) if isinstance(slugs, str) else slugs
    except Exception:
        raise HTTPException(400, "slugs must be JSON array")
    if not target_slugs or not isinstance(target_slugs, list):
        raise HTTPException(400, "Select at least 1 group")

    # Validate message + image size
    if not message.strip() and not image:
        raise HTTPException(400, "Message or image required")
    if len(message) > 4000:
        raise HTTPException(400, "Message too long (max 4000)")

    # Parse buttons -> Telegram inline_keyboard
    reply_markup = None
    if buttons:
        try:
            btn_list = _json.loads(buttons)
            if isinstance(btn_list, list) and btn_list:
                keyboard = []
                for b in btn_list[:10]:  # max 10 buttons
                    txt = (b.get("text") or "").strip()[:64]
                    url = (b.get("url") or "").strip()[:256]
                    if txt and url and (url.startswith("http://") or url.startswith("https://") or url.startswith("tg://")):
                        keyboard.append([{"text": txt, "url": url}])
                if keyboard:
                    reply_markup = {"inline_keyboard": keyboard}
        except Exception as exc:
            logger.warning("buttons parse failed: %s", exc)

    # Lookup chat_ids
    groups = await pool.fetch("""
        SELECT slug::text AS slug, chat_id, title
        FROM group_registry WHERE slug = ANY($1::groupslug[]) AND is_active = TRUE
    """, target_slugs)
    if not groups:
        raise HTTPException(404, "No matching active groups")

    if not GUARDIAN_BOT_TOKEN:
        raise HTTPException(500, "GUARDIAN_BOT_TOKEN not configured")

    # Read image bytes once
    image_bytes = None
    image_filename = None
    if image:
        image_bytes = await image.read()
        if len(image_bytes) > 20 * 1024 * 1024:
            raise HTTPException(400, "Image too large (max 20MB)")
        image_filename = image.filename or "broadcast.jpg"

    await _ensure_history_table()

    # Send to each group
    sent = 0
    failed = 0
    errors: list[dict] = []

    async def _send_to(gid: int, slug: str, title: str):
        nonlocal sent, failed
        try:
            async with httpx.AsyncClient(timeout=30.0) as cli:
                if image_bytes:
                    files = {"photo": (image_filename, image_bytes, "application/octet-stream")}
                    data = {
                        "chat_id": str(gid),
                        "caption": message[:1024],
                        "parse_mode": parse_mode,
                    }
                    if reply_markup:
                        data["reply_markup"] = _json.dumps(reply_markup)
                    r = await cli.post(
                        f"https://api.telegram.org/bot{GUARDIAN_BOT_TOKEN}/sendPhoto",
                        files=files, data=data,
                    )
                else:
                    data = {
                        "chat_id": str(gid),
                        "text": message,
                        "parse_mode": parse_mode,
                        "disable_web_page_preview": disable_preview,
                    }
                    if reply_markup:
                        data["reply_markup"] = reply_markup
                    r = await cli.post(
                        f"https://api.telegram.org/bot{GUARDIAN_BOT_TOKEN}/sendMessage",
                        json=data,
                    )
                resp = r.json()
                if resp.get("ok"):
                    sent += 1
                else:
                    failed += 1
                    errors.append({"slug": slug, "chat_id": gid, "error": resp.get("description", "unknown")})
        except Exception as exc:
            failed += 1
            errors.append({"slug": slug, "chat_id": gid, "error": str(exc)[:200]})
        # Telegram rate limit safety
        await asyncio.sleep(0.5)

    for g in groups:
        await _send_to(int(g["chat_id"]), g["slug"], g["title"])

    # Save history
    try:
        await pool.execute("""
            INSERT INTO group_broadcasts (admin_id, admin_name, target_slugs, message_html, has_image, sent_count, failed_count, errors)
            VALUES ($1, $2, $3::jsonb, $4, $5, $6, $7, $8::jsonb)
        """,
            admin["id"], admin.get("display_name", "?"),
            _json.dumps(target_slugs), message, bool(image_bytes),
            sent, failed, _json.dumps(errors) if errors else None,
        )
    except Exception as exc:
        logger.warning("save broadcast history failed: %s", exc)

    return {
        "ok": True,
        "sent": sent,
        "failed": failed,
        "total": len(groups),
        "errors": errors,
    }


@router.get("/history")
async def broadcast_history(limit: int = 30, admin=Depends(require_role("admin"))):
    """Recent broadcast history."""
    await _ensure_history_table()
    rows = await pool.fetch("""
        SELECT id, admin_name, target_slugs, LEFT(message_html, 200) AS preview,
               has_image, sent_count, failed_count, sent_at
        FROM group_broadcasts ORDER BY id DESC LIMIT $1
    """, max(1, min(limit, 100)))
    return [dict(r) for r in rows]


@router.get("/history/{bid}")
async def broadcast_detail(bid: int, admin=Depends(require_role("admin"))):
    """Full detail of one broadcast (for re-using as template)."""
    row = await pool.fetchrow("SELECT * FROM group_broadcasts WHERE id = $1", bid)
    if not row:
        raise HTTPException(404, "not found")
    return dict(row)
