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

    # FIX 2026-06-17 (POST-HOK-SCAM): extract slip\u0027s receiver bank for strict match
    # Slip2Go data: receiver.account.bank.code (e.g. "014"=KTB, "014/SCB"). Reject if mismatch.
    _slip_bank_raw = (acct.get("bank") or {})
    _slip_bank_code = (_slip_bank_raw.get("code") or "").upper().strip()
    _slip_bank_name = (_slip_bank_raw.get("name") or "").upper().strip()
    # Common Thai bank code mappings
    _bank_code_map = {
        "014": "SCB", "SCB": "SCB",
        "006": "KTB", "KTB": "KTB", "KRUNGTHAI": "KTB",
        "002": "BBL", "BBL": "BBL",
        "004": "KBANK", "KBANK": "KBANK", "KASIKORN": "KBANK",
        "025": "BAY", "BAY": "BAY", "KRUNGSRI": "BAY",
        "011": "TTB", "TTB": "TTB",
        "030": "GSB", "GSB": "GSB",
    }
    _slip_bank_norm = _bank_code_map.get(_slip_bank_code) or _bank_code_map.get(_slip_bank_name) or _slip_bank_code

    for a in accounts:
        # MULTI_KEYWORD — name_keyword may be comma-separated; match ANY
        raw_kw = (a["name_keyword"] or "").strip()
        if raw_kw:
            keywords = [k.strip() for k in raw_kw.split(",") if k.strip()]
            name_ok = any(k.lower() in name.lower() for k in keywords)
        else:
            name_ok = False
        if not name_ok:
            continue

        # FIX 2026-06-17 (POST-HOK-SCAM): STRICT SURNAME GUARD
        # Scammer used "ชาคริต ทิ่งวงษา" account (matches "ชาคริต" keyword + PromptPay 1578).
        # Now ALSO require the first 3 chars of owner surname to appear in slip name.
        # owner_name format: "นาย ชาคริต กิ่งวงษา" → surname="กิ่งวงษา" → prefix="กิ่"
        owner_name = a.get("owner_name") or ""
        owner_tokens = [t for t in owner_name.split() if t and t not in ("นาย", "นาง", "นางสาว")]
        if owner_tokens:
            # Find the longest token that looks like a surname (not the first-name keyword)
            surname = None
            for tok in reversed(owner_tokens):
                if len(tok) >= 3:
                    # Skip the token already covered by name_keyword (Thai first name)
                    if not any(k.lower() in tok.lower() for k in keywords if k.strip()):
                        surname = tok
                        break
            if surname:
                # SMART SURNAME CHECK — handle masked names
                # Slip2Go often masks surname for SCB/KBank:
                #   SCB:    "นาย ชาคริต ก" or "นาย ชาคริต ก."  (1 char or 1 char + dot)
                #   KBank:  "นาย ชาคริต ก"
                #   PromptPay (Krungthai): "นาย ชาคริต กิ่งวงษา" (full)
                # Strategy: extract slip surname (last token after first name)
                # - If slip surname >= 3 chars → STRICT check prefix
                # - If slip surname < 3 chars or just dot → TRUST (cant verify, accept)
                import re as _re
                # Strip first name from slip name to find surname tokens
                slip_after_first = name
                for fn_kw in keywords:
                    if fn_kw.lower() in name.lower():
                        idx = name.lower().find(fn_kw.lower())
                        slip_after_first = name[idx + len(fn_kw):].strip()
                        break
                # Remove trailing dots/spaces/asterisks
                slip_surname_part = _re.sub(r"[\\.\\*\\s]+$", "", slip_after_first).strip()
                # Strip leading delimiters
                slip_surname_part = _re.sub(r"^[\\.\\*\\s]+", "", slip_surname_part)

                surname_prefix = surname[:3].lower()
                import logging as _lg
                if len(slip_surname_part) >= 3:
                    # Surname has enough chars — STRICT check
                    if surname_prefix not in slip_surname_part.lower():
                        _lg.getLogger(__name__).warning(
                            "match_receiver: SURNAME MISMATCH (strict) slip_surname=%r expected=%r owner=%r",
                            slip_surname_part, surname_prefix, owner_name
                        )
                        continue
                else:
                    # Surname masked (1-2 chars) — TRUST + rely on bank/proxy check below
                    _lg.getLogger(__name__).info(
                        "match_receiver: surname masked (allowed) slip_surname=%r owner=%r",
                        slip_surname_part, owner_name
                    )
        # FIX 2026-06-17: STRICT BANK CHECK
        # The receiver_account.bank_code (SCB/KTB/etc) must match slip\u0027s bank
        # This kills the entire "scammer makes their own bank account with same PromptPay last4" attack
        _expected_bank = (a.get("bank_code") or "").upper().strip()
        if _expected_bank and _slip_bank_norm and _expected_bank != _slip_bank_norm:
            import logging as _lg
            _lg.getLogger(__name__).warning(
                "match_receiver: BANK MISMATCH - slip_bank=%r expected=%r account_id=%s",
                _slip_bank_norm, _expected_bank, a.get("id")
            )
            continue

        proxy_ok = a["proxy_last4"] and proxy_digits.endswith(a["proxy_last4"])
        bank_ok = a["bank_last5"] and bank_digits.endswith(a["bank_last5"])

        # ── MASK-AWARE fallback: banks mask different digit positions ──
        # K-Plus  'xxx-x-x3964-x' → digits = '3964' → matches bl5[:4]   (tail mask)
        # SCB     'XXX-X-XX964-2' → digits = '9642' → matches bl5[-4:]  (head mask)
        # General: bank_digits (4-5 chars) must appear contiguously inside bl5 (5 chars).
        # name_ok is already required (we 'continue' above when False) → safe.
        if not (proxy_ok or bank_ok):
            bl5 = a.get("bank_last5") or ""
            if bl5 and len(bank_digits) >= 4 and len(bl5) >= 4:
                # 1. Match last 4 digits — tail mask (K-Plus, BBL)
                if len(bank_digits) >= 4 and bank_digits[-4:] == bl5[:4]:
                    bank_ok = True
                # 2. Match first 4 digits of bank_digits with last 4 of bl5 — head mask (SCB)
                elif len(bank_digits) >= 4 and bank_digits[-4:] == bl5[-4:]:
                    bank_ok = True
                # 3. Substring contains either way (catch other mask patterns)
                elif bank_digits in bl5 or bl5 in bank_digits:
                    bank_ok = True
                # 4. 3-digit fallback (very partial mask) — last resort, name must match
                elif len(bank_digits) >= 3 and (
                    bank_digits[-3:] == bl5[:3] or bank_digits[-3:] == bl5[-3:]
                ):
                    bank_ok = True
            pl4 = a.get("proxy_last4") or ""
            if pl4 and len(proxy_digits) >= 3:
                if (proxy_digits[-3:] == pl4[:3]
                    or proxy_digits[-3:] == pl4[-3:]
                    or proxy_digits in pl4 or pl4 in proxy_digits):
                    proxy_ok = True

        if proxy_ok or bank_ok:
            return a
    return None


