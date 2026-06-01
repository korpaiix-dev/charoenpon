"""Receiver account pool — random pick + match-from-slip + cumulative tracking."""
from __future__ import annotations
import random
from decimal import Decimal
from typing import Optional, Sequence
from sqlalchemy import text
from shared.database import get_session


async def list_enabled() -> list[dict]:
    """Return enabled accounts as list of dicts."""
    async with get_session() as session:
        r = await session.execute(text("""
            SELECT id, account_no, bank_code, bank_name_th, owner_name, name_keyword,
                   bank_last5, promptpay_number, proxy_last4, qr_url, weight,
                   cumulative_received, alert_threshold, last_alert_at_amount
            FROM receiver_accounts WHERE enabled=true ORDER BY id
        """))
        return [dict(row._mapping) for row in r.all()]


async def pick_random() -> Optional[dict]:
    """Pick a random enabled account weighted by `weight`."""
    accounts = await list_enabled()
    if not accounts:
        return None
    total = sum(a["weight"] for a in accounts) or 1
    r = random.random() * total
    cum = 0.0
    for a in accounts:
        cum += a["weight"]
        if r <= cum:
            return a
    return accounts[-1]


def match_receiver(slip_data: dict, accounts: Sequence[dict]) -> Optional[dict]:
    """Find which receiver account in pool matches Slip2Go receiver info.

    AND-logic: name_keyword in receiver_name AND
               (proxy ends with proxy_last4 OR bank ends with bank_last5)
    """
    receiver = (slip_data.get("receiver") or {})
    acct = (receiver.get("account") or {})
    name = acct.get("name") or ""
    bank_account = ((acct.get("bank") or {}).get("account") or "")
    proxy = ((acct.get("proxy") or {}).get("account") or "")
    proxy_digits = "".join(c for c in proxy if c.isdigit())
    bank_digits = "".join(c for c in bank_account if c.isdigit())

    for a in accounts:
        name_ok = a["name_keyword"] in name if a["name_keyword"] else False
        if not name_ok:
            continue
        proxy_ok = a["proxy_last4"] and proxy_digits.endswith(a["proxy_last4"])
        bank_ok = a["bank_last5"] and bank_digits.endswith(a["bank_last5"])
        if proxy_ok or bank_ok:
            return a
    return None


async def record_payment_received(account_id: int, amount: Decimal) -> dict:
    """Update cumulative + check alert threshold. Returns dict with alert info."""
    async with get_session() as session:
        # Atomically update + return new cumulative
        r = await session.execute(text("""
            UPDATE receiver_accounts
            SET cumulative_received = cumulative_received + :amt,
                updated_at = NOW()
            WHERE id = :id
            RETURNING id, owner_name, cumulative_received, alert_threshold, last_alert_at_amount
        """), {"id": account_id, "amt": float(amount)})
        row = r.fetchone()
        await session.commit()
        if not row:
            return {"alert": False}
        cum = float(row.cumulative_received)
        last_alert = float(row.last_alert_at_amount)
        threshold = float(row.alert_threshold) or 5000.0
        # Compute next milestone passed
        new_milestone = int(cum // threshold) * threshold
        if new_milestone > last_alert:
            # Update last_alert_at_amount in same transaction
            async with get_session() as s2:
                await s2.execute(text("""
                    UPDATE receiver_accounts SET last_alert_at_amount = :m WHERE id = :id
                """), {"m": new_milestone, "id": account_id})
                await s2.commit()
            return {
                "alert": True,
                "account_id": row.id,
                "owner_name": row.owner_name,
                "cumulative": cum,
                "milestone": new_milestone,
            }
        return {"alert": False, "cumulative": cum}


async def reset_account(account_id: int) -> bool:
    """Reset cumulative_received + last_alert_at_amount to 0 (admin command)."""
    async with get_session() as session:
        r = await session.execute(text("""
            UPDATE receiver_accounts
            SET cumulative_received = 0, last_alert_at_amount = 0, updated_at = NOW()
            WHERE id = :id RETURNING id
        """), {"id": account_id})
        ok = r.fetchone() is not None
        await session.commit()
        return ok
