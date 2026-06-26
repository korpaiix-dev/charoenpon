"""Promo configuration helpers — Phase B.1 (2026-06-27).

Read promo settings from DB with fail-safe fallback.

Usage:
    from shared.promo_config import get_promo_config

    # Scalar
    days = await get_promo_config("comeback_r1_days_after_expiry", default=3)
    pct = await get_promo_config("comeback_r1_discount_pct", default=30)

    # JSON dict
    caps = await get_promo_config("gacha_discount_cap_per_tier", default={"VIP_300": 50})

CRITICAL: Never raise. Errors → return default.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

logger = logging.getLogger(__name__)

_cache: dict[str, tuple[Any, float]] = {}
_CACHE_TTL_SEC = 60.0


async def get_promo_config(config_key: str, default: Any = None, *, bypass_cache: bool = False) -> Any:
    """Return parsed JSON value for key, or default if missing/error.

    Safe to call on hot path — cached 60 sec.
    """
    hit = _cache.get(config_key)
    if hit and not bypass_cache:
        value, expires = hit
        if expires > time.time():
            return value

    try:
        from shared.database import get_session
        from sqlalchemy import text
        async with get_session() as s:
            row = (await s.execute(text(
                "SELECT value_json FROM promo_config WHERE config_key = :k"
            ), {"k": config_key})).fetchone()
        if row and row.value_json is not None:
            value = row.value_json
            _cache[config_key] = (value, time.time() + _CACHE_TTL_SEC)
            return value
    except Exception as exc:
        logger.warning("get_promo_config(%s) failed: %s", config_key, exc)
    return default


async def list_promo_configs(category: Optional[str] = None) -> list[dict]:
    """For dashboard listing."""
    try:
        from shared.database import get_session
        from sqlalchemy import text
        async with get_session() as s:
            if category:
                rows = (await s.execute(text(
                    "SELECT config_key, value_json, description, category, updated_at, updated_by "
                    "FROM promo_config WHERE category = :c ORDER BY config_key"
                ), {"c": category})).fetchall()
            else:
                rows = (await s.execute(text(
                    "SELECT config_key, value_json, description, category, updated_at, updated_by "
                    "FROM promo_config ORDER BY category, config_key"
                ))).fetchall()
            return [dict(r._mapping) for r in rows]
    except Exception as exc:
        logger.error("list_promo_configs failed: %s", exc)
        return []


def clear_cache(config_key: str | None = None) -> None:
    """Clear cache after dashboard save."""
    if config_key:
        _cache.pop(config_key, None)
    else:
        _cache.clear()
