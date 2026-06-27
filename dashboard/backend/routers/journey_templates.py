"""Customer Journey templates router — Welcome / Comeback / Exit Survey.

Reads from bot_messages where category='journey'.
"""
import json
import logging
from fastapi import APIRouter, Depends, HTTPException, Request

from ..auth.dependencies import require_role
from ..database import pool

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin", tags=["journey-templates"])


JOURNEY_KEYS_META = {
    # Welcome (4 stages) — sent during first 24h
    "journey_welcome_instant": {"flow": "welcome", "label": "Stage 0 — ทักทันที (instant)", "order": 1, "wired": True},
    "journey_welcome_3h":      {"flow": "welcome", "label": "Stage 1 — 3 ชม.", "order": 2, "wired": True},
    "journey_welcome_12h":     {"flow": "welcome", "label": "Stage 2 — 12 ชม.", "order": 3, "wired": True},
    "journey_welcome_23h":     {"flow": "welcome", "label": "Stage 3 — 23 ชม. (ชั่วโมงสุดท้าย)", "order": 4, "wired": True},
    # Comeback Round 1 (4 variants — A/B/C/D test)
    "journey_comeback_r1_a":   {"flow": "comeback", "label": "รอบ 1 · แบบ A — FOMO พลาดคลิป", "order": 5, "wired": True},
    "journey_comeback_r1_b":   {"flow": "comeback", "label": "รอบ 1 · แบบ B — โบนัสกาชา", "order": 6, "wired": True},
    "journey_comeback_r1_c":   {"flow": "comeback", "label": "รอบ 1 · แบบ C — Soft จำเราได้ไหม", "order": 7, "wired": True},
    "journey_comeback_r1_d":   {"flow": "comeback", "label": "รอบ 1 · แบบ D — กระชับ", "order": 8, "wired": True},
    # Comeback Round 2 (3 variants)
    "journey_comeback_r2_a":   {"flow": "comeback", "label": "รอบ 2 · แบบ A — โอกาสสุดท้าย", "order": 9, "wired": True},
    "journey_comeback_r2_b":   {"flow": "comeback", "label": "รอบ 2 · แบบ B — ของขวัญ", "order": 10, "wired": True},
    "journey_comeback_r2_c":   {"flow": "comeback", "label": "รอบ 2 · แบบ C — เน้นราคา", "order": 11, "wired": True},
    # Exit Survey
    "journey_exit_survey_question": {"flow": "exit", "label": "ถามเหตุผล", "order": 12, "wired": True},
    "journey_exit_thanks":          {"flow": "exit", "label": "ส่งส่วนลด", "order": 13, "wired": True},
}


@router.get("/journey-templates")
async def list_journey_templates(admin=Depends(require_role("admin"))):
    """List ทุก template ของ Customer Journey พร้อม metadata."""
    rows = await pool.fetch(
        "SELECT message_key, content_html, description, available_placeholders, updated_at "
        "FROM bot_messages WHERE category = $1 ORDER BY message_key",
        "journey",
    )
    result = []
    for r in rows:
        d = dict(r)
        meta = JOURNEY_KEYS_META.get(d["message_key"], {"flow": "other", "label": d["message_key"], "order": 99, "wired": True})
        d.update(meta)
        # parse placeholders if needed
        if isinstance(d.get("available_placeholders"), str):
            try:
                d["available_placeholders"] = json.loads(d["available_placeholders"])
            except Exception:
                d["available_placeholders"] = []
        result.append(d)
    # sort by order
    result.sort(key=lambda x: x.get("order", 99))
    return result


@router.patch("/journey-templates/{message_key}")
async def update_journey_template(
    message_key: str,
    payload: dict,
    request: Request,
    admin=Depends(require_role("owner")),
):
    """Update content_html + description."""
    if message_key not in JOURNEY_KEYS_META:
        raise HTTPException(404, f"unknown journey key: {message_key}")
    content_html = payload.get("content_html")
    description = payload.get("description")
    if content_html is None and description is None:
        raise HTTPException(400, "no fields to update")

    updates = []
    args = []
    if content_html is not None:
        updates.append(f"content_html = ${len(args)+1}")
        args.append(content_html)
    if description is not None:
        updates.append(f"description = ${len(args)+1}")
        args.append(description)
    updates.append(f"updated_at = NOW()")
    updates.append(f"updated_by = ${len(args)+1}")
    args.append(int(admin.get("telegram_id") or 0) or None)
    args.append(message_key)
    sql = f"UPDATE bot_messages SET {', '.join(updates)} WHERE message_key = ${len(args)}"
    await pool.execute(sql, *args)

    # Clear bot_messages cache (60s TTL ใน Python helper)
    try:
        from shared.bot_messages import _cache
        _cache.pop(message_key, None)
    except Exception:
        pass

    # Audit log
    try:
        await pool.execute(
            "INSERT INTO dashboard_activity_log (admin_id, action, entity_type, entity_id, details, ip_address) "
            "VALUES ($1, $2, $3, $4, $5::jsonb, $6)",
            admin["id"], "update_journey_template", "journey_template", None,
            json.dumps({"message_key": message_key, "preview": (content_html or "")[:200]}),
            request.client.host if request.client else None,
        )
    except Exception as exc:
        logger.warning("audit log failed: %s", exc)

    return {"ok": True, "message_key": message_key}
