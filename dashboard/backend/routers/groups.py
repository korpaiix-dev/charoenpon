"""Group management router."""
from fastapi import APIRouter, Depends, Request, HTTPException
from ..auth.dependencies import require_role
from ..database import pool
from ..services.telegram import create_invite_link, get_chat_member_count
from ..models.schemas import GroupCreate, GroupUpdate
import json

router = APIRouter(prefix="/api/groups", tags=["groups"])

async def _log(admin_id, action, entity_type, entity_id, details, ip):
    await pool.execute(
        "INSERT INTO dashboard_activity_log (admin_id, action, entity_type, entity_id, details, ip_address) VALUES ($1,$2,$3,$4,$5::jsonb,$6)",
        admin_id, action, entity_type, entity_id, json.dumps(details) if details else None, ip
    )

@router.get("")
async def list_groups(admin=Depends(require_role("admin"))):
    rows = await pool.fetch("SELECT * FROM group_registry ORDER BY slug")
    return [dict(r) for r in rows]

@router.get("/categorized")
async def list_groups_categorized(admin=Depends(require_role("admin"))):
    """Return groups split into VIP / Free / Chat categories."""
    rows = await pool.fetch("SELECT * FROM group_registry ORDER BY slug")
    vip = []
    free = []
    chat = []
    vip_tiers = {"TIER_300", "TIER_500", "TIER_1299", "TIER_2499", "TIER_99"}
    chat_slugs = {"CHAT", "TALK", "DISCUSS", "พูดคุย"}
    for r in rows:
        d = dict(r)
        slug_upper = (d.get("slug") or "").upper()
        tier = d.get("min_tier") or ""
        # Classify
        if any(cs in slug_upper for cs in chat_slugs) or tier == "FREE_CHAT":
            chat.append(d)
        elif tier == "FREE":
            free.append(d)
        elif tier in vip_tiers:
            vip.append(d)
        else:
            free.append(d)
    return {"vip": vip, "free": free, "chat": chat}

@router.post("")
async def create_group(req: GroupCreate, request: Request, admin=Depends(require_role("admin"))):
    # FIX 2025-05-21 (Phase D-7): use Pydantic GroupCreate (validated) instead of raw request.json()
    row = await pool.fetchrow("""
        INSERT INTO group_registry (slug, chat_id, title, min_tier, is_active, member_count)
        VALUES ($1::groupslug, $2, $3, $4::packagetier, $5, 0)
        RETURNING id
    """, req.slug, req.chat_id, req.title, req.min_tier, req.is_active)
    ip = request.client.host if request.client else None
    await _log(admin["id"], "create_group", "group", row["id"], {"title": req.title}, ip)
    return {"ok": True, "id": row["id"]}

@router.put("/{group_id}")
async def update_group(group_id: int, req: GroupUpdate, request: Request, admin=Depends(require_role("admin"))):
    # FIX 2025-05-21 (Phase D-7): use Pydantic GroupUpdate; only whitelisted fields allowed
    data = req.dict(exclude_none=True)
    updates = []
    params = []
    idx = 1
    if "title" in data:
        updates.append(f"title = ${idx}")
        params.append(data["title"])
        idx += 1
    if "is_active" in data:
        updates.append(f"is_active = ${idx}")
        params.append(data["is_active"])
        idx += 1
    if "min_tier" in data:
        updates.append(f"min_tier = ${idx}::packagetier")
        params.append(data["min_tier"])
        idx += 1

    if not params:
        raise HTTPException(400, "No fields")
    updates.append("updated_at = NOW()")
    params.append(group_id)
    await pool.execute(f"UPDATE group_registry SET {', '.join(updates)} WHERE id = ${idx}", *params)
    ip = request.client.host if request.client else None
    await _log(admin["id"], "update_group", "group", group_id, data, ip)
    return {"ok": True}

