"""Shared purchase-flow helper — SINGLE source of truth for package pricing + intent creation.

Ported from dashboard/backend/routers/customer_miniapp.py::_compute_pkg_price so that EVERY
purchase entry point (mini-app, Prae AI chat, in-chat buy buttons) quotes the SAME
discount-aware price AND records a persistent purchase_intents row.

Discount rule (best-single, no stacking): campaign promo vs personal retention% vs gacha credit
— keep whichever gives the LOWEST final price.

Everything here uses shared.purchase_intent._connect() (raw asyncpg) so it works inside the
bots as well as the dashboard (both already use this connection successfully).
"""
import json as _json
import logging
from decimal import Decimal  # noqa: F401 (kept for callers)

from shared.purchase_intent import _connect, create_intent, set_intent_receiver

logger = logging.getLogger(__name__)


async def _get_customer_retention_pct(conn, tg_id) -> int:
    """Active per-customer retention discount % (comeback_dm_log, not purchased, within 48h). 0 if none."""
    if not tg_id:
        return 0
    try:
        row = await conn.fetchrow(
            "SELECT discount_pct FROM comeback_dm_log "
            "WHERE telegram_id = $1 AND purchased = FALSE "
            "  AND sent_at > (now() AT TIME ZONE 'UTC') - interval '48 hours' "
            "ORDER BY sent_at DESC LIMIT 1",
            int(tg_id),
        )
        return int(row["discount_pct"]) if row and row["discount_pct"] else 0
    except Exception:
        return 0


async def compute_package_price(tier_full: str, promo_id=None, tg_id=None, conn=None) -> "dict | None":
    """SERVER-SIDE discount-aware price (never trust client price).

    Mirror of dashboard _compute_pkg_price. Returns
    {"base": int, "final": int, "source": str, "credit_used": float} or None if invalid tier.
    """
    tdb = tier_full if (tier_full.startswith("TIER_") or tier_full.startswith("GACHA_")) else f"TIER_{tier_full}"
    tshort = tdb.replace("TIER_", "")
    _own_conn = conn is None
    if _own_conn:
        conn = await _connect()
    try:
        row = await conn.fetchrow(
            "SELECT price FROM packages WHERE tier::text = $1 AND is_active = TRUE", tdb
        )
        if not row:
            return None
        base = float(row["price"])
        final = base

        # 1) campaign promo (validated: active AND tier in package_codes)
        if promo_id:
            try:
                pr = await conn.fetchrow(
                    "SELECT package_codes, discount_type, discount_value, is_active "
                    "FROM promotions WHERE id = $1",
                    int(promo_id),
                )
            except Exception:
                pr = None
            if pr and pr["is_active"]:
                # Cleanup-C: delegate promo math to the ONE primitive (promotion_service.calculate_price)
                from shared.promotion_service import calculate_price as _calc
                _r = _calc(dict(pr), tdb, base)
                if not _r.get("applied"):
                    _r = _calc(dict(pr), tshort, base)
                if _r.get("applied"):
                    final = float(_r["discounted"])

        # 1b) AUTO-DISCOVER active public promotion for this tier (only when caller gave no
        #     explicit promo_id). Lets Prae / in-chat quote launch-promo prices (e.g. Super VIP
        #     4999 -> 2999) without the customer clicking a promo link. Self-expiring via valid_hours.
        if not promo_id:
            try:
                arows = await conn.fetch(
                    "SELECT package_codes, discount_type, discount_value FROM promotions "
                    "WHERE is_active = TRUE "
                    "  AND (starts_at IS NULL OR starts_at <= now()) "
                    "  AND (ends_at IS NULL OR ends_at > now())"
                )
            except Exception:
                arows = []
            from shared.promotion_service import calculate_price as _calc
            for _pr in arows:
                _r = _calc(dict(_pr), tdb, base)
                if not _r.get("applied"):
                    _r = _calc(dict(_pr), tshort, base)
                if _r.get("applied") and float(_r["discounted"]) < final:
                    final = float(_r["discounted"])

        _src = "campaign" if final < base else "none"
        _credit_used = 0.0

        if tg_id:
            # 2) personal RETENTION discount — best-single
            _ret = await _get_customer_retention_pct(conn, tg_id)
            if _ret > 0:
                _ret_final = base * (100 - _ret) / 100
                if _ret_final < final:
                    final = _ret_final
                    _src = "retention:%d%%" % _ret

            # 3) personal GACHA CREDIT (฿ balance) — best-single, capped per tier
            try:
                _br = await conn.fetchrow(
                    "SELECT balance FROM user_discount_credits WHERE telegram_id=$1", int(tg_id)
                )
                _bal = float(_br["balance"]) if _br and _br["balance"] else 0.0
            except Exception:
                _bal = 0.0
            if _bal > 0:
                _cap = 50.0
                try:
                    _cr = await conn.fetchrow(
                        "SELECT value_json FROM promo_config WHERE config_key='gacha_discount_cap_per_tier'"
                    )
                    if _cr and _cr["value_json"]:
                        _cm = _cr["value_json"] if isinstance(_cr["value_json"], dict) else _json.loads(_cr["value_json"])
                        _cap = float(_cm.get(tshort, 50))
                except Exception:
                    _cap = 50.0
                _usable = min(_bal, _cap, base)
                if _usable > 0 and (base - _usable) < final:
                    final = base - _usable
                    _src = "gacha_credit"
                    _credit_used = _usable

        # clamp: a discount can never make the price negative nor exceed the list price
        # (guards a misconfigured promo — same protection promotion_service.calculate_price has).
        final = max(0.0, min(float(final), float(base)))
        return {
            "base": int(round(base)),
            "final": int(round(final)),
            "source": _src,
            "credit_used": round(_credit_used, 2),
        }
    finally:
        if _own_conn:
            await conn.close()


