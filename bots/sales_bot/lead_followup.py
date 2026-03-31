"""Lead Follow-up System — auto DM คนที่ /start แต่ยังไม่ซื้อ.

- รอบ 1: /start แล้ว 2 ชม. + ยังไม่ซื้อ → DM แนะนำ
- รอบ 2: /start แล้ว 24 ชม. + ยังไม่ซื้อ → DM FOMO
- Rate limit: 50 DM/ชม., delay 2 วินาที
- ไม่ส่งซ้ำ (track ใน DB lead_followup_log)
- ไม่ส่งถ้า user ซื้อไปแล้ว หรือ is_banned
- Smart Segmentation: HOT/WARM/COLD + A/B test per segment
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
from datetime import datetime, timedelta, timezone

from sqlalchemy import Integer, select, func, text
from telegram import Bot
from telegram.error import Forbidden
from telegram.ext import ContextTypes

from shared.database import get_session
from shared.models import (
    ContentQueue,
    Lead,
    LeadStatus,
    Subscription,
    SubscriptionStatus,
    User,
)

logger = logging.getLogger(__name__)

TH_TZ = timezone(timedelta(hours=7))

# Config
MAX_DM_PER_HOUR = 50
DM_DELAY_SECONDS = 2
ROUND1_WAIT_HOURS = 2
ROUND2_WAIT_HOURS = 24

ADMIN_GROUP_ID = int(os.environ.get("ADMIN_GROUP_CHAT_ID", "-1003830920430"))

# ─── DB Migration ────────────────────────────────────────────────────────────

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS lead_followup_log (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    telegram_id BIGINT NOT NULL,
    round INTEGER NOT NULL DEFAULT 1,
    sent_at TIMESTAMP NOT NULL DEFAULT NOW(),
    purchased BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS ix_lead_followup_user_id ON lead_followup_log(user_id);
CREATE INDEX IF NOT EXISTS ix_lead_followup_telegram_id ON lead_followup_log(telegram_id);
"""

ALTER_TABLE_SQL = """
ALTER TABLE lead_followup_log ADD COLUMN IF NOT EXISTS segment VARCHAR(10);
ALTER TABLE lead_followup_log ADD COLUMN IF NOT EXISTS variant VARCHAR(10);
"""


async def ensure_table() -> None:
    """Create lead_followup_log table if not exists + add new columns."""
    async with get_session() as session:
        await session.execute(text(CREATE_TABLE_SQL))
        await session.commit()
    try:
        async with get_session() as session:
            await session.execute(text(ALTER_TABLE_SQL))
            await session.commit()
    except Exception as exc:
        logger.warning("lead_followup migration (may already exist): %s", exc)


# ─── Smart Segmentation ─────────────────────────────────────────────────────

async def _classify_lead(user_id: int, lead_created_at: datetime) -> str:
    """Classify lead into HOT / WARM / COLD.

    HOT: มี teaser_click + source มี t_ prefix
    WARM: กด /start แต่ไม่ดู packages (ยังไม่ click teaser)
    COLD: /start นานแล้ว > 3 วัน
    """
    now = datetime.utcnow()
    days_since = (now - lead_created_at).total_seconds() / 86400

    # Check for teaser click
    async with get_session() as session:
        # Check teaser_clicks table
        click_result = await session.execute(
            text("SELECT COUNT(*) FROM teaser_clicks WHERE user_id = :uid"),
            {"uid": user_id},
        )
        has_click = (click_result.scalar() or 0) > 0

        # Check lead source for t_ prefix
        lead_result = await session.execute(
            text("SELECT source FROM leads WHERE user_id = :uid ORDER BY created_at DESC LIMIT 1"),
            {"uid": user_id},
        )
        row = lead_result.fetchone()
        has_teaser_source = row and row.source and row.source.startswith("t_") if row else False

    if has_click or has_teaser_source:
        return "HOT"
    elif days_since > 3:
        return "COLD"
    else:
        return "WARM"


# ─── A/B Test Messages per Segment ──────────────────────────────────────────

def _hot_variant_a(first_name: str, promo_text: str) -> str:
    return (
        f"เห็นว่าสนใจแพ็กเกจอยู่ {first_name} 👀\n"
        f"\n"
        f"ตอนนี้มีส่วนลดพิเศษ {promo_text}\n"
        f"คลิปเต็มไม่เบลอ 10,000+ ชิ้น\n"
        f"\n"
        f'👉 <a href="tg://resolve?domain=NamwarnJarern_bot&start=packages">สมัคร VIP เลย</a>'
    )


def _hot_variant_b(first_name: str, promo_text: str) -> str:
    return (
        f"{first_name} ยังไม่ตัดสินใจเหรอ? 🔥\n"
        f"\n"
        f"แพ็กเกจที่ดูอยู่ ตอนนี้ลดพิเศษ!\n"
        f"{promo_text}\n"
        f"\n"
        f'👉 <a href="tg://resolve?domain=NamwarnJarern_bot&start=packages">ดูแพ็กเกจ</a>'
    )


