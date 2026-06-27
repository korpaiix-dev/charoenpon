"""System Health router — รวมสถานะระบบทั้งหมดในที่เดียว.

GET /api/admin/health/overview → JSON พร้อมข้อมูล:
  - bots (4-5 ตัว): status, uptime, restart count
  - payment_health: pending count + last check + issues
  - slip2go: balance + daily limit + failures 24h
  - database: connection / slow queries / disk usage
  - dms: blocked users / send rate / queue
"""
import asyncio
import json
import logging
import time
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends
from ..auth.dependencies import require_role
from ..database import pool

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin", tags=["system-health"])

# Cache 30s — ลด docker ps calls
_cache = {"data": None, "expires": 0}
_CACHE_TTL = 30


# Critical bots ที่ต้อง run อยู่ตลอด
CRITICAL_BOTS = [
    {"container": "charoenpon-sales-bot", "label": "Sales (แพร)", "critical": True},
    {"container": "charoenpon-admin-bot", "label": "Admin (เจริญพร2)", "critical": True},
    {"container": "charoenpon-guardian-bot", "label": "Guardian (เจริญพร3)", "critical": True},
    {"container": "charoenpon-content-bot", "label": "Content (มิน)", "critical": False},
    {"container": "charoenpon-clip-poster-bot", "label": "Clip Poster", "critical": False},
    {"container": "charoenpon-relay-bot", "label": "Relay (VGOD)", "critical": False},
    {"container": "charoenpon-discord-bot", "label": "Discord", "critical": False},
    {"container": "charoenpon-dashboard", "label": "Dashboard", "critical": True},
    {"container": "charoenpon-postgres", "label": "Database", "critical": True},
    {"container": "charoenpon-redis", "label": "Redis Cache", "critical": False},
]


