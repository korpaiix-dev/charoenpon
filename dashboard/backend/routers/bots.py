"""Bot token + admin ID management endpoints.

These endpoints let owner/admin users:
- View and update bot tokens in /app/host.env (mounted from /root/charoenpon/.env)
- Test bot tokens via Telegram getMe
- View, add, remove ADMIN_TELEGRAM_IDS
- Restart the corresponding docker compose service after a token change

Security: every write requires role="owner". Token values are returned masked
(`AAH****Yow`) in GET responses; clients only ever receive the suffix.
"""
from __future__ import annotations

import asyncio  # FIX 2025-05-21 (Phase D-10): for async subprocess
import logging
import os
import re
import subprocess  # kept for backward-compat (FileNotFoundError detection); no longer used to spawn
from pathlib import Path
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from ..auth.dependencies import require_role
from ..database import pool

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin", tags=["admin-bots"])

# Host .env is mounted at /app/host.env
ENV_PATH = Path(os.environ.get("HOST_ENV_PATH", "/app/host.env"))
COMPOSE_DIR = Path(os.environ.get("HOST_COMPOSE_DIR", "/app"))

# Map of env-var key → docker-compose service name to restart after change
BOT_TOKEN_KEYS: dict[str, str] = {
    "SALES_BOT_TOKEN": "sales-bot",
    "ADMIN_BOT_TOKEN": "admin-bot",
    "GUARDIAN_BOT_TOKEN": "guardian-bot",
    "CONTENT_BOT_TOKEN": "content-bot",
    "ANNOUNCE_BOT_TOKEN": "content-bot",  # announce shares with content
    "DISCORD_BOT_TOKEN": "discord-bot",
}


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _read_env() -> dict[str, str]:
    """Read .env file → dict (preserves comments by skipping them)."""
    if not ENV_PATH.exists():
        raise HTTPException(status_code=500, detail=f"Env file not found: {ENV_PATH}")
    result: dict[str, str] = {}
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.rstrip()
        if not line or line.lstrip().startswith("#"):
            continue
        if "=" not in line:
            continue
        k, _, v = line.partition("=")
        result[k.strip()] = v.strip().strip('"').strip("'")
    return result


def _write_env_value(key: str, value: str) -> None:
    """Atomically rewrite .env replacing one key's value. Preserves all other lines."""
    if not ENV_PATH.exists():
        raise HTTPException(status_code=500, detail=f"Env file not found: {ENV_PATH}")

    src = ENV_PATH.read_text(encoding="utf-8")
    lines = src.splitlines()
    new_lines: list[str] = []
    replaced = False
    pat = re.compile(rf"^\s*{re.escape(key)}\s*=")
    for line in lines:
        if pat.match(line) and not replaced:
            new_lines.append(f"{key}={value}")
            replaced = True
        else:
            new_lines.append(line)
    if not replaced:
        new_lines.append(f"{key}={value}")

    tmp = ENV_PATH.with_suffix(".env.tmp")
    tmp.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    tmp.replace(ENV_PATH)


def _mask(token: str) -> str:
    """Return safe masked form 'PREFIX****SUFFIX'."""
    if not token:
        return ""
    if len(token) < 12:
        return "****"
    return f"{token[:6]}****{token[-4:]}"


async def _telegram_get_me(token: str) -> dict:
    """Call Telegram getMe with the given token. Returns dict with ok/username/error."""
    # FIX 2025-05-21 (Phase D-9): never echo the token (or e.g. urllib3 reprs that may include URL)
    # back to the client. Categorise errors instead.
    if not token:
        return {"ok": False, "error": "empty token"}
    url = f"https://api.telegram.org/bot{token}/getMe"
    try:
        async with httpx.AsyncClient(timeout=10) as cli:
            r = await cli.get(url)
        j = r.json()
        if j.get("ok"):
            return {"ok": True, "username": j["result"].get("username"), "id": j["result"].get("id")}
        return {"ok": False, "error": j.get("description", "unknown")}
    except httpx.HTTPError as e:
        return {"ok": False, "error": f"network: {type(e).__name__}"}
    except Exception:
        logger.exception("telegram getMe failed")
        return {"ok": False, "error": "internal"}


# FIX 2025-05-21 (Phase D-10): whitelist services + use async subprocess (avoid blocking event loop)
_ALLOWED_SERVICES = {
    "sales-bot",
    "admin-bot",
    "guardian-bot",
    "content-bot",
    "discord-bot",
    "finance-scheduler",
    "manager-agent",
    "broadcast-worker",
    "relay-bot",
}


