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

import logging
import os
import re
import subprocess
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
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"network: {e}"}


def _restart_service(service: str) -> dict:
    """Run `docker compose up -d <service>` from /app (compose dir mounted)."""
    cmd = ["docker", "compose", "up", "-d", service]
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(COMPOSE_DIR),
            capture_output=True,
            text=True,
            timeout=120,
        )
        return {
            "ok": proc.returncode == 0,
            "stdout": proc.stdout[-2000:],
            "stderr": proc.stderr[-2000:],
            "returncode": proc.returncode,
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "timeout"}
    except FileNotFoundError:
        return {"ok": False, "error": "docker CLI not installed in container"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}


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
        raise HTTPException(status_code=400, detail=f"telegram rejects token: {test.get('error')}")

    _write_env_value(key, body.token)
    service = BOT_TOKEN_KEYS[key]
    restart = _restart_service(service)

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

    # Restart the bots that use the admin list
    services = ["admin-bot", "content-bot", "sales-bot", "guardian-bot"]
    restarts = {s: _restart_service(s) for s in services}

    ip = request.client.host if request.client else None
    await _log_action(admin["id"], "add_admin_id", str(body.telegram_id), {"ids_after": ids}, ip)

    return {"ok": True, "ids": ids, "restarts": restarts}


@router.delete("/admin-ids/{telegram_id}")
async def remove_admin_id(
    telegram_id: int,
    request: Request,
    admin=Depends(require_role("owner")),
) -> dict:
    env = _read_env()
    ids = _parse_ids(env.get(ADMIN_KEY, ""))
    if telegram_id not in ids:
        raise HTTPException(status_code=404, detail="id not in admin list")
    ids = [i for i in ids if i != telegram_id]
    _write_env_value(ADMIN_KEY, ",".join(str(i) for i in ids))

    services = ["admin-bot", "content-bot", "sales-bot", "guardian-bot"]
    restarts = {s: _restart_service(s) for s in services}

    ip = request.client.host if request.client else None
    await _log_action(admin["id"], "remove_admin_id", str(telegram_id), {"ids_after": ids}, ip)

    return {"ok": True, "ids": ids, "restarts": restarts}
