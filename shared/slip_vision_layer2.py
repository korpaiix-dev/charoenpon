"""Slip2Go Layer 2 — Gemini Vision fallback verifier.

When Slip2Go rejects a slip (wrong-receiver, no-tier, mismatch), we ask Gemini
Vision to read the slip directly. If receiver name contains "ชาคริต" or
"ณธกฤต" AND amount matches a known tier (±2 baht), we auto-approve.

This catches cases where:
- K-Plus mask digits (xxx-x-x3964-x) — Slip2Go fail name match
- Receiver displayed in non-standard format
- Slip2Go down / timeout
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)


# Valid receiver names (must contain at least ONE)
VALID_RECEIVER_KEYWORDS = ["ชาคริต", "CHAKHRIT", "Chakhrit", "ณธกฤต", "NATHAKRIT", "Nathakrit"]

# All known tier amounts the system accepts (from shared/pricing.py)
# Layer 2 verifies amount falls in this set — prevents random amounts from auto-approving
KNOWN_AMOUNTS = {
    # Base
    100, 199, 300, 500, 1299, 2499,
    # Promo retention
    269, 255, 240, 450, 425, 400, 1169, 1104, 1039, 2249, 2124, 1998,
    # Comeback
    180, 210,
    # Lucky 6.6
    166, 266, 666, 2266,
    # Mid-month flash
    349, 999,
    # End-month VIP
    200, 2000,
    # Gachapon bundles
    99, 270, 890,
}


PROMPT = """ดูสลิปโอนเงินนี้แล้วตอบเป็น JSON เท่านั้น (ห้ามมี text อื่น):

{
  "amount": <จำนวนเงิน ทศนิยม 2 ตำแหน่ง เช่น 100.00>,
  "receiver_name": "<ชื่อผู้รับเงิน ตามที่เห็น>",
  "receiver_bank_last4": "<4 ตัวสุดท้ายของเลขบัญชีปลายทาง หรือ '' ถ้าไม่ชัด>",
  "sender_name": "<ชื่อผู้ส่ง>",
  "trans_ref": "<เลขอ้างอิง/transaction ID>",
  "datetime": "<วันที่และเวลา>",
  "looks_real": <true/false — สลิปดูจริงไหม>,
  "suspicious_reason": "<ถ้าน่าสงสัย บอกเหตุผล ไม่งั้นใส่ ''>"
}

กฎสำคัญ:
- ตอบเป็น JSON เท่านั้น ห้ามมี markdown code fence
- amount ต้องเป็นตัวเลขจริง ไม่ใช่ string
- ถ้าอ่านไม่ชัดให้ใส่ค่า null
"""


async def verify_slip_with_vision(image_b64: str) -> dict:
    """Call Gemini Vision to read slip; return parsed dict.

    Returns: {"ok": bool, "data": dict | None, "error": str | None}
    """
    from shared.api_cost_tracker import call_openrouter

    try:
        resp = await call_openrouter(
            model="google/gemini-2.5-flash",
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": PROMPT},
                    {"type": "image_url",
                     "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}},
                ],
            }],
            caller="sales_bot/slip_layer2",
            max_tokens=400,
            temperature=0.1,
        )
        raw = resp["choices"][0]["message"]["content"].strip()
        # Strip code fence if present
        if raw.startswith("```"):
            nl = raw.find("\n")
            if nl > 0:
                raw = raw[nl + 1:]
            if raw.rstrip().endswith("```"):
                raw = raw.rstrip()[:-3].rstrip()
        try:
            parsed = json.loads(raw, strict=False)
            return {"ok": True, "data": parsed, "error": None}
        except json.JSONDecodeError as e:
            # Fallback: regex extract
            m = re.search(r'"amount"\s*:\s*([\d.]+)', raw)
            n = re.search(r'"receiver_name"\s*:\s*"([^"]*)"', raw)
            if m and n:
                return {"ok": True, "data": {
                    "amount": float(m.group(1)),
                    "receiver_name": n.group(1),
                    "looks_real": True,
                }, "error": None}
            logger.warning("Layer 2: JSON parse failed: %s | raw=%s", e, raw[:200])
            return {"ok": False, "data": None, "error": f"json_parse: {e}"}
    except Exception as exc:
        logger.warning("Layer 2 vision call failed: %s", exc)
        return {"ok": False, "data": None, "error": str(exc)[:200]}


def evaluate_layer2_decision(
    vision_data: dict,
    expected_amount: float | None = None,
    expected_tier: str | None = None,
) -> dict:
    """Apply business rules: should we auto-approve based on Vision output?

    Returns: {
        "approve": bool,
        "confidence": float (0..1),
        "reason": str,
        "matched_amount": int | None,
        "tier_match": tuple | None,
    }
    """
    if not vision_data:
        return {"approve": False, "confidence": 0.0, "reason": "no_vision_data"}

    if not vision_data.get("looks_real", True):
        return {"approve": False, "confidence": 0.0,
                "reason": f"AI suspicious: {vision_data.get('suspicious_reason', '?')}"}

    amount = vision_data.get("amount")
    if amount is None:
        return {"approve": False, "confidence": 0.0, "reason": "no_amount"}

    try:
        amt = float(amount)
    except Exception:
        return {"approve": False, "confidence": 0.0, "reason": "amount_not_numeric"}

    # Check receiver name contains valid keyword
    rname = (vision_data.get("receiver_name") or "")
    receiver_ok = any(kw in rname for kw in VALID_RECEIVER_KEYWORDS)
    if not receiver_ok:
        return {"approve": False, "confidence": 0.0,
                "reason": f"receiver_name='{rname[:50]}' no valid keyword"}

    # Tier match
    try:
        from shared.pricing import amount_to_tier
        tier_match = amount_to_tier(int(amt))
    except Exception:
        tier_match = None
    if not tier_match:
        return {"approve": False, "confidence": 0.5,
                "reason": f"amount {amt} not in known tier", "matched_amount": int(amt)}

    # Optional sanity: if expected_amount provided, must match
    if expected_amount is not None:
        if abs(amt - float(expected_amount)) > 2:
            return {"approve": False, "confidence": 0.6,
                    "reason": f"amount {amt} ≠ expected {expected_amount}",
                    "matched_amount": int(amt), "tier_match": tier_match}

    # Optional: if expected_tier provided, must match
    if expected_tier is not None and tier_match[0] != str(expected_tier):
        return {"approve": False, "confidence": 0.7,
                "reason": f"tier {tier_match[0]} ≠ selected {expected_tier}",
                "matched_amount": int(amt), "tier_match": tier_match}

    return {
        "approve": True,
        "confidence": 0.90,
        "reason": f"vision_pass: amount={amt} receiver={rname[:30]}",
        "matched_amount": int(amt),
        "tier_match": tier_match,
    }


async def layer2_verify_and_decide(
    image_b64: str,
    expected_amount: float | None = None,
    expected_tier: str | None = None,
) -> dict:
    """Full Layer 2 flow: vision → evaluate → decision.

    Convenience wrapper for use from payment.py.
    """
    res = await verify_slip_with_vision(image_b64)
    if not res.get("ok"):
        return {"approve": False, "confidence": 0.0,
                "reason": f"vision_call_failed: {res.get('error')}"}
    decision = evaluate_layer2_decision(
        res["data"], expected_amount=expected_amount, expected_tier=expected_tier,
    )
    decision["vision_data"] = res["data"]
    return decision


__all__ = [
    "verify_slip_with_vision",
    "evaluate_layer2_decision",
    "layer2_verify_and_decide",
    "VALID_RECEIVER_KEYWORDS",
    "KNOWN_AMOUNTS",
]
