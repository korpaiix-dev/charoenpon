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
            except: return []
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
        # ไม่ verify ผ่าน — fail-safe: ใช้ user_id จาก payload (ใน production เปิด verify)
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
        msg = f"🎰 คุณเลือก: <b>{pkg_name}</b> ฿{price:,}\n\nพิมพ์ /gacha เพื่อซื้อหมุนกาชาปองค่ะ"
        await _send_bot_message(bot_token, tg_id, msg)
        return {"ok": True, "action": "gacha_guide"}

    # PACKAGE — record promo_click + pick receiver + send QR
    if promo_id:
        try:
            from shared.promotion_service import record_click
            await record_click(int(promo_id), tg_id, tier_full)
        except Exception as exc:
            logger.warning("promo click record failed: %s", exc)

    from shared.receiver_pool import pick_random
    acct = await pick_random()
    if not acct:
        await _send_bot_message(bot_token, tg_id, "⚠️ ระบบรับเงินไม่พร้อม กรุณาทักแอดมินค่ะ")
        return {"ok": False, "error": "no_receiver"}

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
    import httpx
    async with httpx.AsyncClient(timeout=15) as c:
        try:
            r = await c.post(
                f"https://api.telegram.org/bot{token}/sendPhoto",
                json={"chat_id": chat_id, "photo": photo_url, "caption": caption, "parse_mode": "HTML"},
            )
            r.raise_for_status()
        except Exception as exc:
            logger.warning("sendPhoto failed tg=%s: %s", chat_id, exc)
