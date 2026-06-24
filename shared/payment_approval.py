"""Canonical Payment Approval Service — single source of truth.

WHY THIS EXISTS:
  Before this module, 9 different code paths (auto-approve, GACHA, retry worker,
  admin button A/B/C, slip_review, TrueMoney, etc.) each had their own copy of
  approval logic. Each missed different side effects: some forgot audit logs,
  some forgot record_payment_received, some used wrong bot for DM, some expired
  lifetime subs by mistake.

  This module replaces all of them with `apply_payment_approval(inp)`.

USAGE:
    from shared.payment_approval import (
        apply_payment_approval, ApprovalInput, ApprovalSource, ApprovalResult,
    )

    result = await apply_payment_approval(ApprovalInput(
        user_id=db_user.id,
        telegram_id=user.id,
        source=ApprovalSource.SLIP2GO_AUTO,
        explicit_tier=PackageTier.TIER_300,
        amount_paid=Decimal("300"),
        slip_trans_ref="...",
        slip_hash="...",
        matched_receiver_account_id=1,
    ))
    if result.success:
        # caller may format custom reply using result.invite_links etc.
        ...
    else:
        # result.error: "dup_transref" / "dup_hash" / "sender_ring" / etc.
        await update.message.reply_text(f"❌ {result.error}")

STEP ORDER (in one DB transaction):
   1. Upsert user (if not exists, with user_id=None)
   2. Sender Ring Detection (block if scam pattern)
   3. Dup check (transRef + slip_hash + recent same-amount)
   4. Resolve tier + load package
   5. Compute add-on detection
   6. Compute Birthday bonus
   7. Compute end_date (TIER_99=24h, TIER_2499=2099-12-31, else now+duration)
   8. Expire existing active subs (preserve lifetime, only same-pkg for ADD500)
   9. Upsert Payment row (CONFIRMED, verified_by, auto_approved, all slip fields)
  10. Create Subscription (or skip for GACHA)
  11. GACHA: upsert gachapon_credits
  12. SHAKER: assign numbers if TIER_100
  13. Birthday offer: mark used
  14. Comeback promo: mark purchased
  15. Discount credit: apply_usage
  16. record_payment_received (cumulative + milestone alert)
  17. log_admin_action (audit)
  18. COMMIT

After commit (separate try blocks, partial success OK):
  19. Generate invite links via Guardian Bot
  20. Send customer DM via send_to_customer (Sales Bot)
  21. Sheets sync (best-effort)
  22. Admin notification on partial failure
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from enum import Enum
from typing import Any

from sqlalchemy import select, text as sql_text, update as sa_update

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
#   ENUMS / DATACLASSES
# ─────────────────────────────────────────────────────────────────────────────


class ApprovalSource(str, Enum):
    SLIP2GO_AUTO = "slip2go_auto"
    GACHA = "gacha"
    TRUEMONEY = "truemoney"
    RETRY_WORKER = "retry_worker"
    SLIP_REVIEW = "slip_review"
    ADMIN_BY_PRICE = "admin_by_price"
    ADMIN_PROMO = "admin_promo"
    ADMIN_BY_PID = "admin_by_pid"
    MANUAL_BACKFILL = "manual_backfill"


@dataclass
class ApprovalInput:
    user_id: int                                 # DB users.id
    telegram_id: int
    source: ApprovalSource
    amount_paid: Decimal

    # Tier resolution (priority: explicit_tier > explicit_package_id > slip-derived)
    explicit_tier: Any = None                    # PackageTier enum or None
    explicit_package_id: int | None = None
    slip2go_amount: Decimal | None = None

    # Admin context
    admin_id: int | None = None                  # None = system/auto
    payment_id: int | None = None                # set for "update existing" paths

    # Slip metadata
    slip_trans_ref: str | None = None
    slip_hash: str | None = None
    sender_name: str | None = None
    sender_bank_name: str | None = None
    sender_bank_account: str | None = None
    slip_file_id: str | None = None
    method: str = "SLIP"                         # SLIP / TRUEWALLET / PROMPTPAY

    # Receiver match (slip2go_auto sets this; others leave None)
    matched_receiver_account_id: int | None = None

    # Promo/discount context
    comeback_dm_log_id: int | None = None
    discount_credit_used: Decimal = field(default_factory=lambda: Decimal("0"))
    promo_campaign_id: int | None = None
    expected_amount: Decimal | None = None

    # Behavior overrides
    skip_dup_check: bool = False                 # only retry_worker should set this
    skip_dm: bool = False                        # caller will DM differently (e.g. GACHA web_app)
    skip_sender_ring: bool = False               # admin override
    force_amount: bool = False                   # admin "amount_mismatch_warned" branch


@dataclass
class InviteLink:
    title: str
    url: str


@dataclass
class ApprovalResult:
    success: bool
    payment_id: int | None = None
    subscription_id: int | None = None
    invite_links: list[InviteLink] = field(default_factory=list)
    package_name: str = ""
    expires_at: datetime | None = None
    is_lifetime: bool = False
    bonus_days: int = 0
    shaker_numbers: list[str] = field(default_factory=list)
    gacha_credits_added: int = 0
    onboarding_gacha_added: int = 0
    onboarding_discount_added: int = 0
    onboarding_extra_days: int = 0
    customer_dm_sent: bool = False
    error: str | None = None
    error_details: str | None = None             # raw exception detail (debug)
    idempotent_skip: bool = False


# ─────────────────────────────────────────────────────────────────────────────
#   HELPERS
# ─────────────────────────────────────────────────────────────────────────────


# Spin tier → credit count
_GACHA_SPINS = {"GACHA_1": 1, "GACHA_3": 3, "GACHA_10": 10}

# Tiers that have no Subscription (only side effects)
_NO_SUBSCRIPTION_TIERS = {"GACHA_1", "GACHA_3", "GACHA_10"}

# Lifetime end_date sentinel (~30 years out so cron jobs don't expire it)
_LIFETIME_END = datetime(2099, 12, 31, 23, 59, 59)


def _tier_value(tier_or_str) -> str:
    """Normalize tier to its string value (works for enum or raw str)."""
    if tier_or_str is None:
        return ""
    if hasattr(tier_or_str, "value"):
        return tier_or_str.value
    return str(tier_or_str)


async def _resolve_user(session, user_id: int | None, telegram_id: int, first_name: str | None) -> tuple[int, str | None]:
    """Ensure a User row exists; return (user_id, first_name)."""
    from shared.models import User
    if user_id:
        u = (await session.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
        if u:
            return u.id, u.first_name
    # Lookup by telegram_id
    u = (await session.execute(select(User).where(User.telegram_id == telegram_id))).scalar_one_or_none()
    if u:
        return u.id, u.first_name
    # Create new
    u = User(telegram_id=telegram_id, first_name=first_name or "ลูกค้า")
    session.add(u)
    await session.flush()
    return u.id, u.first_name


async def _check_dup(session, inp: ApprovalInput) -> str | None:
    """Return error code if slip already used, else None."""
    from shared.models import Payment
    if inp.slip_trans_ref:
        r = await session.execute(
            select(Payment.id).where(Payment.slip_trans_ref == inp.slip_trans_ref)
        )
        existing = r.scalar_one_or_none()
        if existing and existing != inp.payment_id:
            return f"dup_transref:{existing}"
    if inp.slip_hash:
        from shared.models import PaymentStatus
        r = await session.execute(
            select(Payment.id).where(
                Payment.slip_hash == inp.slip_hash,
                Payment.status.in_([PaymentStatus.CONFIRMED, PaymentStatus.PENDING]),
            )
        )
        existing = r.scalar_one_or_none()
        if existing and existing != inp.payment_id:
            return f"dup_hash:{existing}"
    return None


def _compute_end_date(package, now: datetime, birthday_bonus_days: int = 0) -> tuple[datetime, bool]:
    """Compute subscription end_date. Returns (end_date, is_lifetime)."""
    tier_str = _tier_value(package.tier)
    # Lifetime tiers
    if tier_str in ("2499", "TIER_2499"):
        return _LIFETIME_END, True
    # Add-on lives same expiry as lifetime → keep _LIFETIME_END
    if tier_str in ("ADD500", "TIER_ADD500"):
        return _LIFETIME_END, True
    # 24-hour trial
    if tier_str in ("99", "TIER_99"):
        return now + timedelta(hours=24), False
    # Otherwise duration_days
    dur = int(package.duration_days or 30)
    end = now + timedelta(days=dur)
    if birthday_bonus_days > 0:
        end += timedelta(days=birthday_bonus_days)
    return end, False


# ─────────────────────────────────────────────────────────────────────────────
#   THE MAIN SERVICE
# ─────────────────────────────────────────────────────────────────────────────


async def apply_payment_approval(inp: ApprovalInput) -> ApprovalResult:
    """Canonical approval — see module docstring for full step list."""
    from shared.database import get_session
    from shared.models import (
        User, Package, Payment, Subscription, PackageTier, PaymentStatus,
        PaymentMethod, SubscriptionStatus,
    )

    is_gacha = inp.source == ApprovalSource.GACHA
    is_admin = inp.admin_id is not None
    src_str = inp.source.value if isinstance(inp.source, ApprovalSource) else str(inp.source)

    # Used for post-commit steps (filled inside transaction)
    package_name = ""
    db_user_id = inp.user_id
    user_first_name: str | None = None
    payment_id_final: int | None = None
    subscription_id: int | None = None
    is_lifetime = False
    end_date_final: datetime | None = None
    bonus_days_applied = 0
    shaker_numbers: list[str] = []
    gacha_credits_added = 0
    package_id_final: int | None = None

    # ───── Transactional region ─────
    try:
        async with get_session() as session:

            # STEP 0 (NEW 2026-06-21): Idempotency check
            # ป้องกัน Naca-pattern: retry worker เรียกซ้ำ → สร้าง sub ใหม่ทุกครั้ง.
            if inp.payment_id is not None:
                _exist_pay = (await session.execute(
                    select(Payment).where(Payment.id == inp.payment_id)
                )).scalar_one_or_none()
                if _exist_pay is not None:
                    # FIX 2026-06-21: PaymentStatus.CONFIRMED.value = "confirmed" (lowercase!)
                    _exist_status = str(
                        _exist_pay.status.value if hasattr(_exist_pay.status, "value")
                        else _exist_pay.status
                    ).upper()
                    if _exist_status == "CONFIRMED":
                        _exist_sub = (await session.execute(
                            select(Subscription)
                            .where(Subscription.payment_id == inp.payment_id)
                            .order_by(Subscription.id.desc()).limit(1)
                        )).scalar_one_or_none()
                        if _exist_sub is not None:
                            logger.info(
                                "[approval] STEP 0: payment %s already CONFIRMED with sub %s — skip (idempotent)",
                                inp.payment_id, _exist_sub.id,
                            )
                            return ApprovalResult(
                                success=True,
                                payment_id=inp.payment_id,
                                subscription_id=_exist_sub.id,
                                package_name="(existing)",
                                expires_at=_exist_sub.end_date,
                                is_lifetime=bool(_exist_sub.end_date and _exist_sub.end_date.year >= 2099),
                                idempotent_skip=True,
                            )

            # STEP 1: upsert user
            db_user_id, user_first_name = await _resolve_user(
                session, inp.user_id, inp.telegram_id, None
            )

            # STEP 2: Sender Ring Detection (skippable for admin override)
            if inp.sender_name and not inp.skip_sender_ring:
                try:
                    from shared.sender_ring_check import is_sender_ring_suspicious
                    is_ring, other_uids = await is_sender_ring_suspicious(
                        inp.sender_name, db_user_id
                    )
                    if is_ring:
                        logger.warning(
                            "[approval] SENDER_RING block tg=%s sender=%r other_uids=%s",
                            inp.telegram_id, inp.sender_name, other_uids
                        )
                        return ApprovalResult(
                            success=False,
                            error="sender_ring",
                            error_details=f"sender used by {len(other_uids)} other tg accounts",
                        )
                except Exception as exc:
                    logger.error("[approval] sender_ring check CRASHED: %s", exc, exc_info=True)
                    try:
                        from shared.admin_alert import notify_admin_report
                        _alert_lines = [
                            "WARN: sender_ring check crashed",
                            "tg: " + str(inp.telegram_id),
                            "sender: " + str(inp.sender_name),
                            "error: " + str(exc)[:200],
                            "security check fail — admin please review",
                        ]
                        await notify_admin_report("\n".join(_alert_lines))
                    except Exception:
                        pass

            # STEP 2.5: Blacklist check (banned sender_name + slip)
            try:
                from shared.ban_service import is_sender_blacklisted, is_slip_blacklisted
                if inp.sender_name:
                    is_bl, reason = await is_sender_blacklisted(inp.sender_name)
                    if is_bl:
                        logger.warning("[approval] BLACKLIST sender block tg=%s sender=%r reason=%s",
                                       inp.telegram_id, inp.sender_name, reason)
                        return ApprovalResult(
                            success=False,
                            error="blacklisted_sender",
                            error_details=f"sender {inp.sender_name!r} in blacklist ({reason})",
                        )
                is_bl, reason = await is_slip_blacklisted(inp.slip_trans_ref, inp.slip_hash)
                if is_bl:
                    logger.warning("[approval] BLACKLIST slip block tg=%s reason=%s",
                                   inp.telegram_id, reason)
                    return ApprovalResult(
                        success=False,
                        error="blacklisted_slip",
                        error_details=reason or "slip in blacklist",
                    )
            except Exception as exc:
                logger.warning("[approval] blacklist check failed (allow): %s", exc)

            # STEP 3: Dup check
            if not inp.skip_dup_check:
                dup_err = await _check_dup(session, inp)
                if dup_err:
                    logger.warning("[approval] dup-slip block tg=%s err=%s", inp.telegram_id, dup_err)
                    return ApprovalResult(success=False, error=dup_err)

            # STEP 4: Resolve tier + load Package
            target_tier_str: str | None = None
            if inp.explicit_tier is not None:
                target_tier_str = _tier_value(inp.explicit_tier)
            elif inp.explicit_package_id:
                pkg_q = await session.execute(
                    select(Package).where(Package.id == inp.explicit_package_id)
                )
                pkg_row = pkg_q.scalar_one_or_none()
                if not pkg_row:
                    return ApprovalResult(success=False, error="package_not_found")
                target_tier_str = _tier_value(pkg_row.tier)
            elif inp.slip2go_amount is not None:
                # Derive from amount
                try:
                    from shared.pricing import amount_to_tier
                    t = amount_to_tier(int(inp.slip2go_amount))
                    if t:
                        target_tier_str = t[0]
                except Exception as exc:
                    logger.warning("[approval] amount_to_tier failed: %s", exc)

            if not target_tier_str:
                return ApprovalResult(success=False, error="tier_unresolved")

            # GACHA handled separately — no subscription
            if target_tier_str in _NO_SUBSCRIPTION_TIERS:
                is_gacha = True

            # Load Package row by tier
            if not is_gacha:
                # Match by tier enum value
                try:
                    tier_enum = PackageTier(target_tier_str)
                except ValueError:
                    # Try "TIER_*" variant
                    try:
                        tier_enum = PackageTier(f"TIER_{target_tier_str}")
                    except ValueError:
                        return ApprovalResult(success=False, error=f"unknown_tier:{target_tier_str}")
                pkg_q = await session.execute(
                    select(Package).where(Package.tier == tier_enum, Package.is_active == True)
                )
                package = pkg_q.scalar_one_or_none()
                if not package:
                    return ApprovalResult(success=False, error=f"package_not_active:{target_tier_str}")
                package_name = package.name
                package_id_final = package.id
            else:
                # GACHA — no Subscription, use package_id=1 placeholder
                package = None
                package_name = f"GACHA {target_tier_str.replace('GACHA_','')} หมุน"
                package_id_final = 1

            # STEP 5: ADD-ON detection (already encoded by target_tier_str)
            is_addon = target_tier_str in ("ADD500", "TIER_ADD500")

            # STEP 6: Birthday bonus
            bonus_days_applied = 0
            birthday_offer_id = None
            if package and not is_gacha:
                try:
                    r = await session.execute(sql_text("""
                        SELECT bo.id AS offer_id,
                               GREATEST(0, EXTRACT(DAY FROM sub.end_date - NOW()))::int AS days
                        FROM birthday_upgrade_offers bo
                        JOIN users u ON u.id = bo.user_id
                        LEFT JOIN subscriptions sub ON sub.user_id = bo.user_id
                             AND sub.status = 'ACTIVE'
                             AND sub.end_date > NOW()
                        LEFT JOIN packages pk ON pk.id = sub.package_id AND pk.tier = 'TIER_500'
                        WHERE u.id = :uid AND bo.expires_at > NOW() AND bo.upgraded_to_tier IS NULL
                        ORDER BY sub.end_date DESC LIMIT 1
                    """), {"uid": db_user_id})
                    row = r.fetchone()
                    if row and row.offer_id and (_tier_value(package.tier) in ("TIER_1299", "1299")):
                        birthday_offer_id = int(row.offer_id)
                        bonus_days_applied = int(row.days or 0)
                except Exception as exc:
                    logger.warning("[approval] Birthday bonus lookup failed: %s", exc)

            now = datetime.utcnow()

            # STEP 7: Compute end_date
            if package:
                end_date_final, is_lifetime = _compute_end_date(package, now, bonus_days_applied)
            else:
                end_date_final, is_lifetime = None, False

            # STEP 8: Expire existing subs (with lifetime guard)
            if not is_gacha and package:
                if is_addon:
                    # Only expire same-package subs
                    await session.execute(
                        sa_update(Subscription)
                        .where(
                            Subscription.user_id == db_user_id,
                            Subscription.status == SubscriptionStatus.ACTIVE,
                            Subscription.package_id == package.id,
                        )
                        .values(status=SubscriptionStatus.EXPIRED)
                    )
                else:
                    # Preserve lifetime (TIER_2499) — expire other actives only
                    sub_ids_q = await session.execute(
                        select(Subscription.id)
                        .join(Package, Subscription.package_id == Package.id)
                        .where(
                            Subscription.user_id == db_user_id,
                            Subscription.status == SubscriptionStatus.ACTIVE,
                            Package.tier != PackageTier.TIER_2499,
                        )
                    )
                    non_lifetime_ids = [row[0] for row in sub_ids_q]
                    if non_lifetime_ids:
                        await session.execute(
                            sa_update(Subscription)
                            .where(Subscription.id.in_(non_lifetime_ids))
                            .values(status=SubscriptionStatus.EXPIRED)
                        )

            # STEP 9: Upsert Payment
            method_enum = {
                "SLIP": PaymentMethod.SLIP,
                "PROMPTPAY": PaymentMethod.PROMPTPAY,
                "TRUEWALLET": PaymentMethod.TRUEWALLET,
            }.get(inp.method.upper(), PaymentMethod.SLIP)

            if inp.payment_id:
                # Update existing row
                p = await session.get(Payment, inp.payment_id)
                if not p:
                    return ApprovalResult(success=False, error="payment_not_found")
                p.status = PaymentStatus.CONFIRMED
                p.verified_at = now
                if inp.admin_id is not None:
                    p.verified_by = inp.admin_id
                p.auto_approved = (inp.admin_id is None)
                if inp.slip_trans_ref: p.slip_trans_ref = inp.slip_trans_ref
                if inp.slip_hash: p.slip_hash = inp.slip_hash
                if inp.sender_name: p.sender_name = inp.sender_name
                if inp.sender_bank_name: p.sender_bank_name = inp.sender_bank_name
                if inp.sender_bank_account: p.sender_bank_account = inp.sender_bank_account
                if inp.slip_file_id: p.slip_file_id = inp.slip_file_id
                if package_id_final:
                    p.package_id = package_id_final
                p.amount = inp.amount_paid
            else:
                p = Payment(
                    user_id=db_user_id,
                    package_id=package_id_final or 1,
                    amount=inp.amount_paid,
                    method=method_enum,
                    status=PaymentStatus.CONFIRMED,
                    verified_by=inp.admin_id,
                    verified_at=now,
                    auto_approved=(inp.admin_id is None),
                    slip_trans_ref=inp.slip_trans_ref,
                    slip_hash=inp.slip_hash,
                    sender_name=inp.sender_name,
                    sender_bank_name=inp.sender_bank_name,
                    sender_bank_account=inp.sender_bank_account,
                    slip_file_id=inp.slip_file_id,
                )
                session.add(p)
            await session.flush()
            payment_id_final = p.id

            # STEP 10: Create Subscription (skip for GACHA)
            if not is_gacha and package:
                sub = Subscription(
                    user_id=db_user_id,
                    package_id=package.id,
                    status=SubscriptionStatus.ACTIVE,
                    start_date=now,
                    end_date=end_date_final,
                    payment_id=payment_id_final,
                )
                session.add(sub)
                await session.flush()
                subscription_id = sub.id

            # STEP 11: GACHA — upsert credits
            if is_gacha:
                spins = _GACHA_SPINS.get(target_tier_str, 0)
                gacha_credits_added = spins
                await session.execute(sql_text(
                    "INSERT INTO gachapon_credits (user_id, telegram_id, credits, total_purchased) "
                    "VALUES (:uid, :tg, :sp, :sp) "
                    "ON CONFLICT (user_id) DO UPDATE SET "
                    "  credits = gachapon_credits.credits + :sp, "
                    "  total_purchased = gachapon_credits.total_purchased + :sp, "
                    "  updated_at = NOW()"
                ), {"uid": db_user_id, "tg": inp.telegram_id, "sp": spins})

            # STEP 12: SHAKER number assignment (TIER_100)
            if not is_gacha and package and _tier_value(package.tier) in ("TIER_100", "100"):
                try:
                    from bots.sales_bot.handlers.shaker import assign_shaker_numbers
                    ticket_count = max(1, int(float(inp.amount_paid) // 100))
                    shaker_numbers = await assign_shaker_numbers(
                        db_user_id, inp.telegram_id, ticket_count, payment_id_final,
                    )
                    logger.info("[approval] SHAKER user=%s assigned %s nums", db_user_id, ticket_count)
                except Exception as exc:
                    logger.error("[approval] SHAKER assignment failed: %s", exc)

            # STEP 12.5 (NEW 2026-06-20): First-payment onboarding rewards
            onboarding_gacha = 0
            onboarding_discount = 0
            onboarding_extra_days = 0
            try:
                _prior_pays = (await session.execute(sql_text(
                    "SELECT COUNT(*) FROM payments "
                    "WHERE user_id = :uid AND status = 'CONFIRMED' AND id != :pid"
                ), {"uid": db_user_id, "pid": payment_id_final})).scalar() or 0
                if _prior_pays == 0:  # first-ever confirmed payment
                    _tv = _tier_value(target_tier_str) if target_tier_str else ""
                    _ONB = {
                        "TIER_100":  {"gacha": 1, "discount": 20,  "days": 0},
                        "TIER_300":  {"gacha": 2, "discount": 50,  "days": 0},
                        "TIER_500":  {"gacha": 3, "discount": 100, "days": 3},
                        "TIER_1299": {"gacha": 5, "discount": 200, "days": 0},
                        "TIER_2499": {"gacha": 10,"discount": 500, "days": 0},
                    }
                    _bonus = _ONB.get(str(_tv))
                    if _bonus:
                        if _bonus["gacha"]:
                            await session.execute(sql_text(
                                "INSERT INTO gachapon_credits (user_id, telegram_id, credits, total_purchased) "
                                "VALUES (:uid, :tg, :sp, 0) "
                                "ON CONFLICT (user_id) DO UPDATE SET "
                                "  credits = gachapon_credits.credits + :sp, updated_at = NOW()"
                            ), {"uid": db_user_id, "tg": inp.telegram_id, "sp": _bonus["gacha"]})
                            onboarding_gacha = _bonus["gacha"]
                            gacha_credits_added += _bonus["gacha"]
                        if _bonus["discount"]:
                            await session.execute(sql_text(
                                "INSERT INTO user_discount_credits (user_id, telegram_id, balance, total_earned, updated_at) "
                                "VALUES (:uid, :tg, :amt, :amt, NOW()) "
                                "ON CONFLICT (user_id) DO UPDATE SET "
                                "  balance = user_discount_credits.balance + :amt, "
                                "  total_earned = user_discount_credits.total_earned + :amt, "
                                "  updated_at = NOW()"
                            ), {"uid": db_user_id, "tg": inp.telegram_id, "amt": _bonus["discount"]})
                            onboarding_discount = _bonus["discount"]
                        if _bonus["days"] and subscription_id:
                            await session.execute(sql_text(
                                "UPDATE subscriptions SET end_date = end_date + (:d || ' days')::interval, "
                                "updated_at = NOW() WHERE id = :sid"
                            ), {"d": str(_bonus["days"]), "sid": subscription_id})
                            onboarding_extra_days = _bonus["days"]
                            end_date_final = end_date_final + timedelta(days=_bonus["days"])
                        logger.info(
                            "[onboarding] uid=%s tier=%s gacha=%s discount=%s days=%s",
                            db_user_id, _tv, _bonus["gacha"], _bonus["discount"], _bonus["days"],
                        )
            except Exception as _exc_onb:
                logger.warning("[onboarding] reward failed uid=%s: %s", db_user_id, _exc_onb)

            # STEP 13: Mark birthday offer used
            if birthday_offer_id and package and _tier_value(package.tier) in ("TIER_1299", "1299", "TIER_2499", "2499"):
                try:
                    await session.execute(sql_text("""
                        UPDATE birthday_upgrade_offers
                        SET upgraded_to_tier = :tier, upgraded_at = NOW(), payment_id = :pid
                        WHERE id = :oid
                    """), {
                        "tier": _tier_value(package.tier),
                        "pid": payment_id_final,
                        "oid": birthday_offer_id,
                    })
                except Exception as exc:
                    logger.warning("[approval] Birthday offer mark failed: %s", exc)

            # STEP 14: Mark comeback promo purchased
            if inp.comeback_dm_log_id:
                try:
                    await session.execute(sql_text(
                        "UPDATE comeback_dm_log SET purchased = TRUE, responded = TRUE "
                        "WHERE id = :id"
                    ), {"id": inp.comeback_dm_log_id})
                except Exception as exc:
                    logger.warning("[approval] comeback mark failed: %s", exc)

            # STEP 15: Discount credit apply (best-effort import)
            if inp.discount_credit_used and inp.discount_credit_used > 0:
                try:
                    from shared.discount_helper import apply_usage as _disc_apply
                    await _disc_apply(inp.telegram_id, inp.discount_credit_used, payment_id_final)
                except Exception as exc:
                    logger.warning("[approval] discount apply_usage failed: %s", exc)

            # COMMIT (implicit on async-with exit)

        # ───── End transactional region ─────

        # STEP 16: record_payment_received (outside main session, own commit)
        if inp.matched_receiver_account_id and inp.method.upper() in ("SLIP", "PROMPTPAY"):
            try:
                from shared.receiver_pool import record_payment_received
                await record_payment_received(
                    inp.matched_receiver_account_id, inp.amount_paid
                )
            except Exception as exc:
                logger.warning("[approval] record_payment_received failed: %s", exc)

        # STEP 17: log_admin_action
        try:
            from shared.utils import log_admin_action
            await log_admin_action(
                admin_id=inp.admin_id or 0,
                action=f"payment_approved_{src_str}",
                target_type="payment",
                target_id=payment_id_final,
                details=(
                    f"src={src_str} tier={target_tier_str} amt={inp.amount_paid} "
                    f"tg={inp.telegram_id} sub_id={subscription_id} "
                    f"birthday_bonus={bonus_days_applied} "
                    f"gacha_credits={gacha_credits_added}"
                )[:500],
            )
        except Exception as exc:
            logger.warning("[approval] log_admin_action failed: %s", exc)

        logger.info(
            "[APPROVED] pay=%s src=%s tg=%s tier=%s amt=%s sub=%s bd_bonus=%s gacha=%s",
            payment_id_final, src_str, inp.telegram_id, target_tier_str,
            inp.amount_paid, subscription_id, bonus_days_applied, gacha_credits_added
        )

    except Exception as exc:
        logger.exception("[approval] TRANSACTION FAILED tg=%s: %s", inp.telegram_id, exc)
        return ApprovalResult(
            success=False,
            error="transaction_failed",
            error_details=str(exc)[:300],
        )

    # ───── Post-commit (separate failure boundary) ─────

    invite_links_list: list[InviteLink] = []

    # STEP 19: Generate invite links (Guardian Bot)
    # Skip for GACHA (no groups granted via gacha tiers)
    if not is_gacha and subscription_id:
        try:
            from bots.guardian_bot.group_monitor import generate_invite_links_for_user
            import telegram as _tg
            guardian_token = os.environ.get("GUARDIAN_BOT_TOKEN", "")
            if guardian_token:
                gb = _tg.Bot(token=guardian_token)
                await gb.initialize()
                try:
                    raw_links = await generate_invite_links_for_user(
                        gb, inp.telegram_id, package_id_final
                    )
                    # generate_invite_links_for_user returns dict[str,str]
                    # mapping group_slug -> invite_url. Resolve slug → title.
                    if isinstance(raw_links, dict):
                        # Resolve titles in a separate session
                        from shared.models import GroupRegistry as _GR
                        async with get_session() as _s2:
                            for slug, url in raw_links.items():
                                if not url:
                                    continue
                                title = slug
                                try:
                                    r = await _s2.execute(
                                        select(_GR).where(_GR.slug == slug)
                                    )
                                    grp = r.scalar_one_or_none()
                                    if grp and getattr(grp, "title", None):
                                        title = grp.title
                                except Exception:
                                    pass
                                invite_links_list.append(InviteLink(title=str(title), url=str(url)))
                    elif isinstance(raw_links, list):
                        for entry in raw_links:
                            if isinstance(entry, dict):
                                invite_links_list.append(InviteLink(
                                    title=entry.get("title", "กลุ่ม"),
                                    url=entry.get("url", "")
                                ))
                            elif isinstance(entry, (list, tuple)) and len(entry) >= 2:
                                invite_links_list.append(InviteLink(title=str(entry[0]), url=str(entry[1])))
                finally:
                    try: await gb.shutdown()
                    except Exception: pass
            else:
                logger.error("[approval] GUARDIAN_BOT_TOKEN not set")
        except Exception as exc:
            logger.error("[approval] invite link gen failed: %s", exc)

    # STEP 20: Send customer DM (Sales Bot, ALWAYS)
    customer_dm_sent = False
    if not inp.skip_dm:
        try:
            from shared.customer_dm import send_invite_links_dm
            expiry_text = None
            if end_date_final and not is_lifetime:
                expiry_text = end_date_final.strftime("%d/%m/%Y")
            elif is_lifetime:
                expiry_text = "ถาวร"
            bottom_extra = ""
            if shaker_numbers:
                bottom_extra = f"🎫 เลขลุ้น: {' '.join(shaker_numbers)}"
            if bonus_days_applied > 0:
                bottom_extra += f"\n🎁 โบนัสวันเกิด: +{bonus_days_applied} วัน"
            link_pairs = [(l.title, l.url) for l in invite_links_list]
            customer_dm_sent = await send_invite_links_dm(
                telegram_id=inp.telegram_id,
                first_name=user_first_name,
                package_name=package_name,
                invite_links=link_pairs,
                expires_text=expiry_text,
                extra_bottom_text=bottom_extra,
            )
        except Exception as exc:
            logger.error("[approval] customer DM failed tg=%s: %s", inp.telegram_id, exc)

    # STEP 21 (NEW 2026-06-22): Immediate loyalty rank check
    # ก่อนหน้านี้ลูกค้าต้องรอ scheduler 6 ชม. → trigger ทันทีหลังจ่ายแบบ idempotent
    # promote_user_to_rank มี advisory lock + ตรวจ rank_higher ในตัว → ปลอดภัย
    try:
        from shared.loyalty_rank import compute_rank_for_user, promote_user_to_rank, rank_higher
        async with get_session() as _ls:
            r = await _ls.execute(sql_text("SELECT loyalty_rank FROM users WHERE id=:i"), {"i": db_user_id})
            current_rank = (r.scalar() or "NONE")
        target_rank = await compute_rank_for_user(db_user_id)
        if target_rank and target_rank != "NONE" and rank_higher(target_rank, current_rank):
            logger.info("[approval] loyalty trigger: user=%s %s -> %s", db_user_id, current_rank, target_rank)
            await promote_user_to_rank(db_user_id, target_rank, silent=False)
    except Exception as exc:
        logger.warning("[approval] loyalty trigger failed: %s", exc)

    # STEP 21.5: Marketing conversion attribution
    # ถ้าลูกค้านี้เข้ามาผ่านลิ้ง marketing → ส่งแจ้งเตือนใน feed ของ marketer
    try:
        from shared.discord_notify import notify_marketer_conversion as _mk_notify
        async with get_session() as _ms:
            # หา marketing_invite_join ที่ user_id หรือ telegram_id ตรงกัน + ภายใน 30 วันก่อน join
            row = (await _ms.execute(sql_text("""
                SELECT l.id AS link_id, l.marketer, l.platform,
                       u.telegram_id, u.username, u.first_name,
                       EXTRACT(DAY FROM (now() - j.joined_at))::int AS days_since_join,
                       j.joined_at
                FROM marketing_invite_joins j
                JOIN marketing_invite_links l ON l.id = j.link_id
                JOIN users u ON u.id = :uid
                WHERE (j.user_id = :uid OR j.telegram_id = u.telegram_id)
                  AND j.joined_at >= now() - interval '30 days'
                ORDER BY j.joined_at DESC
                LIMIT 1
            """), {"uid": db_user_id})).first()

            if row:
                # Idempotency: ห้ามแจ้งซ้ำสำหรับ payment เดียวกัน
                marker_key = f"marketing_conv_notified:{payment_id_final}"
                existing = (await _ms.execute(sql_text(
                    "SELECT 1 FROM admin_logs WHERE action = :a LIMIT 1"
                ), {"a": marker_key})).first()
                if not existing:
                    # Marker first (prevents duplicate even if Discord call fails)
                    await _ms.execute(sql_text(
                        "INSERT INTO admin_logs (admin_id, action, details, created_at) "
                        "VALUES (0, :a, :d, now())"
                    ), {"a": marker_key, "d": f"link={row.link_id} marketer={row.marketer}"})
                    await _ms.commit()

                    # Compute marketer's month totals
                    month_row = (await _ms.execute(sql_text("""
                        SELECT COUNT(DISTINCT p.id) AS cnt,
                               COALESCE(SUM(p.amount), 0) AS rev
                        FROM marketing_invite_joins j2
                        JOIN marketing_invite_links l2 ON l2.id = j2.link_id
                        JOIN users u2 ON u2.telegram_id = j2.telegram_id
                        JOIN payments p ON p.user_id = u2.id
                        WHERE l2.marketer = :m
                          AND p.status = 'CONFIRMED' AND p.amount > 0
                          AND (p.created_at AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Bangkok') >= date_trunc('month', now() AT TIME ZONE 'Asia/Bangkok')
                          AND u2.telegram_id < 9000000000
                    """), {"m": row.marketer})).first()
                    m_cnt = int(month_row.cnt or 0) if month_row else 0
                    m_rev = float(month_row.rev or 0) if month_row else 0

                    # Fire Discord notification (don't block)
                    import asyncio as _aio
                    _aio.create_task(_mk_notify(
                        marketer=row.marketer, platform=row.platform,
                        telegram_id=row.telegram_id, tg_username=row.username,
                        tg_first_name=row.first_name, amount=float(inp.amount_paid or 0),
                        tier=str(target_tier_str or "?"),
                        days_since_join=int(row.days_since_join or 0),
                        link_id=int(row.link_id),
                        marketer_month_count=m_cnt,
                        marketer_month_revenue=m_rev,
                    ))
                    logger.info("[approval] marketing conversion: user=%s marketer=%s ฿%s",
                                db_user_id, row.marketer, inp.amount_paid)
    except Exception as _mkx:
        logger.warning("[approval] marketing conversion check failed: %s", _mkx)

    # STEP 22: Admin alert if customer didn't get DM OR no links
    if (not customer_dm_sent and not inp.skip_dm) or (not invite_links_list and not is_gacha):
        try:
            from shared.admin_alert import notify_admin_group
            links_flat = "\n".join([f"  {l.title}: {l.url}" for l in invite_links_list]) or "(no links)"
            await notify_admin_group(
                f"⚠️ <b>Approval partial-fail — manual intervene</b>\n"
                f"🆔 tg=<code>{inp.telegram_id}</code> pay=<code>{payment_id_final}</code>\n"
                f"📦 {package_name} ({_tier_value(target_tier_str)})\n"
                f"💰 ฿{inp.amount_paid}\n"
                f"DM sent: {customer_dm_sent} | links: {len(invite_links_list)}\n\n"
                f"🔗 ลิงก์ที่มี:\n{links_flat}",
                parse_mode="HTML",
            )
        except Exception:
            pass

    return ApprovalResult(
        success=True,
        payment_id=payment_id_final,
        subscription_id=subscription_id,
        invite_links=invite_links_list,
        package_name=package_name,
        expires_at=end_date_final,
        is_lifetime=is_lifetime,
        bonus_days=bonus_days_applied,
        shaker_numbers=shaker_numbers,
        gacha_credits_added=gacha_credits_added,
        onboarding_gacha_added=onboarding_gacha if 'onboarding_gacha' in dir() else 0,
        onboarding_discount_added=onboarding_discount if 'onboarding_discount' in dir() else 0,
        onboarding_extra_days=onboarding_extra_days if 'onboarding_extra_days' in dir() else 0,
        customer_dm_sent=customer_dm_sent,
    )


__all__ = [
    "apply_payment_approval",
    "ApprovalInput",
    "ApprovalResult",
    "ApprovalSource",
    "InviteLink",
]
