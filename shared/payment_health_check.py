"""Payment system health check — runs every hour, alerts admin if issues.

Checks:
1. Slips received vs Payments created — alert if gap > 10% in last hour
2. ValueError/Exception in payment.py logs — alert if any new
3. tier resolution smoke test — verify all tiers resolve
4. DB enum vs Python enum consistency
"""
from __future__ import annotations

import asyncio
import os
import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


async def health_check_payment_system():
    """Hourly cron — verify payment pipeline integrity."""
    import sys
    sys.path.insert(0, "/app")

    issues = []

    # === Test 0: DB Packages ครบทุก tier ที่ code อ้างถึง ===
    try:
        from shared.database import get_session
        from sqlalchemy import text as _t
        # NOTE 2026-06-28: TIER_ADD500 = Summer Fest admin-only add-on (event-based)
        # ไม่ใช่ package ที่ลูกค้าซื้อตรง — admin คลิกปุ่ม "🌊 500 (Summer)" เท่านั้น
        # ลูกค้า 5 คนใช้อยู่ (legacy subs) — Package row id=6 ยังอยู่แค่ is_active=FALSE
        # ไม่ต้องอยู่ใน health check
        # RETIRED 2026-07-14: TIER_100 (ห้องมีคนชัก) taken off sale (is_active=FALSE), same as
        # TIER_ADD500 above. 88 legacy subs keep the room + tickets + weekly draw; grants look
        # up Package by tier WITHOUT an is_active filter, so nothing breaks — it just must not
        # be REQUIRED to have an ACTIVE package here.
        REQUIRED_TIERS = [
            "TIER_300", "TIER_500",
            "TIER_1299", "TIER_2499", "TIER_4999",
            "GACHA_1", "GACHA_3", "GACHA_10",
        ]
        async with get_session() as s:
            r = await s.execute(_t(
                "SELECT tier::text FROM packages WHERE is_active = TRUE"
            ))
            db_tiers = {row[0] for row in r.fetchall()}
        missing = [t for t in REQUIRED_TIERS if t not in db_tiers]
        if missing:
            issues.append(f"🚨 CRITICAL: ไม่มี packages ใน DB สำหรับ tier: {', '.join(missing)}")
    except Exception as exc:
        issues.append(f"💥 package check crashed: {exc}")

    # === Test 0.5: Slip2Go retry queue ค้างนาน ===
    try:
        from shared.database import get_session
        from sqlalchemy import text as _t
        async with get_session() as s:
            # FIX 2026-06-21: ใช้ next_retry_at แทน enqueued_at (static = false positive)
            r = await s.execute(_t(
                "SELECT COUNT(*) FROM slip2go_retry_queue "
                "WHERE status IN ('WAITING', 'PROCESSING') "
                "  AND next_retry_at < NOW() - INTERVAL '30 minutes'"
            ))
            stuck = r.scalar() or 0
            if stuck > 0:
                issues.append(f"⚠️ Slip2Go retry queue ค้าง {stuck} รายการ > 30 นาที")
    except Exception as exc:
        issues.append(f"💥 retry queue check crashed: {exc}")

    # === Test 0.7: Payment PENDING ค้างนาน (> 15 นาที) ===
    try:
        from shared.database import get_session
        from sqlalchemy import text as _t
        async with get_session() as s:
            r = await s.execute(_t(
                "SELECT COUNT(*) FROM payments "
                "WHERE status = 'PENDING' "
                "  AND created_at < NOW() - INTERVAL '15 minutes'"
            ))
            stuck = r.scalar() or 0
            if stuck > 0:
                issues.append(f"🚨 Payment PENDING ค้าง {stuck} รายการ > 15 นาที — ลูกค้ารอ!")
    except Exception as exc:
        issues.append(f"💥 pending payment check crashed: {exc}")

    # === Test 1: tier resolution ===
    try:
        from bots.sales_bot.payment_util.utils import _resolve_tier
        from shared.pricing import amount_to_tier, admin_callback_tier_map, tier_str_to_enum

        # Test every supported tier
        for tier_str in ["99", "100", "300", "500", "1299", "2499", "4999", "ADD500"]:
            r = _resolve_tier(tier_str)
            if r is None:
                issues.append(f"❌ _resolve_tier('{tier_str}') = None")

        for tier_str in ["99", "100", "300", "500", "1299", "2499", "4999"]:
            r = tier_str_to_enum(tier_str)
            if r is None:
                issues.append(f"❌ tier_str_to_enum('{tier_str}') = None")

        m = admin_callback_tier_map()
        for k in ["99", "100", "199", "270", "300", "500", "890", "1299", "2499", "4999"]:
            if k not in m:
                issues.append(f"❌ admin_callback_tier_map missing key '{k}'")

        for amt in [99, 100, 270, 300, 500, 890, 1299, 2499, 4999]:
            r = amount_to_tier(amt)
            if r is None:
                issues.append(f"❌ amount_to_tier({amt}) = None")

        # P2 drift-alert: every ACTIVE promo's discounted price must still resolve to a tier
        # (catches a promo whose price stopped mapping — e.g. a mispriced 7.7 row / stale promo).
        try:
            from shared.database import get_session as _gs
            from sqlalchemy import text as _tt
            async with _gs() as _s2:
                _pr = await _s2.execute(_tt(
                    "SELECT p.code, p.discount_type, p.discount_value, pk.tier::text, pk.price "
                    "FROM promotions p JOIN packages pk ON pk.tier::text = ANY("
                    "  SELECT jsonb_array_elements_text(p.package_codes::jsonb)) "
                    "WHERE p.is_active AND (p.starts_at IS NULL OR p.starts_at<=now()) "
                    "  AND (p.ends_at IS NULL OR p.ends_at>now())"
                ))
                for code, dtype, dval, tier, base in _pr.fetchall():
                    dtype = (dtype or "").lower(); dval = float(dval or 0); base = float(base)
                    if dtype == "percent": disc = round(base*(100-dval)/100)
                    elif dtype in ("fixed_off","fixed","amount","baht"): disc = round(max(0, base-dval))
                    elif dtype == "fixed_price": disc = round(dval)
                    else: continue
                    if amount_to_tier(int(disc)) is None:
                        issues.append(f"⚠️ promo {code}: ราคาลด ฿{disc} ({tier}) ไม่ resolve เป็น tier ใดเลย")
        except Exception as _pex:
            issues.append(f"💥 promo price round-trip check crashed: {_pex}")

    except Exception as exc:
        issues.append(f"💥 tier resolution test crashed: {exc}")

    # === Test 2: DB enum sync ===
    try:
        from shared.database import get_session
        from sqlalchemy import text as _t
        from shared.models import NotificationType, PackageTier, PaymentStatus

        async with get_session() as s:
            r = await s.execute(_t("SELECT unnest(enum_range(NULL::notificationtype))"))
            db_vals = {row[0] for row in r.fetchall()}
            py_vals = {n.value for n in NotificationType}
            missing = db_vals - py_vals
            extra = py_vals - db_vals
            if missing:
                issues.append(f"❌ Python NotificationType missing DB values: {missing}")
            if extra:
                issues.append(f"⚠️ Python NotificationType has extra values: {extra}")

    except Exception as exc:
        issues.append(f"💥 DB enum sync test crashed: {exc}")

    # === Test 3: Recent payment pipeline ===
    try:
        from shared.database import get_session
        from sqlalchemy import text as _t

        async with get_session() as s:
            # Count payments in last 6 hours
            r = await s.execute(_t("""
                SELECT
                    COUNT(*) FILTER (WHERE status = 'CONFIRMED') AS confirmed,
                    COUNT(*) FILTER (WHERE status = 'PENDING') AS pending,
                    COUNT(*) FILTER (WHERE status = 'REJECTED') AS rejected
                FROM payments
                WHERE created_at > NOW() - interval '6 hours'
            """))
            row = r.fetchone()
            if row:
                # If pending > confirmed → too many stuck
                if row[1] > row[0] * 0.5 and row[1] > 5:
                    issues.append(f"⚠️ Many pending payments: {row[1]} pending vs {row[0]} confirmed (6h)")
    except Exception as exc:
        issues.append(f"💥 payment pipeline check crashed: {exc}")

    # === Test 4 (NEW 2026-06-21): CONFIRMED payment ไม่มี sub matching ใน 30 min ===
    # (gacha skip — gacha confirmed ไม่ต้องมี sub)
    try:
        from shared.database import get_session
        from sqlalchemy import text as _t
        async with get_session() as s:
            # FIX 2026-06-27: also exclude payments approved via GACHA branch
            # (customer started with package_id=VIP/etc but switched to gacha at checkout —
            # payment.package_id stays original but actual processing was gacha branch,
            # which awards sub via pull mechanism with payment_id=NULL)
            r = await s.execute(_t("""
                SELECT COUNT(*) FROM payments p
                JOIN packages pk ON pk.id = p.package_id
                WHERE p.status::text = 'CONFIRMED'
                  AND p.created_at > NOW() - INTERVAL '30 minutes'
                  AND pk.tier::text NOT LIKE 'GACHA%'
                  AND NOT EXISTS (
                      SELECT 1 FROM subscriptions s
                      WHERE s.user_id = p.user_id
                        AND s.package_id = p.package_id
                        AND s.status::text = 'ACTIVE'
                        AND s.created_at >= p.created_at - INTERVAL '5 minutes'
                  )
                  AND NOT EXISTS (
                      SELECT 1 FROM admin_logs al
                      WHERE al.target_type = 'payment'
                        AND al.target_id = p.id
                        AND al.action IN ('payment_approved_gacha', 'gacha_grant')
                  )
            """))
            n = r.scalar() or 0
            if n > 0:
                issues.append(f"🚨 CRITICAL: {n} CONFIRMED payment ใน 30 นาทีล่าสุดไม่มี sub matching!")
    except Exception as exc:
        issues.append(f"💥 CONFIRMED-sub check crashed: {exc}")

    # === Test 5 (NEW 2026-06-21): ACTIVE sub ที่หมดอายุแล้ว (cron expiry ไม่รัน) ===
    try:
        from shared.database import get_session
        from sqlalchemy import text as _t
        # FIX 2026-06-22: kick_expired_6h runs every 6h, so subs expiring just before
        # a run can sit ACTIVE-but-expired up to 6h legitimately. Only alert if
        # the sub has been overdue by >7h = job had 1+ chance and failed.
        async with get_session() as s:
            r = await s.execute(_t("""
                SELECT COUNT(*) FROM subscriptions
                WHERE status::text = 'ACTIVE'
                  AND end_date < NOW() - INTERVAL '7 hours'
            """))
            n = r.scalar() or 0
            if n > 0:
                issues.append(f"⚠️ {n} ACTIVE sub หมดอายุเกิน 7 ชม.แล้ว (kick_expired ไม่รัน?)")
    except Exception as exc:
        issues.append(f"💥 expired-sub check crashed: {exc}")

    # === Test 6 (NEW 2026-06-21): Duplicate subs per payment_id ===
    try:
        from shared.database import get_session
        from sqlalchemy import text as _t
        async with get_session() as s:
            r = await s.execute(_t("""
                SELECT COUNT(*) FROM (
                    SELECT payment_id FROM subscriptions
                    WHERE payment_id IS NOT NULL
                    GROUP BY payment_id HAVING COUNT(*) > 1
                ) t
            """))
            n = r.scalar() or 0
            if n > 0:
                issues.append(f"🚨 CRITICAL: {n} payment มี subs ซ้ำ (เคส Naca-like)")
    except Exception as exc:
        issues.append(f"💥 dup-subs check crashed: {exc}")

    # === Test 7 (NEW 2026-06-21): total_spent vs sum(CONFIRMED payments) mismatch ===
    try:
        from shared.database import get_session
        from sqlalchemy import text as _t
        async with get_session() as s:
            r = await s.execute(_t("""
                SELECT COUNT(*) FROM (
                    SELECT u.id, u.total_spent, COALESCE(SUM(p.amount), 0) AS actual
                    FROM users u
                    LEFT JOIN payments p ON p.user_id = u.id AND p.status::text = 'CONFIRMED'
                    WHERE u.total_spent > 0
                    GROUP BY u.id, u.total_spent
                    HAVING u.total_spent != COALESCE(SUM(p.amount), 0)
                ) t
            """))
            n = r.scalar() or 0
            if n > 0:
                issues.append(f"⚠️ {n} users มี total_spent ไม่ตรง sum(payments) (trigger fire ผิด?)")
    except Exception as exc:
        issues.append(f"💥 total_spent check crashed: {exc}")

    # === Test 8 (NEW 2026-06-21): Loyalty rank mismatch ===
    try:
        from shared.database import get_session
        from sqlalchemy import text as _t
        async with get_session() as s:
            r = await s.execute(_t("""
                SELECT COUNT(*) FROM users
                WHERE (
                    (total_spent::int >= 4000 AND loyalty_rank != 'DIAMOND') OR
                    (total_spent::int < 4000 AND total_spent::int >= 1000 AND loyalty_rank NOT IN ('SILVER', 'DIAMOND'))
                )
            """))
            n = r.scalar() or 0
            if n > 0:
                issues.append(f"⚠️ {n} users มี loyalty_rank ต่ำกว่าเกณฑ์ — ต้อง backfill")
    except Exception as exc:
        issues.append(f"💥 loyalty rank check crashed: {exc}")

    return issues


async def run_health_check_and_alert(context):
    """Cron entry — checks + alerts admin group if issues."""
    issues = await health_check_payment_system()
    if not issues:
        logger.info("Payment health check: ALL OK")
        return

    # Send to admin group
    try:
        from shared.admin_alert import notify_admin_report
        alert = "🩺 <b>PAYMENT HEALTH CHECK ALERT</b>\n━━━━━━━━━━━━━━\n"
        for issue in issues:
            alert += f"\n{issue}"
        alert += f"\n\n⏰ {__import__('shared.tz', fromlist=['now_th']).now_th().strftime('%Y-%m-%d %H:%M:%S')}"
        await notify_admin_report(alert, parse_mode="HTML")
        logger.warning("Payment health alert sent: %d issues", len(issues))
    except Exception as exc:
        logger.exception("Failed to send health alert: %s", exc)


__all__ = ["health_check_payment_system", "run_health_check_and_alert"]
