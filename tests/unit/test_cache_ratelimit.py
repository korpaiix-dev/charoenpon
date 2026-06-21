"""Test Redis cache wrapper — used by sales-bot, dashboard, gacha-api."""
from __future__ import annotations
import pytest


@pytest.mark.asyncio
async def test_cache_set_get_basic():
    from shared.cache import cache_set, cache_get, cache_del
    ok = await cache_set("test:key1", {"a": 1, "b": "hello"}, ttl=10)
    assert ok is True
    got = await cache_get("test:key1")
    assert got == {"a": 1, "b": "hello"}
    await cache_del("test:key1")
    assert (await cache_get("test:key1")) is None


@pytest.mark.asyncio
async def test_cache_get_missing_returns_none():
    from shared.cache import cache_get
    assert (await cache_get("test:does-not-exist:xyz")) is None


@pytest.mark.asyncio
async def test_cache_set_with_ttl_expires():
    from shared.cache import cache_set, cache_get
    import asyncio
    await cache_set("test:ttl1", "value", ttl=1)
    assert (await cache_get("test:ttl1")) == "value"
    await asyncio.sleep(1.3)
    assert (await cache_get("test:ttl1")) is None
