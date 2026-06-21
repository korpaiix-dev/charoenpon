"""Regression test — promote_user_to_rank advisory lock + atomic check-and-set.

Bug: ถ้า scheduler รัน promote คนเดียวกัน 2 ครั้งซ้อน — ลูกค้าได้ rewards 2 ครั้ง
(2x gacha credits, 2x sub days).

Fix (Sprint C5): pg_advisory_xact_lock + atomic UPDATE WHERE prev_rank.
"""
import asyncio
import pytest
from decimal import Decimal


@pytest.mark.regression
@pytest.mark.critical
async def test_concurrent_promote_to_silver_awards_rewards_once(clean_db):
    """รัน promote_user_to_rank('SILVER') 5 ครั้ง concurrent → ได้ rewards ครั้งเดียว."""
    from shared.loyalty_rank import promote_user_to_rank
    from shared.database import get_session
    from sqlalchemy import text

    # สร้าง test user ที่ qualify เป็น SILVER (total_spent >= 1000)
    tg_id = 9_900_000_100
    async with get_session() as s:
        r = await s.execute(text(
            "INSERT INTO users (telegram_id, first_name, total_spent, loyalty_rank, loyalty_first_paid_at) "
            "VALUES (:tg, :name, 1500, 'NONE', NOW() - INTERVAL '95 days') RETURNING id"
        ), {"tg": tg_id, "name": "TEST_PYTEST_silver_candidate"})
        user_id = r.scalar()
        await s.commit()

    # Run 5 concurrent promotes
    tasks = [promote_user_to_rank(user_id, "SILVER", silent=True) for _ in range(5)]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    # ทั้ง 5 ต้อง return (ไม่ exception)
    for i, r in enumerate(results):
        if isinstance(r, Exception):
            raise AssertionError(f"Promote #{i} raised exception: {r}")

    # นับคนที่ "ทำจริง" (ไม่ skip)
    actual_promotions = [r for r in results if not r.get("skip", False)]
    skipped = [r for r in results if r.get("skip", False)]

    assert len(actual_promotions) == 1, \
        f"Expected exactly 1 actual promotion, got {len(actual_promotions)}. " \
        f"Skipped: {len(skipped)} reasons: {[r.get('reason') for r in skipped]}"

    # ตรวจ DB — rewards ต้องมี 1 ชุด ไม่ใช่ 5
    async with get_session() as s:
        # gachapon_credits — ต้องมี 5 credits (ไม่ใช่ 25)
        r = await s.execute(text(
            "SELECT credits FROM gachapon_credits WHERE telegram_id = :tg"
        ), {"tg": tg_id})
        credits = r.scalar() or 0

        # subscriptions TIER_1299 — ต้องมี 1 อัน
        r = await s.execute(text(
            "SELECT COUNT(*) FROM subscriptions s JOIN packages p ON p.id = s.package_id "
            "WHERE s.user_id = :uid AND p.tier::text = 'TIER_1299'"
        ), {"uid": user_id})
        sub_count = r.scalar()

        # users.loyalty_rank = SILVER
        r = await s.execute(text(
            "SELECT loyalty_rank FROM users WHERE id = :uid"
        ), {"uid": user_id})
        final_rank = r.scalar()

    try:
        assert credits == 5, f"Expected 5 gacha credits, got {credits} (race condition!)"
        assert sub_count == 1, f"Expected 1 TIER_1299 sub, got {sub_count} (duplicate award!)"
        assert final_rank == "SILVER", f"Expected SILVER rank, got {final_rank}"
    finally:
        # Cleanup test artifacts (กัน mismatch ตอน invariants test รันหลัง)
        async with get_session() as s:
            await s.execute(text("DELETE FROM admin_logs WHERE target_id = :uid AND target_type = 'user'"), {"uid": user_id})
            await s.execute(text("DELETE FROM gachapon_credits WHERE telegram_id = :tg"), {"tg": tg_id})
            await s.execute(text("DELETE FROM subscriptions WHERE user_id = :uid"), {"uid": user_id})
            await s.execute(text("DELETE FROM users WHERE id = :uid"), {"uid": user_id})
            await s.commit()
