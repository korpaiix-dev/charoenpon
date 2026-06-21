"""Pytest fixtures + global setup for charoenpon tests.

Test strategy:
- ใช้ database จริง (postgres ใน docker) — แต่ใช้ schema/prefix แยก (ไม่กระทบ production data)
- Mock Telegram bot — ไม่ส่งจริง
- Mock external APIs (Slip2Go, Gemini Vision)

วิธีรัน:
    docker exec charoenpon-sales-bot pytest /app/tests/ -v
"""
from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio

# Path setup — ใช้ใน docker container
sys.path.insert(0, "/app")

# IMPORTANT: ตั้ง test mode ก่อน import shared.* — ป้องกัน test ส่ง notification เข้าห้องจริง
os.environ["CHAROENPON_TEST_MODE"] = "1"




# ─── Test data prefix — กัน collision กับ production ────────────────────
TEST_PREFIX = "TEST_PYTEST_"
TEST_TG_BASE = 9_900_000_000  # tg id ที่ไม่มีในธรรมชาติ


def make_test_tg_id(offset: int = 0) -> int:
    """Generate unique test telegram_id ที่จะไม่ชนกับลูกค้าจริง."""
    return TEST_TG_BASE + offset


def make_test_name(suffix: str = "") -> str:
    """Generate test user name พร้อม prefix สำหรับล้างทีหลัง."""
    return f"{TEST_PREFIX}{suffix}"


# ─── Cleanup helper — เรียกจาก fixture เพื่อล้าง test data หลัง test ────
async def cleanup_test_data():
    """ลบ test rows ทุกตารางที่เกี่ยวข้องตาม prefix."""
    from shared.database import get_session
    from sqlalchemy import text

    # Use raw asyncpg via get_session — commit each step independently to survive missing tables
    cleanup_queries = [
        "DELETE FROM admin_logs WHERE details LIKE %TEST_PYTEST%",
        "DELETE FROM expiry_notifications WHERE user_id IN (SELECT id FROM users WHERE telegram_id >= 9900000000)",
        "DELETE FROM gachapon_pulls WHERE user_id IN (SELECT id FROM users WHERE telegram_id >= 9900000000)",
        "DELETE FROM gachapon_credits WHERE telegram_id >= 9900000000",
        "DELETE FROM subscriptions WHERE user_id IN (SELECT id FROM users WHERE telegram_id >= 9900000000)",
        "DELETE FROM slip2go_retry_queue WHERE telegram_id >= 9900000000",
        "DELETE FROM payments WHERE user_id IN (SELECT id FROM users WHERE telegram_id >= 9900000000)",
        "DELETE FROM comeback_dm_log WHERE telegram_id >= 9900000000",
        "DELETE FROM user_discount_credits WHERE telegram_id >= 9900000000",
        "DELETE FROM silver_backfill_done WHERE telegram_id >= 9900000000",
        "DELETE FROM welcome_journey_log WHERE telegram_id >= 9900000000",
        "DELETE FROM users WHERE telegram_id >= 9900000000",
    ]
    for q in cleanup_queries:
        try:
            async with get_session() as s:
                await s.execute(text(q))
                await s.commit()
        except Exception:
            pass  # table อาจไม่มี / FK กัน


@pytest_asyncio.fixture
async def clean_db():
    """Fixture — ล้าง test data ก่อน + หลัง test."""
    await cleanup_test_data()
    yield
    await cleanup_test_data()


# ─── Fixture — สร้าง test user ──────────────────────────────────────────
@pytest_asyncio.fixture
async def test_user(clean_db):
    """Create test user + return (user_id, telegram_id)."""
    from shared.database import get_session
    from sqlalchemy import text

    tg_id = make_test_tg_id(1)
    async with get_session() as s:
        r = await s.execute(text(
            "INSERT INTO users (telegram_id, first_name, total_spent, loyalty_rank) "
            "VALUES (:tg, :name, 0, 'NONE') RETURNING id"
        ), {"tg": tg_id, "name": make_test_name("user1")})
        user_id = r.scalar()
        await s.commit()

    return user_id, tg_id


# ─── Mock Telegram Bot ──────────────────────────────────────────────────
@pytest.fixture
def mock_telegram_bot():
    """Mock telegram.Bot — ไม่ส่งจริง"""
    bot = MagicMock()
    bot.initialize = AsyncMock(return_value=None)
    bot.shutdown = AsyncMock(return_value=None)
    bot.send_message = AsyncMock(return_value=MagicMock(message_id=12345))
    bot.send_photo = AsyncMock(return_value=MagicMock(message_id=12345))
    bot.create_chat_invite_link = AsyncMock(
        return_value=MagicMock(invite_link="https://t.me/+TEST_INVITE_LINK")
    )
    bot.get_chat_member = AsyncMock(
        return_value=MagicMock(status="left")  # default: not in group
    )
    return bot


# ─── Mock Slip2Go ────────────────────────────────────────────────────────
@pytest.fixture
def mock_slip2go_ok():
    """Mock Slip2Go ส่ง response สำเร็จ (amount match expected)."""
    async def _mock(*args, **kwargs):
        return {
            "success": True,
            "amount": kwargs.get("expected_amount", Decimal("300")),
            "trans_ref": f"TEST_TRANS_REF_{datetime.now().strftime('%H%M%S')}",
            "sender_name": "TEST SENDER",
            "sender_bank": "KBANK",
            "sender_account": "1234",
            "receiver_match": True,
        }
    return _mock


# ─── Autouse: cleanup test rows ก่อนแต่ละ test ──────────────────────────
@pytest_asyncio.fixture(autouse=True, loop_scope="session")
async def cleanup_before_each_test():
    """ล้าง test data ก่อนแต่ละ test เพื่อกัน collision จาก previous run."""
    await cleanup_test_data()
    yield