@router.delete("/{group_id}")
async def delete_group(group_id: int, request: Request, admin=Depends(require_role("admin"))):
    await pool.execute("DELETE FROM group_registry WHERE id = $1", group_id)
    ip = request.client.host if request.client else None
    await _log(admin["id"], "delete_group", "group", group_id, None, ip)
    return {"ok": True}

@router.get("/{group_id}/members")
async def group_members(group_id: int, admin=Depends(require_role("admin"))):
    group = await pool.fetchrow("SELECT * FROM group_registry WHERE id = $1", group_id)
    if not group:
        raise HTTPException(404, "Group not found")
    
    # FIX 2025-05-21 (Phase D-7): match groups_access as JSONB array exact-element (was LIKE substring,
    # which falsely matched e.g. 'TIER_2499_X' for slug 'TIER_249')
    rows = await pool.fetch("""
        SELECT u.id, u.telegram_id, u.username, u.first_name, s.status, s.end_date, p.name as package_name
        FROM users u
        JOIN subscriptions s ON s.user_id = u.id AND s.status = 'ACTIVE'
        JOIN packages p ON s.package_id = p.id
        WHERE EXISTS (
            SELECT 1 FROM jsonb_array_elements_text(p.groups_access::jsonb) AS slug
            WHERE slug = $1
        )
        ORDER BY u.first_name LIMIT 100
    """, group["slug"])
    return [dict(r) for r in rows]

@router.post("/{group_id}/invite-link")
async def gen_invite_link(group_id: int, request: Request, admin=Depends(require_role("admin"))):
    group = await pool.fetchrow("SELECT * FROM group_registry WHERE id = $1", group_id)
    if not group:
        raise HTTPException(404, "Group not found")
    
    result


# ====== Audit 2026-06-25: Groups → relay-bot sync ======
# Relay-bot is Node.js, reads DEST_CHAT_IDS from env, NOT from group_registry table.
# This endpoint computes the diff + writes env + restarts relay-bot service.
import os as _os
import asyncio as _asyncio
from pathlib import Path as _Path

_ENV_PATH = _Path(_os.environ.get("HOST_ENV_PATH", "/app/host.env"))


def _read_env_groups_sync() -> dict:
    if not _ENV_PATH.exists():
        return {}
    result = {}
    for line in _ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.rstrip()
        if not line or line.lstrip().startswith("#"):
            continue
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        result[k.strip()] = v.strip()
    return result


def _write_env_var_groups_sync(key: str, value: str):
    """Set/update one env var preserving comments."""
    lines = _ENV_PATH.read_text(encoding="utf-8").splitlines() if _ENV_PATH.exists() else []
    found = False
    new_lines = []
    for line in lines:
        if not line.lstrip().startswith("#") and "=" in line:
            k = line.split("=", 1)[0].strip()
            if k == key:
                new_lines.append(f"{key}={value}")
                found = True
                continue
        new_lines.append(line)
    if not found:
        new_lines.append(f"{key}={value}")
    _ENV_PATH.write_text("\n".join(new_lines) + "\n", encoding="utf-8")


@router.get("/relay-sync-status")
async def relay_sync_status(admin=Depends(require_role("admin"))):
    """Compare group_registry vs relay-bot DEST_CHAT_IDS env. Returns diff."""
    # 1. Get active FREE groups from DB
    rows = await pool.fetch("""
        SELECT slug::text AS slug, chat_id, title, is_active
        FROM group_registry
        WHERE min_tier = 'FREE' AND is_active = TRUE
        ORDER BY slug
    """)
    db_chat_ids = sorted(int(r["chat_id"] or 0) for r in rows)
    db_slugs = {int(r["chat_id"] or 0): r["slug"] for r in rows}
    db_titles = {int(r["chat_id"] or 0): r["title"] for r in rows}

    # 2. Get current env value
    env = _read_env_groups_sync()
    env_value = env.get("DEST_CHAT_IDS", "")
    env_chat_ids = []
    if env_value:
        for s in env_value.split(","):
            s = s.strip()
            if s.lstrip("-").isdigit():
                env_chat_ids.append(int(s))
    env_chat_ids.sort()

    # 3. Compute diff
    db_set = set(db_chat_ids)
    env_set = set(env_chat_ids)
    only_in_db = sorted(db_set - env_set)
    only_in_env = sorted(env_set - db_set)
    in_sync = (db_set == env_set)

    return {
        "in_sync": in_sync,
        "db_count": len(db_chat_ids),
        "env_count": len(env_chat_ids),
        "missing_in_relay": [
            {"chat_id": cid, "slug": db_slugs.get(cid, "?"), "title": db_titles.get(cid, "?")}
            for cid in only_in_db
        ],
        "extra_in_relay": [{"chat_id": cid} for cid in only_in_env],
        "db_chat_ids": db_chat_ids,
        "env_chat_ids": env_chat_ids,
    }


