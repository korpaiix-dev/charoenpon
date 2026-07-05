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
from time import monotonic as _monotonic

# Circuit breaker for HTTP 429 — pause 5 min after rate-limit detected
_SLIP2GO_RATE_LIMIT_UNTIL: dict = {"until": 0.0}
_SLIP2GO_RATE_LIMIT_PAUSE_SEC = 300  # 5 นาที


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
    """Send slip image to Slip2Go with retry on transient errors.

    2026-06-16 FIX: retry 2x with backoff on network/5xx (was one-shot fail).
    Returns the `data` dict if success. Raises Slip2GoError on permanent failure.
    """
    if not SECRET:
        raise Slip2GoError("NO_SECRET", "SLIP2GO_SECRET not configured")
    url = f"{BASE_URL}/api/verify-slip/qr-image/info"
    headers = {"Authorization": f"Bearer {SECRET}"}
    files = {"file": ("slip.jpg", image_bytes, "image/jpeg")}

    import asyncio as _asyncio

    # FIX 2026-06-21: Circuit breaker — ถ้าเคย 429 ใน 5 นาทีล่าสุด, fail fast
    remaining = _SLIP2GO_RATE_LIMIT_UNTIL["until"] - _monotonic()
    if remaining > 0:
        raise Slip2GoError("RATE_LIMITED", f"slip2go circuit open, {int(remaining)}s remaining")

    last_err: Exception | None = None
    for attempt in range(3):
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                r = await client.post(url, headers=headers, files=files)
            # FIX 2026-06-21: HTTP 429 → ไม่ retry + open circuit + alert
            if r.status_code == 429:
                _SLIP2GO_RATE_LIMIT_UNTIL["until"] = _monotonic() + _SLIP2GO_RATE_LIMIT_PAUSE_SEC
                # Alert ห้อง Report
                try:
                    from shared.admin_alert import notify_admin_report
                    await notify_admin_report(
                        f"⚠️ <b>Slip2Go Rate Limit (HTTP 429)</b>\n"
                        f"━━━━━━━━━━━━\n"
                        f"⏸ pause {_SLIP2GO_RATE_LIMIT_PAUSE_SEC // 60} นาที\n"
                        f"📊 response: {r.text[:200]}\n\n"
                        f"<i>ระบบจะ fallback เป็น Layer 2 (Gemini Vision) จนกว่าจะหายต้อง</i>",
                        parse_mode="HTML",
                    )
                except Exception:
                    pass
                raise Slip2GoError("RATE_LIMITED", f"http 429: {r.text[:100]}")
            # Treat 5xx as transient
            if 500 <= r.status_code < 600 and attempt < 2:
                last_err = Slip2GoError("HTTP_5XX", f"http {r.status_code}: {r.text[:100]}")
                delay = 2 ** (attempt + 1)
                await _asyncio.sleep(delay)
                continue
            try:
                body = r.json()
            except Exception:
                raise Slip2GoError("BAD_RESPONSE", f"non-json response: {r.text[:200]}")
            code = body.get("code", "")
            msg = body.get("message", "")
            if str(code) == "200000":
                return body.get("data", {}) or {}
            # Permanent business-logic error — no retry
            raise Slip2GoError(str(code), msg or r.text[:200])
        except httpx.RequestError as exc:
            last_err = Slip2GoError("NETWORK", f"network error: {exc}")
            if attempt < 2:
                delay = 2 ** (attempt + 1)  # 2s, 4s
                await _asyncio.sleep(delay)
                continue
            raise last_err
    # Exhausted retries
    raise last_err or Slip2GoError("UNKNOWN", "retries exhausted")


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


# ─── Phase 2: delegate amount_to_tier to shared.pricing (single source of truth) ─
def amount_to_tier(amount):
    from shared.pricing import amount_to_tier as _hub_amount_to_tier
    return _hub_amount_to_tier(amount)


def _check_slip_age(slip_data: dict, max_days: int = 7) -> None:
    """Reject slips older than max_days. Raises Slip2GoError on failure."""
    from datetime import datetime, timedelta, timezone
    try:
        trans_date_raw = slip_data.get("transDate") or slip_data.get("date") or ""
        if not trans_date_raw:
            return  # no date info — let other guards handle
        # Slip2Go returns ISO format usually
        for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                td = datetime.strptime(trans_date_raw[:19], fmt)
                if td.tzinfo is None:
                    td = td.replace(tzinfo=timezone.utc)
                age = datetime.now(timezone.utc) - td
                if age > timedelta(days=max_days):
                    raise Slip2GoError("TRANS_TOO_OLD", f"slip is {age.days} days old (max {max_days})")
                return
            except ValueError:
                continue
    except Slip2GoError:
        raise
    except Exception:
        pass  # silent — don't break verification on parse error

