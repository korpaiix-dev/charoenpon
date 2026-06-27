"""Maintenance mode check — shared across bots.

When `system_maintenance_mode` flag is ON in feature_flags table:
- sales_bot blocks new orders (/start /buy /packages /gacha)
- gacha spinning disabled
- slip auto-approve disabled (goes to manual review)
- VIP customers still access groups normally
- admin_bot still works

Cached 5s to avoid hammering DB.
"""
import os, time, logging
from typing import Optional

logger = logging.getLogger(__name__)

_cache = {"value": None, "ts": 0.0}
_TTL = 5.0

ADMIN_CONTACT_URL = "https://t.me/sperm6969"
ADMIN_CONTACT_NAME = "บอสไผ่"


async def is_maintenance_mode() -> bool:
    """Returns True if system is in maintenance mode. False on error (fail-open)."""
    now = time.time()
    if _cache["value"] is not None and now - _cache["ts"] < _TTL:
        return _cache["value"]
    try:
        import asyncpg
        url = os.environ.get("DATABASE_URL", "").replace("postgresql+asyncpg://", "postgresql://")
        if not url:
            return False
        conn = await asyncpg.connect(url)
        try:
            row = await conn.fetchrow(
                "SELECT enabled FROM feature_flags WHERE flag_key = $1",
                "system_maintenance_mode",
            )
            val = bool(row["enabled"]) if row else False
        finally:
            await conn.close()
        _cache["value"] = val
        _cache["ts"] = now
        return val
    except Exception as exc:
        logger.warning("maintenance check failed: %s", exc)
        return False  # fail open — don't accidentally block all sales on DB outage


def build_maintenance_reply():
    """Returns (text, InlineKeyboardMarkup) for blocking purchase intent."""
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    text = (
        "🔧 <b>กำลังปรับปรุงระบบครั้งใหญ่</b>\n\n"
        "ทีมงานเจริญพรกำลังพัฒนาระบบให้ดีขึ้น\n"
        "<b>การสมัครอัตโนมัติชั่วคราวปิดอยู่</b>\n\n"
        "หากอยากสมัครหรือมีเรื่องเร่งด่วน\n"
        "👇 ติดต่อแอดมินได้เลย"
    )
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton(f"💬 ติดต่อแอดมิน", url=ADMIN_CONTACT_URL),
    ]])
    return text, kb
