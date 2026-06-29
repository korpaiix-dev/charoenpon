"""Gachapon API v3 — Smart prize logic + credit system.

Endpoints:
  POST /api/gacha/state   - return user credits + spin count + recent pulls
  POST /api/gacha/spin    - smart roll: re-roll for duplicate clips / no-value prizes
  POST /api/gacha/claim   - apply prize (subscription/credit/clip)

Smart logic for repeat prizes:
  CLIP_A/B/C duplicate           -> re-roll (no value added)
  Subscription = user's current  -> extend +X days
  Subscription < user's current  -> convert to discount credit ฿50
  GOD ถาวร when user has GOD ถาวร-> convert to discount credit ฿50
  DISCOUNT_50                    -> add to balance (stack)
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import random
import time
from datetime import datetime
from typing import Optional
from urllib.parse import unquote, parse_qsl

import asyncpg
from gacha_deliver import deliver_prize as _gacha_deliver_prize
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

BOT_TOKEN = os.environ.get("SALES_BOT_TOKEN") or os.environ.get("BOT_TOKEN", "")
DB_URL = os.environ.get("DATABASE_URL",
                        "postgresql://postgres:postgres@charoenpon-postgres:5432/charoenpon")

app = FastAPI(title="Gachapon API v3", docs_url=None, redoc_url=None)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://telebord.net"],
    allow_methods=["POST", "GET"],
    allow_headers=["*"],
)

# Tier ranking (higher = better)
TIER_RANK = {
    "TIER_100": 1, "TIER_300": 2, "TIER_500": 3, "TIER_1299": 4, "TIER_2499": 99,
}
DISCOUNT_CONVERT_THB = 50.0  # value when a tier-down prize is converted to credit

_pool: Optional[asyncpg.Pool] = None


async def _get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(DB_URL, min_size=1, max_size=5)
    return _pool


def _verify_init_data(init_data: str) -> dict:
    if not BOT_TOKEN:
        raise HTTPException(500, "bot token not configured")
    parsed = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = parsed.pop("hash", None)
    if not received_hash:
        raise HTTPException(401, "missing hash")
    auth_date = int(parsed.get("auth_date", "0"))
    if auth_date and (time.time() - auth_date) > 86400:
        raise HTTPException(401, "initData too old")
    check_string = "\n".join(f"{k}={v}" for k, v in sorted(parsed.items()))
    secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    computed = hmac.new(secret_key, check_string.encode(), hashlib.sha256).hexdigest()
    if computed != received_hash:
        raise HTTPException(401, "invalid initData hash")
    user_json = parsed.get("user")
    if not user_json:
        raise HTTPException(401, "no user")
    return json.loads(user_json)


class StateRequest(BaseModel):
    init_data: str


class SpinRequest(BaseModel):
    init_data: str


class ClaimRequest(BaseModel):
    init_data: str
    pull_id: int


@app.post("/api/gacha/state")
async def state(req: StateRequest):
    user = _verify_init_data(req.init_data)
    tg_id = int(user["id"])
    pool = await _get_pool()
    async with pool.acquire() as conn:
        c_row = await conn.fetchrow(
            "SELECT credits, total_purchased, total_spun FROM gachapon_credits WHERE telegram_id=$1",
            tg_id
        )
        d_row = await conn.fetchrow(
            "SELECT balance FROM user_discount_credits WHERE telegram_id=$1",
            tg_id
        )
        prizes = await conn.fetch(
            "SELECT code, label, probability, color_hex FROM gachapon_prizes "
            "WHERE is_active=true ORDER BY probability DESC"
        )
        recent = await conn.fetch(
            "SELECT prize_label, outcome, pulled_at FROM gachapon_pulls "
            "WHERE telegram_id=$1 ORDER BY pulled_at DESC LIMIT 5", tg_id
        )
    return {
        "user": {"id": tg_id, "first_name": user.get("first_name", "")},
        "credits": int(c_row["credits"]) if c_row else 0,
        "total_purchased": int(c_row["total_purchased"]) if c_row else 0,
        "total_spun": int(c_row["total_spun"]) if c_row else 0,
        "discount_balance": float(d_row["balance"]) if d_row else 0,
        "prizes": [
            {"code": p["code"], "label": p["label"], "color": p["color_hex"] or "#888"}
            for p in prizes
        ],
        "recent": [{"label": r["prize_label"], "outcome": r["outcome"], "at": r["pulled_at"].isoformat()}
                   for r in recent],
    }


def _roll_prize(prizes: list) -> dict:
    r = random.random()
    cum = 0.0
    for p in prizes:
        cum += float(p["probability"])
        if r <= cum:
            return p
    return prizes[-1]


async def _user_inventory(conn, user_id: int) -> set:
    rows = await conn.fetch(
        "SELECT prize_code FROM gacha_user_inventory WHERE user_id=$1", user_id
    )
    return {r["prize_code"] for r in rows}


async def _user_current_tier(conn, user_id: int) -> str | None:
    """Return highest active subscription tier of user (or None)."""
    row = await conn.fetchrow("""
        SELECT pk.tier::text AS tier FROM subscriptions sub
        JOIN packages pk ON pk.id = sub.package_id
        WHERE sub.user_id = $1 AND sub.status = 'ACTIVE' AND sub.end_date > NOW()
        ORDER BY CASE pk.tier::text
            WHEN 'TIER_2499' THEN 99
            WHEN 'TIER_1299' THEN 4
            WHEN 'TIER_500' THEN 3
            WHEN 'TIER_300' THEN 2
            WHEN 'TIER_100' THEN 1
            ELSE 0 END DESC LIMIT 1
    """, user_id)
    return row["tier"] if row else None


async def _decide_outcome(conn, user_id: int, prize: dict) -> tuple[str, dict]:
    """Return (outcome_code, detail_dict). Outcomes:
      'normal'   - prize awarded as-is
      'extend'   - subscription extended by duration_days
      'credit'   - converted to ฿50 discount credit
      'reroll'   - should be re-rolled (no value)
    """
    code = prize["code"]
    ptype = prize["type"]

    if ptype == "clip_pack":
        inv = await _user_inventory(conn, user_id)
        if code in inv:
            return "reroll", {"reason": "duplicate_clip"}
        return "normal", {"code": code}

    if ptype == "subscription":
        prize_tier = prize.get("tier")
        current_tier = await _user_current_tier(conn, user_id)
        if not current_tier:
            return "normal", {"created": "new"}
        prize_rank = TIER_RANK.get(prize_tier, 0)
        current_rank = TIER_RANK.get(current_tier, 0)
        if current_tier == "TIER_2499":
            # Already lifetime — every other sub is worthless (including GOD ถาวร dup)
            return "credit", {"reason": "user_has_lifetime", "amount": DISCOUNT_CONVERT_THB}
        if prize_tier == current_tier:
            return "extend", {"days": prize.get("duration_days") or 30}
        if prize_rank < current_rank:
            return "credit", {"reason": "tier_below_current", "amount": DISCOUNT_CONVERT_THB}
        # prize_rank > current_rank => upgrade (replace + carry remaining days)
        return "normal", {"upgrade": current_tier, "to": prize_tier}

    if ptype == "discount":
        return "normal", {"add_balance": float(prize.get("value_thb") or 0)}

    return "normal", {}


@app.post("/api/gacha/spin")
async def spin(req: SpinRequest):
    user = _verify_init_data(req.init_data)
    tg_id = int(user["id"])
    pool = await _get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            # FIX 2026-06-21: Block banned users from spinning (scam ring defense)
            ban_row = await conn.fetchrow(
                "SELECT id, is_banned FROM users WHERE telegram_id=$1", tg_id
            )
            if ban_row and ban_row["is_banned"]:
                raise HTTPException(403, "account banned")

            row = await conn.fetchrow(
                "SELECT user_id, credits FROM gachapon_credits WHERE telegram_id=$1 FOR UPDATE",
                tg_id
            )
            if not row or int(row["credits"]) <= 0:
                raise HTTPException(403, "no credits")
            user_id = int(row["user_id"])

            prize_rows = await conn.fetch(
                "SELECT code, label, probability, type, value_thb, tier, "
                "duration_days, source_chat_id, color_hex FROM gachapon_prizes WHERE is_active=true"
            )
            prizes = [dict(p) for p in prize_rows]

            # Roll with re-roll support (max 3 re-rolls to prevent infinite)
            attempts = []
            for _ in range(4):
                prize = _roll_prize(prizes)
                outcome, detail = await _decide_outcome(conn, user_id, prize)
                attempts.append({"prize": prize, "outcome": outcome, "detail": detail})
                if outcome != "reroll":
                    break

            final = attempts[-1]
            prize = final["prize"]
            outcome = final["outcome"]
            detail = final["detail"]
            rerolled = len(attempts) > 1

            # Deduct credit
            await conn.execute(
                "UPDATE gachapon_credits SET credits = credits - 1, "
                "total_spun = total_spun + 1, updated_at = NOW() WHERE telegram_id=$1",
                tg_id
            )

            # Insert pull record (unclaimed)
            pull_id = await conn.fetchval(
                "INSERT INTO gachapon_pulls "
                "(user_id, telegram_id, prize_code, prize_label, prize_value_thb, "
                " outcome, outcome_detail, rerolled, claimed) "
                "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,false) RETURNING id",
                user_id, tg_id, prize["code"], prize["label"],
                prize.get("value_thb") or 0,
                outcome, json.dumps(detail), rerolled,
            )

    return {
        "pull_id": pull_id,
        "prize": {
            "code": prize["code"],
            "label": prize["label"],
            "type": prize["type"],
            "value_thb": float(prize.get("value_thb") or 0),
            "color": prize.get("color_hex") or "#888",
        },
        "outcome": outcome,
        "outcome_detail": detail,
        "rerolled": rerolled,
        "reroll_path": [
            {"prize": a["prize"]["code"], "outcome": a["outcome"]} for a in attempts
        ],
    }


@app.post("/api/gacha/claim")
async def claim(req: ClaimRequest):
    user = _verify_init_data(req.init_data)
    tg_id = int(user["id"])
    pool = await _get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            pull = await conn.fetchrow(
                "SELECT id, user_id, telegram_id, prize_code, prize_label, "
                "outcome, outcome_detail, claimed FROM gachapon_pulls "
                "WHERE id=$1 AND telegram_id=$2 FOR UPDATE", req.pull_id, tg_id
            )
            if not pull:
                raise HTTPException(404, "pull not found")
            if pull["claimed"]:
                return {"ok": True, "already_claimed": True}

            prize = await conn.fetchrow(
                "SELECT code, type, tier, duration_days, value_thb "
                "FROM gachapon_prizes WHERE code=$1", pull["prize_code"]
            )
            outcome = pull["outcome"]
            detail = pull["outcome_detail"] if isinstance(pull["outcome_detail"], dict) else (
                json.loads(pull["outcome_detail"]) if pull["outcome_detail"] else {}
            )

            applied = []

            if outcome == "credit":
                amount = float(detail.get("amount") or DISCOUNT_CONVERT_THB)
                await conn.execute(
                    "INSERT INTO user_discount_credits (user_id, telegram_id, balance, total_earned) "
                    "VALUES ($1, $2, $3, $3) "
                    "ON CONFLICT (user_id) DO UPDATE SET "
                    "balance = user_discount_credits.balance + $3, "
                    "total_earned = user_discount_credits.total_earned + $3, "
                    "updated_at = NOW()",
                    int(pull["user_id"]), tg_id, amount
                )
                applied.append({"action": "credit_added", "amount": amount})

            elif outcome == "extend":
                days = int(detail.get("days") or 30)
                tier = prize["tier"]
                # extend latest active sub of matching tier
                await conn.execute(
                    "UPDATE subscriptions sub SET end_date = end_date + ($3 * INTERVAL '1 day') "
                    "FROM packages pk WHERE pk.id = sub.package_id AND pk.tier::text = $2 "
                    "AND sub.user_id = $1 AND sub.status = 'ACTIVE' "
                    "AND sub.end_date > NOW()",
                    int(pull["user_id"]), tier, days
                )
                applied.append({"action": "subscription_extended", "tier": tier, "days": days})

            elif outcome == "normal":
                if prize["type"] == "subscription" and prize["tier"]:
                    pkg_id = await conn.fetchval(
                        "SELECT id FROM packages WHERE tier::text=$1 AND is_active=true LIMIT 1",
                        prize["tier"]
                    )
                    if pkg_id:
                        # Expire lower tier active subs
                        await conn.execute(
                            "UPDATE subscriptions SET status='EXPIRED', updated_at=NOW() "
                            "WHERE user_id=$1 AND status='ACTIVE' "
                            "AND package_id IN (SELECT id FROM packages WHERE tier::text IN ('TIER_100','TIER_300','TIER_500'))",
                            int(pull["user_id"])
                        )
                        if prize["tier"] == "TIER_2499":
                            await conn.execute(
                                "INSERT INTO subscriptions (user_id, package_id, status, start_date, end_date) "
                                "VALUES ($1, $2, 'ACTIVE', NOW(), '2099-12-31'::timestamp)",
                                int(pull["user_id"]), int(pkg_id)
                            )
                        else:  # AUDIT FIX M3: parameterize duration_days (เดิม f-string เข้า SQL)
                            await conn.execute(
                                "INSERT INTO subscriptions (user_id, package_id, status, start_date, end_date) "
                                "VALUES ($1, $2, 'ACTIVE', NOW(), NOW() + ($3 * INTERVAL '1 day'))",
                                int(pull["user_id"]), int(pkg_id), int(prize["duration_days"])
                            )
                        applied.append({"action": "subscription_created", "tier": prize["tier"]})
                elif prize["type"] == "discount":
                    amount = float(prize.get("value_thb") or 0)
                    await conn.execute(
                        "INSERT INTO user_discount_credits (user_id, telegram_id, balance, total_earned) "
                        "VALUES ($1, $2, $3, $3) "
                        "ON CONFLICT (user_id) DO UPDATE SET "
                        "balance = user_discount_credits.balance + $3, "
                        "total_earned = user_discount_credits.total_earned + $3, "
                        "updated_at = NOW()",
                        int(pull["user_id"]), tg_id, amount
                    )
                    applied.append({"action": "discount_added", "amount": amount})
                elif prize["type"] == "clip_pack":
                    # Mark in inventory (worker will forward later)
                    await conn.execute(
                        "INSERT INTO gacha_user_inventory (user_id, telegram_id, prize_code) "
                        "VALUES ($1, $2, $3) ON CONFLICT DO NOTHING",
                        int(pull["user_id"]), tg_id, prize["code"]
                    )
                    applied.append({"action": "clip_pack_pending", "code": prize["code"]})

            await conn.execute(
                "UPDATE gachapon_pulls SET claimed=true, claimed_at=NOW() WHERE id=$1",
                pull["id"]
            )

    # Build a customer-friendly message
    msg_label = pull["prize_label"]
    if outcome == "credit":
        msg = f"คุณมีของชั้นนี้อยู่แล้ว — แปลงเป็นส่วนลด ฿{detail.get('amount', DISCOUNT_CONVERT_THB):.0f} เก็บไว้ใช้ตอนซื้อ"
    elif outcome == "extend":
        msg = f"ขยายอายุ {detail.get('tier', '')} ของคุณ +{detail.get('days', 30)} วัน"
    elif outcome == "normal":
        if prize["type"] == "discount":
            msg = "เพิ่มส่วนลด ฿50 ในบัญชีคุณ"
        elif prize["type"] == "subscription":
            msg = "ระบบใส่สิทธิ์ให้คุณเรียบร้อย"
        elif prize["type"] == "clip_pack":
            msg = "ระบบกำลังส่งคลิปทั้งหมดให้คุณ — รอใน DM นะคะ"
        else:
            msg = "ระบบจะส่งให้คุณ"
    else:
        msg = msg_label

    # ───── EVENT-DRIVEN DELIVERY (2026-06-22) ─────
    try:
        await _gacha_deliver_prize(
            pool, int(pull["id"]), tg_id,
            str(pull["prize_code"]), str(pull["prize_label"]),
            str(prize["type"]),
            str(prize["tier"]) if prize.get("tier") else None,
            float(prize.get("value_thb") or 0),
            str(outcome),
        )
    except Exception as exc:
        print("[gacha_claim] delivery failed pull=", pull["id"], "err=", exc)

    return {"ok": True, "outcome": outcome, "applied": applied, "message": msg}


@app.get("/health")
async def health():
    return {"ok": True}
