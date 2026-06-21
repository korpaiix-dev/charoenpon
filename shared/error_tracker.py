"""Charoenpon Error Tracker — self-hosted, no third-party.

ใช้แทน Sentry. เก็บ errors ทุกตัวลง DB + ดูที่ dashboard.

Usage:
    from shared.error_tracker import track_error
    try:
        ...
    except Exception as e:
        await track_error(e, container="sales-bot", telegram_id=tg_id, context={"step": "STEP_9"})
        raise
"""
from __future__ import annotations
import hashlib
import json
import logging
import os
import traceback
from typing import Any

logger = logging.getLogger(__name__)


def _fingerprint(error_type: str, error_msg: str, stack_top: str = "") -> str:
    """Fingerprint = group errors เหมือนกันเป็น 1 issue."""
    msg_normalized = error_msg[:200].split("\n")[0]
    sig = f"{error_type}|{msg_normalized}|{stack_top}"
    return hashlib.sha256(sig.encode()).hexdigest()[:16]


def _extract_stack_top(tb_str: str) -> str:
    lines = [l for l in tb_str.split("\n") if 'File "' in l and 'site-packages' not in l]
    return lines[-1].strip()[:200] if lines else ""


async def track_error(
    error: Exception,
    *,
    container: str | None = None,
    level: str = "ERROR",
    telegram_id: int | None = None,
    user_id: int | None = None,
    payment_id: int | None = None,
    context: dict | None = None,
) -> int | None:
    """Save error to DB. Returns error_log.id or None on failure."""
    try:
        from shared.database import get_session
        from sqlalchemy import text as _t

        err_type = type(error).__name__
        err_msg = str(error)[:2000]
        stack = traceback.format_exc()[:8000]
        stack_top = _extract_stack_top(stack)
        fp = _fingerprint(err_type, err_msg, stack_top)
        ctnr = container or os.environ.get("BOT_MODULE", "unknown").split(".")[-1]
        ctx_json = json.dumps(context, default=str, ensure_ascii=False)[:5000] if context else None

        async with get_session() as s:
            r = await s.execute(_t("""
                INSERT INTO error_log
                    (container, level, error_type, error_msg, stack_trace, fingerprint,
                     telegram_id, user_id, payment_id, context)
                VALUES (:c, :lvl, :et, :em, :st, :fp, :tg, :uid, :pid, CAST(:ctx AS JSONB))
                RETURNING id
            """), {
                "c": ctnr, "lvl": level, "et": err_type, "em": err_msg, "st": stack,
                "fp": fp, "tg": telegram_id, "uid": user_id, "pid": payment_id, "ctx": ctx_json,
            })
            error_id = r.scalar()
            await s.commit()
            return error_id
    except Exception as exc:
        logger.warning("track_error failed: %s", exc)
        return None


__all__ = ["track_error"]