def _warm_variant_a(first_name: str, promo_text: str) -> str:
    return (
        f"VIP เจริญพร คลิปใหม่ทุกวัน 🎬 {first_name}\n"
        f"\n"
        f"สมาชิก 10,000+ คลิปเต็มไม่เบลอ\n"
        f"{promo_text}\n"
        f"\n"
        f'👉 <a href="tg://resolve?domain=NamwarnJarern_bot&start=packages">ดูแพ็กเกจ VIP</a>'
    )


def _warm_variant_b(first_name: str, promo_text: str) -> str:
    return (
        f"สนใจ VIP อยู่ไหมคะ? {first_name} 😊\n"
        f"\n"
        f"ตอนนี้มีโปร {promo_text}\n"
        f"\n"
        f'👉 <a href="tg://resolve?domain=NamwarnJarern_bot&start=packages">ดูแพ็กเกจ</a>'
    )


def _cold_variant_a(first_name: str, promo_text: str) -> str:
    return (
        f"คิดถึงจัง! {first_name} กลับมาดูไหม? 🥺\n"
        f"\n"
        f"คลิปใหม่เพียบ ไม่อยากให้พลาด\n"
        f"{promo_text}\n"
        f"\n"
        f'👉 <a href="tg://resolve?domain=NamwarnJarern_bot&start=packages">ดูแพ็กเกจ VIP</a>'
    )


def _cold_variant_b(first_name: str, promo_text: str) -> str:
    return (
        f"ว่างไหมคะ {first_name}? มีคลิปใหม่เยอะมาก! 🎁\n"
        f"\n"
        f"VIP 30 วัน ดูได้ทุกคลิป\n"
        f"{promo_text}\n"
        f"\n"
        f'👉 <a href="tg://resolve?domain=NamwarnJarern_bot&start=packages">สมัคร VIP</a>'
    )


SEGMENT_VARIANTS = {
    "HOT": [("A", _hot_variant_a), ("B", _hot_variant_b)],
    "WARM": [("A", _warm_variant_a), ("B", _warm_variant_b)],
    "COLD": [("A", _cold_variant_a), ("B", _cold_variant_b)],
}

# Round 2 messages (same for all segments — more urgent)
def _r2_variant_a(first_name: str, clips_text: str) -> str:
    return (
        f"คลิปใหม่วันนี้ {clips_text}! 🔥\n"
        f"\n"
        f"สมัคร VIP ดูฟรีทั้งหมด\n"
        f"\n"
        f'👉 <a href="tg://resolve?domain=NamwarnJarern_bot&start=packages">สมัคร VIP</a>'
    )


def _r2_variant_b(first_name: str, clips_text: str) -> str:
    return (
        f"{first_name} ยังไม่สมัคร VIP เหรอ? 😢\n"
        f"\n"
        f"วันนี้มีคลิปใหม่ {clips_text}\n"
        f"สมาชิกดูได้ทุกชิ้น!\n"
        f"\n"
        f'👉 <a href="tg://resolve?domain=NamwarnJarern_bot&start=packages">ดูแพ็กเกจ VIP</a>'
    )


R2_VARIANTS = [("A", _r2_variant_a), ("B", _r2_variant_b)]


# ─── Helpers ─────────────────────────────────────────────────────────────────

async def _has_active_subscription(user_id: int) -> bool:
    """Check if user has an active subscription."""
    async with get_session() as session:
        result = await session.execute(
            select(Subscription).where(
                Subscription.user_id == user_id,
                Subscription.status == SubscriptionStatus.ACTIVE,
                Subscription.end_date > datetime.utcnow(),
            )
        )
        return result.scalar_one_or_none() is not None


async def _has_any_payment(user_id: int) -> bool:
    """Check if user has any confirmed payment."""
    async with get_session() as session:
        result = await session.execute(
            text("SELECT 1 FROM payments WHERE user_id = :uid AND status = 'CONFIRMED' LIMIT 1"),
            {"uid": user_id},
        )
        return result.fetchone() is not None


async def _already_sent(user_id: int, dm_round: int) -> bool:
    """Check if DM already sent for this round."""
    async with get_session() as session:
        result = await session.execute(
            text("SELECT 1 FROM lead_followup_log WHERE user_id = :uid AND round = :round LIMIT 1"),
            {"uid": user_id, "round": dm_round},
        )
        return result.fetchone() is not None


