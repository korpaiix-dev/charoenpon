"""Slip2Go integration — verify slip via ITMX-backed API.

Boss's receiving account:
  Bank: SCB 4142039642 ชาคริต
  PromptPay: 0988351578 (last 4: 1578)
"""
from __future__ import annotations
import os, logging, asyncio
from decimal import Decimal
from typing import Optional
import httpx

logger = logging.getLogger(__name__)

BASE_URL = os.environ.get("SLIP2GO_BASE_URL", "https://connect.slip2go.com")
SECRET = os.environ.get("SLIP2GO_SECRET", "")
NAME_KEYWORD = os.environ.get("SLIP2GO_RECEIVER_NAME_KEYWORD", "ชาคริต")
PROXY_LAST4 = os.environ.get("SLIP2GO_RECEIVER_PROXY_LAST4", "1578")
BANK_LAST5 = os.environ.get("SLIP2GO_RECEIVER_BANK_LAST5", "39642")


class Slip2GoError(Exception):
    """Slip2Go API call failed or invalid response."""
    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


async def verify_slip_image(image_bytes: bytes, timeout: float = 30.0) -> dict:
    """Send slip image (raw bytes) to Slip2Go multipart endpoint.

    Returns the `data` dict if success. Raises Slip2GoError on failure.
    Response data has: transRef, dateTime, amount, receiver{...}, sender{...}, referenceId
    """
    if not SECRET:
        raise Slip2GoError("NO_SECRET", "SLIP2GO_SECRET not configured")
    url = f"{BASE_URL}/api/verify-slip/qr-image/info"
    headers = {"Authorization": f"Bearer {SECRET}"}
    files = {"file": ("slip.jpg", image_bytes, "image/jpeg")}
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.post(url, headers=headers, files=files)
    # Bug #10 — catch all httpx transport errors, not just timeout+network
    except httpx.RequestError as exc:
        raise Slip2GoError("NETWORK", f"network error: {exc}")
    try:
        body = r.json()
    except Exception:
        raise Slip2GoError("BAD_RESPONSE", f"non-json response: {r.text[:200]}")
    code = body.get("code", "")
    msg = body.get("message", "")
    # Slip2Go may return 201 — rely on code "200000" alone
    if str(code) == "200000":
        return body.get("data", {}) or {}
    # Common error codes: 400002 file incorrect, 400005 base64 invalid, ...
    raise Slip2GoError(str(code), msg or r.text[:200])


async def get_account_info(timeout: float = 10.0) -> dict:
    """Return current account info — used for token-balance alert."""
    if not SECRET:
        raise Slip2GoError("NO_SECRET", "SLIP2GO_SECRET not configured")
    url = f"{BASE_URL}/api/account/info"
    headers = {"Authorization": f"Bearer {SECRET}"}
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.get(url, headers=headers)
    body = r.json()
    # # Slip2Go uses code "200001" for /api/account/info (verify endpoints use 200000)
    if str(body.get("code", "")) == "200001":
        return body.get("data", {}) or {}
    raise Slip2GoError(str(body.get("code", "?")), body.get("message", "?"))


def receiver_is_boss(data: dict) -> tuple[bool, str]:
    """# >>> FIX_RECEIVER_AND <<< — Tightened from OR→AND-mixed.

    Requires: name contains ชาคริต AND (proxy ends with last4 OR bank ends with last5).
    Prevents fraud where attacker uses any other "ชาคริต" recipient.
    """
    receiver = data.get("receiver", {}) or {}
    acct = receiver.get("account", {}) or {}
    name = (acct.get("name") or "")
    bank_account = ((acct.get("bank") or {}).get("account") or "")
    proxy = ((acct.get("proxy") or {}).get("account") or "")
    proxy_digits = "".join(c for c in proxy if c.isdigit())
    bank_digits = "".join(c for c in bank_account if c.isdigit())

    name_ok = NAME_KEYWORD in name
    proxy_ok = PROXY_LAST4 and proxy_digits.endswith(PROXY_LAST4)
    bank_ok = BANK_LAST5 and bank_digits.endswith(BANK_LAST5)

    if name_ok and (proxy_ok or bank_ok):
        which = "proxy" if proxy_ok else "bank"
        return True, f"name+{which}:{name}/{proxy if proxy_ok else bank_account}"
    return False, (
        f"INSUFFICIENT MATCH name_ok={name_ok} proxy_ok={proxy_ok} bank_ok={bank_ok} "
        f"name='{name}' proxy='{proxy}' bank='{bank_account}'"
    )


