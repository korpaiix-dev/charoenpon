"""Comeback DM System — ส่ง DM ลูกค้าเก่าที่หมดอายุ พร้อมส่วนลด.

- รอบ 1: หมดอายุ > 3 วัน → ส่วนลด 20%
- รอบ 2: DM รอบ 1 แล้ว 14 วัน + ยังไม่ซื้อ → ส่วนลด 30%
- Rate limit: 30 DM/วัน, delay 3 วินาที
- Promo code หมดอายุ 48 ชม.
"""

from __future__ import annotations

import asyncio
import logging
import os
import secrets
import string
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import httpx
from sqlalchemy import select, and_, func
from telegram import Bot
from telegram.error import Forbidden
from telegram.ext import ContextTypes

from shared.database import get_session
from shared.models import (
    ComebackDmLog,
    Subscription,
    SubscriptionStatus,
    User,
)

logger = logging.getLogger(__name__)

TH_TZ = timezone(timedelta(hours=7))

# Config
MAX_DM_PER_DAY = 30
DM_DELAY_SECONDS = 3
PROMO_EXPIRY_HOURS = 48
BASE_PRICE = Decimal("300")  # VIP 30 วัน ราคาปกติ

ADMIN_GROUP_ID = int(os.environ.get("ADMIN_GROUP_CHAT_ID", "-1003830920430"))


def _generate_promo_code() -> str:
    """สร้าง promo code 8 ตัวอักษร."""
    chars = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(chars) for _ in range(8))


def _calculate_discounted_price(discount_pct: int) -> int:
    """คำนวณราคาหลังลด."""
    return int(BASE_PRICE * (100 - discount_pct) / 100)


