"""Promo + TrueMoney helpers extracted from handlers/payment.py (Round 3 retry).
Read-only DB lookups + external API calls.
"""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Optional

import httpx
from sqlalchemy import select
from shared.database import get_session
from shared.models import ComebackDmLog

logger = logging.getLogger(__name__)

TRUEMONEY_PATTERN = re.compile(
    r'https?://gift\.truemoney\.com/campaign/?\?v=([a-zA-Z0-9]+)', re.IGNORECASE
)

async def _get_active_promo_for_user(telegram_id: int) -> dict | None:
    """Look up active (unexpired, unpurchased) promo in comeback_dm_log for a user."""
    from shared.models import ComebackDmLog
    async with get_session() as session:
        result = await session.execute(
            select(ComebackDmLog).where(
                ComebackDmLog.telegram_id == telegram_id,
                ComebackDmLog.purchased == False,  # noqa: E712
            ).order_by(ComebackDmLog.sent_at.desc()).limit(1)
        )
        dm_log = result.scalar_one_or_none()

    if not dm_log:
        return None

    from datetime import timedelta
    expiry = dm_log.sent_at + timedelta(hours=48)
    if datetime.utcnow() > expiry:
        return None

    # Determine promo source label
    variant = getattr(dm_log, "variant", "") or ""
    dm_round = dm_log.round
    if dm_round >= 200:
        source = "Retention"
    elif dm_round >= 100:
        source = "Lead Followup"
    else:
        source = "Comeback"

    from bots.sales_bot.comeback_dm import _calculate_discounted_price
    discounted_price = _calculate_discounted_price(dm_log.discount_pct)

    return {
        "source": source,
        "discount_pct": dm_log.discount_pct,
        "discounted_price": discounted_price,
        "promo_code": dm_log.promo_code,
    }


TRUEMONEY_PATTERN = re.compile(
    r"https?://gift\.truemoney\.com/campaign/??\?v=([a-zA-Z0-9]+)", re.IGNORECASE
)

# OCR patterns to extract amount from slip
AMOUNT_PATTERNS = [
    re.compile(r"(?:จำนวนเงิน|จำนวน|amount|ยอด|total|ยอดเงิน)[:\s]*([0-9,]+(?:\.\d{2})?)\s*(?:บาท|baht|thb)?", re.IGNORECASE),
    re.compile(r"([0-9,]+(?:\.\d{2})?)\s*(?:บาท|baht|thb)", re.IGNORECASE),
    re.compile(r"THB\s*([0-9,]+(?:\.\d{2})?)", re.IGNORECASE),
    re.compile(r"([0-9,]+\.\d{2})\s*THB", re.IGNORECASE),
]

DATE_PATTERNS = [
    re.compile(r"(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{2,4})"),
    re.compile(r"(\d{1,2})\s+(?:ม\.ค\.|ก\.พ\.|มี\.ค\.|เม\.ย\.|พ\.ค\.|มิ\.ย\.|ก\.ค\.|ส\.ค\.|ก\.ย\.|ต\.ค\.|พ\.ย\.|ธ\.ค\.)\s+(\d{2,4})"),
]

async def _verify_truemoney_link(link: str) -> dict:
    """Redeem TrueMoney gift link — เติมเงินเข้าวอลเล็ทจริง.

    Returns dict with: valid (bool), amount (Decimal|None), voucher_id (str), error (str).
    """
    match = TRUEMONEY_PATTERN.search(link)
    if not match:
        return {"valid": False, "amount": None, "voucher_id": "", "error": "invalid_link"}

    voucher_id = match.group(1)
    my_wallet = os.environ.get("MY_WALLET", "").strip()
    if not my_wallet:
        return {"valid": False, "amount": None, "voucher_id": voucher_id, "error": "no_wallet"}

    headers = {
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Linux; Android 13; SM-S918B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Mobile Safari/537.36",
        "Accept": "application/json",
        "Origin": "https://gift.truemoney.com",
        "Referer": f"https://gift.truemoney.com/campaign/?v={voucher_id}",
        "Accept-Language": "th-TH,th;q=0.9,en;q=0.8",
    }
    payload = {"mobile": my_wallet, "voucher_hash": voucher_id}

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"https://gift.truemoney.com/campaign/vouchers/{voucher_id}/redeem",
                json=payload,
                headers=headers,
            )

            if resp.status_code == 403:
                return {"valid": False, "amount": None, "voucher_id": voucher_id, "error": "forbidden"}

            try:
                data = resp.json()
            except Exception:
                return {"valid": False, "amount": None, "voucher_id": voucher_id, "error": "invalid_response"}

            status_code = data.get("status", {}).get("code", "")

            if status_code == "SUCCESS":
                amount_str = data.get("data", {}).get("my_ticket", {}).get("amount_baht", "0")
                try:
                    amount = Decimal(str(int(float(amount_str))))
                except (InvalidOperation, ValueError):
                    amount = None
                return {"valid": True, "amount": amount, "voucher_id": voucher_id, "error": ""}

            elif status_code == "CANNOT_GET_OWN_VOUCHER":
                return {"valid": False, "amount": None, "voucher_id": voucher_id, "error": "own_voucher"}
            elif status_code == "TARGET_USER_NOT_FOUND":
                return {"valid": False, "amount": None, "voucher_id": voucher_id, "error": "wallet_not_found"}
            else:
                return {"valid": False, "amount": None, "voucher_id": voucher_id, "error": f"api_{status_code}"}

    except Exception as exc:
        logger.warning("TrueMoney redeem failed: %s", exc)
        return {"valid": False, "amount": None, "voucher_id": voucher_id, "error": "timeout"}
