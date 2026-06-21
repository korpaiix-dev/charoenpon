"""Approval compat shim.

REFACTOR 2026-06-18: This module USED to contain ~200 lines of approval logic
(create subscription, expire old, birthday bonus, shaker, invite links, etc.).
That logic now lives in `shared.payment_approval.apply_payment_approval`.

This file keeps the OLD function signature `_approve_payment(payment, tg, bot)`
so existing callers (sales handler/payment.py, truemoney_handler, retry_worker,
discord_bot) keep working without code change. Internally we delegate.

NEW callers (slip_review, payment_actions admin) should call
`apply_payment_approval` directly to access the full ApprovalResult.
"""
from __future__ import annotations

import logging
from decimal import Decimal
from typing import Optional

from shared.models import Payment

logger = logging.getLogger(__name__)


async def _approve_payment(
    payment: Payment,
    user_telegram_id: int,
    bot,                                          # kept for backwards compat, unused
    *,
    source: Optional[str] = None,
    matched_receiver_account_id: Optional[int] = None,
    comeback_dm_log_id: Optional[int] = None,
    discount_credit_used: Optional[Decimal] = None,
) -> list[str]:
    """Approve payment via the canonical service.

    Returns a list[str] in the legacy format:
      Position 0 (if SHAKER): "🎫 เลขลุ้น: <num1> <num2>..."
      Others: "• <group_title>: <invite_url>"

    Callers can additionally pass `source` to attribute the path in audit logs.
    If omitted, defaults to LEGACY_SHIM. All callers SHOULD pass a specific
    source so we can track migration progress.
    """
    from shared.payment_approval import (
        apply_payment_approval, ApprovalInput, ApprovalSource,
    )

    # Source attribution — caller MUST pass source explicitly (FIX 2026-06-21)
    if not source:
        import logging
        logging.getLogger(__name__).error(
            'CALLER BUG: _approve_payment called without source param! '
            'This will mis-attribute audit logs. Caller stack will show this.',
            stack_info=True,
        )
    src_enum: ApprovalSource = ApprovalSource.MANUAL_BACKFILL
    if source:
        for s in ApprovalSource:
            if s.value == source or s.name == source:
                src_enum = s
                break

    # Payment.method may be enum (PaymentMethod.SLIP) or string
    method_str = "SLIP"
    try:
        if hasattr(payment, "method") and payment.method:
            mv = getattr(payment.method, "value", str(payment.method))
            if mv in ("SLIP", "PROMPTPAY", "TRUEWALLET"):
                method_str = mv
    except Exception:
        pass

    result = await apply_payment_approval(ApprovalInput(
        user_id=payment.user_id,
        telegram_id=user_telegram_id,
        source=src_enum,
        amount_paid=Decimal(str(payment.amount)),
        explicit_package_id=payment.package_id,
        payment_id=payment.id,
        slip_trans_ref=getattr(payment, "slip_trans_ref", None),
        slip_hash=getattr(payment, "slip_hash", None),
        sender_name=getattr(payment, "sender_name", None),
        sender_bank_name=getattr(payment, "sender_bank_name", None),
        sender_bank_account=getattr(payment, "sender_bank_account", None),
        slip_file_id=getattr(payment, "slip_file_id", None),
        method=method_str,
        matched_receiver_account_id=matched_receiver_account_id,
        comeback_dm_log_id=comeback_dm_log_id,
        discount_credit_used=discount_credit_used or Decimal("0"),
        skip_dup_check=True,                      # row already exists / caller dedup-ed
        skip_sender_ring=True,                    # caller already filtered
        skip_dm=True,                             # legacy callers do their own DM
    ))

    if not result.success:
        logger.error("_approve_payment shim: %s (%s)", result.error, result.error_details)
        return []

    # Format legacy list[str]
    out: list[str] = []
    if result.shaker_numbers:
        out.append(f"🎫 เลขลุ้น: {' '.join(result.shaker_numbers)}")
    for link in result.invite_links:
        out.append(f"• {link.title}: {link.url}")
    return out


# Re-exports kept for callers that imported helpers from here:
WELCOME_REFERRAL_DM = (
    '✅ สมัครสำเร็จ! ชวนเพื่อน 1 คน ได้ VIP ฟรี 7 วัน\n'
    '\n'
    '👉 /invite\n'
    '\n'
    'ข้อความชวนเพื่อน (คัดลอกส่งได้เลย):\n'
    '<code>มา VIP เจริญพร กัน! คลิปเต็มไม่เบลอทุกวัน 10,000+ คลิป สมัครที่ @NamwarnJarern_bot</code>'
)


__all__ = ["_approve_payment", "WELCOME_REFERRAL_DM"]
