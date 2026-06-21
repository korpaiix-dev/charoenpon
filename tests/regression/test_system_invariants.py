"""System-wide invariant tests — รัน hourly + ก่อน deploy.

ตรวจ data integrity ของระบบ — ทุก check ผ่าน = ระบบสะอาด.
"""
import pytest
from shared.database import get_session
from sqlalchemy import text


@pytest.mark.critical
async def test_no_active_sub_past_expiry():
    """ไม่มี subscription ACTIVE ที่ end_date < NOW (cron expiry ต้องรัน)."""
    async with get_session() as s:
        r = await s.execute(text(
            "SELECT COUNT(*) FROM subscriptions WHERE status::text = 'ACTIVE' AND end_date < NOW()"
        ))
        n = r.scalar()
    assert n == 0, f"Found {n} ACTIVE subs past expiry (kick_expired not running!)"


@pytest.mark.critical
async def test_no_duplicate_subs_per_payment():
    """ไม่มี payment_id ที่มี sub มากกว่า 1 อัน (Naca-pattern)."""
    async with get_session() as s:
        r = await s.execute(text("""
            SELECT COUNT(*) FROM (
                SELECT payment_id FROM subscriptions
                WHERE payment_id IS NOT NULL
                GROUP BY payment_id HAVING COUNT(*) > 1
            ) t
        """))
        n = r.scalar()
    assert n == 0, f"Found {n} payments with duplicate subs (Naca-like bug!)"


@pytest.mark.critical
async def test_total_spent_matches_sum_of_payments():
    """users.total_spent ต้องตรง SUM(payments WHERE status=CONFIRMED)."""
    async with get_session() as s:
        r = await s.execute(text("""
            SELECT COUNT(*) FROM (
                SELECT u.id, u.total_spent, COALESCE(SUM(p.amount), 0) AS actual
                FROM users u
                LEFT JOIN payments p ON p.user_id = u.id AND p.status::text = 'CONFIRMED'
                WHERE u.total_spent > 0
                GROUP BY u.id, u.total_spent
                HAVING u.total_spent != COALESCE(SUM(p.amount), 0)
            ) t
        """))
        n = r.scalar()
    assert n == 0, f"Found {n} users with total_spent mismatch (trigger double-fire?)"


@pytest.mark.critical
async def test_loyalty_rank_matches_rules():
    """loyalty_rank ต้องตรงเกณฑ์ V2 (DIAMOND/SILVER/BRONZE/NONE)."""
    async with get_session() as s:
        r = await s.execute(text("""
            SELECT COUNT(*) FROM users
            WHERE (
                (total_spent::int >= 4000 AND loyalty_rank != 'DIAMOND') OR
                (total_spent::int < 4000 AND total_spent::int >= 1000 AND loyalty_rank NOT IN ('SILVER', 'DIAMOND'))
            )
        """))
        n = r.scalar()
    assert n == 0, f"Found {n} users with wrong loyalty_rank"


@pytest.mark.critical
async def test_no_stuck_retry_queue():
    """ไม่มี retry_queue row ค้าง PROCESSING > 30 นาที."""
    async with get_session() as s:
        r = await s.execute(text("""
            SELECT COUNT(*) FROM slip2go_retry_queue
            WHERE status IN ('WAITING', 'PROCESSING')
              AND next_retry_at < NOW() - INTERVAL '30 minutes'
        """))
        n = r.scalar()
    assert n == 0, f"Found {n} retry queue rows stuck (Naca-like loop!)"


@pytest.mark.critical
async def test_python_enum_matches_db_enum_groupslug():
    """GroupSlug Python enum ต้องครอบ DB enum values ทั้งหมด (กัน FREE19-bug)."""
    from shared.models import GroupSlug

    async with get_session() as s:
        r = await s.execute(text("SELECT unnest(enum_range(NULL::groupslug))::text AS v"))
        db_values = {row.v for row in r.fetchall()}

    py_values = {g.value for g in GroupSlug}
    missing_in_py = db_values - py_values

    assert not missing_in_py, \
        f"Python GroupSlug missing DB values: {missing_in_py} (FREE19-bug regression!)"


@pytest.mark.critical
async def test_all_payment_confirmed_have_admin_log():
    """ทุก CONFIRMED payment ต้องมี admin_log entry (audit trail)."""
    async with get_session() as s:
        r = await s.execute(text("""
            SELECT COUNT(*) FROM payments p
            WHERE p.status::text = 'CONFIRMED'
              AND NOT EXISTS (
                  SELECT 1 FROM admin_logs al
                  WHERE al.target_type = 'payment' AND al.target_id = p.id
                    AND al.action::text LIKE 'payment_approved%'
              )
        """))
        n = r.scalar()
    assert n == 0, f"Found {n} CONFIRMED payments without admin_log (audit trail missing!)"
