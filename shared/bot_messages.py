"""Bot message helpers — phase A.1 (2026-06-26).

Read customer-facing text from DB if available, else fall back to caller default.

Usage:
    from shared.bot_messages import get_bot_message
    from shared.feature_flags import is_flag_enabled

    HARDCODED_WELCOME = "หวัดดีค่า~ ยินดีต้อนรับสู่..."

    text = HARDCODED_WELCOME
    if await is_flag_enabled("bot_messages_enabled", telegram_id=user_id):
        db_msg = await get_bot_message("welcome_new")
        if db_msg:
            text = render_placeholders(db_msg, customer_name=name)

CRITICAL: Never raise. Return None → caller uses fallback.
"""

from __future__ import annotations

import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)

_cache: dict[str, tuple[str, float]] = {}
_CACHE_TTL_SEC = 60.0


async def get_bot_message(message_key: str, *, bypass_cache: bool = False) -> Optional[str]:
    """Return content_html for key, or None if missing/error.

    Safe to call on hot path — cached 60 sec.
    """
    cache_hit = _cache.get(message_key)
    if cache_hit and not bypass_cache:
        content, expires = cache_hit
        if expires > time.time():
            return content or None

    try:
        from shared.database import get_session
        from sqlalchemy import text
        async with get_session() as session:
            row = (await session.execute(text(
                "SELECT content_html FROM bot_messages WHERE message_key = :k"
            ), {"k": message_key})).fetchone()
        content = (row.content_html if row else "") or ""
        _cache[message_key] = (content, time.time() + _CACHE_TTL_SEC)
        return content or None
    except Exception as exc:
        logger.warning("get_bot_message(%s) failed: %s", message_key, exc)
        return None


def render_placeholders(template: str, **kwargs) -> str:
    """Replace {placeholders} in template with kwargs. Missing keys remain literal."""
    if not template:
        return template
    try:
        for k, v in kwargs.items():
            template = template.replace("{" + k + "}", str(v) if v is not None else "")
        return template
    except Exception as exc:
        logger.warning("render_placeholders failed: %s", exc)
        return template


async def list_bot_messages(category: str | None = None) -> list[dict]:
    """For dashboard listing."""
    try:
        from shared.database import get_session
        from sqlalchemy import text
        async with get_session() as session:
            if category:
                rows = (await session.execute(text(
                    "SELECT message_key, content_html, description, category, "
                    "available_placeholders, updated_at, updated_by "
                    "FROM bot_messages WHERE category = :c ORDER BY message_key"
                ), {"c": category})).fetchall()
            else:
                rows = (await session.execute(text(
                    "SELECT message_key, content_html, description, category, "
                    "available_placeholders, updated_at, updated_by "
                    "FROM bot_messages ORDER BY category, message_key"
                ))).fetchall()
            return [dict(r._mapping) for r in rows]
    except Exception as exc:
        logger.error("list_bot_messages failed: %s", exc)
        return []


def clear_cache(message_key: str | None = None) -> None:
    """Force re-read from DB on next call. After dashboard save."""
    if message_key:
        _cache.pop(message_key, None)
    else:
        _cache.clear()

async def render_or_fallback(
    message_key: str,
    fallback: str,
    *,
    bypass_cache: bool = False,
    **placeholders,
) -> str:
    """DB-first lookup with safe fallback. Never raises.

    Order:
      1. Try get_bot_message(key) — 60s cache
      2. If returns content → render_placeholders → return
      3. Else / on any error → return fallback (with placeholders rendered)

    Safe to call on hot path. Drop-in replacement for hardcoded strings.
    """
    try:
        db_msg = await get_bot_message(message_key, bypass_cache=bypass_cache)
        if db_msg:
            return render_placeholders(db_msg, **placeholders) if placeholders else db_msg
    except Exception as exc:
        logger.warning("render_or_fallback(%s) failed: %s", message_key, exc)
    # Fallback: render placeholders in hardcoded text too
    return render_placeholders(fallback, **placeholders) if placeholders else fallback

