"""Lao Lotto API client (apilotto.com).

Fetch latest Lao lottery results. Use `fetch_latest_2digit_bottom()`
to get the winning number (00-99) for ห้องมีคนชัก draw.

Each Monday 20:30 BKK = Lao Lotto draw time.
Bot pulls at 20:35, announces 21:00.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

_BASE_URL = os.environ.get("APILOTTO_BASE_URL", "https://api.apilotto.com/api/v1")
_API_KEY = os.environ.get("APILOTTO_API_KEY", "")


async def fetch_lao_lotto_latest(timeout: float = 10.0) -> Optional[dict]:
    """Fetch latest Lao lottery results.

    Returns:
        dict with keys:
          - laolast4: '5245'
          - laolast3: '245'
          - laolast2: {'top': '45', 'bottom': '52'}
          - laopattana: list of 5 numbers
          - animalname: e.g. 'สิงโต'
          - date: '9 มิถุนายน 2569'
        Returns None on error or invalid response.
    """
    if not _API_KEY:
        logger.error("APILOTTO_API_KEY not set")
        return None
    url = f"{_BASE_URL}/laolotto"
    headers = {"x-api-key": _API_KEY}
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.get(url, headers=headers)
        r.raise_for_status()
        data = r.json()
        if data.get("status") != 1:
            logger.warning("apilotto returned non-success: %s", data)
            return None
        payload = data.get("data")
        if not payload or payload.get("laolast2", {}).get("bottom") in (None, "xx"):
            logger.warning("apilotto data still pending (xx): %s", payload)
            return None
        return payload
    except Exception as exc:
        logger.error("apilotto fetch failed: %s", exc)
        return None


async def fetch_latest_2digit_bottom() -> Optional[str]:
    """Get just the 2-digit bottom number ('00'-'99') of latest Lao lottery."""
    data = await fetch_lao_lotto_latest()
    if not data:
        return None
    bottom = data.get("laolast2", {}).get("bottom")
    if not bottom or len(bottom) != 2 or not bottom.isdigit():
        return None
    return bottom


__all__ = ["fetch_lao_lotto_latest", "fetch_latest_2digit_bottom"]
