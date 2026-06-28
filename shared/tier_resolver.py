"""Tier resolver — unify FALLBACK chain for slip handler.

Replaces 3 ad-hoc blocks in payment.py:
  - GACHA tier override (amount matches GACHA tier)
  - LAYER 0 INTENT FALLBACK (Mini App purchase ticket)
  - PRAE-CHAT FALLBACK (Prae conversation without button press)

All three try to recover a `selected_tier` and clear `missing_context`
when the customer didn't press a tier button before sending slip.

This helper returns a single dataclass with the resolution source +
the recovered tier. Caller decides what to do.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Any
import logging

logger = logging.getLogger(__name__)


@dataclass
class TierResolution:
    tier: Optional[str]            # short tier e.g. "300", "GACHA_3"
    source: str                    # "intent" | "gacha_amount" | "prae_amount" | "none"
    missing_context: bool          # whether handler should still treat as missing
    intent: Optional[dict] = None  # purchase_intent row if matched
    expected_price: Optional[Any] = None  # Decimal — for the resolved tier


async def resolve_tier(
    user_telegram_id: int,
    current_selected_tier: Optional[str],
    current_missing_context: bool,
    slip2go_data: Optional[dict],
    context_user_data: dict,
    get_effective_price,
) -> TierResolution:
    """Try every fallback in priority order: INTENT → GACHA-amount → PRAE-amount.

    Args:
        user_telegram_id: customer's tg_id
        current_selected_tier: what handler currently thinks (may be None)
        current_missing_context: True if handler hasn't resolved yet
        slip2go_data: Slip2Go API result (may be None if failed)
        context_user_data: PTB context.user_data (mutated on match)
        get_effective_price: async callable(tier, ctx_user_data) -> Decimal

    Returns:
        TierResolution(tier, source, missing_context, intent?, expected_price?)
    """
    # If handler already has a tier, no fallback needed
    if not current_missing_context and current_selected_tier:
        return TierResolution(
            tier=current_selected_tier,
            source="explicit",
            missing_context=False,
        )

    # PRIORITY 1: Purchase Intent (Mini App ticket) — most accurate
    try:
        from shared.purchase_intent import find_latest_pending
        intent = await find_latest_pending(user_telegram_id)
        if intent:
            tier_full = (intent.get("tier") or "").strip()
            tier_short = tier_full.replace("TIER_", "") if tier_full else ""
            if tier_short:
                expected = await get_effective_price(tier_short, context_user_data)
                if expected:
                    context_user_data["selected_tier"] = tier_short
                    logger.info(
                        "TIER_RESOLVE(intent): tg=%s intent_id=%s tier=%s",
                        user_telegram_id, intent.get("id"), tier_short,
                    )
                    return TierResolution(
                        tier=tier_short,
                        source="intent",
                        missing_context=False,
                        intent=intent,
                        expected_price=expected,
                    )
    except Exception as exc:
        logger.warning("TIER_RESOLVE(intent) failed: %s", exc)

    # PRIORITY 2: Slip2Go amount → tier (covers GACHA + Prae-chat in one)
    if slip2go_data:
        try:
            from shared.pricing import amount_to_tier
            amt = float(slip2go_data.get("amount") or 0)
            tinfo = amount_to_tier(int(amt))
            if tinfo:
                tier_short, label, is_promo = tinfo
                # If GACHA tier, only set if current is not GACHA already
                is_gacha = tier_short.startswith("GACHA_")
                cur_is_gacha = (current_selected_tier or "").startswith("GACHA_")
                if is_gacha and not cur_is_gacha:
                    context_user_data["selected_tier"] = tier_short
                    logger.info(
                        "TIER_RESOLVE(gacha_amount): tg=%s amt=%s -> %s",
                        user_telegram_id, amt, tier_short,
                    )
                    return TierResolution(
                        tier=tier_short,
                        source="gacha_amount",
                        missing_context=False,
                    )
                # Non-GACHA — normal amount-based resolution
                if not is_gacha:
                    expected = await get_effective_price(tier_short, context_user_data)
                    if expected:
                        context_user_data["selected_tier"] = tier_short
                        logger.info(
                            "TIER_RESOLVE(prae_amount): tg=%s amt=%s -> %s",
                            user_telegram_id, amt, tier_short,
                        )
                        return TierResolution(
                            tier=tier_short,
                            source="prae_amount",
                            missing_context=False,
                            expected_price=expected,
                        )
        except Exception as exc:
            logger.warning("TIER_RESOLVE(amount) failed: %s", exc)

    # No fallback worked — return original state
    return TierResolution(
        tier=current_selected_tier,
        source="none",
        missing_context=current_missing_context,
    )
