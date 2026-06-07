"""Pure utilities extracted from handlers/payment.py (Round 1 strangler-fig).

These have no side effects on state and are safe to call from anywhere.
Imports below are kept minimal to avoid circular imports.
"""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

# Re-export OCR/TM patterns so slip_handler + extract helpers can import from utils
from bots.sales_bot.payment_util.promo_helpers import AMOUNT_PATTERNS, DATE_PATTERNS, TRUEMONEY_PATTERN  # noqa: E402,F401

# Constants required by slip-parsing functions
NON_SLIP_AD_KEYWORDS = (
    "เครดิตฟรี",
    "เครดิตฟรี",
    "เว็บพนัน",
    "คาสิโน",
    "บาคาร่า",
    "สล็อต",
    "ufa",
    "ufabet",
    "casino",
    "เครดิตฟรี50",
    "เครดิต ฟรี",
    "ปั่นหมุน",
    "ฝากรับ",
    "รับฟรี",
    "โปรโมชันแนะนำ",
    "โปรโมชั่นแนะนำ",
    # # >>> CASINO_BLOCK <<< — added 2026-06-02
    # Casino brand names + slot keywords commonly seen in ads sent as fake slips
    "nova777",
    "nova 777",
    ".online",
    "knockout",
    "dish delights",
    "คอมโบทำเงิน",
    "ทำเงิน",
    "แตกแจกถอน",
    "เบทละ",
    "ก้อนโต",
    "cashback",
    "วงล้อนำโชค",
    "วงล้อ",
    "แนะนำเพื่อน",
    "คลิกเลย",
    "joker",
    "pgslot",
    "pg slot",
    "สล็อต",
    "slotxo",
    "ufabet",
    "ufa",
    "lava",
    "ฝากเครดิต",
    "ฝาก-ถอน",
    "ฝาก ถอน",
    "ฟรีสปิน",
    "free spin",
    "bonus",
    "โบนัส",
    "หวย",
    "บาคาร่า",
    "baccarat",
)


def _check_date_within_24h(text: str) -> bool:
    """Check whether OCR date looks current in Thai time.

    OCR often reads only date from Thai slips, so do not compare against midnight UTC.
    Treat today or yesterday in Thailand as valid.
    """
    thai_tz = timezone(timedelta(hours=7))
    now_th = datetime.now(thai_tz)
    valid_dates = {now_th.date(), (now_th - timedelta(days=1)).date()}

    for pattern in DATE_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        try:
            groups = match.groups()
            if len(groups) != 3:
                continue
            day, month, year = int(groups[0]), int(groups[1]), int(groups[2])
            if year < 100:
                year = 2500 + year if year >= 50 else 2000 + year
            if year > 2400:
                year -= 543
            slip_date = datetime(year, month, day, tzinfo=thai_tz).date()
            return slip_date in valid_dates
        except (ValueError, OverflowError):
            continue
    return True

def _extract_amount_from_ocr(text: str) -> Decimal | None:
    """Extract transfer amount from OCR text."""
    for pattern in AMOUNT_PATTERNS:
        match = pattern.search(text)
        if match:
            amount_str = match.group(1).replace(",", "")
            try:
                return Decimal(amount_str)
            except InvalidOperation:
                continue
    return None

def _looks_like_non_slip_ad(text: str | None) -> bool:
    """Detect gambling/promo creatives that OCR can mistake for payment slips."""
    if not text:
        return False
    normalized = re.sub(r"\s+", "", text.casefold())
    return any(keyword.casefold().replace(" ", "") in normalized for keyword in NON_SLIP_AD_KEYWORDS)


TIER_PRICES: dict[str, Decimal] = {
    "99": Decimal("99"),
    "300": Decimal("300"),
    "500": Decimal("500"),
    "1299": Decimal("1299"),
    "2499": Decimal("2499"),
    "ADD500": Decimal("500"),
}

async def _notify_discord(title: str, details: str, color: int = 0xFFA500, fields: list = None) -> None:
    """[DEPRECATED — use shared.notify.notify(event_key, ...) instead]
    Delegates to shared.discord_alert for now to keep callers working."""
    from shared.discord_alert import notify_discord as _hub_notify
    try:
        title = locals().get("title") or locals().get("event") or "Notification"
        desc  = (locals().get("details") or locals().get("description") or locals().get("body") or locals().get("msg") or "")
        if not isinstance(title, str): title = str(title)
        if not isinstance(desc, str): desc = str(desc)
        return await _hub_notify("payment", title, desc, silent_on_error=True)
    except Exception:
        return False