# Map: amount → (tier, label, is_promo)
# Used for Smart Match — customer pays any of these amounts → auto-assign tier.
def amount_to_tier(amount: Decimal) -> Optional[tuple[str, str, bool]]:
    """Return (tier_callback_value, label, is_promo) or None if amount doesn't match any tier.

    Smart Match accepts both base prices and promo prices.
    """
    # Import lazily to avoid circular
    from shared.endmonth_vip_promo import (
        is_endmonth_vip_promo_active, is_may_combo_promo_active,
        PROMO_MAY_END_TH, PROMO_END_TH, TH_TZ,
    )
    from datetime import datetime as _dt, timedelta as _td2
    def _may_or_recent():
        now = _dt.now(TH_TZ)
        # 24h grace after promo end — admin can manually handle disputes
        return now < PROMO_MAY_END_TH + _td2(hours=24)
    def _endmonth_or_recent():
        now = _dt.now(TH_TZ)
        return now < PROMO_END_TH + _td2(hours=24)
    amt = int(amount)  # ignore satang
    # Exact match table: amount → (price callback string, descriptive label, is_promo_branch)
    # The price callback string is what admin_bot's tier_map can resolve to a Package.tier
    # TIER_99 removed 2026-06-01 — no longer offered
    # if amt == 99: ...
    if amt == 199:   return ("199", "Flash Sale", False)
    if amt == 300:   return ("300", "VIP 30 วัน", False)
    if amt == 500:   return ("500", "OnlyFans+VIP 30 วัน", False)
    if amt == 1299:  return ("1299", "GOD MODE 90 วัน", False)
    if amt == 2499:  return ("2499", "GOD MODE ถาวร", False)
    # Promo prices — only valid during active promo
    if amt == 200 and _endmonth_or_recent():   return ("200", "VIP โปร (300→200)", True)
    if amt == 2000 and _endmonth_or_recent():  return ("2000", "GOD โปร (2499→2000)", True)
    if amt == 349 and _may_or_recent():      return ("349", "OF โปร (500→349)", True)
    if amt == 999 and _may_or_recent():      return ("999", "3M โปร (1299→999)", True)
    # COMEBACK_PROMO_PRICES — always-on (per-user validation in payment.py)
    if amt == 180:   return ("180", "Comeback ลด 40% (300→180)", True)
    if amt == 210:   return ("210", "Comeback ลด 30% (300→210)", True)
    # LUCKY_6.6 — only valid on 6 มิ.ย. 2026 BKK
    def _lucky_6_or_recent():
        from datetime import datetime as _dt, timedelta as _td, timezone as _tz
        now = _dt.now(_tz(_td(hours=7)))
        return now.year == 2026 and now.month == 6 and now.day == 6
    if amt == 166 and _lucky_6_or_recent():   return ("166", "Lucky 6.6 VIP", True)
    if amt == 266 and _lucky_6_or_recent():   return ("266", "Lucky 6.6 OF", True)
    if amt == 666 and _lucky_6_or_recent():   return ("666", "Lucky 6.6 GOD3M", True)
    if amt == 2266 and _lucky_6_or_recent():  return ("2266", "Lucky 6.6 GOD ถาวร", True)
    return None


# # >>> RECEIVER_POOL_DYNAMIC <<<
# New pool-aware receiver match — use this from payment handler.
# Returns (ok, reason, matched_account_dict or None).
async def receiver_match_pool(data: dict) -> tuple[bool, str, dict | None]:
    """Match slip receiver against ANY enabled account in receiver_accounts table."""
    try:
        from shared.receiver_pool import list_enabled, match_receiver
    except Exception as e:
        return False, f"receiver_pool import fail: {e}", None
    accounts = await list_enabled()
    if not accounts:
        return False, "NO_ENABLED_ACCOUNTS in pool", None
    matched = match_receiver(data, accounts)
    if matched:
        return True, f"matched id={matched['id']} ({matched['owner_name']})", matched
    # Build informative rejection reason
    receiver = (data.get("receiver") or {})
    acct = (receiver.get("account") or {})
    name = acct.get("name") or ""
    bank = ((acct.get("bank") or {}).get("account") or "")
    proxy = ((acct.get("proxy") or {}).get("account") or "")
    return False, f"INSUFFICIENT MATCH name='{name}' proxy='{proxy}' bank='{bank}'", None