async def _get_new_content_count(since_days: int = 1) -> int:
    """นับจำนวนคลิปใหม่."""
    cutoff = datetime.utcnow() - timedelta(days=since_days)
    async with get_session() as session:
        result = await session.execute(
            select(func.count(ContentQueue.id)).where(
                ContentQueue.created_at >= cutoff,
                ContentQueue.is_used == True,  # noqa: E712
            )
        )
        return result.scalar() or 0


async def _get_current_flash_sale_text() -> str | None:
    """Get current active flash sale info if any."""
    async with get_session() as session:
        result = await session.execute(
            text("""
                SELECT name, flash_price, original_price
                FROM flash_sales
                WHERE is_active = true AND ends_at > NOW()
                LIMIT 1
            """)
        )
        row = result.fetchone()
        if row:
            return f"Flash Sale {row.name} ฿{row.flash_price} (จาก ฿{row.original_price})"
    return None


# ─── Round 1: 2 hours after /start ──────────────────────────────────────────

async def get_round1_leads() -> list[dict]:
    """ดึง leads ที่ /start > 2 ชม. + ยังไม่ซื้อ + ยังไม่เคย DM รอบ 1."""
    cutoff = datetime.utcnow() - timedelta(hours=ROUND1_WAIT_HOURS)

    async with get_session() as session:
        # Users who already got round 1 DM
        already_sent_sq = (
            select(text("user_id"))
            .select_from(text("lead_followup_log"))
            .where(text("round = 1"))
            .scalar_subquery()
        )

        result = await session.execute(
            select(User, Lead)
            .join(Lead, Lead.user_id == User.id)
            .where(
                Lead.status == LeadStatus.NEW,
                Lead.created_at < cutoff,
                User.is_banned == False,  # noqa: E712
                User.id.notin_(already_sent_sq),
            )
            .order_by(Lead.created_at.asc())
            .limit(MAX_DM_PER_HOUR)
        )
        rows = result.all()

    leads = []
    seen = set()
    for user, lead in rows:
        if user.id in seen:
            continue
        seen.add(user.id)
        leads.append({
            "user_id": user.id,
            "telegram_id": user.telegram_id,
            "first_name": user.first_name or user.username or "คุณ",
            "lead_created_at": lead.created_at,
        })
    return leads


# ─── Round 2: 24 hours after /start ─────────────────────────────────────────

async def get_round2_leads() -> list[dict]:
    """ดึง leads ที่ DM รอบ 1 แล้ว + ผ่านไป 24 ชม. + ยังไม่ซื้อ + ยังไม่ DM รอบ 2."""
    cutoff = datetime.utcnow() - timedelta(hours=ROUND2_WAIT_HOURS)

    async with get_session() as session:
        # Already got round 2
        already_r2 = (
            select(text("user_id"))
            .select_from(text("lead_followup_log"))
            .where(text("round = 2"))
            .scalar_subquery()
        )

        result = await session.execute(
            text("""
                SELECT u.id, u.telegram_id, u.first_name, u.username
                FROM lead_followup_log lf
                JOIN users u ON u.id = lf.user_id
                WHERE lf.round = 1
                  AND lf.purchased = false
                  AND lf.sent_at < :cutoff
                  AND u.is_banned = false
                  AND u.id NOT IN (SELECT user_id FROM lead_followup_log WHERE round = 2)
                ORDER BY lf.sent_at ASC
                LIMIT :lim
            """),
            {"cutoff": cutoff, "lim": MAX_DM_PER_HOUR},
        )
        rows = result.fetchall()

    leads = []
    seen = set()
    for row in rows:
        if row.id in seen:
            continue
        seen.add(row.id)
        leads.append({
            "user_id": row.id,
            "telegram_id": row.telegram_id,
            "first_name": row.first_name or row.username or "คุณ",
        })
    return leads


# ─── Send DM ────────────────────────────────────────────────────────────────

