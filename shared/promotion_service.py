"""Promotion service — single source of truth for sales-facing promo data.

The `promotions` table stores 1 row per campaign with EVERYTHING:
- which packages get what discount
- caption + image + buttons (used in group post + customer DM)
- which groups to post in + at what times
- date range

This module exposes:
- list_active_promotions() — for content_bot scheduler + Dashboard list
- get_promotion(code) — for sales_bot deep link handler
- calculate_price(promo, package_code) — returns (original, discounted, savings)
- click_promotion(promo_id, tg_id, package_code) — records click + returns expiry
- has_pending_click(tg_id, promo_id) — check if customer has unconsumed click

Caching: 60s TTL since edits via Dashboard are not super frequent.
"""
from __future__ import annotations
import os
import time
import logging
import json as _json
from datetime import datetime, timezone, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

# ─── Cache (60s TTL) ────────────────────────────────────────────────
_cache: dict = {}
_cache_ts: float = 0.0
_CACHE_TTL = 60.0


def _conn_str() -> str:
    return os.environ.get("DATABASE_URL", "").replace("postgresql+asyncpg://", "postgresql://")


async def _connect():
    import asyncpg
    return await asyncpg.connect(_conn_str())


async def list_active_promotions() -> list[dict]:
    """Return all currently-active promotions (respects starts_at/ends_at)."""
    global _cache, _cache_ts
    now = time.time()
    if "active" in _cache and now - _cache_ts < _CACHE_TTL:
        return _cache["active"]

    try:
        conn = await _connect()
        try:
            rows = await conn.fetch("""
                SELECT id, code, name, is_active, package_codes,
                       discount_type, discount_value, valid_hours,
                       starts_at, ends_at
                FROM promotions
                WHERE is_active = TRUE
                  AND (starts_at IS NULL OR starts_at <= NOW())
                  AND (ends_at IS NULL OR ends_at > NOW())
                ORDER BY id
            """)
        finally:
            await conn.close()
        result = [dict(r) for r in rows]
        _cache["active"] = result
        _cache_ts = now
        return result
    except Exception as exc:
        logger.warning("list_active_promotions failed: %s", exc)
        return _cache.get("active", [])


async def get_promotion(code: str) -> Optional[dict]:
    """Fetch a single promotion by code. Used by sales_bot deep link handler."""
    try:
        conn = await _connect()
        try:
            row = await conn.fetchrow("""
                SELECT id, code, name, is_active, package_codes,
                       discount_type, discount_value, valid_hours,
                       starts_at, ends_at
                FROM promotions WHERE code = $1
            """, code)
        finally:
            await conn.close()
        return dict(row) if row else None
    except Exception as exc:
        logger.warning("get_promotion(%s) failed: %s", code, exc)
        return None


def _normalise_codes(value) -> list:
    """JSONB column can come back as list, str, or already-parsed. Normalise to list."""
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            return _json.loads(value)
        except Exception:
            return []
    return list(value) if hasattr(value, "__iter__") else []


def calculate_price(promo: dict, package_code: str, original_price: float) -> dict:
    """Apply promo discount to a package price.

    Returns {
        "original": 300,
        "discounted": 240,
        "savings": 60,
        "applied": True,   # False if package not eligible
    }
    """
    pkg_codes = _normalise_codes(promo.get("package_codes"))
    if package_code not in pkg_codes:
        return {
            "original": float(original_price),
            "discounted": float(original_price),
            "savings": 0.0,
            "applied": False,
        }

    dt = (promo.get("discount_type") or "none").lower()
    dv = float(promo.get("discount_value") or 0)
    orig = float(original_price)

    if dt == "percent":
        # 20% off
        discounted = orig * (100 - dv) / 100
    elif dt == "fixed_off":
        # X baht off
        discounted = max(0, orig - dv)
    elif dt == "fixed_price":
        # set to specific price
        discounted = dv
    else:  # 'none'
        discounted = orig

    # Round to whole baht
    discounted = round(discounted)
    # FIX 2026-07-04 (P1-2): clamp against misconfigured promos (percent>100, fixed_price>orig,
    # negative discount_value) — price must stay within [0, original], never negative/inflated.
    discounted = max(0.0, min(orig, float(discounted)))
    return {
        "original": orig,
        "discounted": float(discounted),
        "savings": round(orig - discounted, 2),
        "applied": (discounted < orig),
    }


