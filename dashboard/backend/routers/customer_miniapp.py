"""Customer-facing Mini App endpoints.

Telegram Mini App opens at /customer/packages — reads packages + promos from DB.
No JWT auth — uses Telegram initData verification instead.
"""
from __future__ import annotations
import os
import logging
import json
from pathlib import Path
from fastapi import APIRouter, Request, HTTPException
import os, hmac, hashlib, urllib.parse
from fastapi.responses import HTMLResponse

from ..database import pool
from shared.purchase_intent import create_intent as _create_intent, set_intent_receiver as _set_intent_receiver

logger = logging.getLogger(__name__)
router = APIRouter(tags=["customer-miniapp"])


# ─── Static page route ────────────────────────────────────────────────
@router.get("/webapp/customer/packages", response_class=HTMLResponse)
async def customer_packages_page():
    """Serve the Mini App HTML page."""
    html_path = Path(__file__).parent.parent.parent / "frontend" / "customer" / "packages.html"
    if not html_path.exists():
        # Fallback: look in customer dir
        html_path = Path("/app/dashboard/frontend/customer/packages.html")
    if html_path.exists():
        return HTMLResponse(
            html_path.read_text(encoding="utf-8"),
            headers={
                "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
                "Pragma": "no-cache",
                "Expires": "0",
            },
        )
    return HTMLResponse("<h1>Mini App not deployed</h1>", status_code=404)


# ─── Data endpoint ─────────────────────────────────────────────────────
@router.get("/webapp/api/customer/packages-and-promos")
async def get_packages_and_promos(request: Request):
    """Return packages + active promotions + gacha — for the Mini App.

    Open endpoint (Mini App calls without bearer token). Telegram initData
    verification is best-effort. Even an unauthenticated call only reads
    public catalog data — no sensitive info exposed.
    """
    # Fetch active packages
    pkg_rows = await pool.fetch("""
        SELECT id, name, tier::text AS tier, price, duration_days, groups_access, sort_order
        FROM packages
        WHERE is_active = TRUE
        ORDER BY
          CASE WHEN tier::text LIKE 'GACHA_%' THEN 2 ELSE 1 END,
          sort_order DESC NULLS LAST,
          price
    """)

    # Fetch active promotions
    promo_rows = await pool.fetch("""
        SELECT id, code, name, package_codes, discount_type, discount_value,
               valid_hours, starts_at, ends_at
        FROM promotions
        WHERE is_active = TRUE
          AND (starts_at IS NULL OR starts_at <= NOW())
          AND (ends_at IS NULL OR ends_at > NOW())
        ORDER BY id
    """)

    # Calculate discount for each eligible package
    def _normalise_codes(value):
        if value is None: return []
        if isinstance(value, list): return value
        if isinstance(value, str):
            try: return json.loads(value)
            except Exception: return []
        return list(value) if hasattr(value, "__iter__") else []

    def _apply_discount(promo, pkg_tier, original_price):
        codes = _normalise_codes(promo["package_codes"])
        if pkg_tier not in codes:
            return None
        dt = (promo["discount_type"] or "none").lower()
        dv = float(promo["discount_value"] or 0)
        orig = float(original_price)
        if dt == "percent":
            discounted = orig * (100 - dv) / 100
        elif dt == "fixed_off":
            discounted = max(0, orig - dv)
        elif dt == "fixed_price":
            discounted = dv
        else:
            return None
        discounted = round(discounted)
        savings = orig - discounted
        if savings <= 0:
            return None
        return {
            "promo_id": promo["id"],
            "promo_code": promo["code"],
            "promo_name": promo["name"],
            "original": orig,
            "discounted": float(discounted),
            "savings": round(savings, 2),
        }

    # personal retention discount (best-single vs campaign) — frontend sends X-Telegram-InitData
    _tg = 0
    try:
        _init = request.headers.get("X-Telegram-InitData", "")
        if _init:
            _ui = _verify_telegram_init_data(_init, os.environ.get("SALES_BOT_TOKEN", ""))
            if _ui:
                _tg = int(_ui.get("id") or 0)
    except Exception:
        _tg = 0
    _ret_pct = await _get_customer_retention_pct(_tg) if _tg else 0

    # Split packages vs gacha
    packages = []
    gachas = []
    for pkg in pkg_rows:
        d = dict(pkg)
        d["price"] = float(d["price"])
        # Find best discount for this package
        best = None
        for promo in promo_rows:
            calc = _apply_discount(promo, d["tier"], d["price"])
            if calc and (best is None or calc["savings"] > best["savings"]):
                best = calc
        # personal retention (best-single): show it only if it beats the campaign
        if _ret_pct > 0 and not d["tier"].startswith("GACHA_"):
            _rdisc = round(d["price"] * (100 - _ret_pct) / 100)
            _rsav = d["price"] - _rdisc
            if best is None or _rsav > best["savings"]:
                best = {
                    "promo_id": None,
                    "promo_code": "RETENTION",
                    "promo_name": f"ส่วนลดต่ออายุ {_ret_pct}%",
                    "original": float(d["price"]),
                    "discounted": float(_rdisc),
                    "savings": round(_rsav, 2),
                }
        d["discount"] = best

        if d["tier"].startswith("GACHA_"):
            gachas.append(d)
        else:
            packages.append(d)

    return {
        "packages": packages,
        "gacha": gachas,
        "promos": [
            {
                "id": p["id"],
                "code": p["code"],
                "name": p["name"],
                "discount_type": p["discount_type"],
                "discount_value": float(p["discount_value"] or 0),
            }
            for p in promo_rows
        ],
    }


