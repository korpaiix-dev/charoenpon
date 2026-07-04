"""Slip2Go Retry Worker.

Background processor that retries Slip2Go verification for slips that
returned "200404: Slip not found" on first try — typically because ITMX
hasn't synced yet (5-15 min lag).

Logic:
- Every 2 minutes, pick all WAITING rows where next_retry_at <= NOW().
- Call Slip2Go again with the cached slip_file_id.
- If success: approve via _approve_payment + DM customer + mark RESOLVED.
- If still "not found": increment attempt, schedule next retry +5 min.
- If max_attempts reached: mark FAILED + escalate to admin (post in admin
  group with the original buttons so admin can manually decide).

Idempotency:
- Multiple concurrent workers shouldn't double-process — uses FOR UPDATE
  SKIP LOCKED.
"""
from __future__ import annotations

import asyncio
import io
import logging
import os
from datetime import datetime

from sqlalchemy import text as sql_text
from telegram import Bot
from telegram.constants import ParseMode

from shared.database import get_session

logger = logging.getLogger(__name__)

SALES_BOT_TOKEN = os.environ.get("SALES_BOT_TOKEN") or os.environ.get("TELEGRAM_BOT_TOKEN", "")
ADMIN_GROUP_CHAT_ID = int(os.environ.get("ADMIN_GROUP_CHAT_ID", "0") or "0")

POLL_INTERVAL_SEC = 120  # check queue every 2 min
RETRY_DELAY_MIN = 5     # 5 min between retries
MAX_ATTEMPTS = 3


async def _claim_pending_rows():
    """Pick rows ready for retry. Uses FOR UPDATE SKIP LOCKED for concurrency."""
    async with get_session() as s:
        r = await s.execute(sql_text("""
            SELECT id, payment_id, user_id, telegram_id, slip_file_id, slip_hash,
                   selected_tier, expected_amount, attempt, max_attempts
            FROM slip2go_retry_queue
            WHERE status = 'WAITING' AND next_retry_at <= NOW()
            ORDER BY id
            LIMIT 20
            FOR UPDATE SKIP LOCKED
        """))
        rows = [dict(row._mapping) for row in r.all()]
        # Mark them as PROCESSING within the same tx so other workers skip
        if rows:
            ids = [r["id"] for r in rows]
            await s.execute(
                sql_text("UPDATE slip2go_retry_queue SET status='PROCESSING' WHERE id = ANY(:ids)"),
                {"ids": ids},
            )
            await s.commit()
        return rows