async def prepare_purchase(
    tg_id: int,
    tier: str,
    promo_id=None,
    source: str = "chat",
    ttl_minutes: int = 1440,
) -> dict:
    """One call for a non-mini-app purchase: compute discount-aware price, create a persistent
    purchase_intent, pick a receiver account and bind it to the intent.

    Returns on success:
      {"ok": True, "base": int, "final": int, "tier": "TIER_xxx",
       "credit_used": float, "discount_source": str, "intent_id": int|None,
       "receiver": {<receiver_pool row>}}
    On failure: {"error": "invalid_tier"|"no_receiver", ...}
    """
    tdb = tier if (tier.startswith("TIER_") or tier.startswith("GACHA_")) else f"TIER_{tier}"

    pinfo = await compute_package_price(tdb, promo_id, tg_id)
    if not pinfo:
        return {"error": "invalid_tier", "tier": tdb}

    base = pinfo["base"]
    final = pinfo["final"]

    intent_id = await create_intent(
        tg_id=tg_id,
        tier=tdb,
        original_price=base,
        final_price=final,
        promo_id=int(promo_id) if promo_id else None,
        source=source,
        ttl_minutes=ttl_minutes,
        discount_credit=pinfo.get("credit_used", 0),
    )

    receiver = None
    try:
        from shared.receiver_pool import pick_random
        acct = await pick_random()
        if acct:
            receiver = acct
            if intent_id:
                try:
                    await set_intent_receiver(intent_id, acct["id"])
                except Exception as _bex:
                    logger.warning("prepare_purchase set_intent_receiver failed: %s", _bex)
    except Exception as exc:
        logger.warning("prepare_purchase receiver pick failed: %s", exc)

    if not receiver:
        return {
            "error": "no_receiver",
            "intent_id": intent_id,
            "base": base,
            "final": final,
            "tier": tdb,
        }

    return {
        "ok": True,
        "base": base,
        "final": final,
        "tier": tdb,
        "credit_used": pinfo.get("credit_used", 0),
        "discount_source": pinfo.get("source", "none"),
        "intent_id": intent_id,
        "receiver": receiver,
    }


async def effective_price_for_user(tier: str, context_user_data: dict):
    """ONE effective-price resolver for the slip + TrueMoney entry points (was copy-pasted in
    payment.py and truemoney_handler.py). Precedence: active Day-0/campaign promo (best savings,
    minus gacha credit) > per-user comeback promo > pricing hub (Lucky6/Birthday/EndMonth/base)."""
    from decimal import Decimal as _D
    from shared.pricing import effective_price as _hub, TIER_PRICES as _HUB
    base_price = _HUB.get(tier, _D("0"))
    try:
        from shared.promotion_service import list_active_promotions, calculate_price as _calc
        _promos = await list_active_promotions()
        _key = f"TIER_{tier}"
        _best = None
        for _pm in _promos:
            _codes = _pm.get("package_codes") or []
            if isinstance(_codes, str):
                import json as _j
                try: _codes = _j.loads(_codes)
                except Exception: _codes = []
            if _key in _codes:
                _c = _calc(_pm, _key, float(base_price))
                if _c.get("applied") and _c["savings"] > 0 and (_best is None or _c["savings"] > _best["savings"]):
                    _best = _c
        if _best:
            _price = _D(str(int(_best["discounted"])))
            _credit = context_user_data.get("gacha_credit_use") or 0
            try: _credit = _D(str(_credit))
            except Exception: _credit = _D("0")
            return max(_D("0"), _price - _credit)
    except Exception as _exc:
        import logging; logging.getLogger(__name__).warning("effective_price_for_user dayzero check failed: %s", _exc)
    comeback_promo = context_user_data.get("comeback_promo")
    if comeback_promo:
        from bots.sales_bot.comeback_dm import validate_promo_code
        promo = await validate_promo_code(comeback_promo)
        if promo:
            return _D(str(int(base_price * (100 - promo["discount_pct"]) / 100)))
    return _hub(tier, context_user_data)