# ─── Buy endpoint — Mini App POSTs here when user clicks "ซื้อเลย" ───
def _verify_telegram_init_data(init_data: str, bot_token: str) -> dict | None:
    """Verify initData from Telegram WebApp. Returns parsed user dict if valid."""
    if not init_data or not bot_token:
        return None
    try:
        parsed = urllib.parse.parse_qs(init_data)
        # Build data_check_string
        recv_hash = parsed.pop("hash", [None])[0]
        if not recv_hash:
            return None
        kv = sorted(f"{k}={v[0]}" for k, v in parsed.items())
        data_check_string = "\n".join(kv)
        # Secret key = HMAC_SHA256(bot_token, "WebAppData")
        secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
        my_hash = hmac.new(secret, data_check_string.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(my_hash, recv_hash):
            return None
        # Extract user
        user_str = parsed.get("user", [""])[0]
        if user_str:
            return json.loads(user_str)
    except Exception as exc:
        logger.warning("initData verify failed: %s", exc)
    return None


async def _get_customer_retention_pct(tg_id) -> int:
    """Active per-customer retention discount % (comeback_dm_log, not purchased, within 48h). 0 if none."""
    if not tg_id:
        return 0
    try:
        row = await pool.fetchrow(
            "SELECT discount_pct FROM comeback_dm_log "
            "WHERE telegram_id = $1 AND purchased = FALSE "
            "  AND sent_at > (now() AT TIME ZONE 'UTC') - interval '48 hours' "
            "ORDER BY sent_at DESC LIMIT 1",
            int(tg_id),
        )
        return int(row["discount_pct"]) if row and row["discount_pct"] else 0
    except Exception:
        return 0


async def _compute_pkg_price(tier_full: str, promo_id, tg_id=None) -> "dict | None":
    """SERVER-SIDE price for a package (revenue-leak fix — never trust client payload.price).

    Returns {"base": int, "final": int}: real DB price minus a *validated* campaign promo
    (promo must be active AND the package tier must be in its package_codes). None if invalid tier.
    """
    import json as _json
    tdb = tier_full if (tier_full.startswith("TIER_") or tier_full.startswith("GACHA_")) else f"TIER_{tier_full}"
    tshort = tdb.replace("TIER_", "")
    row = await pool.fetchrow("SELECT price FROM packages WHERE tier::text = $1 AND is_active = TRUE", tdb)
    if not row:
        return None
    base = float(row["price"])
    final = base
    if promo_id:
        try:
            pr = await pool.fetchrow(
                "SELECT package_codes, discount_type, discount_value, is_active FROM promotions WHERE id = $1",
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
                elif dt == "fixed_off":
                    final = max(0, base - dv)
                elif dt == "fixed_price":
                    final = dv
    # personal RETENTION discount — best-single (no stacking): keep whichever gives the lowest price
    _src = "campaign" if final < base else "none"
    _credit_used = 0.0
    if tg_id:
        _ret = await _get_customer_retention_pct(tg_id)
        if _ret > 0:
            _ret_final = base * (100 - _ret) / 100
            if _ret_final < final:
                final = _ret_final
                _src = "retention:%d%%" % _ret
        # personal GACHA CREDIT (฿ balance) — best-single (compare vs current best price)
        try:
            _br = await pool.fetchrow("SELECT balance FROM user_discount_credits WHERE telegram_id=$1", int(tg_id))
            _bal = float(_br["balance"]) if _br and _br["balance"] else 0.0
        except Exception:
            _bal = 0.0
        if _bal > 0:
            _cap = 50.0
            try:
                _cr = await pool.fetchrow("SELECT value_json FROM promo_config WHERE config_key='gacha_discount_cap_per_tier'")
                if _cr and _cr["value_json"]:
                    import json as _jgc
                    _cm = _cr["value_json"] if isinstance(_cr["value_json"], dict) else _jgc.loads(_cr["value_json"])
                    _cap = float(_cm.get(tshort, 50))
            except Exception:
                _cap = 50.0
            _usable = min(_bal, _cap, base)
            if _usable > 0 and (base - _usable) < final:
                final = base - _usable
                _src = "gacha_credit"
                _credit_used = _usable
    return {"base": int(round(base)), "final": int(round(final)), "source": _src, "credit_used": round(_credit_used, 2)}


@router.post("/webapp/api/customer/buy")
async def customer_buy(request: Request):
    """Customer ใน Mini App กด 'ซื้อเลย' → POST มาที่นี่ → ส่ง QR กลับใน chat ผ่าน Bot API."""
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(400, "invalid JSON")

    init_data = payload.get("init_data") or request.headers.get("X-Telegram-InitData", "")
    bot_token = os.environ.get("SALES_BOT_TOKEN", "")
    user_info = _verify_telegram_init_data(init_data, bot_token)
    if not user_info:
        # FIX 2026-06-28: fail-closed — reject if HMAC verify fails (gan spoof tg_id)
        # Allow only when init_data is empty (e.g., bot uploaded direct test) AND ENV ALLOW_TEST_BUY=1
        if init_data or os.environ.get("ALLOW_TEST_BUY", "0") != "1":
            raise HTTPException(401, "initData verification failed")
        user_info = payload.get("user") or {}
    tg_id = int(user_info.get("id") or 0)
    if not tg_id:
        raise HTTPException(400, "user not found")

    tier_full = (payload.get("tier") or "").strip()
    short_tier = tier_full.replace("TIER_", "")
    price = int(payload.get("price") or 0)
    pkg_name = payload.get("name") or tier_full
    promo_id = payload.get("promo_id")

    logger.info("MINIAPP_BUY: tg=%s tier=%s price=%s promo=%s", tg_id, tier_full, price, promo_id)

    # GACHA — ส่ง message guide
    if tier_full.startswith("GACHA_"):
        # ส่งปุ่ม callback ลูกค้ากด → เข้า gacha_buy flow เดิม
        spins = 1
        if tier_full == "GACHA_3": spins = 3
        elif tier_full == "GACHA_10": spins = 10
        msg = (
            f"🎰 <b>คุณเลือก: {pkg_name}</b>\n"
            f"💰 ราคา ฿{price:,}\n\n"
            "กดปุ่มด้านล่าง 👇 เพื่อซื้อ"
        )
        keyboard = {
            "inline_keyboard": [[
                {"text": f"🎫 ซื้อ {spins} หมุน ฿{price:,}", "callback_data": f"gacha_buy_{spins}"}
            ]]
        }
        # GACHA intent (กรณีลูกค้าโอนเลยไม่กดปุ่ม callback)
        try:
            await _create_intent(
                tg_id=tg_id, tier=tier_full,
                original_price=price, final_price=price,
                promo_id=int(promo_id) if promo_id else None,
                source="miniapp", ttl_minutes=30,
            )
        except Exception as _gex:
            logger.warning("gacha intent create failed: %s", _gex)
        await _send_bot_message(bot_token, tg_id, msg, reply_markup=keyboard)
        return {"ok": True, "action": "gacha_button_sent"}

    # ── SERVER-SIDE price (revenue-leak fix): recompute from DB, ignore client payload.price ──
    _pinfo = await _compute_pkg_price(tier_full, promo_id, tg_id)
    if not _pinfo:
        await _send_bot_message(bot_token, tg_id, "⚠️ แพ็กเกจไม่ถูกต้อง กรุณาลองใหม่ค่ะ")
        return {"ok": False, "error": "invalid_package"}
    if price and price != _pinfo["final"]:
        logger.warning("MINIAPP_BUY price corrected: client=%s server=%s tg=%s tier=%s",
                       price, _pinfo["final"], tg_id, tier_full)
    price = _pinfo["final"]

    # PACKAGE — record promo_click + pick receiver + send QR
    if promo_id:
        try:
            from shared.promotion_service import record_click
            await record_click(int(promo_id), tg_id, tier_full)
        except Exception as exc:
            logger.warning("promo click record failed: %s", exc)

    # Create purchase_intent (ตั๋ว) — sales bot ใช้เป็น fallback selected_tier
    _orig_price = _pinfo["base"]
    _intent_id = await _create_intent(
        tg_id=tg_id,
        tier=tier_full if tier_full.startswith("TIER_") else f"TIER_{tier_full}",
        original_price=_orig_price,
        final_price=price,
        promo_id=int(promo_id) if promo_id else None,
        source="miniapp",
        ttl_minutes=30,
        discount_credit=_pinfo.get("credit_used", 0),
    )

    from shared.receiver_pool import pick_random
    acct = await pick_random()
    if not acct:
        await _send_bot_message(bot_token, tg_id, "⚠️ ระบบรับเงินไม่พร้อม กรุณาทักแอดมินค่ะ")
        return {"ok": False, "error": "no_receiver"}
    # เก็บบัญชีที่สุ่มได้ลง intent -> ใช้ตอนนับยอด (รู้ตั้งแต่ pick)
    if _intent_id:
        try:
            await _set_intent_receiver(_intent_id, acct["id"])
        except Exception:
            pass

    body = (
        f"💳 <b>คำสั่งซื้อ: {pkg_name}</b>\n"
        "━━━━━━━━━━━━━━━\n\n"
        f"💰 ยอดที่ต้องโอน: <b>฿{price:,}</b>\n\n"
        f"🏦 ธนาคาร: <b>{acct.get('bank_name_th', '')}</b>\n"
        f"👤 ชื่อบัญชี: <code>{acct.get('owner_name', '')}</code>\n"
        f"🔢 เลขบัญชี: <code>{acct.get('account_no', '')}</code>\n"
    )
    if acct.get("promptpay_number"):
        body += f"📱 PromptPay: <code>{acct['promptpay_number']}</code>\n"
    body += (
        "\n━━━━━━━━━━━━━━━\n"
        "📸 ส่ง <b>สลิปการโอน</b> ในแชทนี้\n"
        "⚡ ระบบจะอัปเกรดอัตโนมัติทันที"
    )
    await _send_bot_message(bot_token, tg_id, body)

    qr_url = acct.get("qr_url") or ""
    if qr_url:
        await _send_bot_photo(bot_token, tg_id, qr_url,
            caption=f"📱 สแกน QR เพื่อโอน <b>฿{price:,}</b>")

    return {"ok": True, "action": "qr_sent"}


async def _send_bot_message(token: str, chat_id: int, text: str, reply_markup: dict = None):
    import httpx
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    async with httpx.AsyncClient(timeout=10) as c:
        try:
            r = await c.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json=payload,
            )
            r.raise_for_status()
        except Exception as exc:
            logger.warning("sendMessage failed tg=%s: %s", chat_id, exc)


async def _send_bot_photo(token: str, chat_id: int, photo_url: str, caption: str = ""):
    import httpx, os as _os
    url = f"https://api.telegram.org/bot{token}/sendPhoto"
    async with httpx.AsyncClient(timeout=20) as c:
        try:
            if photo_url.startswith("http"):
                r = await c.post(url, json={"chat_id": chat_id, "photo": photo_url, "caption": caption, "parse_mode": "HTML"})
            else:
                # AUDIT FIX: relative /assets path -> upload bytes (Telegram fetch relative URL ไม่ได้)
                local = _os.path.realpath("/app/dashboard/frontend" + photo_url)
                if not local.startswith("/app/dashboard/frontend/assets/receiver_qr/"):
                    logger.warning("QR path rejected (traversal?): %s", photo_url)
                    return
                if not _os.path.exists(local):
                    logger.warning("QR file not found: %s", local)
                    return
                with open(local, "rb") as _fp:
                    r = await c.post(url, data={"chat_id": str(chat_id), "caption": caption, "parse_mode": "HTML"}, files={"photo": _fp})
            r.raise_for_status()
        except Exception as exc:
            logger.warning("sendPhoto failed tg=%s: %s", chat_id, exc)
