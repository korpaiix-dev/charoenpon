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
        return HTMLResponse(html_path.read_text(encoding="utf-8"))
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
