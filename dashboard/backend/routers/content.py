"""Content management router."""
from fastapi import APIRouter, Depends, Request, HTTPException
from ..auth.dependencies import get_current_admin, require_role
from ..database import pool
import json

router = APIRouter(prefix="/api/content", tags=["content"])

async def _log(admin_id, action, entity_type, entity_id, details, ip):
    await pool.execute(
        "INSERT INTO dashboard_activity_log (admin_id, action, entity_type, entity_id, details, ip_address) VALUES ($1,$2,$3,$4,$5::jsonb,$6)",
        admin_id, action, entity_type, entity_id, json.dumps(details) if details else None, ip
    )

@router.get("/queue")
async def list_queue(admin=Depends(get_current_admin)):
    rows = await pool.fetch("""
        SELECT * FROM content_queue WHERE is_used = FALSE
        ORDER BY created_at ASC
    """)
    return [dict(r) for r in rows]

@router.delete("/queue/{item_id}")
async def delete_queue_item(item_id: int, request: Request, admin=Depends(require_role("admin"))):
    await pool.execute("DELETE FROM content_queue WHERE id = $1", item_id)
    ip = request.client.host if request.client else None
    await _log(admin["id"], "delete_content_queue", "content_queue", item_id, None, ip)
    return {"ok": True}

@router.post("/queue/reorder")
async def reorder_queue(request: Request, admin=Depends(require_role("admin"))):
    body = await request.json()
    id_order = body.get("id_order", [])
    # We'll update created_at to enforce ordering
    for i, cid in enumerate(id_order):
        await pool.execute(
            "UPDATE content_queue SET created_at = NOW() + ($1 || ' seconds')::interval WHERE id = $2",
            str(i), cid
        )
    return {"ok": True}

@router.get("/schedule")
async def list_schedule(admin=Depends(get_current_admin)):
    rows = await pool.fetch("""
        SELECT * FROM content_schedule
        ORDER BY scheduled_at DESC LIMIT 100
    """)
    return [dict(r) for r in rows]

@router.put("/schedule/{schedule_id}")
async def update_schedule(schedule_id: int, request: Request, admin=Depends(require_role("admin"))):
    body = await request.json()
    updates = []
    params = []
    idx = 1
    for field in ["scheduled_at", "group_slug", "caption", "content_type"]:
        if field in body:
            if field == "scheduled_at":
                updates.append(f"{field} = ${idx}::timestamp")
            elif field == "group_slug":
                updates.append(f"{field} = ${idx}::groupslug")
            else:
                updates.append(f"{field} = ${idx}")
            params.append(body[field])
            idx += 1
    if not updates:
        raise HTTPException(400, "No fields")
    params.append(schedule_id)
    await pool.execute(f"UPDATE content_schedule SET {', '.join(updates)} WHERE id = ${idx}", *params)
    ip = request.client.host if request.client else None
    await _log(admin["id"], "update_content_schedule", "content_schedule", schedule_id, body, ip)
    return {"ok": True}

@router.post("/schedule")
async def create_schedule(request: Request, admin=Depends(require_role("admin"))):
    body = await request.json()
    row = await pool.fetchrow("""
        INSERT INTO content_schedule (group_slug, scheduled_at, content_type, caption, is_sent, created_by)
        VALUES ($1::groupslug, $2::timestamp, $3, $4, FALSE, $5)
        RETURNING id
    """, body["group_slug"], body["scheduled_at"], body.get("content_type", "teaser"), 
        body.get("caption", ""), admin["id"])
    return {"ok": True, "id": row["id"]}

@router.get("/teaser-stats")
async def teaser_stats(days: int = 30, admin=Depends(require_role("admin"))):
    rows = await pool.fetch("""
        SELECT tc.created_at::date as date, COUNT(*) as clicks
        FROM teaser_clicks tc
        WHERE tc.created_at >= CURRENT_DATE - $1
        GROUP BY tc.created_at::date ORDER BY date DESC
    """, days)
    
    schedules = await pool.fetch("""
        SELECT scheduled_at::date as date, COUNT(*) as sent
        FROM content_schedule WHERE is_sent = TRUE AND scheduled_at >= CURRENT_DATE - $1
        GROUP BY scheduled_at::date ORDER BY date DESC
    """, days)
    
    return {"clicks": [dict(r) for r in rows], "schedules": [dict(r) for r in schedules]}

@router.get("/caption-template")
async def get_caption(admin=Depends(get_current_admin)):
    # Store in a simple key-value approach — use first content_schedule caption as template
    return {"template": "🔥 {name}\n💰 ราคา: {price} บาท\n🔗 สมัคร: {link}"}

@router.put("/caption-template")
async def update_caption(request: Request, admin=Depends(require_role("admin"))):
    body = await request.json()
    # For now just return ok — template stored in frontend/env
    return {"ok": True, "template": body.get("template", "")}