async def send_lead_followup_dm(
    bot: Bot,
    lead: dict,
    dm_round: int,
    message: str,
    segment: str | None = None,
    variant: str | None = None,
) -> bool:
    """ส่ง follow-up DM. Returns True if sent."""
    # Skip if user already purchased
    if await _has_any_payment(lead["user_id"]):
        return False

    try:
        await bot.send_message(
            chat_id=lead["telegram_id"],
            text=message,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
    except Forbidden:
        logger.info("Cannot DM lead %d — blocked/never started", lead["telegram_id"])
        return False
    except Exception as exc:
        logger.error("Failed to DM lead %d: %s", lead["telegram_id"], exc)
        return False

    # Log to DB with segment + variant
    async with get_session() as session:
        await session.execute(
            text("""
                INSERT INTO lead_followup_log (user_id, telegram_id, round, segment, variant)
                VALUES (:uid, :tgid, :round, :seg, :var)
            """),
            {
                "uid": lead["user_id"],
                "tgid": lead["telegram_id"],
                "round": dm_round,
                "seg": segment,
                "var": variant,
            },
        )
        await session.commit()

    logger.info(
        "Lead followup DM sent: user_id=%d tg=%d round=%d segment=%s variant=%s",
        lead["user_id"], lead["telegram_id"], dm_round, segment or "-", variant or "-",
    )
    return True


# ─── Scheduler Job ──────────────────────────────────────────────────────────

async def run_lead_followup_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Scheduled job: ส่ง follow-up DM ให้ leads ที่ยังไม่ซื้อ.

    - รอบ 1: /start > 2 ชม. + ยังไม่ซื้อ → Smart Segmentation + A/B test
    - รอบ 2: DM รอบ 1 > 24 ชม. + ยังไม่ซื้อ → A/B test
    """
    bot = context.bot
    now_th = datetime.now(TH_TZ)
    logger.info("🔄 Lead follow-up job started at %s", now_th.strftime("%Y-%m-%d %H:%M"))

    # Ensure table exists
    await ensure_table()

    # Get flash sale info for message
    flash_sale = await _get_current_flash_sale_text()
    new_clips = await _get_new_content_count(since_days=1)

    total_sent = 0
    total_failed = 0
    dm_budget = MAX_DM_PER_HOUR
    segment_counts: dict[str, int] = {}
    variant_counts: dict[str, int] = {}

    # ---- รอบ 1: /start > 2 ชม. — Smart Segmentation ----
    round1_leads = await get_round1_leads()
    round1_sent = 0

    promo_text = flash_sale or "VIP 30 วัน ฿300"
    for lead in round1_leads:
        if dm_budget <= 0:
            break

        # Classify lead
        lead_created = lead.get("lead_created_at", datetime.utcnow())
        segment = await _classify_lead(lead["user_id"], lead_created)

        # Pick variant for segment
        variants = SEGMENT_VARIANTS.get(segment, SEGMENT_VARIANTS["WARM"])
        v_name, v_func = random.choice(variants)
        msg = v_func(lead["first_name"], promo_text)

        success = await send_lead_followup_dm(
            bot, lead, dm_round=1, message=msg,
            segment=segment, variant=v_name,
        )
        if success:
            round1_sent += 1
            total_sent += 1
            dm_budget -= 1
            segment_counts[segment] = segment_counts.get(segment, 0) + 1
            variant_counts[f"{segment}_{v_name}"] = variant_counts.get(f"{segment}_{v_name}", 0) + 1
        else:
            total_failed += 1
        await asyncio.sleep(DM_DELAY_SECONDS)

    # ---- รอบ 2: DM รอบ 1 > 24 ชม. ----
    round2_sent = 0

    if dm_budget > 0:
        round2_leads = await get_round2_leads()
        clips_text = f"{new_clips} ชิ้น" if new_clips > 0 else "เพียบ"
        for lead in round2_leads:
            if dm_budget <= 0:
                break
            v_name, v_func = random.choice(R2_VARIANTS)
            msg = v_func(lead["first_name"], clips_text)
            success = await send_lead_followup_dm(
                bot, lead, dm_round=2, message=msg,
                segment="R2", variant=v_name,
            )
            if success:
                round2_sent += 1
                total_sent += 1
                dm_budget -= 1
                variant_counts[f"R2_{v_name}"] = variant_counts.get(f"R2_{v_name}", 0) + 1
            else:
                total_failed += 1
            await asyncio.sleep(DM_DELAY_SECONDS)

    # ---- Summary ----
    seg_summary = ", ".join(f"{k}:{v}" for k, v in sorted(segment_counts.items()))
    var_summary = ", ".join(f"{k}:{v}" for k, v in sorted(variant_counts.items()))
    summary = (
        f"📊 Lead Follow-up Summary ({now_th.strftime('%d/%m/%Y %H:%M')})\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"รอบ 1 (2 ชม.): ส่ง {round1_sent}\n"
        f"  Segments: {seg_summary or 'N/A'}\n"
        f"รอบ 2 (24 ชม.): ส่ง {round2_sent}\n"
        f"Variants: {var_summary or 'N/A'}\n"
        f"รวม: ส่ง {total_sent} / ไม่ได้ {total_failed}"
    )
    logger.info(summary)

    # Admin notification if any sent
    if total_sent > 0:
        admin_token = os.environ.get("ADMIN_BOT_TOKEN", "")
        if admin_token:
            try:
                admin_bot = Bot(token=admin_token)
                await admin_bot.initialize()
                await admin_bot.send_message(
                    chat_id=ADMIN_GROUP_ID,
                    text=(
                        f"📬 <b>Lead Follow-up Report</b>\n\n{summary}\n\n"
                        f"🔬 Variants: {var_summary or 'N/A'}"
                    ),
                    parse_mode="HTML",
                )
            except Exception as exc:
                logger.error("Failed to send lead followup admin notification: %s", exc)
