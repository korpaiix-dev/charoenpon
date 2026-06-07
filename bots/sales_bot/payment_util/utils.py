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
    """Send payment notification to Discord #alerts as embed."""
    discord_token = os.environ.get("DISCORD_BOT_TOKEN", "")
    discord_ch = os.environ.get("DISCORD_CH_ALERTS", "")
    if not discord_token or not discord_ch:
        return
    try:
        now_th = datetime.now(timezone(timedelta(hours=7)))
        embed = {
            "title": title,
            "description": details,
            "color": color,
            "footer": {"text": f"⊙ ระบบตรวจสลิป เจริญพร | วันนี้ เวลา {now_th.strftime('%H:%M')}"},
        }
        if fields:
            embed["fields"] = fields
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(
                f"https://discord.com/api/v10/channels/{discord_ch}/messages",
                headers={"Authorization": f"Bot {discord_token}", "Content-Type": "application/json"},
                json={"embeds": [embed]},
            )
    except Exception as exc:
        logger.error("Discord notification failed: %s", exc)
