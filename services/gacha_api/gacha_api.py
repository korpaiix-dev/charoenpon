"""Gachapon API — FastAPI service for Web App.

Endpoints:
  POST /api/gacha/state   - return user credits + recent pulls (from Telegram initData)
  POST /api/gacha/spin    - validate, deduct credit, return prize result
  POST /api/gacha/claim   - apply prize (subscription/credit/clip), mark claimed

Auth: Telegram WebApp initData HMAC verification (per Telegram official spec).
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
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# ───────── Config ─────────
BOT_TOKEN = os.environ.get("SALES_BOT_TOKEN") or os.environ.get("BOT_TOKEN", "")
DB_URL = os.environ.get("DATABASE_URL", "postgresql://postgres:bW667Kr0haqVgVcuuKmA@charoenpon-postgres:5432/charoenpon")

app = FastAPI(title="Gachapon API", docs_url=None, redoc_url=None)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://telebord.net"],
    allow_methods=["POST", "GET"],
    allow_headers=["*"],
)

_pool: Optional[asyncpg.Pool] = None


async def _get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(DB_URL, min_size=1, max_size=5)
    return _pool


# ───────── Auth: validate Telegram WebApp initData ─────────
def _verify_init_data(init_data: str) -> dict:
    """Verify HMAC of Telegram WebApp initData, return parsed user dict.

    Per Telegram spec:
      secret_key = HMAC_SHA256(bot_token, "WebAppData")
      check_string = '\n'.join(f"{k}={v}" for k,v in sorted(data items if k != "hash"))
      computed_hash = HMAC_SHA256(secret_key, check_string)
      must equal hash from initData.
    """
    if not BOT_TOKEN:
        raise HTTPException(500, "bot token not configured")
    parsed = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = parsed.pop("hash", None)
    if not received_hash:
        raise HTTPException(401, "missing hash")
    # auth_date freshness (max 24h)
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
    user = json.loads(user_json)
    return user


# ───────── Models ─────────
class StateRequest(BaseModel):
    init_data: str


class SpinRequest(BaseModel):
    init_data: str


# ───────── Endpoints ─────────
@app.post("/api/gacha/state")
async def state(req: StateRequest):
    user = _verify_init_data(req.init_data)
    tg_id = int(user["id"])
    pool = await _get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT credits, total_purchased, total_spun FROM gachapon_credits "
            "WHERE telegram_id=$1", tg_id
        )
        credits = int(row["credits"]) if row else 0
        total_p = int(row["total_purchased"]) if row else 0
        total_s = int(row["total_spun"]) if row else 0

        recent = await conn.fetch(
            "SELECT prize_label, pulled_at FROM gachapon_pulls "
            "WHERE telegram_id=$1 ORDER BY pulled_at DESC LIMIT 5", tg_id
        )
        prizes = await conn.fetch(
            "SELECT code, label, probability, color_hex "
            "FROM gachapon_prizes WHERE is_active=true ORDER BY probability DESC"
        )
    return {
        "user": {"id": tg_id, "first_name": user.get("first_name", "")},
        "credits": credits,
        "total_purchased": total_p,
        "total_spun": total_s,
        "recent": [{"label": r["prize_label"], "at": r["pulled_at"].isoformat()} for r in recent],
        "prizes": [
            {"code": p["code"], "label": p["label"], "probability": float(p["probability"]),
             "color": p["color_hex"] or "#888"}
            for p in prizes
        ],
    }


def _roll_prize(prizes: list) -> dict:
    """Weighted random roll. Returns the prize dict."""
    r = random.random()
    cum = 0.0
    for p in prizes:
        cum += float(p["probability"])
        if r <= cum:
            return p
    return prizes[-1]


@app.post("/api/gacha/spin")
async def spin(req: SpinRequest):
    user = _verify_init_data(req.init_data)
    tg_id = int(user["id"])
    pool = await _get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "SELECT user_id, credits FROM gachapon_credits "
                "WHERE telegram_id=$1 FOR UPDATE", tg_id
            )
            if not row or int(row["credits"]) <= 0:
                raise HTTPException(403, "no credits")
            user_id = int(row["user_id"])

            prizes = await conn.fetch(
                "SELECT code, label, probability, type, value_thb, tier, duration_days, color_hex "
                "FROM gachapon_prizes WHERE is_active=true"
            )
            prize = _roll_prize([dict(p) for p in prizes])

            await conn.execute(
                "UPDATE gachapon_credits SET credits = credits - 1, "
                "total_spun = total_spun + 1, updated_at = NOW() "
                "WHERE telegram_id=$1", tg_id
            )

            pull_id = await conn.fetchval(
                "INSERT INTO gachapon_pulls (user_id, telegram_id, prize_code, prize_label, "
                "prize_value_thb, claimed) VALUES ($1, $2, $3, $4, $5, false) RETURNING id",
                user_id, tg_id, prize["code"], prize["label"],
                prize.get("value_thb") or 0,
            )

    return {
        "pull_id": pull_id,
        "prize": {
            "code": prize["code"],
            "label": prize["label"],
            "type": prize["type"],
            "value_thb": float(prize.get("value_thb") or 0),
            "tier": prize.get("tier"),
            "duration_days": prize.get("duration_days"),
            "color": prize.get("color_hex") or "#888",
        },
    }


class ClaimRequest(BaseModel):
    init_data: str
    pull_id: int


@app.post("/api/gacha/claim")
async def claim(req: ClaimRequest):
    """Apply the prize (create subscription / give discount / forward clips)."""
    user = _verify_init_data(req.init_data)
    tg_id = int(user["id"])
    pool = await _get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            pull = await conn.fetchrow(
                "SELECT id, user_id, telegram_id, prize_code, prize_label, claimed "
                "FROM gachapon_pulls WHERE id=$1 AND telegram_id=$2 FOR UPDATE",
                req.pull_id, tg_id
            )
            if not pull:
                raise HTTPException(404, "pull not found")
            if pull["claimed"]:
                return {"ok": True, "already_claimed": True}

            prize = await conn.fetchrow(
                "SELECT code, type, tier, duration_days, value_thb FROM gachapon_prizes "
                "WHERE code=$1", pull["prize_code"]
            )
            await conn.execute(
                "UPDATE gachapon_pulls SET claimed=true, claimed_at=NOW() WHERE id=$1",
                pull["id"]
            )

            # Apply per type
            applied = "logged"
            if prize["type"] == "subscription" and prize["tier"]:
                # Create subscription via models
                pkg_id = await conn.fetchval(
                    "SELECT id FROM packages WHERE tier=$1::packagetier AND is_active=true LIMIT 1",
                    prize["tier"]
                )
                if pkg_id:
                    # Expire active TIER_300/500 if existing (let lifetime/1299 stack)
                    if prize["tier"] in ("TIER_300", "TIER_500", "TIER_100"):
                        pass  # let new sub create — old will naturally expire
                    end_expr = "'2099-12-31'" if prize["tier"] == "TIER_2499" else f"NOW() + INTERVAL '{prize['duration_days']} days'"
                    await conn.execute(
                        f"INSERT INTO subscriptions (user_id, package_id, status, start_date, end_date) "
                        f"VALUES ($1, $2, 'ACTIVE', NOW(), {end_expr})",
                        int(pull["user_id"]), int(pkg_id)
                    )
                    applied = f"subscription:{prize['tier']}"
            elif prize["type"] == "discount":
                # Insert credit/discount voucher (simple ledger)
                await conn.execute(
                    "INSERT INTO admin_logs (admin_id, action, target_type, target_id, details) "
                    "VALUES ($1, 'gacha_discount_50', 'user', $2, $3)",
                    0, int(pull["user_id"]), f"prize {pull['prize_code']}"
                )
                applied = "discount_50"
            elif prize["type"] == "clip_pack":
                applied = "clip_pack_pending"

    return {"ok": True, "applied": applied}


@app.get("/health")
async def health():
    return {"ok": True}
