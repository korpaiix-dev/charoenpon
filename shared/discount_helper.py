"""Discount usage helper — compute & apply discount credit when buying packages.

Rules:
- Only apply to TIER_399 / TIER_999 / TIER_1299 / TIER_2499 (not GACHA bundles, not 100, not 300/500)
- Discount cap per tier:
    * TIER_399  → max ฿50  (12.5%)
    * TIER_999  → max ฿100 (10%)
    * TIER_1299 → max ฿100 (7.7%)
    * TIER_2499 → max ฿200 (8%)
    * "300"/"500" legacy → max ฿50
- Customer always pays the reduced amount via slip; we credit them back the full tier
  on approval AND deduct discount balance.
- Discount cannot exceed the user's credit balance.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Optional, Tuple

from sqlalchemy import text as _t

from shared.database import get_session

# Tier-string → max discount cap (in THB)
DISCOUNT_CAP = {
    "300": Decimal("50"),
    "500": Decimal("50"),
    "399": Decimal("50"),
    "999": Decimal("100"),
    "1299": Decimal("100"),
    "2499": Decimal("200"),
}


async def get_balance(telegram_id: int) -> Decimal:
    """Return current discount credit balance for a user."""
    async with get_session() as s:
        r = await s.execute(
            _t("SELECT balance FROM user_discount_credits WHERE telegram_id = :tg"),
            {"tg": telegram_id},
        )
        row = r.fetchone()
    return Decimal(row[0]) if row else Decimal("0")


def compute_use(balance: Decimal, tier_str: str, base_price: Decimal) -> Decimal:
    """How much discount we should apply, given balance + tier."""
    cap = DISCOUNT_CAP.get(str(tier_str))
    if cap is None or balance <= 0 or base_price <= 0:
        return Decimal("0")
    use = min(balance, cap, base_price - Decimal("1"))  # never make slip = 0
    if use < Decimal("1"):
        return Decimal("0")
    # Round down to nearest integer THB (avoid fractional slips)
    return Decimal(int(use))


async def reserve_in_context(
    context, telegram_id: int, tier_str: str, base_price: Decimal
) -> Tuple[Decimal, Decimal, Decimal]:
    """Store pending discount in user_data so payment handler can pick it up.

    Returns (use, expected_slip_amount, balance).
    """
    bal = await get_balance(telegram_id)
    use = compute_use(bal, tier_str, base_price)
    expected = base_price - use
    context.user_data["use_discount_pending"] = float(use)
    context.user_data["use_discount_expected_slip"] = float(expected)
    context.user_data["use_discount_base_price"] = float(base_price)
    context.user_data["use_discount_balance_before"] = float(bal)
    return use, expected, bal


def clear_context(context) -> None:
    for k in (
        "use_discount_pending",
        "use_discount_expected_slip",
        "use_discount_base_price",
        "use_discount_balance_before",
    ):
        context.user_data.pop(k, None)


async def apply_usage(
    telegram_id: int,
    payment_id: Optional[int],
    tier_purchased: str,
    full_price: Decimal,
    discount_used: Decimal,
    actual_paid: Decimal,
) -> bool:
    """Deduct balance + record usage. Returns True on success.

    Idempotent on payment_id: if a row with this payment_id already logged,
    skip without double-deducting.
    """
    if discount_used <= 0:
        return False
    async with get_session() as s:
        # Idempotency check
        if payment_id is not None:
            dup = await s.execute(
                _t("SELECT 1 FROM discount_usage_log WHERE payment_id = :pid"),
                {"pid": payment_id},
            )
            if dup.fetchone():
                return False
        # Lock balance row
        cur = await s.execute(
            _t("""SELECT balance FROM user_discount_credits
                   WHERE telegram_id = :tg FOR UPDATE"""),
            {"tg": telegram_id},
        )
        row = cur.fetchone()
        balance_before = Decimal(row[0]) if row else Decimal("0")
        if balance_before < discount_used:
            # Not enough balance — log but don't deduct (defensive)
            await s.execute(
                _t("""INSERT INTO discount_usage_log
                     (telegram_id, payment_id, tier_purchased, full_price,
                      discount_used, actual_paid, balance_before, balance_after)
                     VALUES (:tg, :pid, :tier, :full, 0, :paid, :bal, :bal)"""),
                {"tg": telegram_id, "pid": payment_id, "tier": tier_purchased,
                 "full": float(full_price), "paid": float(actual_paid),
                 "bal": float(balance_before)},
            )
            await s.commit()
            return False

        new_balance = balance_before - discount_used
        await s.execute(
            _t("""UPDATE user_discount_credits
                   SET balance = :nb, total_used = total_used + :du, updated_at = NOW()
                   WHERE telegram_id = :tg"""),
            {"nb": float(new_balance), "du": float(discount_used), "tg": telegram_id},
        )
        await s.execute(
            _t("""INSERT INTO discount_usage_log
                 (telegram_id, payment_id, tier_purchased, full_price,
                  discount_used, actual_paid, balance_before, balance_after)
                 VALUES (:tg, :pid, :tier, :full, :du, :paid, :bb, :ba)"""),
            {"tg": telegram_id, "pid": payment_id, "tier": tier_purchased,
             "full": float(full_price), "du": float(discount_used),
             "paid": float(actual_paid), "bb": float(balance_before),
             "ba": float(new_balance)},
        )
        await s.commit()
    return True


__all__ = [
    "DISCOUNT_CAP", "get_balance", "compute_use",
    "reserve_in_context", "clear_context", "apply_usage",
]