@router.post("/relay-sync")
async def sync_relay_bot(admin=Depends(require_role("admin"))):
    """Write current FREE groups to DEST_CHAT_IDS env + restart relay-bot."""
    rows = await pool.fetch("""
        SELECT chat_id FROM group_registry
        WHERE min_tier = 'FREE' AND is_active = TRUE
        ORDER BY id
    """)
    chat_ids = [str(r["chat_id"]) for r in rows]
    new_value = ",".join(chat_ids)

    # Write env
    try:
        _write_env_var_groups_sync("DEST_CHAT_IDS", new_value)
    except Exception as exc:
        raise HTTPException(500, f"write env failed: {exc}")

    # Restart relay-bot
    try:
        proc = await _asyncio.create_subprocess_exec(
            "docker", "compose", "up", "-d", "--force-recreate", "--no-deps", "--", "relay-bot",
            cwd="/app",
            stdout=_asyncio.subprocess.PIPE,
            stderr=_asyncio.subprocess.PIPE,
        )
        stdout, stderr = await _asyncio.wait_for(proc.communicate(), timeout=120)
        if proc.returncode != 0:
            return {
                "ok": False,
                "synced_count": len(chat_ids),
                "env_written": True,
                "restart_error": stderr.decode(errors="replace")[-500:],
            }
    except _asyncio.TimeoutError:
        return {"ok": False, "synced_count": len(chat_ids), "env_written": True, "restart_error": "timeout"}
    except Exception as exc:
        return {"ok": False, "synced_count": len(chat_ids), "env_written": True, "restart_error": str(exc)[:300]}

    # Audit log
    try:
        await pool.execute(
            "INSERT INTO admin_logs (admin_id, action, target_type, target_id, details) "
            "VALUES ($1, 'relay_bot_sync', 'system', 0, $2)",
            admin["telegram_id"], f"synced {len(chat_ids)} free groups"
        )
    except Exception:
        pass

    return {
        "ok": True,
        "synced_count": len(chat_ids),
        "env_written": True,
        "restarted": True,
        "chat_ids": chat_ids,
    }




