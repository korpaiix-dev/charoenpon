"""Shared admin permission helper for bot Python code.

Used by admin_bot, guardian_bot, etc. — single source of truth.

CASCADE FALLBACK (safe for production):
  1. Query admin_bot_permissions (60s cache)
  2. Fall back to env ADMIN_TELEGRAM_IDS
  3. Owner (env) always allowed: 8502597269

Never raises — returns False on total failure.
"""
from __future__ import annotations
import os
import time
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# 60-second cache: {bot_key: (set_of_tg_ids, expires_at)}
_perm_cache: dict[str, tuple[frozenset[int], float]] = {}
_CACHE_TTL = 60.0


def _env_admins() -> set[int]:
    """Read ADMIN_TELEGRAM_IDS env into a set of ints."""
    raw = os.environ.get("ADMIN_TELEGRAM_IDS", "8502597269")
    return {int(x.strip()) for x in raw.split(",") if x.strip().isdigit()}


def _db_admins_sync(bot_key: str) -> Optional[set[int]]:
    """Synchronous query of admin_bot_permissions. Returns None on failure."""
    try:
        import asyncio as _aio
        # Reuse async path via run_until_complete in a temp loop
        loop = _aio.new_event_loop()
        try:
            result = loop.run_until_complete(_db_admins_async(bot_key))
        finally:
            loop.close()
        return result
    except Exception as exc:
        logger.warning("admin_perms sync query failed for %s: %s", bot_key, exc)
        return None


async def _db_admins_async(bot_key: str) -> Optional[set[int]]:
    """Async query of admin_bot_permissions. Returns None on failure (not empty set)."""
    try:
        import asyncpg
        url = os.environ.get("DATABASE_URL", "").replace("postgresql+asyncpg://", "postgresql://")
        if not url:
            return None
        conn = await asyncpg.connect(url)
        try:
            rows = await conn.fetch(
                "SELECT da.telegram_id FROM admin_bot_permissions abp "
                "JOIN dashboard_admins da ON da.id = abp.admin_id "
                "WHERE abp.bot_key = $1 AND da.is_active = TRUE",
                bot_key,
            )
            return {int(r["telegram_id"]) for r in rows}
        finally:
            await conn.close()
    except Exception as exc:
        logger.warning("admin_perms async query failed for %s: %s", bot_key, exc)
        return None


def is_admin_for_bot(tg_id: int, bot_key: str = "admin_bot") -> bool:
    """Check if tg_id has permission for this bot.

    SAFE CASCADE:
      1. Cached DB set (refreshed 60s) → if member, allow
      2. Env ADMIN_TELEGRAM_IDS → if member, allow (NEVER lose access during outage)
      3. Otherwise deny.

    Sync entry point — safe to call from any code path.
    """
    if not isinstance(tg_id, int) or tg_id <= 0:
        return False

    now = time.time()
    cached = _perm_cache.get(bot_key)
    if cached and cached[1] > now:
        db_set = cached[0]
    else:
        fetched = _db_admins_sync(bot_key)
        if fetched is not None:
            db_set = frozenset(fetched)
            _perm_cache[bot_key] = (db_set, now + _CACHE_TTL)
        else:
            # DB unreachable — use cached value if any, else empty
            db_set = cached[0] if cached else frozenset()

    if tg_id in db_set:
        return True

    # Env fallback — preserves access during DB outage AND for users not yet
    # ticked in Dashboard
    env_set = _env_admins()
    if tg_id in env_set:
        return True

    return False


async def is_admin_for_bot_async(tg_id: int, bot_key: str = "admin_bot") -> bool:
    """Async variant. Use in async handlers to avoid loop juggling."""
    if not isinstance(tg_id, int) or tg_id <= 0:
        return False

    now = time.time()
    cached = _perm_cache.get(bot_key)
    if cached and cached[1] > now:
        db_set = cached[0]
    else:
        fetched = await _db_admins_async(bot_key)
        if fetched is not None:
            db_set = frozenset(fetched)
            _perm_cache[bot_key] = (db_set, now + _CACHE_TTL)
        else:
            db_set = cached[0] if cached else frozenset()

    if tg_id in db_set:
        return True
    if tg_id in _env_admins():
        return True
    return False


def get_allowed_admins(bot_key: str = "admin_bot") -> set[int]:
    """Return UNION of (DB admins + env admins). Used by code that needs full list."""
    now = time.time()
    cached = _perm_cache.get(bot_key)
    if cached and cached[1] > now:
        db_set = set(cached[0])
    else:
        fetched = _db_admins_sync(bot_key)
        if fetched is not None:
            db_set = set(fetched)
            _perm_cache[bot_key] = (frozenset(db_set), now + _CACHE_TTL)
        else:
            db_set = set(cached[0]) if cached else set()
    return db_set | _env_admins()
