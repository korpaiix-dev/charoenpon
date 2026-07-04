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


async def compute_package_price(tier_full: str, promo_id=None, tg_id=None) -> "dict | None":
    """SERVER-SIDE discount-aware price (never trust client price).

    Mirror of dashboard _compute_pkg_price. Returns
    {"base": int, "final": int, "source": str, "credit_used": float} or None if invalid tier.
    """
    tdb = tier_full if (tier_full.startswith("TIER_") or tier_full.startswith("GACHA_")) else f"TIER_{tier_full}"
    tshort = tdb.replace("TIER_", "")
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
                raw = pr["package_codes"]
                if isinstance(raw, str):
                    try:
                        codes = _json.loads(raw)
                    except Exception:
                        codes = []
                else:
                    codes = list(raw) if raw else []
                if tdb in codes or tshort in codes:
                    dt = (pr["discount_type"] or "none").lower()
                    dv = float(pr["discount_value"] or 0)
                    if dt == "percent":
                        final = base * (100 - dv) / 100
                    elif dt in ("fixed_off", "fixed", "amount", "baht"):  # P1-10: one discount vocab (match pricing.py)
                        final = max(0, base - dv)
                    elif dt == "fixed_price":
                        final = dv

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
            for _pr in arows:
                _raw = _pr["package_codes"]
                if isinstance(_raw, str):
                    try:
                        _codes = _json.loads(_raw)
                    except Exception:
                        _codes = []
                else:
                    _codes = list(_raw) if _raw else []
                if tdb in _codes or tshort in _codes:
                    _dt = (_pr["discount_type"] or "none").lower()
                    _dv = float(_pr["discount_value"] or 0)
                    if _dt == "percent":
                        _cand = base * (100 - _dv) / 100
                    elif _dt in ("fixed_off", "fixed", "amount", "baht"):  # P1-10: one discount vocab (match pricing.py)
                        _cand = max(0, base - _dv)
                    elif _dt == "fixed_price":
                        _cand = _dv
                    else:
                        _cand = base
                    if _cand < final:
                        final = _cand

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

        return {
            "base": int(round(base)),
            "final": int(round(final)),
            "source": _src,
            "credit_used": round(_credit_used, 2),
        }
    finally:
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
