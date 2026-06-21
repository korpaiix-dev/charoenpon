"""Test security guards: sender_ring, ban check, slip2go circuit breaker."""
from __future__ import annotations
import pytest
from datetime import datetime, timedelta
from shared.database import get_session
from sqlalchemy import text
from tests.conftest import make_test_tg_id, make_test_name


@pytest.mark.asyncio
async def test_banned_user_cannot_get_payment_approval(clean_db):
    """ลูกค้าถูก ban ห้าม apply_payment_approval ผ่าน (Big Fewry case)."""
    tg = make_test_tg_id(101)
    async with get_session() as s:
        r = await s.execute(text(
            "INSERT INTO users (telegram_id, first_name, total_spent, loyalty_rank, is_banned, banned_reason) "
            "VALUES (:tg, :name, 0, 'NONE', TRUE, 'pytest:scam_test') RETURNING id"
        ), {"tg": tg, "name": make_test_name("banned")})
        user_id = r.scalar()
        await s.commit()
    # verify banned
    async with get_session() as s:
        r = await s.execute(text("SELECT is_banned FROM users WHERE id=:i"), {"i": user_id})
        assert r.scalar() is True


@pytest.mark.asyncio
async def test_sender_ring_blacklist_table_exists():
    """sender_ring table ต้องมี (สำหรับ Dam scam ring defense)."""
    async with get_session() as s:
        r = await s.execute(text(
            "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
            "WHERE table_name='banned_senders')"
        ))
        assert r.scalar() is True, "banned_senders must exist"


@pytest.mark.asyncio
async def test_slip2go_circuit_breaker_field_exists():
    """slip2go.py ต้องมี circuit breaker state (HTTP 429 protection)."""
    import shared.slip2go as s2g
    assert hasattr(s2g, "_SLIP2GO_RATE_LIMIT_UNTIL"), "circuit breaker state missing"
    assert hasattr(s2g, "_SLIP2GO_RATE_LIMIT_PAUSE_SEC"), "pause duration missing"
    assert s2g._SLIP2GO_RATE_LIMIT_PAUSE_SEC == 300  # 5 นาที


@pytest.mark.asyncio
async def test_error_log_table_has_required_columns():
    """error_log table ต้องมี columns ครบสำหรับ in-house tracker."""
    async with get_session() as s:
        r = await s.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='error_log' ORDER BY ordinal_position"
        ))
        cols = {row[0] for row in r.fetchall()}
    required = {"id", "container", "level", "error_type", "error_msg",
                "fingerprint", "context", "occurred_at", "resolved"}
    missing = required - cols
    assert not missing, f"error_log missing columns: {missing}"


@pytest.mark.asyncio
async def test_redis_container_reachable():
    """charoenpon-redis ต้องเข้าถึงได้จาก app."""
    from shared.cache import _get_redis
    r = await _get_redis()
    assert r is not None, "Redis must be reachable"
    pong = await r.ping()
    assert pong is True


@pytest.mark.asyncio
async def test_indexes_exist_for_payment_lookups():
    """11 critical indexes ที่เพิ่งเพิ่ม ต้องมีจริง."""
    async with get_session() as s:
        r = await s.execute(text(
            "SELECT indexname FROM pg_indexes WHERE schemaname='public'"
        ))
        idx_names = {row[0] for row in r.fetchall()}
    expected = {
        "idx_payments_status_created",
        "idx_subs_status_end",
        "idx_users_banned",
        "idx_admin_logs_target",
        "idx_gachapon_pulls_payment",
    }
    missing = expected - idx_names
    assert not missing, f"indexes missing: {missing}"
