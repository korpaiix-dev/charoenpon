"""Test rate_limit — requires fastapi (dashboard, gacha-api containers)."""
from __future__ import annotations
import pytest

pytest.importorskip("fastapi")


@pytest.mark.asyncio
async def test_rate_limit_fail_open_when_redis_down(monkeypatch):
    from shared import rate_limit_simple
    from shared import cache as shared_cache
    from unittest.mock import MagicMock

    async def fake_get_redis():
        return None
    monkeypatch.setattr(shared_cache, "_get_redis", fake_get_redis)
    req = MagicMock()
    req.client.host = "127.0.0.1"
    req.headers = {}
    await rate_limit_simple.rate_limit_check(req, key="test_failopen", limit=1, window=10)


@pytest.mark.asyncio
async def test_rate_limit_blocks_after_exceed():
    from shared.rate_limit_simple import rate_limit_check
    from fastapi import HTTPException
    from unittest.mock import MagicMock
    import time

    req = MagicMock()
    req.client.host = f"1.2.3.{int(time.time()) % 250}"
    req.headers = {}

    blocked = 0
    for i in range(5):
        try:
            await rate_limit_check(req, key="pytest_rl", limit=3, window=60)
        except HTTPException as e:
            assert e.status_code == 429
            blocked += 1
    assert blocked == 2


@pytest.mark.asyncio
async def test_rate_limit_uses_forwarded_for():
    from shared.rate_limit_simple import rate_limit_check
    from fastapi import HTTPException
    from unittest.mock import MagicMock
    import time

    req = MagicMock()
    req.client.host = "127.0.0.1"
    unique_ip = f"9.8.7.{int(time.time() * 7) % 250}"
    req.headers = {"x-forwarded-for": f"{unique_ip}, 10.0.0.1"}

    await rate_limit_check(req, key="pytest_rl_xff", limit=1, window=60)
    try:
        await rate_limit_check(req, key="pytest_rl_xff", limit=1, window=60)
        assert False, "second request should be blocked"
    except HTTPException as e:
        assert e.status_code == 429
