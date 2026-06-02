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
ASSETS_DIR = Path("/root/charoenpon/assets/campaigns")
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


def mask_telegram_name(name: str | None, fallback: str = "ลูกค้า VIP") -> str:
    """Mask a Telegram first name → first 2 chars + ***"""
    if not name:
        return fallback
    name = name.strip()
    if len(name) <= 2:
        return name + "***"
    return name[:2] + "***"


def pick_welcome_image() -> Path | None:
    """Pick a random welcome image from assets dir."""
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


async def build_welcome_caption(tg_user_first_name: str | None = None) -> str:
    """Build dynamic welcome caption with social proof + 1 rotating review.

    Returns HTML-formatted text suitable for Telegram parse_mode='HTML'.
    """
    stats = await get_stats()
    ever_paid = stats.get("ever_paid", 400)
    # Floor to keep number "round" + impressive
    ever_paid_display = f"{(ever_paid // 50) * 50:,}+"

    quote, tier_label = get_random_review()

    greet = "สวัสดีค่ะ"
    if tg_user_first_name:
        greet = f"สวัสดีค่ะ คุณ{tg_user_first_name}"

    return (
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