def _build_comeback_message(first_name: str, discount_pct: int, promo_code: str) -> str:
    """สร้างข้อความ DM COMEBACK."""
    discounted_price = _calculate_discounted_price(discount_pct)
    return (
        f"สวัสดีค่ะ คุณ {first_name} 💕\n"
        f"\n"
        f"VIP เจริญพร ของคุณหมดอายุไปแล้ว...\n"
        f"แต่เรามีคลิปใหม่ๆ เพียบ!\n"
        f"\n"
        f"🔥 กลับมาวันนี้ รับส่วนลด {discount_pct}%\n"
        f"สมาชิก VIP 30 วัน ฿{discounted_price} (จาก ฿300)\n"
        f"\n"
        f"✅ คลิปเต็มไม่เบลอ ทุกวัน\n"
        f"✅ คลิป Exclusive ก่อนใคร\n"
        f"✅ รวมกว่า 10,000 คลิป\n"
        f"\n"
        f"⏰ สิทธิ์นี้ใช้ได้ 48 ชม. เท่านั้น\n"
        f"\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f'📩 <b>สมัครต่ออายุเลย 👇</b>\n'
        f'👉 <a href="tg://resolve?domain=jarernAD1_bot&start=comeback_{promo_code}">⚡ สมัคร VIP เจริญพร ลด {discount_pct}% ⚡</a>\n'
        f"━━━━━━━━━━━━━━━━━━"
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

        # ดึง user ที่มี subscription EXPIRED + end_date < cutoff + ยังไม่เคยส่ง DM
        result = await session.execute(
            select(User, Subscription)
            .join(Subscription, Subscription.user_id == User.id)
            .where(
                Subscription.status == SubscriptionStatus.EXPIRED,
                Subscription.end_date < cutoff,
                User.id.notin_(already_sent),
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


async def get_round2_customers() -> list[dict]:
    """ดึงลูกค้าที่ DM รอบ 1 แล้ว 14 วัน + ยังไม่ซื้อ + ยังไม่เคย DM รอบ 2."""
    cutoff = datetime.utcnow() - timedelta(days=14)

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


async def send_comeback_dm(bot: Bot, user: dict, discount_pct: int = 20, dm_round: int = 1) -> bool:
    """ส่ง DM ให้ลูกค้าเก่าพร้อมส่วนลด.

    Returns True if sent successfully, False if failed.
    """
    promo_code = _generate_promo_code()
    message = _build_comeback_message(user["first_name"], discount_pct, promo_code)

    try:
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

    # Log to DB
    async with get_session() as session:
        log_entry = ComebackDmLog(
            user_id=user["user_id"],
            telegram_id=user["telegram_id"],
            discount_pct=discount_pct,
            promo_code=promo_code,
            round=dm_round,
        )
        session.add(log_entry)

    logger.info(
        "COMEBACK DM sent: user_id=%d tg=%d round=%d discount=%d%% code=%s",
        user["user_id"], user["telegram_id"], dm_round, discount_pct, promo_code,
    )
    return True


async def validate_promo_code(promo_code: str) -> dict | None:
    """ตรวจสอบ promo code ว่าถูกต้อง + ยังไม่หมดอายุ (48 ชม.).

    Returns dict with discount_pct, user_id, telegram_id or None if invalid.
    """
    async with get_session() as session:
        result = await session.execute(
            select(ComebackDmLog).where(
                ComebackDmLog.promo_code == promo_code,
                ComebackDmLog.purchased == False,  # noqa: E712
            )
        )
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
    """ส่ง log ไป Discord #system-logs."""
    discord_token = os.environ.get("DISCORD_BOT_TOKEN", "")
    discord_ch = os.environ.get("DISCORD_CH_SYSTEM_LOGS", "")
    if not discord_token or not discord_ch:
        return
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(
                f"https://discord.com/api/v10/channels/{discord_ch}/messages",
                headers={
                    "Authorization": f"Bot {discord_token}",
                    "Content-Type": "application/json",
                },
                json={"content": message},
            )
    except Exception as exc:
        logger.warning("Discord system log failed: %s", exc)


async def run_comeback_dm_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Scheduled job: ส่ง DM COMEBACK ลูกค้าเก่า.

    - รอบ 1: หมดอายุ > 3 วัน, discount 20%
    - รอบ 2: DM รอบ 1 แล้ว 14 วัน + ยังไม่ซื้อ, discount 30%
    """
    bot = context.bot
    now_th = datetime.now(TH_TZ)
    logger.info("🔄 COMEBACK DM job started at %s", now_th.strftime("%Y-%m-%d %H:%M"))

    total_sent = 0
    total_failed = 0
    dm_budget = MAX_DM_PER_DAY

    # ---- รอบ 1: ลูกค้าหมดอายุ > 3 วัน ----
    round1_customers = await get_expired_customers(days_since_expire=3)
    round1_sent = 0
    round1_failed = 0

    for customer in round1_customers:
        if dm_budget <= 0:
            break
        success = await send_comeback_dm(bot, customer, discount_pct=20, dm_round=1)
        if success:
            round1_sent += 1
            total_sent += 1
            dm_budget -= 1
        else:
            round1_failed += 1
            total_failed += 1
        await asyncio.sleep(DM_DELAY_SECONDS)

    # ---- รอบ 2: DM รอบ 1 แล้ว 14 วัน + ยังไม่ซื้อ ----
    round2_sent = 0
    round2_failed = 0

    if dm_budget > 0:
        round2_customers = await get_round2_customers()
        for customer in round2_customers:
            if dm_budget <= 0:
                break
            success = await send_comeback_dm(bot, customer, discount_pct=30, dm_round=2)
            if success:
                round2_sent += 1
                total_sent += 1
                dm_budget -= 1
            else:
                round2_failed += 1
                total_failed += 1
            await asyncio.sleep(DM_DELAY_SECONDS)

    # ---- Summary ----
    summary = (
        f"📊 COMEBACK DM Summary ({now_th.strftime('%d/%m/%Y')})\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"รอบ 1 (ลด 20%): ส่ง {round1_sent} / ไม่ได้ {round1_failed}\n"
        f"รอบ 2 (ลด 30%): ส่ง {round2_sent} / ไม่ได้ {round2_failed}\n"
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
                f"รอบ 1 (ลด 20%): {round1_sent} คน\n"
                f"รอบ 2 (ลด 30%): {round2_sent} คน"
            )
            await admin_bot.send_message(
                chat_id=ADMIN_GROUP_ID,
                text=admin_text,
                parse_mode="HTML",
            )
        except Exception as exc:
            logger.error("Failed to send COMEBACK admin notification: %s", exc)