async def _process_one(row: dict, bot: Bot) -> None:
    """Try Slip2Go again, approve if successful, else schedule retry/escalate."""
    from shared.slip2go import verify_slip_image, Slip2GoError
    from shared.receiver_pool import list_enabled, match_receiver
    from bots.sales_bot.payment_util.approve import _approve_payment
    from sqlalchemy import select
    from shared.models import Payment

    row_id = row["id"]
    payment_id = row["payment_id"]
    attempt = row["attempt"] + 1

    logger.info("Slip2Go retry attempt %d/%d for payment %s", attempt, MAX_ATTEMPTS, payment_id)

    # NEW 2026-06-21: Stop retry ถ้า payment confirmed แล้ว (admin manual approve)
    if payment_id:
        try:
            from sqlalchemy import select
            from shared.models import Payment
            from shared.database import get_session as _gs
            async with _gs() as _s:
                _p = (await _s.execute(select(Payment).where(Payment.id == payment_id))).scalar_one_or_none()
                if _p and str(_p.status.value if hasattr(_p.status, "value") else _p.status).upper() == "CONFIRMED":
                    logger.info("Payment %s already CONFIRMED — skip retry + close queue", payment_id)
                    await _mark_status(row_id, "COMPLETED", attempt - 1, "payment already confirmed by admin")
                    return
        except Exception as _exc:
            logger.warning("retry pre-check failed: %s", _exc)

    # Download the slip image via cached file_id
    try:
        f = await bot.get_file(row["slip_file_id"])
        buf = io.BytesIO()
        await f.download_to_memory(buf)
        buf.seek(0)
        slip_bytes = buf.read()
    except Exception as exc:
        logger.error("Failed to fetch slip file_id for retry %s: %s", row_id, exc)
        await _mark_retry_failed(row_id, attempt, f"file fetch failed: {exc}", bot)
        return

    # Verify with Slip2Go
    try:
        s2g = await verify_slip_image(slip_bytes)
    except Slip2GoError as exc:
        # Still "not found"? — schedule next retry or escalate
        err_msg = str(exc)
        if attempt >= MAX_ATTEMPTS:
            logger.warning("Slip2Go retry exhausted for payment %s — escalating to admin", payment_id)
            await _escalate_to_admin(row, attempt, err_msg, bot)
            await _mark_status(row_id, "FAILED", attempt, err_msg)
        else:
            logger.info("Slip2Go still fail (attempt %d) — scheduling +%d min", attempt, RETRY_DELAY_MIN)
            await _reschedule(row_id, attempt, err_msg)
        return
    except Exception as exc:
        logger.error("Slip2Go unexpected error for retry %s: %s", row_id, exc)
        await _reschedule(row_id, attempt, f"unexpected: {exc}")
        return

    # Check receiver match (must be one of our enabled accounts)
    accounts = await list_enabled()
    matched = match_receiver(s2g, accounts)
    if not matched:
        logger.warning("Slip2Go OK but receiver not in our pool for payment %s — escalate", payment_id)
        await _escalate_to_admin(row, attempt, "wrong receiver", bot)
        await _mark_status(row_id, "FAILED", attempt, "wrong receiver")
        return

    # Amount must match expected
    # FIX 2026-06-29 (#439): Re-calculate effective_price จาก current promo state
    # เดิม: ใช้ row["expected_amount"] static ตอน enqueue → ไม่ apply Day-0 promo
    # → ลูกค้าจ่ายราคาโปรลด → mismatch → reject ทุกใบ
    # → admin ต้อง manual approve ทุกใบ (กระทบทั้งระบบ)
    s2g_amount = float(s2g.get("amount", 0))
    queue_expected = float(row["expected_amount"])
    expected = queue_expected  # fallback

    # FIX 2026-07-04: record the ACTUAL amount read from the slip (not the enqueue estimate),
    # so both auto-approve and admin-fallback record the real paid amount (discount over-count fix).
    try:
        async with get_session() as _amt_s:
            await _amt_s.execute(sql_text("UPDATE payments SET amount = :amt WHERE id = :pid"),
                                 {"amt": s2g_amount, "pid": payment_id})
            await _amt_s.commit()
    except Exception as _amt_e:
        logger.warning("update payment %s amount->actual %s failed: %s", payment_id, s2g_amount, _amt_e)

    # Try recalc using selected_tier + current Day-0 promos
    selected_tier = (row.get("selected_tier") or "").replace("TIER_", "").strip()
    if selected_tier:
        try:
            from shared.pricing import TIER_PRICES
            from shared.promotion_service import list_active_promotions, calculate_price
            base = float(TIER_PRICES.get(selected_tier, queue_expected))
            recalc = base
            promos = await list_active_promotions()
            tier_key = f"TIER_{selected_tier}"
            best_savings = 0
            for p in promos:
                codes = p.get("package_codes") or []
                if isinstance(codes, str):
                    import json as _j
                    try: codes = _j.loads(codes)
                    except Exception: codes = []
                if tier_key in codes:
                    calc = calculate_price(p, tier_key, base)
                    if calc.get("applied") and calc.get("savings", 0) > best_savings:
                        recalc = int(calc["discounted"])
                        best_savings = calc["savings"]
            if recalc != queue_expected:
                logger.info(
                    "Slip2Go expected recalc: queue=%s base=%s recalc=%s (tier=%s, promo savings=%s)",
                    queue_expected, base, recalc, selected_tier, best_savings,
                )
                expected = float(recalc)
        except Exception as exc:
            logger.warning("expected recalc failed (non-fatal): %s — using queue value", exc)

    # FIX 2026-07-04: accept ANY valid price for this customer+tier — base, campaign promo,
    # OR per-customer RETENTION discount (comeback_dm_log). Before, only campaign promos were
    # considered -> retention buyers (e.g. VIP -15% = 255) were falsely flagged "amount mismatch".
    acceptable = {expected, queue_expected}
    _ret_used = False
    if selected_tier:
        try:
            from shared.pricing import TIER_PRICES as _TP2
            _base2 = float(_TP2.get(selected_tier, queue_expected))
            acceptable.add(_base2)
            _tg2 = int(row.get("telegram_id") or 0)
            if _tg2:
                async with get_session() as _ret_s:
                    _ret_r = await _ret_s.execute(sql_text(
                        "SELECT discount_pct FROM comeback_dm_log WHERE telegram_id = :tg "
                        "AND purchased = FALSE AND sent_at > NOW() - interval '48 hours' "
                        "ORDER BY sent_at DESC LIMIT 1"), {"tg": _tg2})
                    _pct2 = _ret_r.scalar()
                if _pct2:
                    _ret_price = float(round(_base2 * (100 - float(_pct2)) / 100))
                    acceptable.add(_ret_price)
                    if abs(s2g_amount - _ret_price) <= 1.0:
                        _ret_used = True
        except Exception as _acc_e:
            logger.warning("acceptable-amount build failed (non-fatal): %s", _acc_e)

    # ±1 baht tolerance for rounding edge cases
    if not any(abs(s2g_amount - a) <= 1.0 for a in acceptable):
        logger.warning("Slip2Go OK but amount mismatch (s2g=%s acceptable=%s queue=%s) for payment %s — escalate",
                       s2g_amount, sorted(acceptable), queue_expected, payment_id)
        await _escalate_to_admin(row, attempt, f"amount mismatch s2g={s2g_amount} expected={expected}", bot)
        await _mark_status(row_id, "FAILED", attempt, "amount mismatch")
        return

    # Customer used their retention discount -> mark it purchased (prevent reuse)
    if _ret_used:
        try:
            async with get_session() as _mk_s:
                await _mk_s.execute(sql_text(
                    "UPDATE comeback_dm_log SET purchased = TRUE, responded = TRUE "
                    "WHERE telegram_id = :tg AND purchased = FALSE "
                    "AND sent_at > NOW() - interval '48 hours'"), {"tg": int(row.get("telegram_id") or 0)})
                await _mk_s.commit()
        except Exception as _mk_e:
            logger.warning("mark retention purchased failed: %s", _mk_e)

    # All checks pass — APPROVE
    async with get_session() as s:
        r = await s.execute(select(Payment).where(Payment.id == payment_id))
        payment = r.scalar_one_or_none()
    if not payment:
        await _mark_status(row_id, "FAILED", attempt, "payment not found")
        return

    try:
        links = await _approve_payment(payment, row["telegram_id"], bot, source="retry_worker")
        await _send_customer_dm(bot, row["telegram_id"], links)
        await _mark_status(row_id, "RESOLVED", attempt, "approved via auto-retry")
        logger.info("AUTO-APPROVED payment %s via retry (attempt %d)", payment_id, attempt)
    except Exception as exc:
        logger.error("Approve failed in retry for payment %s: %s", payment_id, exc)
        await _escalate_to_admin(row, attempt, f"approve failed: {exc}", bot)
        await _mark_status(row_id, "FAILED", attempt, f"approve failed: {exc}")