async def _restart_service(service: str) -> dict:
    """Run `docker compose up -d --force-recreate --no-deps <service>` asynchronously."""
    if service not in _ALLOWED_SERVICES:
        return {"ok": False, "error": "service not in whitelist"}
    try:
        proc = await asyncio.create_subprocess_exec(
            "docker", "compose", "up", "-d", "--force-recreate", "--no-deps", "--", service,
            cwd=str(COMPOSE_DIR),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        return {"ok": False, "error": "docker CLI not installed in container"}
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
    except asyncio.TimeoutError:
        proc.kill()
        return {"ok": False, "error": "timeout"}
    return {
        "ok": proc.returncode == 0,
        "stdout": stdout.decode(errors="replace")[-2000:],
        "stderr": stderr.decode(errors="replace")[-2000:],
        "returncode": proc.returncode,
    }


async def _log_action(admin_id: int, action: str, target: str, payload: dict, ip: str | None) -> None:
    """Best-effort log into dashboard_admin_log (created if missing)."""
    try:
        await pool.execute("""
            CREATE TABLE IF NOT EXISTS dashboard_admin_log (
                id SERIAL PRIMARY KEY,
                admin_id INT,
                action TEXT,
                target_type TEXT,
                target_id TEXT,
                payload JSONB,
                ip TEXT,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        import json as _json
        await pool.execute(
            "INSERT INTO dashboard_admin_log (admin_id, action, target_type, target_id, payload, ip) "
            "VALUES ($1, $2, 'bot_setting', $3, $4::jsonb, $5)",
            admin_id, action, target, _json.dumps(payload), ip,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("admin log write failed: %s", e)


# ─── Bot tokens ──────────────────────────────────────────────────────────────


class TokenUpdate(BaseModel):
    token: str = Field(..., min_length=20, max_length=128)


@router.get("/bots")
async def list_bots(_admin=Depends(require_role("admin"))) -> dict:
    """Return current tokens (masked) + live getMe results."""
    env = _read_env()
    out = []
    for key, service in BOT_TOKEN_KEYS.items():
        token = env.get(key, "")
        info = await _telegram_get_me(token)
        out.append({
            "key": key,
            "service": service,
            "token_masked": _mask(token),
            "has_token": bool(token),
            "live": info,
        })
    return {"bots": out}


@router.post("/bots/{key}/test")
async def test_bot(key: str, _admin=Depends(require_role("admin"))) -> dict:
    """Call Telegram getMe with the currently-stored token for this key."""
    if key not in BOT_TOKEN_KEYS:
        raise HTTPException(status_code=404, detail="unknown bot key")
    env = _read_env()
    return await _telegram_get_me(env.get(key, ""))


@router.put("/bots/{key}")
async def update_bot(
    key: str,
    body: TokenUpdate,
    request: Request,
    admin=Depends(require_role("owner")),
) -> dict:
    """Replace token value in .env, optionally restart the corresponding service."""
    if key not in BOT_TOKEN_KEYS:
        raise HTTPException(status_code=404, detail="unknown bot key")

    # Pre-validate the token against Telegram before saving
    test = await _telegram_get_me(body.token)
    if not test.get("ok"):
        # FIX 2025-05-21 (Phase D-9): don't echo telegram's error verbatim — it may include token-derived data
        raise HTTPException(status_code=400, detail="telegram rejects token")

    _write_env_value(key, body.token)
    service = BOT_TOKEN_KEYS[key]
    restart = await _restart_service(service)

    ip = request.client.host if request.client else None
    await _log_action(
        admin["id"], "update_bot_token", key,
        {"username": test.get("username"), "service": service, "restart_ok": restart.get("ok")},
        ip,
    )

    return {
        "ok": True,
        "key": key,
        "username": test.get("username"),
        "service": service,
        "restart": restart,
    }


# ─── Admin IDs ───────────────────────────────────────────────────────────────


ADMIN_KEY = "ADMIN_TELEGRAM_IDS"


def _parse_ids(raw: str) -> list[int]:
    out: list[int] = []
    for piece in raw.split(","):
        piece = piece.strip()
        if not piece:
            continue
        try:
            out.append(int(piece))
        except ValueError:
            pass
    return out


class AdminAdd(BaseModel):
    telegram_id: int = Field(..., gt=0)


@router.get("/admin-ids")
async def list_admin_ids(_admin=Depends(require_role("admin"))) -> dict:
    env = _read_env()
    return {"ids": _parse_ids(env.get(ADMIN_KEY, ""))}


@router.post("/admin-ids")
async def add_admin_id(
    body: AdminAdd,
    request: Request,
    admin=Depends(require_role("owner")),
) -> dict:
    env = _read_env()
    ids = _parse_ids(env.get(ADMIN_KEY, ""))
    if body.telegram_id in ids:
        return {"ok": True, "ids": ids, "note": "already present"}
    ids.append(body.telegram_id)
    _write_env_value(ADMIN_KEY, ",".join(str(i) for i in ids))

    # FIX 2025-05-21 (Phase D-10): _restart_service is now async — await each call
    # FIX 2026-06-26 (audit): completed truncated function + added DELETE endpoint
    services = ["admin-bot", "content-bot", "sales-bot", "guardian-bot"]
    restart_results = {}
    for svc in services:
        restart_results[svc] = await _restart_service(svc)

    # Audit log
    try:
        from ..database import pool as _pool
        await _pool.execute(
            "INSERT INTO admin_logs (admin_id, action, target_type, target_id, details) "
            "VALUES ($1, 'admin_id_add', 'admin_telegram_id', $2, $3)",
            admin["telegram_id"], body.telegram_id,
            f"added {body.telegram_id} (now {len(ids)} admins) restarts={','.join(k for k,v in restart_results.items() if v.get('ok'))}"
        )
    except Exception:
        pass

    return {
        "ok": True,
        "ids": ids,
        "added": body.telegram_id,
        "restarts": restart_results,
    }


@router.delete("/admin-ids/{tid}")
async def delete_admin_id(
    tid: int,
    request: Request,
    admin=Depends(require_role("owner")),
) -> dict:
    """Remove a telegram_id from ADMIN_TELEGRAM_IDS env + restart bots."""
    env = _read_env()
    ids = _parse_ids(env.get(ADMIN_KEY, ""))
    if tid not in ids:
        return {"ok": True, "ids": ids, "note": "not present"}
    # Safety: don't remove the caller themselves
    if tid == admin.get("telegram_id"):
        raise HTTPException(400, "Cannot remove yourself")
    # Safety: must keep at least 1 admin
    if len(ids) <= 1:
        raise HTTPException(400, "Cannot remove the last admin")

    ids.remove(tid)
    _write_env_value(ADMIN_KEY, ",".join(str(i) for i in ids))

    services = ["admin-bot", "content-bot", "sales-bot", "guardian-bot"]
    restart_results = {}
    for svc in services:
        restart_results[svc] = await _restart_service(svc)

    try:
        from ..database import pool as _pool
        await _pool.execute(
            "INSERT INTO admin_logs (admin_id, action, target_type, target_id, details) "
            "VALUES ($1, 'admin_id_remove', 'admin_telegram_id', $2, $3)",
            admin["telegram_id"], tid,
            f"removed {tid} (now {len(ids)} admins)"
        )
    except Exception:
        pass

    return {
        "ok": True,
        "ids": ids,
        "removed": tid,
        "restarts": restart_results,
    }


# ==================================================================
# Phase A.4 (2026-06-27): Restart container (whitelist)
# ==================================================================
_RESTART_WHITELIST = {
    "charoenpon-sales-bot",
    "charoenpon-guardian-bot",
    "charoenpon-admin-bot",
    "charoenpon-relay-bot",
    "charoenpon-discord-bot",
    # NOT dashboard (would kill our own request)
}


@router.post("/bots/{container}/restart")
async def restart_container(container: str, _admin=Depends(require_role("admin"))) -> dict:
    """Restart a whitelisted docker container. Owner-only operation."""
    if container not in _RESTART_WHITELIST:
        raise HTTPException(status_code=400, detail=f"container {container} not in whitelist")
    import subprocess as _subp
    try:
        r = _subp.run(["docker", "restart", container], capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            raise HTTPException(status_code=500, detail=f"restart failed: {r.stderr[:200]}")
        return {"ok": True, "container": container, "output": r.stdout.strip()}
    except _subp.TimeoutExpired:
        raise HTTPException(status_code=504, detail="restart timed out")


# ==================================================================
# Phase A.6 (2026-06-27): bot × group target matrix
# ==================================================================

@router.get("/bots/{bot_key}/groups")
async def get_bot_target_groups(bot_key: str, _admin=Depends(require_role("admin"))):
    """Return: { bot, all_groups, targets: {group_id: [roles...]} }

    UI uses this to render checkbox grid (groups × target_role).
    """
    bot = await pool.fetchrow(
        "SELECT bot_key, display_name, icon, description FROM bot_registry "
        "WHERE bot_key=$1 AND is_active=TRUE", bot_key,
    )
    if not bot:
        raise HTTPException(404, f"unknown bot_key {bot_key}")
    groups = await pool.fetch(
        "SELECT chat_id, slug::text AS slug, title, min_tier::text AS min_tier "
        "FROM group_registry WHERE is_active=TRUE ORDER BY min_tier, slug"
    )
    targets = await pool.fetch(
        "SELECT chat_id, target_role FROM bot_group_targets "
        "WHERE bot_key=$1 AND is_active=TRUE", bot_key,
    )
    # group target rows by chat_id → list of roles
    target_map: dict[int, list[str]] = {}
    for t in targets:
        target_map.setdefault(t["chat_id"], []).append(t["target_role"])
    return {
        "bot": dict(bot),
        "all_groups": [dict(g) for g in groups],
        "targets": target_map,
    }


@router.patch("/bots/{bot_key}/groups")
async def set_bot_target_groups(
    bot_key: str,
    payload: dict,
    request: Request,
    admin=Depends(require_role("super_admin")),
):
    """Replace bot's group assignments for ONE target_role at a time.

    Body: { "target_role": "distribution"|"source"|"monitor", "chat_ids": [...] }

    Atomic: deletes existing rows for (bot_key, target_role), inserts new.
    """
    bot = await pool.fetchrow("SELECT bot_key FROM bot_registry WHERE bot_key=$1", bot_key)
    if not bot:
        raise HTTPException(404, "bot not found")
    target_role = (payload.get("target_role") or "distribution").strip()
    if target_role not in {"distribution", "source", "monitor"}:
        raise HTTPException(400, "target_role must be distribution|source|monitor")
    chat_ids = payload.get("chat_ids") or []
    if not isinstance(chat_ids, list):
        raise HTTPException(400, "chat_ids must be array")
    # Validate chat_ids exist
    if chat_ids:
        ok = await pool.fetch(
            "SELECT chat_id FROM group_registry WHERE chat_id = ANY($1::bigint[])",
            [int(x) for x in chat_ids],
        )
        valid = {r["chat_id"] for r in ok}
        bad = set(int(x) for x in chat_ids) - valid
        if bad:
            raise HTTPException(400, f"unknown chat_ids: {sorted(bad)}")
    # Atomic replace
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "DELETE FROM bot_group_targets "
                "WHERE bot_key=$1 AND target_role=$2",
                bot_key, target_role,
            )
            if chat_ids:
                await conn.executemany(
                    "INSERT INTO bot_group_targets "
                    "(bot_key, chat_id, target_role, added_by) "
                    "VALUES ($1, $2, $3, $4)",
                    [(bot_key, int(cid), target_role, admin["telegram_id"]) for cid in chat_ids],
                )
    return {
        "ok": True,
        "bot_key": bot_key,
        "target_role": target_role,
        "chat_ids": [int(x) for x in chat_ids],
    }


@router.get("/bots-registry")
async def list_bots_registry(_admin=Depends(require_role("admin"))):
    """List all bots with counts (groups assigned per target_role).

    For the new "🤖 บอท" management page.
    """
    rows = await pool.fetch("""
        SELECT br.bot_key, br.display_name, br.icon, br.description, br.is_active,
               COALESCE(json_object_agg(bgt.target_role, bgt.cnt) FILTER (WHERE bgt.target_role IS NOT NULL), '{}'::json) AS group_counts
        FROM bot_registry br
        LEFT JOIN (
            SELECT bot_key, target_role, COUNT(*) AS cnt
            FROM bot_group_targets WHERE is_active=TRUE
            GROUP BY bot_key, target_role
        ) bgt ON bgt.bot_key = br.bot_key
        GROUP BY br.bot_key, br.display_name, br.icon, br.description, br.is_active, br.sort_order
        ORDER BY br.sort_order
    """)
    return [dict(r) for r in rows]


# ==================================================================
# Phase A.7 (2026-06-27): Group member analytics
# ==================================================================
import httpx as _httpx_an
import logging as _log_an
_log_an_logger = _log_an.getLogger(__name__)


async def _snapshot_one_group(bot_token: str, chat_id: int) -> int | None:
    """Call Telegram getChatMemberCount via guardian-bot token. Returns None on fail."""
    try:
        url = f"https://api.telegram.org/bot{bot_token}/getChatMemberCount?chat_id={chat_id}"
        async with _httpx_an.AsyncClient(timeout=10) as c:
            r = await c.get(url)
            j = r.json()
            if not j.get("ok"):
                return None
            return int(j["result"])
    except Exception as exc:
        _log_an_logger.warning("snapshot %s failed: %s", chat_id, exc)
        return None


@router.post("/snapshot-group-members")
async def snapshot_group_members(_admin=Depends(require_role("admin"))):
    """Snapshot member_count for ALL active groups.

    Called by cron hourly + manual via Dashboard "Refresh".
    Uses GUARDIAN_BOT_TOKEN (already in all groups for moderation).
    """
    import os as _os_an
    bot_token = _os_an.environ.get("GUARDIAN_BOT_TOKEN", "") or _os_an.environ.get("SALES_BOT_TOKEN", "")
    if not bot_token:
        raise HTTPException(500, "GUARDIAN_BOT_TOKEN not configured")

    groups = await pool.fetch(
        "SELECT chat_id FROM group_registry WHERE is_active = TRUE"
    )
    snapshots = []
    failed = []
    for g in groups:
        cnt = await _snapshot_one_group(bot_token, int(g["chat_id"]))
        if cnt is None:
            failed.append(int(g["chat_id"]))
        else:
            snapshots.append((int(g["chat_id"]), cnt))

    if snapshots:
        await pool.executemany(
            "INSERT INTO group_member_snapshots (chat_id, member_count) VALUES ($1, $2)",
            snapshots,
        )
        # Update group_registry.member_count to latest value (denorm for fast queries)
        for cid, cnt in snapshots:
            await pool.execute(
                "UPDATE group_registry SET member_count=$1, updated_at=NOW() WHERE chat_id=$2",
                cnt, cid,
            )

    return {
        "ok": True,
        "snapshotted": len(snapshots),
        "failed": failed,
        "total_groups": len(groups),
    }


@router.get("/groups/analytics")
async def group_analytics(_admin=Depends(require_role("admin"))):
    """Return per-group: now / delta today / week / month.

    Uses group_member_snapshots time series with window functions.
    """
    rows = await pool.fetch("""
        WITH latest AS (
            SELECT DISTINCT ON (chat_id) chat_id, member_count, snapshot_at
            FROM group_member_snapshots
            ORDER BY chat_id, snapshot_at DESC
        ),
        day_ago AS (
            SELECT DISTINCT ON (chat_id) chat_id, member_count
            FROM group_member_snapshots
            WHERE snapshot_at <= NOW() - INTERVAL '1 day'
            ORDER BY chat_id, snapshot_at DESC
        ),
        week_ago AS (
            SELECT DISTINCT ON (chat_id) chat_id, member_count
            FROM group_member_snapshots
            WHERE snapshot_at <= NOW() - INTERVAL '7 days'
            ORDER BY chat_id, snapshot_at DESC
        ),
        month_ago AS (
            SELECT DISTINCT ON (chat_id) chat_id, member_count
            FROM group_member_snapshots
            WHERE snapshot_at <= NOW() - INTERVAL '30 days'
            ORDER BY chat_id, snapshot_at DESC
        )
        SELECT
            g.chat_id, g.slug::text AS slug, g.title, g.min_tier::text AS min_tier, g.is_active,
            COALESCE(l.member_count, g.member_count, 0) AS current,
            l.snapshot_at AS last_snapshot,
            (COALESCE(l.member_count, g.member_count, 0) - d.member_count) AS delta_day,
            (COALESCE(l.member_count, g.member_count, 0) - w.member_count) AS delta_week,
            (COALESCE(l.member_count, g.member_count, 0) - m.member_count) AS delta_month
        FROM group_registry g
        LEFT JOIN latest    l ON l.chat_id = g.chat_id
        LEFT JOIN day_ago   d ON d.chat_id = g.chat_id
        LEFT JOIN week_ago  w ON w.chat_id = g.chat_id
        LEFT JOIN month_ago m ON m.chat_id = g.chat_id
        WHERE g.is_active = TRUE
        ORDER BY g.min_tier, g.slug
    """)
    return [dict(r) for r in rows]


@router.get("/groups/{chat_id}/timeseries")
async def group_timeseries(chat_id: int, days: int = 30, _admin=Depends(require_role("admin"))):
    """Return member_count time series for a single group (for chart)."""
    days = max(1, min(days, 365))
    rows = await pool.fetch(
        "SELECT snapshot_at, member_count FROM group_member_snapshots "
        "WHERE chat_id=$1 AND snapshot_at > NOW() - INTERVAL '%d days' "
        "ORDER BY snapshot_at" % days,
        chat_id,
    )
    return [{"t": r["snapshot_at"].isoformat(), "n": int(r["member_count"])} for r in rows]


@router.get("/groups/analytics-v2")
async def group_analytics_v2(range_days: int = 7, _admin=Depends(require_role("admin"))):
    """Modern analytics: per-group current, delta_range, growth%, sparkline points.

    Single query — no extra round trips for sparklines.
    range_days: 1 (24h) / 7 / 30 / 90 — drives delta + sparkline window.
    """
    range_days = max(1, min(int(range_days), 365))
    rows = await pool.fetch(f"""
        WITH latest AS (
            SELECT DISTINCT ON (chat_id) chat_id, member_count, snapshot_at
            FROM group_member_snapshots
            ORDER BY chat_id, snapshot_at DESC
        ),
        range_ago AS (
            SELECT DISTINCT ON (chat_id) chat_id, member_count
            FROM group_member_snapshots
            WHERE snapshot_at <= NOW() - INTERVAL '{int(range_days)} days'
            ORDER BY chat_id, snapshot_at DESC
        ),
        spark AS (
            SELECT chat_id,
                   json_agg(member_count ORDER BY snapshot_at) AS series,
                   COUNT(*) AS pts
            FROM (
                SELECT chat_id, member_count, snapshot_at,
                       ROW_NUMBER() OVER (PARTITION BY chat_id ORDER BY snapshot_at) AS rn,
                       COUNT(*) OVER (PARTITION BY chat_id) AS tot
                FROM group_member_snapshots
                WHERE snapshot_at > NOW() - INTERVAL '{int(range_days)} days'
            ) t
            -- sample 20 points if >20 snapshots
            WHERE rn % GREATEST(1, tot/20) = 0 OR rn = 1 OR rn = tot
            GROUP BY chat_id
        )
        SELECT
            g.chat_id, g.slug::text AS slug, g.title, g.min_tier::text AS min_tier,
            COALESCE(l.member_count, g.member_count, 0) AS current,
            l.snapshot_at AS last_snapshot,
            (COALESCE(l.member_count, g.member_count, 0) - COALESCE(r.member_count, l.member_count)) AS delta,
            CASE
                WHEN r.member_count IS NULL OR r.member_count = 0 THEN NULL
                ELSE ROUND(100.0 * (COALESCE(l.member_count, g.member_count, 0) - r.member_count) / r.member_count, 1)
            END AS delta_pct,
            COALESCE(s.series, '[]'::json) AS spark,
            COALESCE(s.pts, 0) AS spark_points
        FROM group_registry g
        LEFT JOIN latest    l ON l.chat_id = g.chat_id
        LEFT JOIN range_ago r ON r.chat_id = g.chat_id
        LEFT JOIN spark     s ON s.chat_id = g.chat_id
        WHERE g.is_active = TRUE
        ORDER BY g.min_tier, g.slug
    """)
    # Build summary
    items = [dict(r) for r in rows]
    total = sum(r["current"] or 0 for r in items)
    total_delta = sum((r["delta"] or 0) for r in items)
    gainers = sorted([r for r in items if (r["delta"] or 0) > 0], key=lambda r: -(r["delta"] or 0))[:3]
    losers = sorted([r for r in items if (r["delta"] or 0) < 0], key=lambda r: (r["delta"] or 0))[:3]
    last_snap = max((r["last_snapshot"] for r in items if r["last_snapshot"]), default=None)
    return {
        "range_days": range_days,
        "total_now": total,
        "total_delta": total_delta,
        "groups_gaining": len([r for r in items if (r["delta"] or 0) > 0]),
        "groups_losing": len([r for r in items if (r["delta"] or 0) < 0]),
        "groups_flat": len([r for r in items if (r["delta"] or 0) == 0]),
        "last_snapshot": last_snap.isoformat() if last_snap else None,
        "top_gainers": [{"slug": g["slug"], "title": g["title"], "delta": g["delta"]} for g in gainers],
        "top_losers": [{"slug": g["slug"], "title": g["title"], "delta": g["delta"]} for g in losers],
        "groups": items,
    }


# ==================================================================
# Phase A.8 (2026-06-27): Bot schedule manager
# ==================================================================

@router.get("/bots/{bot_key}/schedules")
async def list_schedules(bot_key: str, _admin=Depends(require_role("admin"))):
    """List all scheduled jobs for a bot with current next-run estimate."""
    rows = await pool.fetch("""
        SELECT id, job_name, display_name, description, schedule_hour, schedule_minute,
               is_enabled, job_type, handler_key, category, sort_order, updated_at
        FROM bot_schedules
        WHERE bot_key=$1
        ORDER BY category, sort_order, schedule_hour, schedule_minute
    """, bot_key)
    return [dict(r) for r in rows]


@router.post("/bots/{bot_key}/schedules")
async def create_bot_schedule(bot_key: str, payload: dict, _admin=Depends(require_role("admin"))):
    """B.1.D: Create new schedule (typically generic_template for new promos).
    
    Required body: template_key (job name = 'template_<key>'), schedule_hour, schedule_minute
    Optional: display_name (auto-derived), description, category
    """
    template_key = (payload.get("template_key") or "").strip()
    if not template_key:
        raise HTTPException(400, "template_key required")
    try:
        hour = int(payload.get("schedule_hour", 9))
        minute = int(payload.get("schedule_minute", 0))
    except (TypeError, ValueError):
        raise HTTPException(400, "schedule_hour/minute must be integers")
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise HTTPException(400, "hour 0-23, minute 0-59")
    
    job_name = f"template_{template_key}"
    
    # Look up template for default display_name
    tpl_row = await pool.fetchrow(
        "SELECT display_name FROM content_templates WHERE bot_key=$1 AND template_key=$2",
        bot_key, template_key,
    )
    display_name = (payload.get("display_name") or "").strip()
    if not display_name:
        tpl_name = tpl_row["display_name"] if tpl_row else template_key
        display_name = f"{tpl_name} ({hour:02d}:{minute:02d})"
    
    try:
        row = await pool.fetchrow(
            "INSERT INTO bot_schedules (bot_key, job_name, display_name, schedule_hour, "
            "schedule_minute, is_enabled, handler_key, category) "
            "VALUES ($1, $2, $3, $4, $5, TRUE, 'generic_template', $6) RETURNING id, job_name",
            bot_key, job_name, display_name, hour, minute,
            payload.get("category") or "promo",
        )
    except Exception as exc:
        msg = str(exc)
        if "unique" in msg.lower() or "duplicate" in msg.lower():
            raise HTTPException(409, f"schedule '{job_name}' มีอยู่แล้ว")
        logger.exception("create_bot_schedule failed: %s", exc)
        raise HTTPException(500, f"DB error: {msg[:200]}")
    return {"id": row["id"], "job_name": row["job_name"], "needs_restart": True}


@router.delete("/bots/schedules/{sched_id}")
async def delete_bot_schedule(sched_id: int, _admin=Depends(require_role("admin"))):
    """B.1.D: Delete a schedule (Dashboard only). Needs bot restart to fully drop job."""
    row = await pool.fetchrow("SELECT bot_key, job_name FROM bot_schedules WHERE id=$1", sched_id)
    if not row:
        raise HTTPException(404, "not found")
    await pool.execute("DELETE FROM bot_schedules WHERE id=$1", sched_id)
    return {"deleted": True, "job_name": row["job_name"], "needs_restart": True}


@router.patch("/bots/schedules/{sched_id}")
async def update_schedule(
    sched_id: int,
    payload: dict,
    request: Request,
    admin=Depends(require_role("admin")),
):
    """Update job schedule: time or is_enabled.

    Body (any subset): { is_enabled: bool, schedule_hour: 0-23, schedule_minute: 0-59 }
    """
    row = await pool.fetchrow("SELECT bot_key, job_name, display_name FROM bot_schedules WHERE id=$1", sched_id)
    if not row:
        raise HTTPException(404, "schedule not found")

    updates = []
    args = []
    if "is_enabled" in payload:
        updates.append(f"is_enabled=${len(args)+1}")
        args.append(bool(payload["is_enabled"]))
    if "schedule_hour" in payload:
        h = int(payload["schedule_hour"])
        if not 0 <= h <= 23:
            raise HTTPException(400, "schedule_hour must be 0-23")
        updates.append(f"schedule_hour=${len(args)+1}")
        args.append(h)
    if "schedule_minute" in payload:
        m = int(payload["schedule_minute"])
        if not 0 <= m <= 59:
            raise HTTPException(400, "schedule_minute must be 0-59")
        updates.append(f"schedule_minute=${len(args)+1}")
        args.append(m)
    if not updates:
        raise HTTPException(400, "no fields to update")

    updates.append(f"updated_at=NOW()")
    updates.append(f"updated_by=${len(args)+1}")
    args.append(int(admin["telegram_id"]))

    args.append(sched_id)
    await pool.execute(
        f"UPDATE bot_schedules SET {', '.join(updates)} WHERE id=${len(args)}",
        *args,
    )
    ip = request.client.host if request.client else None
    await _log_action(
        admin["id"], "update_schedule", f"schedule:{sched_id}",
        {"job": row["display_name"], **payload}, ip,
    )
    return {"ok": True, "id": sched_id, "applied": payload}


# ==================================================================
# Phase B.1.B (2026-06-27): Content templates editor
# ==================================================================

@router.post("/content-templates")
async def create_content_template(payload: dict, _admin=Depends(require_role("admin"))):
    """B.1.D (2026-06-27): Create new content template.
    
    Required body: template_key, display_name
    Optional: description, caption_html, image_path, buttons (JSONB), category
    """
    template_key = (payload.get("template_key") or "").strip()
    display_name = (payload.get("display_name") or "").strip()
    if not template_key or not display_name:
        raise HTTPException(400, "template_key + display_name required")
    # Sanitise template_key (lowercase, underscore only)
    import re as _re
    if not _re.match(r"^[a-z0-9_]+$", template_key):
        raise HTTPException(400, "template_key: lowercase letters/digits/underscore only")
    
    # Insert (upsert if exists — Dashboard treats this as create)
    try:
        row = await pool.fetchrow(
            "INSERT INTO content_templates (bot_key, template_key, display_name, "
            "description, caption_html, image_path, buttons, category, is_enabled) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8, TRUE) RETURNING id, template_key",
            "content_bot",
            template_key,
            display_name,
            payload.get("description") or "",
            payload.get("caption_html") or "",
            payload.get("image_path") or "",
            payload.get("buttons") or [],
            payload.get("category") or "promo",
        )
    except Exception as exc:
        msg = str(exc)
        if "unique" in msg.lower() or "duplicate" in msg.lower():
            raise HTTPException(409, f"template_key '{template_key}' มีอยู่แล้ว")
        logger.exception("create_content_template failed: %s", exc)
        raise HTTPException(500, f"DB error: {msg[:200]}")
    return {"id": row["id"], "template_key": row["template_key"]}


@router.delete("/content-templates/{tpl_id}")
async def delete_content_template(tpl_id: int, _admin=Depends(require_role("admin"))):
    """B.1.D: Delete a content template."""
    row = await pool.fetchrow("SELECT template_key FROM content_templates WHERE id=$1", tpl_id)
    if not row:
        raise HTTPException(404, "not found")
    # Also remove any schedule that references this template
    await pool.execute(
        "DELETE FROM bot_schedules WHERE bot_key='content_bot' AND job_name=$1",
        f"template_{row['template_key']}",
    )
    await pool.execute("DELETE FROM content_templates WHERE id=$1", tpl_id)
    return {"deleted": True, "template_key": row["template_key"]}


@router.get("/content-templates")
async def list_content_templates(category: str = None, _admin=Depends(require_role("admin"))):
    """List all content templates, optionally filtered by category."""
    if category:
        rows = await pool.fetch(
            "SELECT * FROM content_templates WHERE category=$1 "
            "AND bot_key='content_bot' ORDER BY sort_order, id",
            category,
        )
    else:
        rows = await pool.fetch(
            "SELECT * FROM content_templates WHERE bot_key='content_bot' "
            "ORDER BY category, sort_order, id"
        )
    return [dict(r) for r in rows]


@router.patch("/content-templates/{tpl_id}")
async def update_content_template(
    tpl_id: int,
    payload: dict,
    request: Request,
    admin=Depends(require_role("admin")),
):
    """Update template caption / image / is_enabled.

    Body (any subset): { caption_html, image_path, is_enabled }
    """
    row = await pool.fetchrow("SELECT id, display_name FROM content_templates WHERE id=$1", tpl_id)
    if not row:
        raise HTTPException(404, "template not found")

    updates = []
    args = []
    for field in ("caption_html", "image_path"):
        if field in payload:
            updates.append(f"{field}=${len(args)+1}")
            args.append(str(payload[field]))
    if "buttons" in payload:
        import json as _json_b
        # Validate: list of {label,url} or [[{label,url}]]
        btns = payload["buttons"]
        if not isinstance(btns, list):
            raise HTTPException(400, "buttons must be array")
        normalized = []
        for b in btns:
            if isinstance(b, dict):
                lab = str(b.get("label", "")).strip()
                url = str(b.get("url", "")).strip()
                if lab and url:
                    normalized.append({"label": lab, "url": url})
        updates.append(f"buttons=${len(args)+1}")
        args.append(normalized)  # codec handles JSONB encoding (was double-encoding via json.dumps)
    if "is_enabled" in payload:
        updates.append(f"is_enabled=${len(args)+1}")
        args.append(bool(payload["is_enabled"]))
    if not updates:
        raise HTTPException(400, "no fields to update")
    updates.append("updated_at=NOW()")
    updates.append(f"updated_by=${len(args)+1}")
    args.append(int(admin["telegram_id"]))
    args.append(tpl_id)

    await pool.execute(
        f"UPDATE content_templates SET {', '.join(updates)} WHERE id=${len(args)}",
        *args,
    )
    ip = request.client.host if request.client else None
    await _log_action(
        admin["id"], "update_content_template", f"template:{tpl_id}",
        {"display_name": row["display_name"]}, ip,
    )
    return {"ok": True, "id": tpl_id}


@router.post("/upload-content-image")
async def upload_content_image(
    request: Request,
    admin=Depends(require_role("admin")),
):
    """Upload an image for content_templates. Saves to /app/assets/uploads/.

    Returns {path: "/app/assets/uploads/...png", url: "/assets/uploads/...png"}
    """
    import os as _os_up
    from fastapi import UploadFile, File
    form = await request.form()
    file = form.get("file")
    if not file:
        raise HTTPException(400, "no file uploaded")

    upload_dir = "/app/assets/uploads"
    _os_up.makedirs(upload_dir, exist_ok=True)

    # Generate safe filename: timestamp + original ext
    import time as _t_up, uuid as _uu
    ext = ".png"
    fname = getattr(file, "filename", "")
    if "." in fname:
        ext = "." + fname.rsplit(".", 1)[-1].lower()
        if ext not in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
            ext = ".png"
    safe = f"{int(_t_up.time())}_{str(_uu.uuid4())[:8]}{ext}"
    full_path = f"{upload_dir}/{safe}"

    content = await file.read()
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(400, "file too large (max 10MB)")
    with open(full_path, "wb") as f:
        f.write(content)

    return {
        "path": full_path,
        "url": f"/assets/uploads/{safe}",
        "size": len(content),
    }


@router.get("/asset")
async def serve_asset(path: str, token: str = None, request: Request = None):
    """Serve a file from /app/assets/* for image preview in dashboard.

    Auth: <img> tags cannot send Bearer header. Accept token via query parameter
    (?token=...) as a fallback. Falls through to standard Bearer check via header.

    Path must start with /app/assets/ to prevent directory traversal.
    """
    import os as _os_a
    from fastapi.responses import FileResponse
    from ..auth.jwt import decode_token

    # Auth check — accept token via query OR Authorization header
    auth_token = token or ""
    if not auth_token and request:
        h = request.headers.get("authorization", "")
        if h.startswith("Bearer "):
            auth_token = h[7:]
    payload = None
    try:
        if auth_token:
            payload = decode_token(auth_token)
    except Exception:
        payload = None
    if not payload:
        raise HTTPException(401, "auth required")
    # FIX (audit): re-check session ใน DB — revoked/disabled admin ต้องใช้ไม่ได้ทันที
    from ..database import pool as _pool_a
    _srow = await _pool_a.fetchrow(
        "SELECT s.revoked_at, a.is_active, a.role FROM dashboard_sessions s "
        "JOIN dashboard_admins a ON a.id = s.admin_id WHERE s.token_jti = $1",
        payload.get("jti"))
    if not _srow or _srow["revoked_at"] or not _srow["is_active"]:
        raise HTTPException(401, "session invalid")
    if _srow["role"] not in ("owner", "super_admin", "admin"):
        raise HTTPException(403, "admin role required")

    if not path.startswith("/app/assets/"):
        raise HTTPException(400, "path must be under /app/assets/")
    if ".." in path:
        raise HTTPException(400, "invalid path")
    if not _os_a.path.exists(path):
        raise HTTPException(404, "file not found")
    return FileResponse(path)

