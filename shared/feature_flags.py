"""Feature flag helpers — phase A.1 (2026-06-26).

Usage:
    from shared.feature_flags import is_flag_enabled

    if await is_flag_enabled("bot_messages_enabled", telegram_id=user_id):
        msg = await get_bot_message("welcome_new")
    else:
        msg = HARDCODED_WELCOME

CRITICAL: All flags default OFF. If table missing / lookup fails → return False.
NEVER raise from this module — flag check failing should mean "use old behavior".
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Tiny in-process cache (60 sec) to avoid hitting DB on every bot message
_cache: dict[str, tuple[bool, str, list[int] | None, float]] = {}
_CACHE_TTL_SEC = 60.0


async def is_flag_enabled(
    flag_key: str,
    telegram_id: int | None = None,
    *,
    bypass_cache: bool = False,
) -> bool:
    """Return True if the feature should activate for this user.

    Resolution:
      - flag missing       → False (= old behavior)
      - enabled=False      → False
      - scope=all          → True
      - scope=admin        → True if telegram_id is admin (uses .env ADMIN_TELEGRAM_IDS)
      - scope=canary       → True if telegram_id in canary_user_ids

    Errors → False (fail-closed for safety).
    """
    import time

    cache_hit = _cache.get(flag_key)
    if cache_hit and not bypass_cache:
        enabled, scope, canary, expires = cache_hit
        if expires > time.time():
            return _resolve_scope(enabled, scope, canary, telegram_id)

    try:
        from shared.database import get_session
        from sqlalchemy import text

        async with get_session() as session:
            row = (await session.execute(text(
                "SELECT enabled, scope, canary_user_ids "
                "FROM feature_flags WHERE flag_key = :k"
            ), {"k": flag_key})).fetchone()

        if not row:
            _cache[flag_key] = (False, "all", None, time.time() + _CACHE_TTL_SEC)
            return False

        enabled = bool(row.enabled)
        scope = row.scope or "all"
        canary = list(row.canary_user_ids or []) if row.canary_user_ids else None
        _cache[flag_key] = (enabled, scope, canary, time.time() + _CACHE_TTL_SEC)
        return _resolve_scope(enabled, scope, canary, telegram_id)
    except Exception as exc:
        logger.warning("feature flag check failed for %s: %s", flag_key, exc)
        return False


def _resolve_scope(
    enabled: bool, scope: str, canary: list[int] | None, telegram_id: int | None,
) -> bool:
    if not enabled:
        return False
    if scope == "all":
        return True
    if scope == "admin":
        # delegate to env-based admin check
        try:
            import os
            ids = [int(x) for x in (os.getenv("ADMIN_TELEGRAM_IDS") or "").split(",") if x.strip()]
            return telegram_id is not None and telegram_id in ids
        except Exception:
            return False
    if scope == "canary":
        return telegram_id is not None and canary is not None and telegram_id in canary
    return False


async def list_flags() -> list[dict]:
    """Return all flags (for dashboard UI)."""
    try:
        from shared.database import get_session
        from sqlalchemy import text
        async with get_session() as session:
            rows = (await session.execute(text(
                "SELECT flag_key, enabled, scope, canary_user_ids, description, updated_at, updated_by "
                "FROM feature_flags ORDER BY flag_key"
            ))).fetchall()
            return [dict(r._mapping) for r in rows]
    except Exception as exc:
        logger.error("list_flags failed: %s", exc)
        return []


def clear_cache() -> None:
    """Force re-read from DB on next call (used after dashboard toggle)."""
    _cache.clear()
