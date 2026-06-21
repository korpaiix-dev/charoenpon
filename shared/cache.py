"""Simple Redis cache wrapper — async-safe.

Usage:
    from shared.cache import cache_get, cache_set, cached
    
    # Manual
    val = await cache_get("key")
    if val is None:
        val = compute_expensive()
        await cache_set("key", val, ttl=300)
    
    # Decorator
    @cached(ttl=300, key_prefix="user_balance")
    async def get_balance(tg_id):
        ...
"""
from __future__ import annotations
import json
import logging
import os
from functools import wraps
from typing import Any, Callable

logger = logging.getLogger(__name__)

_REDIS = None


async def _get_redis():
    global _REDIS
    if _REDIS is None:
        try:
            import redis.asyncio as _redis
            _REDIS = _redis.from_url(
                os.environ.get("REDIS_URL", "redis://charoenpon-redis:6379/0"),
                decode_responses=True,
                socket_timeout=2,
                socket_connect_timeout=2,
            )
        except Exception as e:
            logger.warning("redis init failed: %s", e)
            _REDIS = False
    return _REDIS if _REDIS else None


async def cache_get(key: str) -> Any | None:
    r = await _get_redis()
    if r is None:
        return None
    try:
        v = await r.get(key)
        return json.loads(v) if v else None
    except Exception as e:
        logger.warning("cache_get(%s) failed: %s", key, e)
        return None


async def cache_set(key: str, value: Any, ttl: int = 300) -> bool:
    r = await _get_redis()
    if r is None:
        return False
    try:
        await r.set(key, json.dumps(value, default=str, ensure_ascii=False), ex=ttl)
        return True
    except Exception as e:
        logger.warning("cache_set(%s) failed: %s", key, e)
        return False


async def cache_del(key: str) -> bool:
    r = await _get_redis()
    if r is None:
        return False
    try:
        await r.delete(key)
        return True
    except Exception:
        return False


async def cache_del_pattern(pattern: str) -> int:
    """Delete keys matching pattern (e.g., 'user_balance:*')."""
    r = await _get_redis()
    if r is None:
        return 0
    try:
        deleted = 0
        async for key in r.scan_iter(pattern):
            await r.delete(key)
            deleted += 1
        return deleted
    except Exception:
        return 0


def cached(ttl: int = 300, key_prefix: str = ""):
    """Decorator — cache async function result by args."""
    def deco(fn: Callable):
        @wraps(fn)
        async def wrapped(*args, **kwargs):
            try:
                cache_key = f"{key_prefix}:" + ":".join(str(a) for a in args) + ":" + ":".join(f"{k}={v}" for k, v in sorted(kwargs.items()))
                v = await cache_get(cache_key)
                if v is not None:
                    return v
                result = await fn(*args, **kwargs)
                if result is not None:
                    await cache_set(cache_key, result, ttl=ttl)
                return result
            except Exception:
                return await fn(*args, **kwargs)
        return wrapped
    return deco


__all__ = ["cache_get", "cache_set", "cache_del", "cache_del_pattern", "cached"]
