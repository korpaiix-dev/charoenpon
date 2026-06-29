"""
Bank Notification Webhook — รับ webhook จาก PUSHERRR app
==========================================================

Phase 1 (Probe mode): catch-all endpoint — log ทุก request ลง DB + ไฟล์
                       เพื่อให้บอสกดทดสอบใน PUSHERRR แล้วเราดู format ที่ส่งมา

วิธีใช้:
- ใส่ URL ใน PUSHERRR: http://139.59.123.146:8010/api/bank-notify
- กดปุ่ม "ทดสอบ" หรือ "ทดสอบส่งการแจ้งเตือน" ใน app
- ดู log: tail -f /var/log/charoenpon/bank_notify.log
- หรือดู DB: SELECT * FROM bank_notify_probe ORDER BY created_at DESC LIMIT 10;
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from typing import Any

import asyncpg
from fastapi import APIRouter, Request, Depends
from ..auth.dependencies import require_role

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["bank-notify"])


def _conn_str() -> str:
    return os.environ.get("DATABASE_URL", "").replace("postgresql+asyncpg://", "postgresql://")


async def _ensure_probe_table():
    """One-shot create — runs at first request."""
    try:
        conn = await asyncpg.connect(_conn_str())
        try:
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS bank_notify_probe (
                    id SERIAL PRIMARY KEY,
                    method VARCHAR(10),
                    headers JSONB,
                    body_raw TEXT,
                    body_json JSONB,
                    remote_ip VARCHAR(64),
                    user_agent VARCHAR(256),
                    created_at TIMESTAMP NOT NULL DEFAULT NOW()
                );
                CREATE INDEX IF NOT EXISTS idx_bank_notify_probe_created
                    ON bank_notify_probe (created_at DESC);
                """
            )
        finally:
            await conn.close()
    except Exception as exc:
        logger.error("ensure bank_notify_probe table failed: %s", exc)


@router.api_route("/bank-notify", methods=["GET", "POST", "PUT", "PATCH"])
async def bank_notify_catchall(request: Request) -> dict[str, Any]:
    """รับทุก method + ทุก content-type — แค่ log ไม่ทำอะไรกับข้อมูล

    Phase 1: เก็บข้อมูลเพื่อดู format จาก PUSHERRR
    Phase 2: เมื่อรู้ format แล้ว → parse + match กับ payments → call record_payment_received
    """
    await _ensure_probe_table()

    # อ่าน body raw (รองรับ JSON + form + raw text)
    body_raw = ""
    body_json: dict | list | None = None
    try:
        body_bytes = await request.body()
        body_raw = body_bytes.decode("utf-8", errors="replace") if body_bytes else ""
    except Exception as exc:
        logger.warning("read body failed: %s", exc)

    if body_raw:
        try:
            body_json = json.loads(body_raw)
        except Exception:
            body_json = None  # not JSON — keep raw

    headers = dict(request.headers)
    remote_ip = request.client.host if request.client else ""
    user_agent = headers.get("user-agent", "")[:256]

    # Log to DB
    try:
        conn = await asyncpg.connect(_conn_str())
        try:
            await conn.execute(
                """
                INSERT INTO bank_notify_probe
                    (method, headers, body_raw, body_json, remote_ip, user_agent)
                VALUES ($1, $2::jsonb, $3, $4::jsonb, $5, $6)
                """,
                request.method,
                json.dumps(headers, ensure_ascii=False),
                body_raw[:8000],  # cap to 8KB
                json.dumps(body_json, ensure_ascii=False) if body_json is not None else None,
                remote_ip,
                user_agent,
            )
        finally:
            await conn.close()
    except Exception as exc:
        logger.error("log to bank_notify_probe failed: %s", exc)

    # Log to console (so admin sees it in `docker logs`)
    logger.info(
        "[BANK_NOTIFY_PROBE] method=%s ip=%s ua=%s body_len=%d body_preview=%s",
        request.method,
        remote_ip,
        user_agent[:60],
        len(body_raw),
        body_raw[:200].replace("\n", " "),
    )

    # ตอบกลับเสมอ 200 OK — บาง app expect specific response
    return {
        "status": "ok",
        "received_at": datetime.utcnow().isoformat(),
        "echo": {
            "method": request.method,
            "body_len": len(body_raw),
        },
    }


@router.get("/bank-notify/probe-logs")
async def list_probe_logs(limit: int = 20, admin=Depends(require_role("admin"))) -> dict:
    """View probe logs (no auth — internal use only, port 8010 not exposed publicly).

    Use Dashboard query param ?limit=20 to see recent received payloads.
    """
    await _ensure_probe_table()
    try:
        conn = await asyncpg.connect(_conn_str())
        try:
            rows = await conn.fetch(
                """
                SELECT id, method, body_raw, body_json, remote_ip, user_agent, created_at
                FROM bank_notify_probe
                ORDER BY created_at DESC
                LIMIT $1
                """,
                int(limit),
            )
        finally:
            await conn.close()
        items = []
        for r in rows:
            items.append({
                "id": r["id"],
                "method": r["method"],
                "body_raw": (r["body_raw"] or "")[:500],
                "body_json": r["body_json"],
                "remote_ip": r["remote_ip"],
                "user_agent": (r["user_agent"] or "")[:100],
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            })
        return {"items": items, "count": len(items)}
    except Exception as exc:
        return {"error": str(exc), "items": [], "count": 0}
