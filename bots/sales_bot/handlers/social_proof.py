"""Social proof / welcome message stats + reviews + image rotation.

Used by start.py welcome flow.

- Lifetime stats (cached 10 min) — total members, ever paid, etc
- Rotating review quotes (admin can edit list)
- Random welcome image from /root/charoenpon/assets/campaigns/
- Designed to NOT depend on daily volatility (boss directive)
"""
from __future__ import annotations

import logging
import os
import random
import time
from pathlib import Path

from sqlalchemy import func, select, text

from shared.database import get_session
from shared.models import Payment, PaymentStatus, User

logger = logging.getLogger(__name__)

# ─── Assets ───────────────────────────────────────────────────────────────────
ASSETS_DIR = Path(__file__).resolve().parents[3] / "assets" / "campaigns"
WELCOME_IMAGE_GLOB = "01_welcome*.png"

# ─── Brand constants (boss-configured) ────────────────────────────────────────
NETWORK_MEMBERS_TOTAL = 100_000   # รวมทุก channel (FB, IG, Telegram, etc) — boss directive
CONTENT_LIBRARY_SIZE = "10,000+"  # คลิป
RATING_DISPLAY = "⭐ 4.8/5"
REVIEW_COUNT = "200+"

# ─── Rotating reviews pool (Thai, no daily volatility) ────────────────────────
REVIEW_QUOTES = [
    ("ของจริง คุ้มสุด ใช้มาเป็นปีแล้ว", "GOD MODE"),
    ("GOD MODE สุดยอด มีคลิปแรร์เยอะ", "GOD ถาวร"),
    ("แอดมินตอบเร็ว เคลียร์ดี", "VIP 30 วัน"),
    ("OnlyFans Combo คุ้มมาก", "OF+VIP"),
    ("จ่ายครั้งเดียวดูตลอดชีพ คุ้มเกินคุ้ม", "GOD ถาวร"),
    ("VIP เจริญพร อัพเดทใหม่ทุกวัน", "VIP 30 วัน"),
    ("หาที่เจ๋งกว่านี้ไม่มีแล้ว", "GOD MODE"),
    ("ดูครบทุกแนว ราคาดีงาม", "VIP 30 วัน"),
    ("สมัครเสร็จซื้อถาวรเลย ไม่ผิดหวัง", "GOD ถาวร"),
    ("GOD 90 วัน ยังคงเป็น VIP เหมือนเดิม", "GOD MODE"),
    ("คลิป HD เด็ดๆเยอะ ระบบเสถียร", "OF+VIP"),
    ("ราคาเป็นกันเอง คอนเทนต์เยอะมาก", "VIP 30 วัน"),
]

# ─── Cache ────────────────────────────────────────────────────────────────────
_stats_cache: dict | None = None
_stats_ttl_ts: float = 0.0
_STATS_TTL_SECONDS = 600  # 10 min


async def _query_stats() -> dict:
    """Query DB for live stats (paid customers + most-popular tier).

    Falls back to safe defaults on error.
    """
    try:
        async with get_session() as session:
            ever_paid = await session.scalar(
                select(func.count(func.distinct(Payment.user_id)))
                .where(Payment.status == PaymentStatus.CONFIRMED)
            )
            total_users = await session.scalar(select(func.count(User.id)))
            return {
                "ever_paid": int(ever_paid or 0),
                "total_users_in_bot": int(total_users or 0),
            }
    except Exception as exc:
        logger.warning("social_proof._query_stats failed: %s", exc)
        return {"ever_paid": 400, "total_users_in_bot": 13000}


async def get_stats() -> dict:
    """Cached live stats (10 min TTL)."""
    global _stats_cache, _stats_ttl_ts
    now = time.time()
    if _stats_cache is None or now - _stats_ttl_ts > _STATS_TTL_SECONDS:
        _stats_cache = await _query_stats()
        _stats_ttl_ts = now
    return _stats_cache


def get_random_review() -> tuple[str, str]:
    """Return a random (quote, tier_label) tuple."""
    return random.choice(REVIEW_QUOTES)




async def _get_active_flash() -> object | None:
    """Return active FlashSale row or None."""
    # flash_sale retired -- always no active flash
    return None


async def pick_welcome_image_dynamic() -> Path | None:
    """Active-aware welcome image picker.

    Priority:
    - Lucky 6.6 active → 06_lucky66.png
    - Flash Sale active → 03_flash1.png
    - else → 01_welcome*.png (random pool)
    Auto-reverts when the active windows end (date-based checks).
    """
    # Lucky 6.6 first (highest priority — most aggressive sale)
    try:
        from shared.endmonth_vip_promo import is_lucky_6_active, is_birthday_promo_active
        if is_lucky_6_active():
            candidates = list(ASSETS_DIR.glob("06_lucky66*.png"))
            if candidates:
                return random.choice(candidates)
        if is_birthday_promo_active():
            candidates = list(ASSETS_DIR.glob("07_birthday*.png"))
            if candidates:
                return random.choice(candidates)
    except Exception:
        pass
    # Mid-month flash
    flash = await _get_active_flash()
    if flash is not None:
        candidates = list(ASSETS_DIR.glob("03_flash1*.png"))
        if candidates:
            return random.choice(candidates)
    # Fallback to welcome pool
    try:
        candidates = list(ASSETS_DIR.glob(WELCOME_IMAGE_GLOB))
        if candidates:
            return random.choice(candidates)
    except Exception as exc:
        logger.warning("pick_welcome_image_dynamic failed: %s", exc)
    return None


