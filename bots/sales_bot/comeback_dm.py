"""Comeback DM System — ส่ง DM ลูกค้าเก่าที่หมดอายุ พร้อมส่วนลด.

- รอบ 1: หมดอายุ > 3 วัน → ส่วนลด 30%
- รอบ 2: DM รอบ 1 แล้ว 7 วัน + ยังไม่ซื้อ → ส่วนลด 40%
- Rate limit: 30 DM/วัน, delay 3 วินาที
- Promo code หมดอายุ 48 ชม.
- A/B Testing: หลาย variant per round + adaptive weighted random
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
import secrets
import string
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import httpx
from sqlalchemy import Integer, select, and_, func, text
from telegram import Bot
from telegram.error import Forbidden
from telegram.ext import ContextTypes

from shared.database import get_session
from shared.models import (
    ComebackDmLog,
    ContentQueue,
    Subscription,
    SubscriptionStatus,
    User,
)

logger = logging.getLogger(__name__)

from shared.tz import TH_TZ
from shared.admin_alert import _admin_group_id

# Config
MAX_DM_PER_DAY = 30
DM_DELAY_SECONDS = 3
PROMO_EXPIRY_HOURS = 48
BASE_PRICE = Decimal("300")  # VIP 30 วัน ราคาปกติ


# Phase B.1 (2026-06-27): DB-backed config with feature flag
async def _cfg(key: str, default):
    """Read promo_config from DB if flag comeback_config_from_db is ON.

    Errors / flag OFF -> return hardcoded default.
    """
    try:
        from shared.feature_flags import is_flag_enabled
        if not await is_flag_enabled("comeback_config_from_db"):
            return default
        from shared.promo_config import get_promo_config
        v = await get_promo_config(key, default=default)
        return v if v is not None else default
    except Exception:
        return default


ADMIN_GROUP_ID = _admin_group_id()

# ─── DB Migration ────────────────────────────────────────────────────────────

MIGRATION_SQL = """
ALTER TABLE comeback_dm_log ADD COLUMN IF NOT EXISTS variant VARCHAR(10);
"""


async def ensure_columns() -> None:
    """Add variant column if not exists."""
    try:
        async with get_session() as session:
            await session.execute(text(MIGRATION_SQL))
            await session.commit()
    except Exception as exc:
        logger.warning("comeback_dm migration (may already exist): %s", exc)


# ─── A/B Test Message Variants ──────────────────────────────────────────────

def _calculate_discounted_price(discount_pct: int) -> int:
    """คำนวณราคาหลังลด."""
    return int(BASE_PRICE * (100 - discount_pct) / 100)


def _generate_promo_code() -> str:
    """สร้าง promo code 8 ตัวอักษร."""
    chars = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(chars) for _ in range(8))


# Round 1 Variants
def _variant_r1_a(first_name: str, discount_pct: int, promo_code: str, new_clips: int) -> str:
    """FOMO: คุณพลาดคลิปใหม่ XX ชิ้น"""
    price = _calculate_discounted_price(discount_pct)
    clips_text = f"{new_clips} ชิ้น" if new_clips > 0 else "เพียบ"
    return (
        f"คุณ {first_name} พลาดคลิปใหม่ {clips_text} แล้วนะ 🔥\n"
        f"\n"
        f"กลับมาวันนี้ ลด {discount_pct}%\n"
        f"VIP 30 วัน ฿{price} (จาก ฿300)\n"
        f"\n"
        f"⏰ ใช้ได้ 48 ชม.\n"
        f'👉 <a href="tg://resolve?domain=NamwarnJarern_bot&start=comeback_{promo_code}">สมัครต่อเลย</a>'
    )


def _variant_r1_b(first_name: str, discount_pct: int, promo_code: str, new_clips: int) -> str:
    """Social proof: เมื่อวานมี XX คนกลับมาสมัคร"""
    price = _calculate_discounted_price(discount_pct)
    # ใช้ random range สำหรับ social proof number (realistic)
    social_count = random.randint(8, 25)
    return (
        f"คุณ {first_name} รู้มั้ย? เมื่อวานมี {social_count} คนกลับมาสมัคร VIP แล้ว 🎉\n"
        f"\n"
        f"ลด {discount_pct}% เฉพาะคุณ → ฿{price} เท่านั้น!\n"
        f"\n"
        f"⏰ หมดเขต 48 ชม.\n"
        f'👉 <a href="tg://resolve?domain=NamwarnJarern_bot&start=comeback_{promo_code}">สมัคร VIP ลด {discount_pct}%</a>'
    )


def _variant_r1_c(first_name: str, discount_pct: int, promo_code: str, new_clips: int) -> str:
    """Scarcity: เหลืออีก XX ชั่วโมงเท่านั้น"""
    price = _calculate_discounted_price(discount_pct)
    return (
        f"⚠️ คุณ {first_name} โปรนี้เหลืออีก 48 ชั่วโมงเท่านั้น!\n"
        f"\n"
        f"VIP 30 วัน ลด {discount_pct}% → ฿{price}\n"
        f"คลิปเต็มไม่เบลอ 10,000+ ชิ้น\n"
        f"\n"
        f"หมดแล้วหมดเลย!\n"
        f'👉 <a href="tg://resolve?domain=NamwarnJarern_bot&start=comeback_{promo_code}">รับส่วนลดเลย</a>'
    )


def _variant_r1_d(first_name: str, discount_pct: int, promo_code: str, new_clips: int) -> str:
    """Direct/สั้น: VIP ลด 30% วันนี้วันเดียว"""
    price = _calculate_discounted_price(discount_pct)
    return (
        f"VIP เจริญพร ลด {discount_pct}% → ฿{price} 🔥\n"
        f"⏰ 48 ชม. เท่านั้น\n"
        f'👉 <a href="tg://resolve?domain=NamwarnJarern_bot&start=comeback_{promo_code}">สมัครเลย</a>'
    )


MESSAGE_VARIANTS_ROUND1 = [
    ("A", _variant_r1_a),
    ("B", _variant_r1_b),
    ("C", _variant_r1_c),
    ("D", _variant_r1_d),
]


# Round 2 Variants
def _variant_r2_a(first_name: str, discount_pct: int, promo_code: str, new_clips: int) -> str:
    """โอกาสสุดท้าย urgent"""
    price = _calculate_discounted_price(discount_pct)
    clips_text = f"{new_clips} ชิ้น" if new_clips > 0 else "เพียบ"
    return (
        f"โอกาสสุดท้ายค่ะ คุณ {first_name} 🚨\n"
        f"\n"
        f"คลิปใหม่{clips_text}รอคุณอยู่\n"
        f"ลดพิเศษ {discount_pct}% → ฿{price} เท่านั้น!\n"
        f"\n"
        f"⏰ หมดเขต 48 ชม.\n"
        f'👉 <a href="tg://resolve?domain=NamwarnJarern_bot&start=comeback_{promo_code}">สมัคร VIP ลด {discount_pct}%</a>'
    )


def _variant_r2_b(first_name: str, discount_pct: int, promo_code: str, new_clips: int) -> str:
    """ลดเพิ่มเป็น 40%"""
    price = _calculate_discounted_price(discount_pct)
    return (
        f"คุณ {first_name} ลดเพิ่มเป็น {discount_pct}% แล้วค่ะ! 🎉\n"
        f"\n"
        f"VIP 30 วัน เหลือ ฿{price} เท่านั้น\n"
        f"คลิปเต็มไม่เบลอ 10,000+ ชิ้น\n"
        f"\n"
        f"⏰ 48 ชม. สุดท้าย!\n"
        f'👉 <a href="tg://resolve?domain=NamwarnJarern_bot&start=comeback_{promo_code}">รับส่วนลด {discount_pct}%</a>'
    )


def _variant_r2_c(first_name: str, discount_pct: int, promo_code: str, new_clips: int) -> str:
    """ให้ทดลอง 24 ชม. ฟรีก่อน"""
    price = _calculate_discounted_price(discount_pct)
    return (
        f"คุณ {first_name} ลองดูฟรี 24 ชม. ก่อนก็ได้นะ! 🎁\n"
        f"\n"
        f"หรือถ้าชอบ สมัคร VIP เลย ลด {discount_pct}% → ฿{price}\n"
        f"\n"
        f'👉 <a href="tg://resolve?domain=NamwarnJarern_bot&start=comeback_{promo_code}">เริ่มทดลองฟรี / สมัคร VIP</a>'
    )


MESSAGE_VARIANTS_ROUND2 = [
    ("A", _variant_r2_a),
    ("B", _variant_r2_b),
    ("C", _variant_r2_c),
]


# 2026-06-28: DB-driven message templates (fallback to hardcoded variants above)
_DB_COMEBACK_KEYS = {
    (1, "A"): "journey_comeback_r1_a", (1, "B"): "journey_comeback_r1_b",
    (1, "C"): "journey_comeback_r1_c", (1, "D"): "journey_comeback_r1_d",
    (2, "A"): "journey_comeback_r2_a", (2, "B"): "journey_comeback_r2_b",
    (2, "C"): "journey_comeback_r2_c",
}


async def _build_comeback_from_db_or_fallback(dm_round: int, variant: str, first_name: str,
                                               discount_pct: int, promo_code: str, new_clips: int) -> str:
    """DB-first message build with hardcoded fallback."""
    try:
        from shared.bot_messages import get_bot_message, render_placeholders
        db_key = _DB_COMEBACK_KEYS.get((dm_round, variant))
        if db_key:
            template = await get_bot_message(db_key)
            if template:
                price = _calculate_discounted_price(discount_pct)
                clips_text = f"{new_clips} ชิ้น" if new_clips > 0 else "เพียบ"
                deep_link = f"tg://resolve?domain=NamwarnJarern_bot&start=comeback_{promo_code}"
                return render_placeholders(template,
                    first_name=first_name or "คุณ",
                    discount_pct=discount_pct,
                    discounted_price=price,
                    clips_text=clips_text,
                    promo_code=promo_code,
                    deep_link=deep_link,
                )
    except Exception:
        pass
    # Fallback
    variants = MESSAGE_VARIANTS_ROUND1 if dm_round == 1 else MESSAGE_VARIANTS_ROUND2
    for v_name, v_fn in variants:
        if v_name == variant:
            return v_fn(first_name, discount_pct, promo_code, new_clips)
    # last-resort fallback
    return variants[0][1](first_name, discount_pct, promo_code, new_clips)


# ─── Adaptive Variant Selection ─────────────────────────────────────────────

async def _get_variant_weights(dm_round: int) -> dict[str, float]:
    """ดึง conversion rate ของแต่ละ variant จาก 7 วันที่ผ่านมา.

    Returns dict เช่น {"A": 0.3, "B": 0.5, "C": 0.1, "D": 0.1}
    ถ้าข้อมูลไม่พอ 7 วัน → return dict เปล่า (ใช้ uniform random)
    """
    cutoff = datetime.utcnow() - timedelta(days=7)
    async with get_session() as session:
        # เช็คว่ามีข้อมูล variant ครบ 7 วันไหม
        count_result = await session.execute(
            text("""
                SELECT COUNT(*) FROM comeback_dm_log
                WHERE round = :round AND variant IS NOT NULL AND sent_at >= :cutoff
            """),
            {"round": dm_round, "cutoff": cutoff},
        )
        total = count_result.scalar() or 0

        if total < 20:  # ต้องมีอย่างน้อย 20 records ถึงจะใช้ adaptive
            return {}

        # ดึง conversion rate per variant
        result = await session.execute(
            text("""
                SELECT variant,
                       COUNT(*) as sent,
                       COALESCE(SUM(CASE WHEN purchased THEN 1 ELSE 0 END), 0) as purchased,
                       COALESCE(SUM(CASE WHEN responded THEN 1 ELSE 0 END), 0) as responded
                FROM comeback_dm_log
                WHERE round = :round AND variant IS NOT NULL AND sent_at >= :cutoff
                GROUP BY variant
            """),
            {"round": dm_round, "cutoff": cutoff},
        )
        rows = result.fetchall()

    if not rows:
        return {}

    # คำนวณ weight: purchased * 3 + responded * 1 + baseline 1
    weights = {}
    for row in rows:
        score = (row.purchased * 3) + (row.responded * 1) + 1
        weights[row.variant] = float(score)

    # Normalize
    total_weight = sum(weights.values())
    if total_weight > 0:
        weights = {k: v / total_weight for k, v in weights.items()}

    return weights


def _pick_variant(variants: list[tuple[str, callable]], weights: dict[str, float]) -> tuple[str, callable]:
    """เลือก variant โดยใช้ weighted random (ถ้ามี weights) หรือ uniform random."""
    if weights:
        # Weighted random
        variant_names = [v[0] for v in variants]
        variant_weights = [weights.get(v[0], 0.1) for v in variants]  # default 0.1 for unseen
        total = sum(variant_weights)
        variant_weights = [w / total for w in variant_weights]

        chosen_name = random.choices(variant_names, weights=variant_weights, k=1)[0]
        for v in variants:
            if v[0] == chosen_name:
                return v
    # Uniform random
    return random.choice(variants)


# ─── Analytics ───────────────────────────────────────────────────────────────

async def analyze_comeback_performance() -> dict:
    """วิเคราะห์ performance ของ Comeback DM แยกตาม variant.

    Returns dict:
    {
        "round1": {"A": {"sent": 10, "responded": 3, "purchased": 1, "conv_rate": 0.1}, ...},
        "round2": {"A": {...}, ...},
        "total_sent": 100,
        "total_responded": 20,
        "total_purchased": 5,
        "best_variant_r1": "B",
        "best_variant_r2": "A",
    }
    """
    cutoff = datetime.utcnow() - timedelta(days=7)
    result_data = {"round1": {}, "round2": {}, "total_sent": 0, "total_responded": 0, "total_purchased": 0}

    async with get_session() as session:
        for dm_round in [1, 2]:
            result = await session.execute(
                text("""
                    SELECT variant,
                           COUNT(*) as sent,
                           COALESCE(SUM(CASE WHEN responded THEN 1 ELSE 0 END), 0) as responded,
                           COALESCE(SUM(CASE WHEN purchased THEN 1 ELSE 0 END), 0) as purchased
                    FROM comeback_dm_log
                    WHERE round = :round AND sent_at >= :cutoff
                    GROUP BY variant
                    ORDER BY variant
                """),
                {"round": dm_round, "cutoff": cutoff},
            )
            rows = result.fetchall()

            round_key = f"round{dm_round}"
            best_conv = -1
            best_variant = None

            for row in rows:
                v_name = row.variant or "NONE"
                sent = row.sent
                responded = row.responded
                purchased = row.purchased
                conv_rate = purchased / sent if sent > 0 else 0.0

                result_data[round_key][v_name] = {
                    "sent": sent,
                    "responded": responded,
                    "purchased": purchased,
                    "conv_rate": round(conv_rate, 4),
                }
                result_data["total_sent"] += sent
                result_data["total_responded"] += responded
                result_data["total_purchased"] += purchased

                if conv_rate > best_conv:
                    best_conv = conv_rate
                    best_variant = v_name

            result_data[f"best_variant_r{dm_round}"] = best_variant

    return result_data


# ─── Existing Helpers (preserved) ────────────────────────────────────────────

async def _get_new_content_count(since_days: int = 7) -> int:
    """นับจำนวนคลิปใหม่ในช่วง X วันที่ผ่านมา."""
    cutoff = datetime.utcnow() - timedelta(days=since_days)
    async with get_session() as session:
        result = await session.execute(
            select(func.count(ContentQueue.id)).where(
                ContentQueue.created_at >= cutoff,
                ContentQueue.is_used == True,  # noqa: E712
            )
        )
        return result.scalar() or 0


def _build_comeback_message_round1(first_name: str, discount_pct: int, promo_code: str, new_clips: int) -> str:
    """สร้างข้อความ DM COMEBACK รอบ 1 — สั้น กระชับ FOMO. (Legacy, kept for compat)"""
    discounted_price = _calculate_discounted_price(discount_pct)
    clips_text = f"คลิปใหม่ {new_clips} ชิ้น" if new_clips > 0 else "คลิปใหม่เพียบ"
    return (
        f"คุณ {first_name} พลาด{clips_text}แล้วนะ 🔥\n"
        f"\n"
        f"กลับมาวันนี้ ลด {discount_pct}%\n"
        f"VIP 30 วัน ฿{discounted_price} (จาก ฿300)\n"
        f"\n"
        f"⏰ ใช้ได้ 48 ชม.\n"
        f'👉 <a href="tg://resolve?domain=NamwarnJarern_bot&start=comeback_{promo_code}">สมัครต่อเลย</a>'
    )


def _build_comeback_message_round2(first_name: str, discount_pct: int, promo_code: str, new_clips: int) -> str:
    """สร้างข้อความ DM COMEBACK รอบ 2 — โอกาสสุดท้าย. (Legacy, kept for compat)"""
    discounted_price = _calculate_discounted_price(discount_pct)
    clips_text = f"{new_clips} ชิ้น" if new_clips > 0 else "เพียบ"
    return (
        f"โอกาสสุดท้ายค่ะ คุณ {first_name} 🚨\n"
        f"\n"
        f"คลิปใหม่{clips_text}รอคุณอยู่\n"
        f"ลดพิเศษ {discount_pct}% → ฿{discounted_price} เท่านั้น!\n"
        f"\n"
        f"⏰ หมดเขต 48 ชม.\n"
        f'👉 <a href="tg://resolve?domain=NamwarnJarern_bot&start=comeback_{promo_code}">สมัคร VIP ลด {discount_pct}%</a>'
    )


async def get_expired_customers(days_since_expire: int = 3) -> list[dict]:
    """ดึงลูกค้าที่ subscription หมดอายุแล้ว X วัน + ยังไม่เคยส่ง DM COMEBACK รอบ 1."""
    now = datetime.utcnow()
    cutoff = now - timedelta(days=days_since_expire)

    async with get_session() as session:
        # Subquery: user_ids ที่เคยส่ง DM COMEBACK รอบ 1 แล้ว
        already_sent = (
            select(ComebackDmLog.user_id)
            .where(ComebackDmLog.round == 1)
            .scalar_subquery()
        )

        # COMEBACK_ACTIVE_SUB_GUARD — users with ANY active sub (renewed) — exclude
        from sqlalchemy import exists as _exists
        Sub2 = Subscription.__table__.alias("sub_active_check")
        users_with_active = (
            select(Sub2.c.user_id)
            .where(Sub2.c.status == SubscriptionStatus.ACTIVE)
            .scalar_subquery()
        )

        # ดึง user ที่มี subscription EXPIRED + end_date < cutoff + ยังไม่เคยส่ง DM + ไม่มี sub active อื่น
        result = await session.execute(
            select(User, Subscription)
            .join(Subscription, Subscription.user_id == User.id)
            .where(
                Subscription.status == SubscriptionStatus.EXPIRED,
                Subscription.end_date < cutoff,
                User.id.notin_(already_sent),
                User.id.notin_(users_with_active),  # FIX: exclude users who renewed
                User.is_banned == False,  # noqa: E712
            )
            .order_by(Subscription.end_date.desc())
            .limit(MAX_DM_PER_DAY)
        )
        rows = result.all()

    customers = []
    seen_user_ids = set()
    for user, sub in rows:
        if user.id in seen_user_ids:
            continue
        seen_user_ids.add(user.id)
        customers.append({
            "user_id": user.id,
            "telegram_id": user.telegram_id,
            "first_name": user.first_name or user.username or "ลูกค้า",
            "username": user.username,
            "end_date": sub.end_date,
        })

    return customers


async def get_round2_customers(days_after_r1: int = 7) -> list[dict]:
    """ดึงลูกค้าที่ DM รอบ 1 แล้ว X วัน + ยังไม่ซื้อ + ยังไม่เคย DM รอบ 2."""
    cutoff = datetime.utcnow() - timedelta(days=days_after_r1)

    async with get_session() as session:
        # User ที่เคย DM รอบ 2 แล้ว
        already_round2 = (
            select(ComebackDmLog.user_id)
            .where(ComebackDmLog.round == 2)
            .scalar_subquery()
        )

        result = await session.execute(
            select(ComebackDmLog, User)
            .join(User, User.id == ComebackDmLog.user_id)
            .where(
                ComebackDmLog.round == 1,
                ComebackDmLog.purchased == False,  # noqa: E712
                ComebackDmLog.sent_at < cutoff,
                ComebackDmLog.user_id.notin_(already_round2),
                User.is_banned == False,  # noqa: E712
            )
            .order_by(ComebackDmLog.sent_at.asc())
            .limit(MAX_DM_PER_DAY)
        )
        rows = result.all()

    customers = []
    seen_user_ids = set()
    for dm_log, user in rows:
        if user.id in seen_user_ids:
            continue
        seen_user_ids.add(user.id)
        customers.append({
            "user_id": user.id,
            "telegram_id": user.telegram_id,
            "first_name": user.first_name or user.username or "ลูกค้า",
            "username": user.username,
        })

    return customers


async def send_comeback_dm(
    bot: Bot,
    user: dict,
    discount_pct: int = 30,
    dm_round: int = 1,
    new_clips: int = 0,
    variant_name: str | None = None,
    variant_func: callable | None = None,
) -> bool:
    """ส่ง DM ให้ลูกค้าเก่าพร้อมส่วนลด.

    Returns True if sent successfully, False if failed.
    """
    promo_code = _generate_promo_code()

    # 2026-06-28: DB-first message build (variant_name → DB key); fallback to variant_func/legacy
    if variant_name:
        message = await _build_comeback_from_db_or_fallback(
            dm_round, variant_name, user["first_name"], discount_pct, promo_code, new_clips
        )
    elif variant_func:
        message = variant_func(user["first_name"], discount_pct, promo_code, new_clips)
    elif dm_round == 2:
        message = _build_comeback_message_round2(user["first_name"], discount_pct, promo_code, new_clips)
    else:
        message = _build_comeback_message_round1(user["first_name"], discount_pct, promo_code, new_clips)

    # WINBACK_IMG_V1 — try photo first, fallback to text on error
    img_path = None
    try:
        from bots.sales_bot.handlers.social_proof import pick_campaign_image
        img_path = pick_campaign_image("winback")
    except Exception:
        img_path = None
    try:
        if img_path and img_path.exists():
            with open(img_path, "rb") as _f:
                await bot.send_photo(
                    chat_id=user["telegram_id"],
                    photo=_f,
                    caption=message,
                    parse_mode="HTML",
                )
        else:
            await bot.send_message(
                chat_id=user["telegram_id"],
                text=message,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
    except Forbidden:
        logger.info(
            "Cannot DM user %s (tg:%d) — never started bot or blocked",
            user.get("username", "?"), user["telegram_id"],
        )
        return False
    except Exception as exc:
        logger.error(
            "Failed to DM user %s (tg:%d): %s",
            user.get("username", "?"), user["telegram_id"], exc,
        )
        return False

    # Log to DB (with variant)
    async with get_session() as session:
        log_entry = ComebackDmLog(
            user_id=user["user_id"],
            telegram_id=user["telegram_id"],
            discount_pct=discount_pct,
            promo_code=promo_code,
            round=dm_round,
        )
        session.add(log_entry)
        await session.flush()

        # Set variant via raw SQL (column may not be in ORM yet)
        if variant_name:
            await session.execute(
                text("UPDATE comeback_dm_log SET variant = :v WHERE id = :id"),
                {"v": variant_name, "id": log_entry.id},
            )

    logger.info(
        "COMEBACK DM sent: user_id=%d tg=%d round=%d discount=%d%% variant=%s code=%s",
        user["user_id"], user["telegram_id"], dm_round, discount_pct, variant_name or "NONE", promo_code,
    )
    return True


async def validate_promo_code(promo_code: str, telegram_id: int | None = None) -> dict | None:
    """ตรวจสอบ promo code ว่าถูกต้อง + ยังไม่หมดอายุ (48 ชม.).

    # >>> FIX_TIE_TG_ID <<<
    If telegram_id is provided, the code only validates if it belongs to that
    user — prevents code sharing.

    Returns dict with discount_pct, user_id, telegram_id or None if invalid.
    """
    async with get_session() as session:
        conds = [
            ComebackDmLog.promo_code == promo_code,
            ComebackDmLog.purchased == False,  # noqa: E712
        ]
        if telegram_id is not None:
            conds.append(ComebackDmLog.telegram_id == telegram_id)
        result = await session.execute(select(ComebackDmLog).where(*conds))
        dm_log = result.scalar_one_or_none()

    if not dm_log:
        return None

    # เช็คหมดอายุ 48 ชม.
    expiry = dm_log.sent_at + timedelta(hours=PROMO_EXPIRY_HOURS)
    if datetime.utcnow() > expiry:
        return None

    return {
        "dm_log_id": dm_log.id,
        "user_id": dm_log.user_id,
        "telegram_id": dm_log.telegram_id,
        "discount_pct": dm_log.discount_pct,
        "promo_code": dm_log.promo_code,
        "discounted_price": _calculate_discounted_price(dm_log.discount_pct),
    }


async def mark_promo_purchased(promo_code: str) -> None:
    """อัพเดท comeback_dm_log ว่าซื้อแล้ว."""
    async with get_session() as session:
        result = await session.execute(
            select(ComebackDmLog).where(ComebackDmLog.promo_code == promo_code)
        )
        dm_log = result.scalar_one_or_none()
        if dm_log:
            dm_log.purchased = True
            dm_log.responded = True


async def mark_promo_responded(promo_code: str) -> None:
    """อัพเดท comeback_dm_log ว่า user กดลิงก์แล้ว."""
    async with get_session() as session:
        result = await session.execute(
            select(ComebackDmLog).where(ComebackDmLog.promo_code == promo_code)
        )
        dm_log = result.scalar_one_or_none()
        if dm_log:
            dm_log.responded = True


async def _notify_discord_system_log(message: str) -> None:
    """[Phase 4 A3] delegated to shared.discord_alert."""
    from shared.discord_alert import notify_discord as _hub
    try:
        # Best-effort: pass any positional/keyword as title + body
        args_str = " | ".join(str(x) for x in locals().values() if isinstance(x, str))[:1000]
        return await _hub("system", "_notify_discord_system_log", args_str, silent_on_error=True)
    except Exception:
        return False

async def run_comeback_dm_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Scheduled job: ส่ง DM COMEBACK ลูกค้าเก่า.

    - รอบ 1: หมดอายุ > 3 วัน, discount 30%, A/B test 4 variants
    - รอบ 2: DM รอบ 1 แล้ว 7 วัน + ยังไม่ซื้อ, discount 40%, A/B test 3 variants
    - Adaptive: ถ้ามีข้อมูล 7 วัน+ ใช้ weighted random
    """
    bot = context.bot
    now_th = datetime.now(TH_TZ)
    logger.info("🔄 COMEBACK DM job started at %s", now_th.strftime("%Y-%m-%d %H:%M"))

    # Ensure variant column exists
    await ensure_columns()

    # ดึงจำนวนคลิปใหม่สำหรับ FOMO message
    new_clips = await _get_new_content_count(since_days=7)

    # ดึง weights สำหรับ adaptive variant selection
    r1_weights = await _get_variant_weights(dm_round=1)
    r2_weights = await _get_variant_weights(dm_round=2)

    total_sent = 0
    total_failed = 0
    dm_budget = MAX_DM_PER_DAY
    variant_counts: dict[str, int] = {}

    # ---- รอบ 1: ลูกค้าหมดอายุ > X วัน, ลด Y% ----
    r1_days = int(await _cfg("comeback_r1_days_after_expiry", 3))
    r1_pct = int(await _cfg("comeback_r1_discount_pct", 30))
    round1_customers = await get_expired_customers(days_since_expire=r1_days)
    round1_sent = 0
    round1_failed = 0

    for customer in round1_customers:
        if dm_budget <= 0:
            break
        v_name, v_func = _pick_variant(MESSAGE_VARIANTS_ROUND1, r1_weights)
        success = await send_comeback_dm(
            bot, customer, discount_pct=r1_pct, dm_round=1, new_clips=new_clips,
            variant_name=v_name, variant_func=v_func,
        )
        if success:
            round1_sent += 1
            total_sent += 1
            dm_budget -= 1
            variant_counts[f"R1_{v_name}"] = variant_counts.get(f"R1_{v_name}", 0) + 1
        else:
            round1_failed += 1
            total_failed += 1
        await asyncio.sleep(DM_DELAY_SECONDS)

    # ---- รอบ 2: DM รอบ 1 แล้ว X วัน + ยังไม่ซื้อ, ลด Y% ----
    r2_days = int(await _cfg("comeback_r2_days_after_r1", 7))
    r2_pct = int(await _cfg("comeback_r2_discount_pct", 40))
    round2_sent = 0
    round2_failed = 0

    if dm_budget > 0:
        round2_customers = await get_round2_customers(days_after_r1=r2_days)
        for customer in round2_customers:
            if dm_budget <= 0:
                break
            v_name, v_func = _pick_variant(MESSAGE_VARIANTS_ROUND2, r2_weights)
            success = await send_comeback_dm(
                bot, customer, discount_pct=r2_pct, dm_round=2, new_clips=new_clips,
                variant_name=v_name, variant_func=v_func,
            )
            if success:
                round2_sent += 1
                total_sent += 1
                dm_budget -= 1
                variant_counts[f"R2_{v_name}"] = variant_counts.get(f"R2_{v_name}", 0) + 1
            else:
                round2_failed += 1
                total_failed += 1
            await asyncio.sleep(DM_DELAY_SECONDS)

    # ---- Summary ----
    variant_summary = ", ".join(f"{k}:{v}" for k, v in sorted(variant_counts.items()))
    summary = (
        f"📊 COMEBACK DM Summary ({now_th.strftime('%d/%m/%Y')})\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"รอบ 1 (ลด 30%): ส่ง {round1_sent} / ไม่ได้ {round1_failed}\n"
        f"รอบ 2 (ลด 40%): ส่ง {round2_sent} / ไม่ได้ {round2_failed}\n"
        f"Variants: {variant_summary or 'N/A'}\n"
        f"Adaptive: {'ON' if r1_weights else 'OFF'}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"รวม: ส่ง {total_sent} / ไม่ได้ {total_failed}"
    )
    logger.info(summary)

    # Discord #system-logs
    await _notify_discord_system_log(summary)

    # Admin group notification
    admin_token = os.environ.get("ADMIN_BOT_TOKEN", "")
    if admin_token and total_sent > 0:
        try:
            admin_bot = Bot(token=admin_token)
            await admin_bot.initialize()

            # ดึงสถิติ responded/purchased ของวันนี้
            today_start = now_th.replace(hour=0, minute=0, second=0, microsecond=0)
            today_start_utc = today_start.astimezone(timezone.utc).replace(tzinfo=None)

            async with get_session() as session:
                stats = await session.execute(
                    select(
                        func.count(ComebackDmLog.id).label("total"),
                        func.sum(
                            func.cast(ComebackDmLog.responded, Integer)
                        ).label("responded"),
                        func.sum(
                            func.cast(ComebackDmLog.purchased, Integer)
                        ).label("purchased"),
                    ).where(ComebackDmLog.sent_at >= today_start_utc)
                )
                row = stats.one()
                total_all = row.total or 0
                responded_all = row.responded or 0
                purchased_all = row.purchased or 0

            admin_text = (
                f"📬 <b>COMEBACK DM Report</b>\n"
                f"📅 {now_th.strftime('%d/%m/%Y')}\n\n"
                f"วันนี้ส่ง DM COMEBACK <b>{total_sent}</b> คน\n"
                f"ตอบกลับ (กดลิงก์): <b>{responded_all}</b> คน\n"
                f"สมัคร VIP: <b>{purchased_all}</b> คน\n\n"
                f"รอบ 1 (ลด 30%): {round1_sent} คน\n"
                f"รอบ 2 (ลด 40%): {round2_sent} คน\n\n"
                f"🔬 <b>A/B Variants:</b> {variant_summary or 'N/A'}\n"
                f"🤖 Adaptive: {'ON' if r1_weights else 'OFF (< 20 records)'}"
            )
            await admin_bot.send_message(
                chat_id=ADMIN_GROUP_ID,
                text=admin_text,
                parse_mode="HTML",
            )
        except Exception as exc:
            logger.error("Failed to send COMEBACK admin notification: %s", exc)
