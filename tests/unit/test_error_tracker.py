"""Test in-house error tracker — fingerprint + DB insert + dedup."""
from __future__ import annotations
import pytest
from shared.database import get_session
from sqlalchemy import text


@pytest.mark.asyncio
async def test_track_error_inserts_into_db():
    """track_error สร้าง row ใน error_log."""
    from shared.error_tracker import track_error
    err_id = await track_error(
        ValueError("test error from pytest"),
        container="test-container",
        level="ERROR",
        context={"test": True},
    )
    assert err_id is not None and err_id > 0
    async with get_session() as s:
        r = await s.execute(text("SELECT container, error_type, error_msg FROM error_log WHERE id=:i"), {"i": err_id})
        row = r.fetchone()
        assert row is not None
        assert row[0] == "test-container"
        assert row[1] == "ValueError"
        assert "test error from pytest" in row[2]
        await s.execute(text("DELETE FROM error_log WHERE id=:i"), {"i": err_id})
        await s.commit()


@pytest.mark.asyncio
async def test_track_error_fingerprint_dedup():
    """track_error 2 errors เดียวกัน → fingerprint เดียวกัน."""
    from shared.error_tracker import track_error
    e1 = await track_error(ValueError("dedup-test-A"), container="test")
    e2 = await track_error(ValueError("dedup-test-A"), container="test")
    assert e1 is not None and e2 is not None
    async with get_session() as s:
        r = await s.execute(text(
            "SELECT fingerprint FROM error_log WHERE id IN (:a, :b)"
        ), {"a": e1, "b": e2})
        fps = [row[0] for row in r.fetchall()]
        assert len(fps) == 2
        assert fps[0] == fps[1], "same error should have same fingerprint"
        await s.execute(text("DELETE FROM error_log WHERE id IN (:a, :b)"), {"a": e1, "b": e2})
        await s.commit()


@pytest.mark.asyncio
async def test_track_error_fingerprint_format():
    from shared.error_tracker import _fingerprint
    fp = _fingerprint("ValueError", "msg", "stack")
    assert isinstance(fp, str)
    assert len(fp) == 16  # truncated sha256
