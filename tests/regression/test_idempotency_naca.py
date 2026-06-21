"""Regression test — เคส Naca 19 subs ซ้ำ (2026-06-21).

Bug: retry worker เรียก apply_payment_approval() ซ้ำ ทำให้ subscription ถูกสร้างใหม่
ทุกครั้ง — Naca จ่าย ฿2499 ครั้งเดียวมี sub 19 อัน.

Fix: STEP 0 ใน apply_payment_approval — ถ้า payment.status = CONFIRMED + มี sub
ที่ payment_id ตรง → return existing (idempotent skip).

Test นี้ป้องกัน regression ถ้ามีคนลบ STEP 0 ในอนาคต.
"""
import pytest
from decimal import Decimal


@pytest.mark.regression
@pytest.mark.critical
async def test_apply_payment_approval_is_idempotent_when_payment_confirmed(test_user, mock_telegram_bot):
    """เรียก apply_payment_approval() 5 ครั้งต่อ payment เดียวกัน → sub ต้องมี 1 อัน."""
    from shared.payment_approval import apply_payment_approval, ApprovalInput, ApprovalSource
    from shared.models import PackageTier
    from shared.database import get_session
    from sqlalchemy import text

    user_id, tg_id = test_user

    # Call ครั้งที่ 1 — สร้าง sub ใหม่
    inp1 = ApprovalInput(
        user_id=user_id,
        telegram_id=tg_id,
        source=ApprovalSource.SLIP2GO_AUTO,
        amount_paid=Decimal("300"),
        explicit_tier=PackageTier.TIER_300,
        slip_trans_ref="TEST_NACA_FIRST_CALL",
        slip_hash="test_naca_hash_1",
    )
    result1 = await apply_payment_approval(inp1)
    assert result1.success, f"First call failed: {result1.error}"
    assert result1.payment_id is not None
    assert result1.subscription_id is not None
    assert not result1.idempotent_skip, "First call should NOT be idempotent skip"

    payment_id = result1.payment_id
    first_sub_id = result1.subscription_id

    # Call ครั้งที่ 2-5 ด้วย payment_id เดิม — ต้อง idempotent skip
    for i in range(2, 6):
        inp_retry = ApprovalInput(
            user_id=user_id,
            telegram_id=tg_id,
            source=ApprovalSource.RETRY_WORKER,
            amount_paid=Decimal("300"),
            explicit_tier=PackageTier.TIER_300,
            slip_trans_ref="TEST_NACA_FIRST_CALL",
            slip_hash="test_naca_hash_1",
            payment_id=payment_id,  # ↑ key — pass existing payment_id
        )
        result_retry = await apply_payment_approval(inp_retry)
        assert result_retry.success, f"Retry call #{i} failed: {result_retry.error}"
        assert result_retry.idempotent_skip, f"Call #{i} should be idempotent_skip"
        assert result_retry.subscription_id == first_sub_id, \
            f"Call #{i} created different sub! (was {first_sub_id}, got {result_retry.subscription_id})"

    # ตรวจ DB จริง — sub ต้องมี 1 อันต่อ payment เดียวกัน
    async with get_session() as s:
        r = await s.execute(text(
            "SELECT COUNT(*) FROM subscriptions WHERE payment_id = :pid"
        ), {"pid": payment_id})
        sub_count = r.scalar()
        assert sub_count == 1, f"Expected 1 sub, got {sub_count} (regression to Naca bug!)"


@pytest.mark.regression
async def test_no_duplicate_subs_across_existing_payments(clean_db):
    """ตรวจ system state — ไม่มี payment_id ที่มี sub มากกว่า 1 อัน."""
    from shared.database import get_session
    from sqlalchemy import text

    async with get_session() as s:
        r = await s.execute(text("""
            SELECT payment_id, COUNT(*) AS dup
            FROM subscriptions
            WHERE payment_id IS NOT NULL
            GROUP BY payment_id HAVING COUNT(*) > 1
        """))
        duplicates = list(r.fetchall())

    assert len(duplicates) == 0, \
        f"Found {len(duplicates)} payments with duplicate subs: {[(d.payment_id, d.dup) for d in duplicates[:5]]}"