@router.get("/relay-status")
async def relay_status(admin=Depends(require_role("admin"))):
    """Read relay-bot state.json + env config to show full sync stats with group names."""
    import json as _json
    import os as _os
    import re as _re

    state_path = '/app/bots/relay_bot/data/state.json'
    log_path = '/app/bots/relay_bot/data/relay.log'

    try:
        if not _os.path.exists(state_path):
            return {"available": False, "reason": "state.json not found"}
        with open(state_path) as f:
            state = _json.load(f)

        stats = state.get('stats', {})
        disabled = state.get('destDisabled', {})
        failures = state.get('destFailures', {})

        # Read SOURCE_CHAT_ID + DEST_CHAT_IDS from /app/host.env
        source_id = None
        dest_ids = []
        try:
            with open('/app/host.env') as f:
                for line in f:
                    if line.startswith('SOURCE_CHAT_ID='):
                        source_id = line.split('=', 1)[1].strip().strip('"').strip("'")
                    elif line.startswith('DEST_CHAT_IDS='):
                        raw = line.split('=', 1)[1].strip().strip('"').strip("'")
                        dest_ids = [d.strip() for d in raw.split(',') if d.strip()]
        except Exception:
            pass

        # Lookup names for source + dests + disabled
        all_ids = set()
        if source_id: all_ids.add(int(source_id))
        for d in dest_ids:
            try: all_ids.add(int(d))
            except: pass
        for k in disabled.keys():
            try: all_ids.add(int(k))
            except: pass

        name_map = {}
        if all_ids:
            rows = await pool.fetch(
                "SELECT chat_id, slug::text AS slug, title FROM group_registry WHERE chat_id = ANY($1::bigint[])",
                list(all_ids)
            )
            for r in rows:
                name_map[str(r['chat_id'])] = {"slug": r['slug'], "title": r['title']}

        def _lookup(cid):
            return name_map.get(str(cid), {"slug": "?", "title": "(ไม่อยู่ใน registry)"})

        source = None
        if source_id:
            info = _lookup(source_id)
            source = {"chat_id": source_id, "slug": info['slug'], "title": info['title']}

        dest_list = []
        for d in dest_ids:
            info = _lookup(d)
            dest_list.append({
                "chat_id": d,
                "slug": info['slug'],
                "title": info['title'],
                "failures": failures.get(d, 0),
                "disabled": d in disabled,
            })

        disabled_list = []
        for k in disabled.keys():
            info = _lookup(k)
            disabled_list.append({
                "chat_id": k,
                "slug": info['slug'],
                "title": info['title'],
                "failures": failures.get(k, 0),
                "in_current_dests": k in dest_ids,
            })

        # Parse last 50 lines of relay.log for failed entries
        recent_fails = []
        try:
            if _os.path.exists(log_path):
                with open(log_path, 'rb') as f:
                    # Read last ~50KB to get recent lines
                    f.seek(0, 2)
                    size = f.tell()
                    f.seek(max(0, size - 50000))
                    tail = f.read().decode('utf-8', errors='ignore')
                lines = tail.split('\n')
                # Match lines like: [TS] ❌ ... → CHAT_ID  OR contains "error", "fail", "blocked"
                pat = _re.compile(r'\[([^\]]+)\]\s*(?:❌|⚠️|🚫|💥)\s*(.+?)(?:→\s*(-?\d+))?$')
                for line in lines[-200:]:
                    line = line.strip()
                    if not line: continue
                    # Quick filter: look for fail/error/❌/⚠️/blocked indicators
                    if not any(tok in line.lower() for tok in ['❌', '⚠️', '🚫', '💥', 'error', 'fail', 'blocked', 'forbidden']):
                        continue
                    m = pat.search(line)
                    if m:
                        ts, desc, cid = m.group(1), m.group(2), m.group(3)
                        info = _lookup(cid) if cid else {"slug": "?", "title": ""}
                        recent_fails.append({
                            "ts": ts,
                            "chat_id": cid,
                            "slug": info['slug'],
                            "title": info['title'],
                            "reason": desc[:120],
                        })
                recent_fails = recent_fails[-20:]
        except Exception as exc:
            recent_fails = [{"ts": "", "reason": f"log parse error: {exc}", "slug": "", "title": ""}]

        # Compute synced groups count from group_registry (active groups that match)
        active_groups = await pool.fetchval("SELECT COUNT(*) FROM group_registry WHERE is_active = TRUE")

        return {
            "available": True,
            "paused": state.get('paused', False),
            "started_at": stats.get('startedAt'),
            "last_forward_at": stats.get('lastForwardAt'),
            "last_closing_at": state.get('lastClosingAt'),
            "total_forwarded": stats.get('forwarded', 0),
            "total_failed": stats.get('failed', 0),
            "closings_sent": stats.get('closingsSent', 0),
            "source": source,
            "destinations": dest_list,
            "disabled_destinations": disabled_list,
            "recent_fails": recent_fails,
            "active_groups": active_groups,
        }
    except Exception as exc:
        return {"available": False, "reason": str(exc)[:200]}