async def record_click(
    promo_id: int,
    user_telegram_id: int,
    package_code: Optional[str] = None,
) -> dict:
    """Record customer clicked a promo. Returns {click_id, expires_at}.

    Used by sales_bot when customer clicks the promo deep link → opens chat.
    """
    try:
        conn = await _connect()
        try:
            # Get promo's valid_hours
            promo_row = await conn.fetchrow(
                "SELECT valid_hours FROM promotions WHERE id = $1", promo_id
            )
            if not promo_row:
                return {"error": "promo_not_found"}

            valid_hours = int(promo_row["valid_hours"] or 48)
            expires_at = datetime.now(timezone.utc) + timedelta(hours=valid_hours)

            # FIX 2026-07-04 (P1-3): dedup — one UNCONSUMED click per (promo, user). Refresh the
            # existing unconsumed click instead of stacking new rows (prevents riding one promo
            # across several separate purchases).
            _existing = await conn.fetchrow(
                "SELECT id FROM promotion_clicks WHERE promotion_id=$1 AND user_telegram_id=$2 "
                "AND consumed_at IS NULL ORDER BY id DESC LIMIT 1",
                promo_id, user_telegram_id,
            )
            if _existing:
                row = await conn.fetchrow(
                    "UPDATE promotion_clicks SET package_code=$2, expires_at=$3 WHERE id=$1 "
                    "RETURNING id, expires_at",
                    _existing["id"], package_code, expires_at,
                )
            else:
                row = await conn.fetchrow(
                    "INSERT INTO promotion_clicks (promotion_id, user_telegram_id, "
                    "package_code, expires_at) VALUES ($1, $2, $3, $4) "
                    "RETURNING id, expires_at",
                    promo_id, user_telegram_id, package_code, expires_at,
                )
        finally:
            await conn.close()
        return {
            "click_id": row["id"],
            "expires_at": row["expires_at"],
        }
    except Exception as exc:
        logger.warning("record_click failed: %s", exc)
        return {"error": str(exc)}


async def get_pending_click(user_telegram_id: int, promo_code: Optional[str] = None) -> Optional[dict]:
    """Find an unconsumed click for this user (optionally filtered by promo).

    Returns the most recent unconsumed click that's not expired yet.
    """
    try:
        conn = await _connect()
        try:
            if promo_code:
                row = await conn.fetchrow("""
                    SELECT pc.id, pc.promotion_id, pc.clicked_at, pc.expires_at, pc.package_code,
                           p.code AS promo_code, p.name AS promo_name
                    FROM promotion_clicks pc
                    JOIN promotions p ON p.id = pc.promotion_id
                    WHERE pc.user_telegram_id = $1
                      AND p.code = $2
                      AND pc.consumed_at IS NULL
                      AND pc.expires_at > NOW()
                    ORDER BY pc.clicked_at DESC LIMIT 1
                """, user_telegram_id, promo_code)
            else:
                row = await conn.fetchrow("""
                    SELECT pc.id, pc.promotion_id, pc.clicked_at, pc.expires_at, pc.package_code,
                           p.code AS promo_code, p.name AS promo_name
                    FROM promotion_clicks pc
                    JOIN promotions p ON p.id = pc.promotion_id
                    WHERE pc.user_telegram_id = $1
                      AND pc.consumed_at IS NULL
                      AND pc.expires_at > NOW()
                    ORDER BY pc.clicked_at DESC LIMIT 1
                """, user_telegram_id)
        finally:
            await conn.close()
        return dict(row) if row else None
    except Exception as exc:
        logger.warning("get_pending_click failed: %s", exc)
        return None


async def consume_click(click_id: int, payment_id: Optional[int] = None) -> bool:
    """Mark a click as consumed (customer completed payment)."""
    try:
        conn = await _connect()
        try:
            r = await conn.execute(
                "UPDATE promotion_clicks SET consumed_at = NOW(), consumed_payment_id = $2 "
                "WHERE id = $1 AND consumed_at IS NULL",
                click_id, payment_id,
            )
        finally:
            await conn.close()
        return "UPDATE 1" in r
    except Exception as exc:
        logger.warning("consume_click failed: %s", exc)
        return False


def clear_cache():
    """Force next call to refresh from DB."""
    global _cache, _cache_ts
    _cache = {}
    _cache_ts = 0.0
