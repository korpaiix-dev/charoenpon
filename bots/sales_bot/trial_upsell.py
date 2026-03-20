"""Trial Upsell DM - Auto DM หลัง Trial หมด 1 ชม.

ส่ง DM อัตโนมัติเพื่อ upsell VIP เต็มหลังจาก trial 24 ชม. หมดอายุ
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy import select, and_
from telegram.ext import ContextTypes

from shared.database import get_session
from shared.models import (
    Package,
    PackageTier,
    Payment,
    PaymentStatus,
    Subscription,
    SubscriptionStatus,
    User,
)

logger = logging.getLogger(__name__)

# ข้อความ upsell หลัง trial หมด
UPSELL_TEXT = (
    "สวัสดีค่ะ คุณ {name} 💕\n\n"
    "Trial VIP เจริญพร ของคุณหมดแล้ว\n"
    "ชอบมั้ยคะ? 😊\n\n"
    "สมัคร VIP เต็มตอนนี้:\n\n"
    "━━━━━━━━━━━━━━━━━━\n"
    '📩 <b>สมัครต่อเลย 👇</b>\n'
    '👉 <a href="tg://resolve?domain=NamwarnJarern_bot&start=packages">⚡ สมัคร VIP เจริญพร ⚡</a>\n'
    "━━━━━━━━━━━━━━━━━━"
)


async def check_trial_upsell(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Scheduled job: เช็ค trial ที่หมดแล้ว 1 ชม. แล้วส่ง DM upsell.

    รันทุก 30 นาที เพื่อจับ trial ที่หมดภายใน window 30 นาที - 1.5 ชม.
    """
    now = datetime.utcnow()
    # Trial ที่หมดอายุระหว่าง 1 ชม. ถึง 1.5 ชม. ที่แล้ว
    window_start = now - timedelta(hours=1, minutes=30)
    window_end = now - timedelta(hours=1)

    sent_count = 0
    error_count = 0

    async with get_session() as session:
        # หา trial package
        pkg_result = await session.execute(
            select(Package).where(Package.tier == PackageTier.TIER_99)
        )
        trial_pkg = pkg_result.scalar_one_or_none()
        if not trial_pkg:
            return

        # หา subscriptions ที่หมดอายุในช่วง window
        subs_result = await session.execute(
            select(Subscription, User)
            .join(User, Subscription.user_id == User.id)
            .where(
                and_(
                    Subscription.package_id == trial_pkg.id,
                    Subscription.status == SubscriptionStatus.EXPIRED,
                    Subscription.end_date >= window_start,
                    Subscription.end_date <= window_end,
                )
            )
        )
        expired_trials = subs_result.all()

    for sub, user in expired_trials:
        # เช็คว่ายังไม่มี active subscription (ไม่ซื้อ VIP แล้ว)
        async with get_session() as session:
            active_result = await session.execute(
                select(Subscription).where(
                    and_(
                        Subscription.user_id == user.id,
                        Subscription.status == SubscriptionStatus.ACTIVE,
                    )
                )
            )
            if active_result.scalar_one_or_none():
                continue  # ซื้อ VIP แล้ว ไม่ต้อง upsell

        name = user.first_name or user.username or "ลูกค้า"
        text = UPSELL_TEXT.format(name=name)

        try:
            await context.bot.send_message(
                chat_id=user.telegram_id,
                text=text,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
            sent_count += 1
            logger.info("Trial upsell DM sent to user %s (tg:%d)", user.username, user.telegram_id)
        except Exception as exc:
            error_count += 1
            logger.warning("Failed to send trial upsell to %d: %s", user.telegram_id, exc)

    if sent_count > 0 or error_count > 0:
        logger.info("Trial upsell check: sent=%d errors=%d", sent_count, error_count)
