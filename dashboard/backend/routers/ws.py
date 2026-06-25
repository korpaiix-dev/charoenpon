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