async def record_payment_received(account_id: int, amount: Decimal, payment_id: int | None = None) -> dict:
    """Update cumulative + check alert threshold (Decimal-safe, single transaction).

    FIX 2026-06-26 (boss audit): added optional payment_id for idempotency.
    Same payment_id can never credit cumulative twice (guard against double-call).
    """
    from decimal import Decimal as _D
    # Ensure Decimal type (prevent float drift)
    amt = amount if isinstance(amount, _D) else _D(str(amount))

    import logging as _lg
    # FIX (audit): marker + increment ใน transaction เดียว + unique index uq_admin_logs_receiver_credit
    # -> นับครั้งเดียวจริง แม้ call ซ้ำ/ชนกัน (ON CONFLICT)
    async with get_session() as session:
        if payment_id is not None:
            _m = await session.execute(text(
                "INSERT INTO admin_logs (admin_id, action, target_type, target_id, details, created_at) "
                "VALUES (0, 'receiver_credit', 'payment', :tid, :d, NOW()) "
                "ON CONFLICT (target_id) WHERE action = 'receiver_credit' DO NOTHING RETURNING id"
            ), {"tid": payment_id, "d": f"account_id={account_id} amount={amt}"})
            if _m.fetchone() is None:
                await session.rollback()
                _lg.getLogger(__name__).info("record_payment_received: payment_id=%s already credited — skip", payment_id)
                return {"alert": False, "skipped": True, "reason": "already_credited"}
        # increment cumulative (transaction เดียวกับ marker -> exactly-once)
        r = await session.execute(text("""
            UPDATE receiver_accounts
            SET cumulative_received = cumulative_received + :amt,
                updated_at = NOW()
            WHERE id = :id
            RETURNING id, owner_name, cumulative_received, alert_threshold, last_alert_at_amount
        """), {"id": account_id, "amt": amt})
        row = r.fetchone()
        if not row:
            await session.commit()
            return {"alert": False}

        cum = _D(str(row.cumulative_received))
        last_alert = _D(str(row.last_alert_at_amount or 0))
        threshold = _D(str(row.alert_threshold or 5000))

        # Milestone math in Decimal (avoid float //)
        new_milestone = (cum // threshold) * threshold

        if new_milestone > last_alert:
            # Update last_alert_at_amount in SAME transaction (race-safe)
            await session.execute(text("""
                UPDATE receiver_accounts SET last_alert_at_amount = :m WHERE id = :id
            """), {"m": new_milestone, "id": account_id})
            await session.commit()
            return {
                "alert": True,
                "account_id": row.id,
                "owner_name": row.owner_name,
                "cumulative": float(cum),
                "milestone": float(new_milestone),
            }
        await session.commit()
        return {"alert": False, "cumulative": float(cum)}


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