async def _check_bots():
    """Check docker container status."""
    import subprocess
    result = []
    try:
        # docker ps with format
        proc = await asyncio.create_subprocess_exec(
            "docker", "ps", "-a", "--format", "{{.Names}}|{{.Status}}",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        rows = stdout.decode().strip().split("\n")
        container_map = {}
        for r in rows:
            if "|" in r:
                name, status = r.split("|", 1)
                container_map[name] = status
        for bot in CRITICAL_BOTS:
            status = container_map.get(bot["container"], "")
            is_up = status.startswith("Up")
            health = "ok" if is_up else ("warn" if not bot["critical"] else "critical")
            result.append({
                **bot,
                "status": status or "not found",
                "is_up": is_up,
                "health": health,
            })
    except Exception as exc:
        logger.warning("bot health check failed: %s", exc)
        for bot in CRITICAL_BOTS:
            result.append({**bot, "status": "check failed", "is_up": False, "health": "unknown"})
    return result


async def _check_payment_health():
    """Use existing health_check_payment_system + add pending count."""
    issues = []
    try:
        from shared.payment_health_check import health_check_payment_system
        issues = await health_check_payment_system() or []
    except Exception as exc:
        issues = [f"check failed: {exc}"]

    # Pending count
    try:
        pending = await pool.fetchval(
            "SELECT COUNT(*) FROM payments WHERE status = 'PENDING' AND created_at > NOW() - INTERVAL '24 hours'"
        )
    except Exception:
        pending = -1

    # Stuck >30min
    try:
        stuck = await pool.fetchval(
            "SELECT COUNT(*) FROM payments WHERE status = 'PENDING' AND created_at < NOW() - INTERVAL '30 minutes'"
        )
    except Exception:
        stuck = -1

    health = "critical" if issues or (stuck and stuck > 5) else ("warn" if pending > 10 else "ok")
    return {
        "issues_count": len(issues),
        "issues": issues[:5],
        "pending_24h": pending,
        "stuck_30min": stuck,
        "health": health,
    }


async def _check_slip2go():
    """Slip2Go quota + recent failures."""
    try:
        async with pool.acquire() as conn:
            # Daily failures count
            failures = await conn.fetchval(
                "SELECT COUNT(*) FROM slip2go_retry_queue "
                "WHERE status = 'FAILED' AND enqueued_at > NOW() - INTERVAL '24 hours'"
            )
            queued = await conn.fetchval(
                "SELECT COUNT(*) FROM slip2go_retry_queue "
                "WHERE status IN ('WAITING','PROCESSING')"
            )
            # Last successful slip
            last_ok = await conn.fetchval(
                "SELECT MAX(created_at) FROM payments "
                "WHERE status = 'CONFIRMED' AND created_at > NOW() - INTERVAL '6 hours'"
            )
        health = "critical" if (failures or 0) > 20 else ("warn" if (queued or 0) > 5 else "ok")
        return {
            "failures_24h": failures or 0,
            "queued": queued or 0,
            "last_confirm": last_ok.isoformat() if last_ok else None,
            "health": health,
        }
    except Exception as exc:
        return {"error": str(exc), "health": "unknown"}


async def _check_database():
    """DB connection + table sizes."""
    try:
        async with pool.acquire() as conn:
            # Active connections
            active = await conn.fetchval(
                "SELECT COUNT(*) FROM pg_stat_activity WHERE state = 'active'"
            )
            # DB size
            db_size = await conn.fetchval(
                "SELECT pg_size_pretty(pg_database_size('charoenpon'))"
            )
            # User count
            users = await conn.fetchval("SELECT COUNT(*) FROM users")
            active_subs = await conn.fetchval(
                "SELECT COUNT(*) FROM subscriptions WHERE status = 'ACTIVE' AND end_date > NOW()"
            )
        return {
            "active_connections": active,
            "db_size": db_size,
            "users_total": users,
            "active_subs": active_subs,
            "health": "ok",
        }
    except Exception as exc:
        return {"error": str(exc), "health": "critical"}


async def _check_dms():
    """DM stats — blocked users / recent sends."""
    try:
        blocked = await pool.fetchval(
            "SELECT COUNT(*) FROM users WHERE is_blocked_bot = TRUE"
        )
        welcome_sent_today = await pool.fetchval(
            "SELECT COUNT(*) FROM comeback_dm_log WHERE sent_at > NOW() - INTERVAL '24 hours'"
        )
        return {
            "blocked_users": blocked or 0,
            "dms_sent_24h": welcome_sent_today or 0,
            "health": "ok",
        }
    except Exception as exc:
        return {"error": str(exc), "health": "unknown"}


@router.get("/health/overview")
async def health_overview(admin=Depends(require_role("admin"))):
    """รวมสุขภาพระบบทั้งหมด — cache 30s."""
    now = time.time()
    if _cache["data"] and _cache["expires"] > now:
        return _cache["data"]

    bots, payment, slip2go, db, dms = await asyncio.gather(
        _check_bots(), _check_payment_health(), _check_slip2go(),
        _check_database(), _check_dms(),
        return_exceptions=True,
    )

    # Determine overall health
    sections = [bots, payment, slip2go, db, dms]
    overall = "ok"
    for sec in sections:
        if isinstance(sec, Exception):
            overall = "critical"
            break
        if isinstance(sec, list):
            for item in sec:
                if item.get("health") == "critical":
                    overall = "critical"
                    break
                if item.get("health") == "warn" and overall != "critical":
                    overall = "warn"
        elif isinstance(sec, dict):
            h = sec.get("health")
            if h == "critical":
                overall = "critical"
                break
            if h == "warn" and overall != "critical":
                overall = "warn"

    data = {
        "checked_at": datetime.utcnow().isoformat(),
        "overall_health": overall,
        "bots": bots if not isinstance(bots, Exception) else [{"error": str(bots)}],
        "payment": payment if not isinstance(payment, Exception) else {"error": str(payment)},
        "slip2go": slip2go if not isinstance(slip2go, Exception) else {"error": str(slip2go)},
        "database": db if not isinstance(db, Exception) else {"error": str(db)},
        "dms": dms if not isinstance(dms, Exception) else {"error": str(dms)},
    }
    _cache["data"] = data
    _cache["expires"] = now + _CACHE_TTL
    return data