async def _send_customer_dm(bot: Bot, telegram_id: int, links: list[str]):
    msg = (
        "🎉 <b>ระบบยืนยันการชำระเงินสำเร็จแล้ว!</b>\n\n"
        "📂 ลิงก์เข้ากลุ่ม VIP ของคุณ:\n\n"
        + "\n".join(links) +
        "\n\n🎁 ขอให้สนุกค่ะ"
    )
    try:
        await bot.send_message(chat_id=telegram_id, text=msg, parse_mode=ParseMode.HTML,
                               disable_web_page_preview=True)
    except Exception as exc:
        logger.warning("Customer DM failed: %s", exc)


async def _reschedule(row_id: int, attempt: int, err: str):
    async with get_session() as s:
        await s.execute(sql_text("""
            UPDATE slip2go_retry_queue
            SET attempt = :a, last_error = :e, status = 'WAITING',
                next_retry_at = NOW() + INTERVAL '5 minutes'
            WHERE id = :i
        """), {"a": attempt, "e": err[:500], "i": row_id})
        await s.commit()


async def _mark_status(row_id: int, status: str, attempt: int, err: str):
    # FIX 2026-06-21: asyncpg "inconsistent types" — :s ใช้ทั้ง SET + CASE WHEN ทำให้ infer type ไม่ได้.
    # แก้โดยตัดสินใจ resolved_at ใน Python แล้วใช้ 2 query แทน.
    is_terminal = status in ("RESOLVED", "FAILED", "COMPLETED")
    async with get_session() as s:
        if is_terminal:
            await s.execute(sql_text("""
                UPDATE slip2go_retry_queue
                SET status = :s, attempt = :a, last_error = :e, resolved_at = NOW()
                WHERE id = :i
            """), {"s": status, "a": attempt, "e": err[:500], "i": row_id})
        else:
            await s.execute(sql_text("""
                UPDATE slip2go_retry_queue
                SET status = :s, attempt = :a, last_error = :e
                WHERE id = :i
            """), {"s": status, "a": attempt, "e": err[:500], "i": row_id})
        await s.commit()


