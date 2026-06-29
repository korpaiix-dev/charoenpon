"""Pricing Hub — single source of truth for prices, promo windows, and tier mapping.

Replaces:
- `shared/slip2go.py amount_to_tier()` (inline hardcoded)
- `bots/sales_bot/handlers/payment.py _get_effective_price()` (inline tier prices)
- `bots/sales_bot/handlers/payment.py tier_map_local` dict (3 places)
- `bots/sales_bot/handlers/payment.py` admin fallback keyboards (3 places)
- `bots/admin_bot/handlers/approval.py tier_map` dispatcher
- All ad-hoc `is_*_active()` checks scattered across packages.py / social_proof.py / etc.

Design:
- One data table (`TIER_PRICES`, `PROMO_PRICES`) for current ladder.
- One function (`amount_to_tier`) for slip + TrueMoney resolution.
- One function (`effective_price`) for "what should the bot quote this user?".
- One function (`current_campaign`) for active promo window detection.
- One generator (`approve_buttons`) for admin keyboards.

Usage:
    from shared.pricing import (
        TIER_PRICES, amount_to_tier, effective_price,
        current_campaign, tier_str_to_enum, approve_buttons,
    )
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable, Optional

from shared.tz import now_th


# ─── Base price ladder ────────────────────────────────────────────────────
# Canonical tier_str ⇄ price ⇄ PackageTier enum mapping.
# Add a new tier here and it propagates automatically.
TIER_PRICES: dict[str, Decimal] = {
    "300":  Decimal("300"),     # TIER_300  — VIP 30 d
    "500":  Decimal("500"),     # TIER_500  — OF+VIP 30 d
    "1299": Decimal("1299"),    # TIER_1299 — GOD 90 d
    "2499": Decimal("2499"),    # TIER_2499 — GOD lifetime
    "100":  Decimal("100"),     # TIER_100  — ห้องมีคนชัก lottery
    "ADD500": Decimal("500"),   # TIER_ADD500 — Summer Fest add-on
    "BIRTHDAY_1299": Decimal("899"),   # Birthday upgrade GOD 3m (only via /upgrade)
    "BIRTHDAY_2499": Decimal("1999"),  # Birthday upgrade GOD lifetime (only via /upgrade)
    "GACHA_1":  Decimal("99"),   # Gacha bundle: 1 spin
    "GACHA_3":  Decimal("270"),  # Gacha bundle: 3 spins
    "GACHA_10": Decimal("890"),  # Gacha bundle: 10 spins
}

# FIX 2026-06-26 (audit): in-memory cache for DB override (refresh every 60s)
import time as _ptime
_gacha_price_cache = {"val": None, "expires": 0}


def gacha_spin_pricing_override() -> dict[str, Decimal]:
    """Read current gacha_spin_pricing from DB. Falls back to TIER_PRICES default."""
    now = _ptime.time()
    if _gacha_price_cache["val"] and _gacha_price_cache["expires"] > now:
        return _gacha_price_cache["val"]
    out = {
        "GACHA_1":  Decimal("99"),
        "GACHA_3":  Decimal("270"),
        "GACHA_10": Decimal("890"),
    }
    try:
        import os
        import psycopg2  # type: ignore
        dsn = os.environ.get("DATABASE_URL") or ""
        dsn = dsn.replace("postgresql+asyncpg://", "postgresql://")
        if dsn:
            with psycopg2.connect(dsn) as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT tier, price_thb FROM gacha_spin_pricing")
                    for tier, price in cur.fetchall():
                        if tier in out:
                            out[tier] = Decimal(str(price))
    except Exception:
        pass
    _gacha_price_cache["val"] = out
    _gacha_price_cache["expires"] = now + 60
    return out


# tier_str → PackageTier enum (imported lazily to avoid circular imports)
def tier_str_to_enum(tier_str: str):
    from shared.models import PackageTier
    return {
        "99":   PackageTier.TIER_99,
        "100":  PackageTier.TIER_100,
        "300":  PackageTier.TIER_300,
        "500":  PackageTier.TIER_500,
        "1299": PackageTier.TIER_1299,
        "2499": PackageTier.TIER_2499,
        "ADD500": PackageTier.TIER_ADD500,
        "BIRTHDAY_1299": PackageTier.TIER_1299,
        "BIRTHDAY_2499": PackageTier.TIER_2499,
    }.get(tier_str)


# ─── Campaign definitions ─────────────────────────────────────────────────
# Each campaign has:
#   key      — short id used in code
#   label    — human-readable name
#   active() — closure returning True if NOW is inside the window (BKK)
#   prices   — amount → (tier_str, label)
#   slip_grace_hours — extra window for slip2go matching (default 0)

@dataclass(frozen=True)
class Campaign:
    key: str
    label: str
    starts_text: str
    is_active: bool
    prices: dict[int, tuple[str, str]]   # amount (int) → (tier_str, button_label)

    def amount_to_tier(self, amt: int) -> Optional[tuple[str, str]]:
        return self.prices.get(amt)


def _lucky_6_active() -> bool:
    """DISABLED 2026-06-21: บอสไม่ใช้แล้ว."""
    return False



def _birthday_active() -> bool:
    """DISABLED 2026-06-21: บอสไม่ใช้แล้ว."""
    return False



def _mid_month_flash_active() -> bool:
    """DISABLED 2026-06-21: บอสไม่ใช้แล้ว."""
    return False



def _endmonth_vip_active() -> bool:
    """DISABLED 2026-06-21: บอสไม่ใช้แล้ว."""
    return False



def _may_combo_active() -> bool:
    """Old promo — kept for slip backward-compat for 24h after end."""
    return False  # currently inactive; flash_sales table is authoritative


def _comeback_grace() -> bool:
    """Comeback prices (180/210) are always allowed at the price-level;
    the per-user entitlement is validated downstream against comeback_dm_log."""
    return True


def _retention_active() -> bool:
    """DISABLED 2026-06-21: บอสไม่ใช้ retention -10/-15/-20% แล้ว
    เปลี่ยนเป็น False ทำให้ TIER_300/500/1299/2499 ราคาเต็ม + เหลือแค่ exit_survey + comeback."""
    return False


# ─── Build campaign list (priority order: first match wins) ──────────────
def _all_campaigns() -> list[Campaign]:
    return [
        Campaign(
            key="lucky_6_6", label="Lucky 6.6 Sale",
            starts_text="6 มิ.ย. 2026",
            is_active=_lucky_6_active(),
            prices={
                166:  ("300",  "🍀 166 (Lucky VIP)"),
                266:  ("500",  "🍀 266 (Lucky OF)"),
                666:  ("1299", "🍀 666 (Lucky GOD3M)"),
                2266: ("2499", "🍀 2266 (Lucky ถาวร)"),
            },
        ),
        Campaign(
            key="birthday", label="Birthday เฮียตั๋ง",
            starts_text="7-10 มิ.ย. 2026",
            is_active=_birthday_active(),
            prices={},   # birthday is a giveaway, not a price discount — base prices apply
        ),
        Campaign(
            key="mid_month_flash", label="Mid-Month Flash 48 ชม.",
            starts_text="15-17 มิ.ย. 2026",
            is_active=_mid_month_flash_active(),
            prices={
                199: ("300",  "🔥 199 (Flash VIP)"),
                349: ("500",  "🔥 349 (Flash OF)"),
                999: ("1299", "🔥 999 (Flash GOD)"),
            },
        ),
        Campaign(
            key="endmonth_vip", label="End-month VIP Promo",
            starts_text="28-30 of month",
            is_active=_endmonth_vip_active(),
            prices={
                200:  ("300",  "🔥 200 (VIP โปร)"),
                2000: ("2499", "💎 2000 (GOD โปร)"),
            },
        ),
        Campaign(
            key="comeback", label="Comeback",
            starts_text="always (per-user)",
            is_active=_comeback_grace(),
            prices={
                180: ("300", "💔 180 (Comeback -40%)"),
                210: ("300", "💔 210 (Comeback -30%)"),
            },
        ),
        Campaign(
            key="retention_discount", label="Retention Discount (per-user)",
            starts_text="always (per-user)",
            is_active=_retention_active(),
            prices={
                # TIER_300 (VIP 30 วัน)
                269: ("300", "🎁 Retention -10% VIP"),
                255: ("300", "🎁 Retention -15% VIP"),
                240: ("300", "🎁 Retention -20% VIP"),
                # TIER_500 (OF + VIP)
                450: ("500", "🎁 Retention -10% OF+VIP"),
                425: ("500", "🎁 Retention -15% OF+VIP"),
                400: ("500", "🎁 Retention -20% OF+VIP"),
                # TIER_1299 (GOD 90 วัน)
                1169: ("1299", "🎁 Retention -10% GOD 90"),
                1104: ("1299", "🎁 Retention -15% GOD 90"),
                1039: ("1299", "🎁 Retention -20% GOD 90"),
                # TIER_2499 (GOD ถาวร)
                2249: ("2499", "🎁 Retention -10% GOD ถาวร"),
                2124: ("2499", "🎁 Retention -15% GOD ถาวร"),
                1998: ("2499", "🎁 Retention -20% GOD ถาวร"),
            },
        ),
        Campaign(
            key="exit_survey", label="Exit Survey Win-back",
            starts_text="always (per-user)",
            is_active=True,
            prices={
                150:  ("300",  "💝 Exit Survey -50% VIP"),
                295:  ("500",  "💝 Exit Survey -40% GOLD"),
                909:  ("1299", "💝 Exit Survey -30% MAS"),
                1997: ("2499", "💝 Exit Survey -20% GOD"),
            },
        ),
    ]


def current_campaign() -> Optional[Campaign]:
    """First active campaign (priority order)."""
    for c in _all_campaigns():
        if c.is_active:
            return c
    return None


def active_campaigns() -> list[Campaign]:
    return [c for c in _all_campaigns() if c.is_active]


# ─── Public API ───────────────────────────────────────────────────────────

# FIX 2026-06-29 (#446): cache + sync reader for DB promotions table
# (amount_to_tier ถูกเรียกจาก sync context — ใช้ psycopg2 ไม่ใช่ asyncpg)
_PROMO_CACHE = {"items": None, "expires": 0.0}


def _load_active_db_promotions_sync() -> list[dict]:
    """Load active promotions from DB. Cached 60s.

    Implementation: tries psycopg2 first (sync path used by dashboard).
    Falls back to asyncpg via thread + new event loop (sales-bot container
    doesn't ship psycopg2 — only asyncpg).
    """
    import time as _t
    now = _t.time()
    if _PROMO_CACHE["items"] is not None and _PROMO_CACHE["expires"] > now:
        return _PROMO_CACHE["items"]
    items: list[dict] = []
    try:
        import os as _os
        db_url = _os.environ.get("DATABASE_URL", "")
        if not db_url:
            _PROMO_CACHE["items"] = items
            _PROMO_CACHE["expires"] = now + 60
            return items
        sync_url = db_url.replace("postgresql+asyncpg://", "postgresql://")

        # ── Path A: psycopg2 (dashboard, etc.) ──
        try:
            import psycopg2  # type: ignore
            from psycopg2.extras import RealDictCursor  # type: ignore
            conn = psycopg2.connect(sync_url)
            try:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute("""
                        SELECT id, code, name, package_codes, discount_type, discount_value
                          FROM promotions
                         WHERE is_active = TRUE
                           AND (starts_at IS NULL OR starts_at <= NOW())
                           AND (ends_at   IS NULL OR ends_at   >= NOW())
                    """)
                    for r in cur.fetchall():
                        items.append(dict(r))
            finally:
                conn.close()
        except ImportError:
            # ── Path B: asyncpg via thread+loop ──
            import asyncio as _aio
            import threading as _th
            async def _fetch():
                import asyncpg
                conn = await asyncpg.connect(sync_url)
                try:
                    rows = await conn.fetch("""
                        SELECT id, code, name, package_codes, discount_type, discount_value
                          FROM promotions
                         WHERE is_active = TRUE
                           AND (starts_at IS NULL OR starts_at <= NOW())
                           AND (ends_at   IS NULL OR ends_at   >= NOW())
                    """)
                    return [dict(r) for r in rows]
                finally:
                    await conn.close()
            result_box = {"v": []}
            def _runner():
                try:
                    loop = _aio.new_event_loop()
                    try:
                        result_box["v"] = loop.run_until_complete(_fetch())
                    finally:
                        loop.close()
                except Exception:
                    pass
            t = _th.Thread(target=_runner, daemon=True)
            t.start()
            t.join(timeout=3.0)
            items = result_box["v"]
    except Exception:
        # fail-open: no DB promos → fallback to base_map
        pass
    _PROMO_CACHE["items"] = items
    _PROMO_CACHE["expires"] = now + 60
    return items


def _check_db_promotion_match(amt: int) -> Optional[tuple[str, str, bool]]:
    """If amt matches a Day-0 promo discounted price → return (tier_str, label, is_promo).
    Else None.
    """
    promos = _load_active_db_promotions_sync()
    if not promos:
        return None
    # Map TIER_xxx in package_codes → base price + label from existing TIER_PRICES + base_map
    # We mirror the base_map's tier-label so output is callback-compatible
    base_label_by_tier_str = {
        "100":  ("100", "ห้องมีคนชัก"),
        "300":  ("300", "VIP 30 วัน"),
        "500":  ("500", "OnlyFans+VIP 30 วัน"),
        "1299": ("1299", "GOD MODE 90 วัน"),
        "2499": ("2499", "GOD MODE ถาวร"),
    }
    for p in promos:
        codes = p.get("package_codes") or []
        if isinstance(codes, str):
            import json as _j
            try: codes = _j.loads(codes)
            except Exception: codes = []
        dtype = (p.get("discount_type") or "").lower()
        try:
            dval = float(p.get("discount_value") or 0)
        except Exception:
            dval = 0.0
        if dval <= 0:
            continue
        for code in codes:
            tier_str = code.replace("TIER_", "")
            if tier_str not in base_label_by_tier_str:
                continue
            base = float(TIER_PRICES.get(tier_str, 0))
            if base <= 0:
                continue
            if dtype == "percent":
                discounted = int(round(base * (100 - dval) / 100))
            elif dtype in ("fixed", "amount", "baht"):
                discounted = int(round(base - dval))
            else:
                continue
            # ±1 baht tolerance for rounding
            if abs(amt - discounted) <= 1:
                _, lbl = base_label_by_tier_str[tier_str]
                promo_name = p.get("name") or p.get("code") or "Promo"
                return (tier_str, f"{promo_name} — {lbl}", True)
    return None


def amount_to_tier(amount) -> Optional[tuple[str, str, bool]]:
    """Map a paid amount to a tier callback string.

    Returns (tier_str, label, is_promo) or None if no match.
    Used by Slip2Go auto-approve AND TrueMoney verification.

    FIX 2026-06-29 (#446): also check promotions table for Day-0 promos
    (Dashboard "Promo Manager" promos like ENDMONTH20 ลด 20%)
    """
    amt = int(Decimal(amount))
    # Base prices always match
    base_map = {
        99:   ("GACHA_1",  "กาชาปอง 1 หมุน", False),
        100:  ("100",  "ห้องมีคนชัก", False),
        270:  ("GACHA_3",  "กาชาปอง 3 หมุน", False),
        890:  ("GACHA_10", "กาชาปอง 10 หมุน", False),
        199:  ("300",  "Flash Sale", False),       # legacy 199 = flash
        300:  ("300",  "VIP 30 วัน", False),
        500:  ("500",  "OnlyFans+VIP 30 วัน", False),
        899:  ("BIRTHDAY_1299", "Birthday GOD 3M", True),
        1299: ("1299", "GOD MODE 90 วัน", False),
        1999: ("BIRTHDAY_2499", "Birthday GOD ถาวร", True),
        2499: ("2499", "GOD MODE ถาวร", False),
    }
    if amt in base_map:
        return base_map[amt]
    # Promo prices — only if their campaign is active (legacy hardcoded campaigns)
    for c in active_campaigns():
        hit = c.amount_to_tier(amt)
        if hit:
            tier_str, label = hit
            return (tier_str, f"{c.label} — {label}", True)
    # FIX 2026-06-29 (#446): Day-0 promos from promotions table (Dashboard Promo Manager)
    db_hit = _check_db_promotion_match(amt)
    if db_hit:
        return db_hit
    return None


def effective_price(tier_str: str, context_user_data: Optional[dict] = None) -> Decimal:
    """What the bot should quote this user for the given tier RIGHT NOW.

    Priority: comeback (per-user promo_code) > active campaign > base.
    """
    base = TIER_PRICES.get(tier_str, Decimal("0"))
    if base == 0:
        return base
    # comeback per-user
    if context_user_data:
        comeback = context_user_data.get("comeback_promo")
        if comeback:
            try:
                from bots.sales_bot.comeback_dm import validate_promo_code
                # Sync wrapper would block; downstream code awaits this — keep async-safe
                # Here we just check if a discount_pct is present in context
                discount_pct = context_user_data.get("comeback_discount", 0)
                if discount_pct:
                    return Decimal(str(int(base * (100 - discount_pct) / 100)))
            except Exception:
                pass
    # Active campaign promo for this tier (skip per-user campaigns — comeback + exit_survey)
    for c in active_campaigns():
        if c.key in ("comeback", "exit_survey"):
            continue
        for amount, (mapped_tier, _label) in c.prices.items():
            if mapped_tier == tier_str:
                return Decimal(str(amount))
    return base


def acceptable_amounts(tier_str: str, context_user_data: Optional[dict] = None) -> set[Decimal]:
    """Set of amounts the system should ACCEPT for this tier today.

    Always includes the base price (so customers who don't know about the promo,
    or who pay full price intentionally, are not rejected — Phase 1 bug fix).
    """
    out: set[Decimal] = {TIER_PRICES.get(tier_str, Decimal("0"))}
    out.add(effective_price(tier_str, context_user_data))
    # Discount price — skip per-user campaigns (comeback + exit_survey are validated downstream)
    for c in active_campaigns():
        if c.key in ("comeback", "exit_survey"):
            continue
        for amount, (mapped_tier, _label) in c.prices.items():
            if mapped_tier == tier_str:
                out.add(Decimal(str(amount)))
    out.discard(Decimal("0"))
    return out


def approve_buttons(user_id: int) -> list[list[dict]]:
    """Build the admin approve-button matrix for the current promo state.

    Returns list-of-rows where each cell is a dict
    {text, callback_data} ready to be wrapped in InlineKeyboardButton.

    Includes base buttons + active-promo buttons + summer add-on.
    """
    rows: list[list[dict]] = []
    # Row 0: Tier 100 (ห้องมีคนชัก SHAKER) + GACHA bundles
    rows.append([
        {"text": "🎰 100 (ชัก)", "callback_data": f"approve_100_{user_id}"},
        {"text": "🎁 99 (Gacha 1)", "callback_data": f"approve_99_{user_id}"},
    ])
    rows.append([
        {"text": "🎁 270 (Gacha 3)", "callback_data": f"approve_270_{user_id}"},
        {"text": "🎁 890 (Gacha 10)", "callback_data": f"approve_890_{user_id}"},
    ])
    # Row 1: VIP and OF
    rows.append([
        {"text": "✅ 300 (VIP)",     "callback_data": f"approve_300_{user_id}"},
        {"text": "✅ 500 (OF)",      "callback_data": f"approve_500_{user_id}"},
    ])
    # Row 2: GOD 3M and GOD lifetime
    rows.append([
        {"text": "✅ 1299 (3M)",     "callback_data": f"approve_1299_{user_id}"},
        {"text": "✅ 2499 (GOD)",    "callback_data": f"approve_2499_{user_id}"},
    ])
    # Row 3: Summer add-on (always)
    rows.append([
        {"text": "🌊 500 (Summer)",  "callback_data": f"approve_ADD500_{user_id}"},
    ])
    # Active-promo rows
    for c in active_campaigns():
        if not c.prices:
            continue
        row: list[dict] = []
        for amount, (_tier, label) in c.prices.items():
            row.append({
                "text": label,
                "callback_data": f"approve_{amount}_{user_id}",
            })
            # 2 per row
            if len(row) == 2:
                rows.append(row)
                row = []
        if row:
            rows.append(row)
    return rows


# Dispatch map for admin approve callbacks (Phase 2 replaces tier_map in approval.py)
def admin_callback_tier_map() -> dict[str, str]:
    """Build {"<callback_amount>": "<tier_str>"} dict at call time."""
    out: dict[str, str] = {
        # Base
        "99":    "GACHA_1",
        "100":   "100",
        "199":   "300",  "200": "300", "300": "300",
        "270":   "GACHA_3",
        "890":   "GACHA_10",
        "349":   "500",  "500": "500",
        "999":   "1299", "1299": "1299",
        "2000":  "2499", "2499": "2499",
        "ADD500": "ADD500",
        # Comeback (always-on)
        "180": "300", "210": "300",
        # FIX 2026-06-29 (#443): amount_to_tier() ตี 1999/899 เป็น BIRTHDAY_*
        #   → ต้องมีใน map เพื่อให้ Slip2Go auto-approve ผ่าน
        #   (สำคัญตอนมีโปรลด 20% ทำให้ TIER_2499 → 1,999)
        "BIRTHDAY_2499": "2499",   # 1999 → TIER_2499
        "BIRTHDAY_1299": "1299",   # 899  → TIER_1299
    }
    # Add active campaign amounts
    for c in active_campaigns():
        for amount, (tier_str, _label) in c.prices.items():
            out[str(amount)] = tier_str
    return out


__all__ = [
    "TIER_PRICES",
    "Campaign",
    "current_campaign",
    "active_campaigns",
    "amount_to_tier",
    "effective_price",
    "acceptable_amounts",
    "approve_buttons",
    "admin_callback_tier_map",
    "tier_str_to_enum",
]
