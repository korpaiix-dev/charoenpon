"""Regression test — Welcome Journey 301-304 partial UNIQUE index.

Bug: ถ้า scheduler รัน send_instant_welcome ซ้ำ → ลูกค้าได้ DM 301 ซ้ำ (และ promo code ซ้ำ).

Fix (Sprint C4): partial UNIQUE index บน (telegram_id, round) WHERE round 301-304
+ ON CONFLICT DO NOTHING ใน _save_log.
"""
import pytest
from shared.database import get_session
from sqlalchemy import text


@pytest.mark.regression
async def test_welcome_301_cannot_insert_duplicate(clean_db):
    """Insert 2 rows สำหรับ (tg, 301) — ครั้งที่ 2 ต้องไม่เพิ่ม."""
    tg_id = 9_900_000_200

    # สร้าง user (FK constraint)
    async with get_session() as s:
        await s.execute(text(
            "INSERT INTO users (telegram_id, first_name, total_spent, loyalty_rank) "
            "VALUES (:tg, :name, 0, 'NONE')"
        ), {"tg": tg_id, "name": "TEST_PYTEST_welcome"})
        user_row = (await s.execute(text("SELECT id FROM users WHERE telegram_id = :tg"), {"tg": tg_id})).fetchone()
        user_id = user_row.id

        # Insert ครั้งที่ 1
        await s.execute(text(
            "INSERT INTO comeback_dm_log "
            "(user_id, telegram_id, discount_pct, promo_code, round, variant) "
            "VALUES (:u, :tg, 25, 'TEST_CODE_A', 301, 'wj_v2') ON CONFLICT DO NOTHING"
        ), {"u": user_id, "tg": tg_id})

        # Insert ครั้งที่ 2 (duplicate)
        await s.execute(text(
            "INSERT INTO comeback_dm_log "
            "(user_id, telegram_id, discount_pct, promo_code, round, variant) "
            "VALUES (:u, :tg, 25, 'TEST_CODE_B', 301, 'wj_v2') ON CONFLICT DO NOTHING"
        ), {"u": user_id, "tg": tg_id})
        await s.commit()

        # ต้องมีแค่ 1 row
        r = await s.execute(text(
            "SELECT COUNT(*) FROM comeback_dm_log WHERE telegram_id = :tg AND round = 301"
        ), {"tg": tg_id})
        count = r.scalar()

    assert count == 1, f"Expected 1 row, got {count} (UNIQUE index broken!)"


@pytest.mark.regression
async def test_comeback_round_1_allows_repeated_sends(clean_db):
    """ลูกค้าหมดอายุครั้งที่ 2 — ส่ง comeback round 1 ใหม่ได้
    (UNIQUE เฉพาะ round 301-304 ไม่ใช่ round 1-3)."""
    tg_id = 9_900_000_201

    async with get_session() as s:
        await s.execute(text(
            "INSERT INTO users (telegram_id, first_name, total_spent, loyalty_rank) "
            "VALUES (:tg, :name, 0, 'NONE')"
        ), {"tg": tg_id, "name": "TEST_PYTEST_comeback"})
        user_id = (await s.execute(text("SELECT id FROM users WHERE telegram_id = :tg"), {"tg": tg_id})).fetchone().id

        # Insert round 1 ครั้งที่ 1
        await s.execute(text(
            "INSERT INTO comeback_dm_log "
            "(user_id, telegram_id, discount_pct, promo_code, round, variant) "
            "VALUES (:u, :tg, 50, 'R1A_FIRST', 1, 'A')"
        ), {"u": user_id, "tg": tg_id})

        # Insert round 1 ครั้งที่ 2 — ต้องสำเร็จ (ลูกค้าหมดอายุครั้งที่ 2)
        await s.execute(text(
            "INSERT INTO comeback_dm_log "
            "(user_id, telegram_id, discount_pct, promo_code, round, variant) "
            "VALUES (:u, :tg, 50, 'R1A_SECOND', 1, 'A')"
        ), {"u": user_id, "tg": tg_id})
        await s.commit()

        r = await s.execute(text(
            "SELECT COUNT(*) FROM comeback_dm_log WHERE telegram_id = :tg AND round = 1"
        ), {"tg": tg_id})
        count = r.scalar()

    assert count == 2, f"Expected 2 rows (comeback allows repeat), got {count}"