async def _mark_retry_failed(row_id: int, attempt: int, err: str, bot: Bot):
    await _mark_status(row_id, "FAILED", attempt, err)


async def _escalate_to_admin(row: dict, attempt: int, err: str, bot: Bot):
    """Post a fallback admin alert so the original buttons logic can be triggered."""
    if not ADMIN_GROUP_CHAT_ID:
        logger.warning("ADMIN_GROUP_CHAT_ID not set — cannot escalate")
        return
    # FIX 2026-07-04: show the ACTUAL paid amount (parsed from err "s2g=...") instead of only
    # the expected price, plus a hint when the customer paid less (likely a discount/promo).
    import re as _re_esc
    _actual = None
    _m_esc = _re_esc.search(r"s2g=([0-9.]+)", err or "")
    if _m_esc:
        try: _actual = float(_m_esc.group(1))
        except Exception: _actual = None
    try:
        _exp_val = float(row.get("expected_amount") or 0)
    except Exception:
        _exp_val = 0.0
    if _actual is not None:
        _amt_line = f"💰 ยอดจ่ายจริง: <b>฿{_actual:,.0f}</b>  (ราคาเต็ม ฿{_exp_val:,.0f})\n"
        if _actual < _exp_val - 1:
            _amt_line += "   ↳ <i>จ่ายน้อยกว่าราคาเต็ม — อาจใช้ส่วนลด/โปร ตรวจก่อนอนุมัติ</i>\n"
        elif _actual > _exp_val + 1:
            _amt_line += "   ↳ <i>จ่ายมากกว่าราคา — ตรวจสอบ</i>\n"
    else:
        _amt_line = f"💰 ยอดที่คาด: ฿{row['expected_amount']}\n"
    msg = (
        "🟡 <b>สลิปต้องตรวจสอบด้วยมือ</b>\n"
        "<i>ระบบตรวจอัตโนมัติไม่ผ่าน — ตรวจแล้วกดอนุมัติ/ปฏิเสธ</i>\n"
        "━━━━━━━━━━━━━━━━\n"
        f"💳 จ่าย: <code>#{row['payment_id']}</code>\n"
        f"👤 ลูกค้า: <code>{row['telegram_id']}</code>\n"
        f"{_amt_line}"
        f"🔁 ลองแล้ว: {attempt}/{MAX_ATTEMPTS} ครั้ง\n"
        f"❌ เหตุ: <code>{err[:160]}</code>\n"
        f"\n👇 กดปุ่มด้านล่างเพื่ออนุมัติ/ปฏิเสธ (หรือดูที่ /pending)"
    )
    # FIX 2026-07-01: escalate via ADMIN bot. The retry worker runs inside the Guardian
    # bot, which is NOT a member of the admin group ("Chat not found") -> escalation was
    # failing silently and stuck slips got no admin notification. Also attach the same
    # approve/reject buttons so admin can act right from the alert.
    import os as _os_esc
    from telegram import Bot as _Bot_esc
    _kb_esc = None
    try:
        from shared.slip_review import build_admin_review_buttons as _bld_esc
        _kb_esc = _bld_esc(row["payment_id"], row["telegram_id"])
    except Exception as _kbe:
        logger.warning("escalate buttons build failed: %s", _kbe)
    _atok = _os_esc.environ.get("ADMIN_BOT_TOKEN", "")
    _sender = _Bot_esc(token=_atok) if _atok else bot
    try:
        if _atok:
            await _sender.initialize()
        await _sender.send_message(chat_id=ADMIN_GROUP_CHAT_ID, text=msg,
                                   parse_mode=ParseMode.HTML, reply_markup=_kb_esc)
        logger.info("Escalation sent to admin group for payment %s", row["payment_id"])
    except Exception as exc:
        logger.warning("Admin escalation send failed: %s", exc)
    finally:
        if _atok:
            try:
                await _sender.shutdown()
            except Exception:
                pass


