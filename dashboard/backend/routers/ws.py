"""Real-time WebSocket for dashboard live updates.

Pattern: client opens /ws/events?token=JWT
- Server polls the DB every 5s for unread events (pending payments, SOS)
- Pushes deltas as JSON

Simpler than pub/sub: avoids needing redis/postgres-notify wiring.
Could be upgraded later.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query

from ..auth.jwt import decode_token
from ..database import pool

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/ws", tags=["ws"])


async def _fetch_snapshot():
    """Return current count of pending payments + open SOS + latest IDs."""
    row = await pool.fetchrow("""
        SELECT
            (SELECT COUNT(*) FROM payments WHERE status='PENDING')::int AS pending_payments,
            (SELECT COUNT(*) FROM sos_alerts WHERE status IN ('OPEN','IN_PROGRESS'))::int AS open_sos,
            (SELECT MAX(id) FROM payments WHERE status='PENDING')::int AS max_pending_id,
            (SELECT MAX(id) FROM sos_alerts WHERE status='OPEN')::int AS max_sos_id
    """)
    return dict(row) if row else {}


@router.websocket("/events")
async def ws_events(websocket: WebSocket, token: Optional[str] = Query(None)):
    """Push live event snapshots every 5s.

    Client: const ws = new WebSocket(`wss://...../ws/events?token=${token}`)
    """
    # Verify JWT
    try:
        payload = decode_token(token or "")
        if not payload:
            await websocket.close(code=1008, reason="invalid token")
            return
        # re-check session not revoked + account active + current role from DB
        # (so demote/disable takes effect immediately, like the HTTP path)
        _sr = await pool.fetchrow(
            "SELECT s.revoked_at, a.is_active, a.role FROM dashboard_sessions s "
            "JOIN dashboard_admins a ON a.id = s.admin_id WHERE s.token_jti = $1",
            payload.get("jti"),
        )
        if (not _sr) or _sr["revoked_at"] or (not _sr["is_active"]):
            await websocket.close(code=1008, reason="session revoked")
            return
        if (_sr["role"] or "") not in ("admin", "super_admin", "owner", "moderator"):
            await websocket.close(code=1008, reason="forbidden")
            return
    except Exception:
        await websocket.close(code=1008, reason="auth failed")
        return

    await websocket.accept()
    logger.info("[WS] connected: telegram_id=%s", payload.get("telegram_id"))

    last_snapshot = await _fetch_snapshot()
    await websocket.send_text(json.dumps({"type": "snapshot", "data": last_snapshot, "ts": int(time.time())}))

    try:
        while True:
            await asyncio.sleep(5)
            cur = await _fetch_snapshot()
            # Detect new payment
            if (cur.get("max_pending_id") or 0) > (last_snapshot.get("max_pending_id") or 0):
                await websocket.send_text(json.dumps({
                    "type": "new_payment",
                    "id": cur["max_pending_id"],
                    "total_pending": cur["pending_payments"],
                    "ts": int(time.time()),
                }))
            # Detect new SOS
            if (cur.get("max_sos_id") or 0) > (last_snapshot.get("max_sos_id") or 0):
                await websocket.send_text(json.dumps({
                    "type": "new_sos",
                    "id": cur["max_sos_id"],
                    "total_open": cur["open_sos"],
                    "ts": int(time.time()),
                }))
            # Always send heartbeat snapshot every iteration so counters stay live
            await websocket.send_text(json.dumps({"type": "tick", "data": cur, "ts": int(time.time())}))
            last_snapshot = cur
    except WebSocketDisconnect:
        logger.info("[WS] disconnected")
    except Exception as exc:
        logger.warning("[WS] error: %s", exc)
        try:
            await websocket.close()
        except Exception:
            pass


# ==================================================================
# Phase A.4 (2026-06-27): Live docker logs streamer
# ==================================================================
ALLOWED_CONTAINERS = {
    "charoenpon-sales-bot",
    "charoenpon-guardian-bot",
    "charoenpon-admin-bot",
    "charoenpon-relay-bot",
    "charoenpon-dashboard",
    "charoenpon-discord-bot",
}


@router.websocket("/logs/{container}")
async def ws_container_logs(websocket: WebSocket, container: str, token: Optional[str] = Query(None)):
    """Stream `docker logs -f --tail=200 <container>` over WebSocket.

    Client: const ws = new WebSocket(`/api/ws/logs/charoenpon-sales-bot?token=${token}`);
    ws.onmessage = (e) => { const m = JSON.parse(e.data); /* m.line / m.eof */ };
    """
    # Auth
    try:
        payload = decode_token(token or "")
        if not payload:
            await websocket.close(code=1008, reason="invalid token")
            return
        # re-check session not revoked + account active + current role from DB
        # (so demote/disable takes effect immediately, like the HTTP path)
        _sr = await pool.fetchrow(
            "SELECT s.revoked_at, a.is_active, a.role FROM dashboard_sessions s "
            "JOIN dashboard_admins a ON a.id = s.admin_id WHERE s.token_jti = $1",
            payload.get("jti"),
        )
        if (not _sr) or _sr["revoked_at"] or (not _sr["is_active"]):
            await websocket.close(code=1008, reason="session revoked")
            return
        # Admin-only (DB role, not token)
        role = _sr["role"] or ""
        if role not in ("admin", "super_admin", "owner"):
            await websocket.close(code=1008, reason="admin only")
            return
    except Exception:
        await websocket.close(code=1008, reason="auth failed")
        return

    # Whitelist check (prevent arbitrary docker exec abuse)
    if container not in ALLOWED_CONTAINERS:
        await websocket.close(code=1008, reason=f"container '{container}' not allowed")
        return

    await websocket.accept()
    logger.info("[WS-LOGS] connected to %s by %s", container, payload.get("telegram_id"))

    import subprocess as _subp
    import asyncio as _aio
    proc = None
    try:
        proc = await _aio.create_subprocess_exec(
            "docker", "logs", "--tail", "200", "-f", container,
            stdout=_subp.PIPE, stderr=_subp.STDOUT,
        )
        # Send heartbeat task
        async def _heartbeat():
            while True:
                await _aio.sleep(15)
                try:
                    await websocket.send_text(json.dumps({"type": "ping", "ts": int(time.time())}))
                except Exception:
                    return
        hb_task = _aio.create_task(_heartbeat())

        # Read stream line-by-line
        assert proc.stdout is not None
        while True:
            try:
                line = await _aio.wait_for(proc.stdout.readline(), timeout=60.0)
            except _aio.TimeoutError:
                continue  # heartbeat already sent
            if not line:
                break
            try:
                text = line.decode("utf-8", errors="ignore").rstrip("\n")
            except Exception:
                text = "<binary log>"
            try:
                await websocket.send_text(json.dumps({"type": "line", "container": container, "line": text}))
            except WebSocketDisconnect:
                break
            except Exception:
                break
        hb_task.cancel()
        try:
            await websocket.send_text(json.dumps({"type": "eof"}))
        except Exception:
            pass
    except WebSocketDisconnect:
        logger.info("[WS-LOGS] disconnected from %s", container)
    except Exception as exc:
        logger.warning("[WS-LOGS] error: %s", exc)
        try:
            await websocket.send_text(json.dumps({"type": "error", "error": str(exc)[:200]}))
        except Exception:
            pass
    finally:
        if proc:
            try:
                proc.terminate()
                await _aio.wait_for(proc.wait(), timeout=2.0)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        try:
            await websocket.close()
        except Exception:
            pass

