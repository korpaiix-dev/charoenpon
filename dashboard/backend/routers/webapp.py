"""Telegram WebApp customer dashboard router."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qsl

from fastapi import APIRouter, HTTPException, Header, Request
from fastapi.responses import HTMLResponse, JSONResponse

from ..database import pool

router = APIRouter(prefix="/webapp", tags=["webapp"])

BOT_TOKEN = os.environ.get("SALES_BOT_TOKEN") or os.environ.get("BOT_TOKEN") or ""
MAX_AGE_SECONDS = 86400

_HTML_PATH = Path(__file__).parent.parent / "static" / "customer_webapp.html"


def _verify_init_data(init_data: str) -> dict | None:
    if not init_data or not BOT_TOKEN:
        return None
    try:
        parsed = dict(parse_qsl(init_data, strict_parsing=False))
    except Exception:
        return None
    received_hash = parsed.pop("hash", None)
    if not received_hash:
        return None
    data_check = "\n".join(f"{k}={v}" for k, v in sorted(parsed.items()))
    secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret_key, data_check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received_hash):
        return None
    try:
        auth_date = int(parsed.get("auth_date", "0"))
        if (datetime.utcnow().timestamp() - auth_date) > MAX_AGE_SECONDS:
            return None
    except Exception:
        return None
    try:
        user = json.loads(parsed.get("user", "{}"))
    except Exception:
        return None
    if not user.get("id"):
        return None
    return user


_LOGO_PATH = Path(__file__).parent.parent / "static" / "logo_charoenpon.png"


@router.get("/logo.png")
async def webapp_logo():
    """Serve VIP เจริญพร logo (transparent PNG) for Mini App."""
    from fastapi.responses import FileResponse
    if _LOGO_PATH.exists():
        return FileResponse(str(_LOGO_PATH), media_type="image/png", headers={"Cache-Control": "public, max-age=86400"})
    from fastapi import HTTPException
    raise HTTPException(404, "logo not found")


@router.get("/customer", response_class=HTMLResponse)
async def customer_page():
    try:
        with open(_HTML_PATH, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    except FileNotFoundError:
        raise HTTPException(404, "webapp template missing")


@router.get("/api/me")
async def api_me(request: Request,
                  x_tg_init_data: str | None = Header(default=None, alias="X-Tg-Init-Data")):
    tg_user = _verify_init_data(x_tg_init_data or "")
    if not tg_user:
        raise HTTPException(status_code=401, detail="invalid init data")
    tg_id = tg_user["id"]
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT u.id, u.telegram_id, u.username, u.first_name, u.last_name, "
            "       u.total_spent, u.loyalty_rank, "
            "       (SELECT COUNT(*) FROM payments p WHERE p.user_id=u.id AND p.status='CONFIRMED') AS paid_count, "
            "       (SELECT COALESCE(SUM(amount),0) FROM payments p WHERE p.user_id=u.id AND p.status='CONFIRMED') AS total_paid "
            "FROM users u WHERE u.telegram_id=$1", tg_id
        )
        if not row:
            return JSONResponse({
                "user": {"telegram_id": tg_id, "first_name": tg_user.get("first_name", ""),
                         "paid_count": 0, "total_paid": 0},
                "subscription": None, "last_payments": [], "gacha": None
            })
        user = dict(row)
        # FIX 2026-06-22: JOIN packages to return tier name + package name + price
        sub_row = await conn.fetchrow(
            "SELECT s.package_id, s.status, s.start_date, s.end_date, "
            "       p.name AS package_name, p.tier AS tier_name, p.price AS tier_price "
            "FROM subscriptions s "
            "LEFT JOIN packages p ON p.id = s.package_id "
            "WHERE s.user_id=$1 ORDER BY s.end_date DESC NULLS LAST LIMIT 1",
            user["id"]
        )
        sub = None
        if sub_row:
            srow = dict(sub_row)
            days_left = None
            if srow["end_date"]:
                from shared.subscription_access import days_left as _days_left; days_left = _days_left(srow["end_date"])  # Cleanup-B: utcnow-based (was now_th, ~7h skew)
            # Friendly tier label (strip TIER_ prefix; LIFETIME = 36500 days)
            tier_label = (srow.get("tier_name") or "").replace("TIER_", "") or str(srow.get("package_id") or "?")
            sub = {
                "tier": tier_label,                            # NEW: clean label เช่น "1299"
                "tier_name": srow.get("tier_name"),            # NEW: "TIER_1299"
                "package_id": srow.get("package_id"),          # raw int (เก็บไว้ debug)
                "package_name": srow.get("package_name") or "",# NEW: "GOD MODE 90 วัน"
                "tier_price": float(srow["tier_price"]) if srow.get("tier_price") is not None else None,
                "status": srow["status"],
                "end_date": srow["end_date"].isoformat() if srow["end_date"] else None,
                "days_left": days_left,
                "is_lifetime": bool(srow["end_date"] and getattr(srow["end_date"], "year", 0) >= 2099),  # Cleanup-B: robust (was days_left>30000, missed 2099 lifetimes)
            }
        pay_rows = await conn.fetch(
            "SELECT amount, status, created_at FROM payments "
            "WHERE user_id=$1 ORDER BY created_at DESC LIMIT 5",
            user["id"]
        )
        pays = [{
            "amount": float(p["amount"]),
            "status": p["status"],
            "created_at": p["created_at"].isoformat() if p["created_at"] else None,
        } for p in pay_rows]
        gacha_row = await conn.fetchrow(
            "SELECT credits FROM gachapon_credits WHERE user_id=$1", user["id"]
        )
        gacha = {"credits": gacha_row[0]} if gacha_row else None

        # NEW 2026-06-28: discount balance + is_god flag
        disc_bal = 0.0
        try:
            disc_row = await conn.fetchrow(
                "SELECT balance FROM user_discount_credits WHERE telegram_id = $1",
                tg_id,
            )
            if disc_row:
                disc_bal = float(disc_row["balance"] or 0)
        except Exception:
            pass
        is_god_flag = bool(sub and sub.get("tier_name") == "TIER_2499")
    return JSONResponse({
        "user": {
            "telegram_id": user["telegram_id"],
            "first_name": user.get("first_name") or "",
            "username": user.get("username"),
            "paid_count": int(user["paid_count"]),
            "total_paid": float(user["total_paid"]),
            "loyalty_rank": user.get("loyalty_rank") or "NONE",
        },
        "subscription": sub,
        "last_payments": pays,
        "gacha": gacha,
        "discount_balance": disc_bal,
        "is_god": is_god_flag,
    })


@router.post("/api/request_packages")
async def request_packages(request: Request,
                            x_tg_init_data: str | None = Header(default=None, alias="X-Tg-Init-Data")):
    """Trigger bot to send packages menu to user via Bot API.

    Discount-aware:
      - Active comeback promo (last 48h, unpurchased): show notice + special button
      - Discount credit balance: show notice (auto-applied at tier-select)
    """
    tg_user = _verify_init_data(x_tg_init_data or "")
    if not tg_user:
        raise HTTPException(status_code=401, detail="invalid init data")
    tg_id = tg_user["id"]

    import httpx
    token = BOT_TOKEN
    if not token:
        raise HTTPException(500, "bot token not configured")

    notices = []
    comeback = None
    discount_bal = 0

    async with pool.acquire() as conn:
        # 1) Check active comeback promo (48h window, not purchased)
        cb = await conn.fetchrow(
            "SELECT discount_pct, promo_code, sent_at, round "
            "FROM comeback_dm_log WHERE telegram_id=$1 "
            "  AND purchased = FALSE "
            "  AND sent_at >= NOW() - interval '48 hours' "
            "ORDER BY sent_at DESC LIMIT 1",
            tg_id
        )
        if cb:
            cb_d = dict(cb)
            comeback = {
                "discount_pct": cb_d["discount_pct"],
                "promo_code": cb_d["promo_code"],
                "round": cb_d["round"],
            }
            source = "ลูกค้ากลับมา" if (cb_d["round"] or 0) < 100 else "Retention"
            notices.append(
                f"\U0001F49D <b>คุณมีโปรพิเศษ {source} ลด {cb_d['discount_pct']}%</b>\n"
                f"   ใช้รหัส: <code>{cb_d['promo_code']}</code> (หมดอายุ 48 ชม.)"
            )

        # 2) Check discount credit balance
        dc = await conn.fetchrow(
            "SELECT balance FROM user_discount_credits WHERE telegram_id=$1",
            tg_id
        )
        if dc and float(dc["balance"]) > 0:
            discount_bal = float(dc["balance"])
            notices.append(
                f"\U0001F3B0 <b>คุณมี gacha credit ฿{discount_bal:,.0f}</b>\n"
                f"   ลดอัตโนมัติตอนเลือกแพ็กเกจ"
            )

        # 3) Get packages
        rows = await conn.fetch(
            "SELECT id, name, price, duration_days FROM packages "
            "WHERE is_active = TRUE ORDER BY price ASC"
        )
        packages = [dict(r) for r in rows]

    # Build message
    header = "<b>\U0001F4E6 แพ็กเกจ VIP เจริญพร</b>\n\n"
    if notices:
        header += "\n\n".join(notices) + "\n\n"
    header += "เลือกแพ็กเกจที่ต้องการ:"

    keyboard = [[{
        "text": "\U0001F4E6 ดูแพ็กเกจทั้งหมด",
        "callback_data": "view_packages"
    }]]

    payload = {
        "chat_id": tg_id,
        "text": header,
        "parse_mode": "HTML",
        "reply_markup": {"inline_keyboard": keyboard},
    }

    async with httpx.AsyncClient(timeout=10.0) as cli:
        r = await cli.post(f"https://api.telegram.org/bot{token}/sendMessage", json=payload)
        if r.status_code != 200:
            raise HTTPException(502, f"bot api error: {r.text[:200]}")

    return JSONResponse({
        "ok": True,
        "has_comeback": comeback is not None,
        "discount_credit": discount_bal,
    })