async def _sweep_stale_processing():
    """FIX 2026-06-21: Sweep stale PROCESSING rows — 2 actions:

    1. ถ้า attempt >= max_attempts → mark FAILED + escalate (ไม่ retry ต่อ)
    2. ถ้า next_retry_at < NOW() - 15 min (worker crashed mid-process) → reset เป็น WAITING

    Bug เดิม: ใช้ enqueued_at (static) → พอเกิน timeout ก็ reset ทุก poll cycle = infinite loop.
    Fix: ใช้ next_retry_at (dynamic) ที่อัปเดตทุก reschedule/claim.
    """
    async with get_session() as s:
        # 1. Mark FAILED rows ที่ attempt >= max_attempts (กัน retry ต่อ)
        r1 = await s.execute(sql_text("""
            UPDATE slip2go_retry_queue
            SET status = 'FAILED', resolved_at = NOW(),
                last_error = COALESCE(last_error, '') || ' [auto-failed: max attempts reached]'
            WHERE status = 'PROCESSING'
              AND attempt >= max_attempts
            RETURNING id
        """))
        failed_rows = list(r1.fetchall())

        # 2. Reset PROCESSING rows ที่ค้าง > 15 min (worker crash recovery)
        r2 = await s.execute(sql_text("""
            UPDATE slip2go_retry_queue
            SET status = 'WAITING', next_retry_at = NOW() + interval '5 minutes'
            WHERE status = 'PROCESSING'
              AND attempt < max_attempts
              AND next_retry_at < NOW() - interval '15 minutes'
            RETURNING id
        """))
        reset_rows = list(r2.fetchall())
        await s.commit()

        if failed_rows:
            logger.warning("Auto-failed %d retry rows (max attempts)", len(failed_rows))
        if reset_rows:
            logger.warning("Swept %d stale PROCESSING rows (worker crash recovery)", len(reset_rows))


async def _cleanup_old_resolved():
    """FIX 2026-06-21: ลบ retry queue rows ที่ resolved/failed > 7 วัน
    (กัน table โตเรื่อยๆ)."""
    async with get_session() as s:
        r = await s.execute(sql_text("""
            DELETE FROM slip2go_retry_queue
            WHERE status IN ('RESOLVED','COMPLETED','FAILED')
              AND resolved_at < NOW() - INTERVAL '7 days'
            RETURNING id
        """))
        rows = list(r.fetchall())
        await s.commit()
        if rows:
            logger.info("Cleaned up %d old retry queue rows (>7d)", len(rows))


async def worker_loop():
    """Main worker loop — called by guardian-bot scheduler every 2 min."""
    bot = Bot(SALES_BOT_TOKEN) if SALES_BOT_TOKEN else None
    if not bot:
        logger.error("SALES_BOT_TOKEN not set — slip2go retry worker disabled")
        return
    try:
        # FIX 2026-06-16: sweep stale PROCESSING (worker crash recovery)
        try:
            await _sweep_stale_processing()
        except Exception as _sw:
            logger.error("Stale sweep failed: %s", _sw)

        # FIX 2026-06-21: cleanup old resolved rows (ทุก poll = เร็ว เพราะ index)
        try:
            await _cleanup_old_resolved()
        except Exception as _cl:
            logger.error("Cleanup failed: %s", _cl)

        rows = await _claim_pending_rows()
        if rows:
            logger.info("Slip2Go retry: processing %d pending rows", len(rows))
            for row in rows:
                try:
                    await _process_one(row, bot)
                except Exception as exc:
                    logger.error("Retry row %s failed: %s", row.get("id"), exc, exc_info=True)
    finally:
        # Bot will be GC'd
        pass


async def enqueue_slip_for_retry(
    payment_id: int,
    user_id: int,
    telegram_id: int,
    slip_file_id: str,
    slip_hash: str,
    selected_tier: str,
    expected_amount: float,
) -> int:
    """Add a slip to the retry queue. Returns row id."""
    async with get_session() as s:
        r = await s.execute(sql_text("""
            INSERT INTO slip2go_retry_queue
              (payment_id, user_id, telegram_id, slip_file_id, slip_hash,
               selected_tier, expected_amount, next_retry_at)
            VALUES (:p, :u, :t, :f, :h, :tier, :amt, NOW() + INTERVAL '5 minutes')
            RETURNING id
        """), {
            "p": payment_id, "u": user_id, "t": telegram_id, "f": slip_file_id,
            "h": slip_hash, "tier": selected_tier, "amt": expected_amount,
        })
        new_id = r.scalar_one()
        await s.commit()
    logger.info("Enqueued slip for retry: queue_id=%s payment_id=%s", new_id, payment_id)
    return new_id


__all__ = ["worker_loop", "enqueue_slip_for_retry"]