def pick_welcome_image() -> Path | None:
    """Sync fallback (legacy callers) — returns regular welcome only."""
    try:
        candidates = list(ASSETS_DIR.glob(WELCOME_IMAGE_GLOB))
        if not candidates:
            return None
        return random.choice(candidates)
    except Exception as exc:
        logger.warning("pick_welcome_image failed: %s", exc)
        return None


def pick_campaign_image(campaign: str) -> Path | None:
    """Pick image for a specific campaign name (welcome, referral, flash1, flash2, winback)."""
    glob_map = {
        "welcome": "01_welcome*.png",
        "referral": "02_referral*.png",
        "flash1": "03_flash1*.png",
        "flash2": "04_flash2*.png",
        "winback": "05_winback*.png",
    }
    pattern = glob_map.get(campaign, f"*{campaign}*.png")
    try:
        candidates = list(ASSETS_DIR.glob(pattern))
        if not candidates:
            return None
        return random.choice(candidates)
    except Exception:
        return None


async def build_welcome_caption(
    tg_user_first_name: str | None = None,
    *,
    telegram_id: int | None = None,
    is_new_user: bool | None = None,
) -> str:
    """Build dynamic welcome caption with social proof + 1 rotating review.

    Auto-prepends Flash Sale banner if active flash sale exists.
    Returns HTML-formatted text suitable for Telegram parse_mode='HTML'.

    Phase A.1c (2026-06-27): if feature flag bot_messages_enabled is ON
    for this user, use DB-stored welcome_new / welcome_returning instead.
    Fallback to hardcoded behavior if flag OFF, key missing, or any error.
    """
    # ---- Phase A.1c canary integration ----
    try:
        if telegram_id is not None and is_new_user is not None:
            from shared.feature_flags import is_flag_enabled
            from shared.bot_messages import get_bot_message, render_placeholders
            if await is_flag_enabled("bot_messages_enabled", telegram_id=telegram_id):
                key = "welcome_new" if is_new_user else "welcome_returning"
                db_msg = await get_bot_message(key)
                if db_msg:
                    return render_placeholders(
                        db_msg,
                        customer_name=tg_user_first_name or "",
                    )
    except Exception as _exc:
        logger.warning("bot_messages override failed: %s", _exc)
    # ---- end canary integration; fall through to original behavior ----

    stats = await get_stats()
    ever_paid = stats.get("ever_paid", 400)
    ever_paid_display = f"{(ever_paid // 50) * 50:,}+"

    quote, tier_label = get_random_review()

    greet = "สวัสดีค่ะ"
    if tg_user_first_name:
        greet = f"สวัสดีค่ะ คุณ{tg_user_first_name}"

    # SALE_BANNER — Phase 3 Round B: load from promotion_campaigns.bot_badge
    flash_banner = ""
    try:
        from shared.endmonth_vip_promo import is_lucky_6_active, is_birthday_promo_active
        from shared.captions import load_caption
        _camp_key = None
        if is_lucky_6_active():           _camp_key = "lucky66"
        elif is_birthday_promo_active():  _camp_key = "birthday"
        if _camp_key:
            _spec = await load_caption(_camp_key)
            if _spec and _spec.bot_badge:
                flash_banner = _spec.bot_badge
    except Exception:
        pass
    if not flash_banner:
        flash = await _get_active_flash()
        if flash is not None:
            flash_banner = (
                f"⚡ <b>FLASH SALE กำลังลด!</b> ⚡\n"
                f"🔥 ลดสูงสุด 30% — เหลือ {max(0, flash.total_slots - flash.sold_slots)} สิทธิ์\n"
                f"⏰ กดปุ่ม <b>⚡ FLASH SALE — กำลังลด!</b> ด้านล่าง\n"
                f"━━━━━━━━━━━━━━━\n\n"
            )

    return (
        f"{flash_banner}"
        f"{greet} 👋 ยินดีต้อนรับสู่ <b>VIP เจริญพร</b> 👑\n\n"
        f"🔥 สมาชิกเครือข่ายเรา <b>{NETWORK_MEMBERS_TOTAL:,}+ คน</b>\n"
        f"💎 ลูกค้า VIP จ่ายจริง <b>{ever_paid_display} คน</b>\n"
        f"🎬 คลิป <b>{CONTENT_LIBRARY_SIZE} ชิ้น</b> อัพเดทใหม่ทุกวัน\n"
        f"{RATING_DISPLAY} จาก <b>{REVIEW_COUNT} รีวิว</b>\n\n"
        f"💬 <i>“{quote}”</i>\n"
        f"   — ลูกค้า {tier_label}\n\n"
        f"━━━━━━━━━━━━━━━\n"
        f"กดเลือกเมนูด้านล่างได้เลยค่ะ 👇"
    )
