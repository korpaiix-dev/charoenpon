"""Retention Alert v2 — แจ้งเตือนหมดอายุ + ส่วนลดจูงใจต่ออายุ.

- 3 วันก่อนหมดอายุ: ลด 10%
- 1 วันก่อนหมดอายุ: ลด 15%
- วันหมดอายุ: ลด 20% (24 ชม. สุดท้าย)
- ใช้ expiry_notifications table กัน duplicate
- Promo code + deep link → ลูกค้ากดลิงก์เข้าบอทแล้วส่วนลด auto-apply
- Round: 200=3day, 201=1day, 202=expiry
"""

from __future__ import annotations

import logging
import os
import secrets
import string
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select, text
from telegram import Bot
from telegram.error import Forbidden
from telegram.ext import ContextTypes

from shared.database import get_session
from shared.models import (
    ExpiryNotification,
    NotificationType,
    Subscription,
    SubscriptionStatus,
    User,
)

logger = logging.getLogger(__name__)

from shared.tz import TH_TZ
from shared.admin_alert import _admin_group_id

ADMIN_GROUP_ID = _admin_group_id()

# Discount tiers: (days_before_expiry, discount_pct, notification_type, round_offset)
# Round numbering: 200=3day, 201=1day, 202=expiry
DISCOUNT_TIERS = [
    (0, 20, NotificationType.EXPIRED, 202),          # Day of expiry
    (1, 15, NotificationType.PRE_EXPIRY_1D, 201),     # 1 day before
    (3, 10, NotificationType.PRE_EXPIRY_3D, 200),     # 3 days before
]

PROMO_EXPIRY_HOURS = 48


# ─── Promo Code Helpers ──────────────────────────────────────────────────────

def _generate_promo_code() -> str:
    """Generate 8-char promo code."""
    chars = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(chars) for _ in range(8))


async def _save_retention_promo(
    user_id: int, telegram_id: int, promo_code: str, discount_pct: int, dm_round: int
) -> None:
    """Save retention promo code to comeback_dm_log."""
    async with get_session() as session:
        await session.execute(
            text("""
                INSERT INTO comeback_dm_log
                    (user_id, telegram_id, discount_pct, promo_code, round, variant)
                VALUES (:uid, :tgid, :disc, :code, :round, :var)
            """),
            {
                "uid": user_id,
                "tgid": telegram_id,
                "disc": discount_pct,
                "code": promo_code,
                "round": dm_round,
                "var": "retention_v2",
            },
        )
        await session.commit()


# ─── Helpers ─────────────────────────────────────────────────────────────────

async def _get_expiring_subscriptions(days_from: int, days_to: int) -> list[dict]:
    """Get active subscriptions expiring within a date range."""
    now = datetime.utcnow()
    start = now + timedelta(days=days_from)
    end = now + timedelta(days=days_to)

    async with get_session() as session:
        result = await session.execute(
            text("""
                SELECT s.id as sub_id, s.user_id, s.package_id, s.end_date,
                       u.telegram_id, u.first_name, u.username,
                       p.name as package_name, p.price as package_price
                FROM subscriptions s
                JOIN users u ON u.id = s.user_id
                JOIN packages p ON p.id = s.package_id
                WHERE s.status = 'ACTIVE'
                  AND s.end_date >= :start
                  AND s.end_date < :end
                  AND u.is_banned = false
                ORDER BY s.end_date ASC
            """),
            {"start": start, "end": end},
        )
        rows = result.fetchall()

    return [
        {
            "sub_id": row.sub_id,
            "user_id": row.user_id,
            "package_id": row.package_id,
            "end_date": row.end_date,
            "telegram_id": row.telegram_id,
            "first_name": row.first_name or row.username or "คุณ",
            "package_name": row.package_name,
            "package_price": Decimal(str(row.package_price)),
        }
        for row in rows
    ]


async def _already_notified(user_id: int, sub_id: int, notif_type: NotificationType) -> bool:
    """Check if this notification was already sent."""
    async with get_session() as session:
        result = await session.execute(
            text("""
                SELECT 1 FROM expiry_notifications
                WHERE user_id = :uid AND subscription_id = :sid AND notification_type = :ntype
                LIMIT 1
            """),
            {"uid": user_id, "sid": sub_id, "ntype": notif_type.value},
        )
        return result.fetchone() is not None


async def _log_notification(user_id: int, sub_id: int, notif_type: NotificationType, message_id: int | None = None) -> None:
    """Log sent notification to prevent duplicates."""
    async with get_session() as session:
        await session.execute(
            text("""
                INSERT INTO expiry_notifications (user_id, subscription_id, notification_type, message_id)
                VALUES (:uid, :sid, :ntype, :mid)
                ON CONFLICT (user_id, subscription_id, notification_type) DO NOTHING
            """),
            {"uid": user_id, "sid": sub_id, "ntype": notif_type.value, "mid": message_id},
        )
        await session.commit()


# ─── Message Builder ─────────────────────────────────────────────────────────

def _build_retention_message(
    first_name: str,
    package_name: str,
    package_price: Decimal,
    discount_pct: int,
    days_left: int,
    promo_code: str,
) -> str:
    """Build retention alert message with discount + promo code + deep link."""
    discounted = int(package_price * (100 - discount_pct) / 100)

    if days_left <= 0:
        urgency = "⚠️ <b>แพ็กเกจหมดอายุวันนี้!</b>"
        time_text = "ภายใน 24 ชม. นี้"
    elif days_left == 1:
        urgency = "⏰ <b>เหลืออีก 1 วัน!</b>"
        time_text = "ภายในวันพรุ่งนี้"
    else:
        urgency = f"📢 <b>เหลืออีก {days_left} วัน</b>"
        time_text = f"ภายใน {days_left} วัน"

    return (
        f"{urgency}\n"
        f"\n"
        f"คุณ {first_name} แพ็กเกจ <b>{package_name}</b> จะหมดอายุ{time_text}ค่ะ\n"
        f"\n"
        f"🎁 ต่ออายุวันนี้รับส่วนลด <b>{discount_pct}%</b>\n"
        f"💰 จ่ายแค่ <b>฿{discounted}</b> (จาก ฿{int(package_price)})\n"
        f"\n"
        # # >>> FIX_NO_CODE_WORDING <<<
        f"⏰ ส่วนลดของคุณใช้ได้ 48 ชม.\n"
        f"\n"
        f"อย่าพลาดสัญญาณดีๆ นะคะ 🙏\n"
        f'👉 <a href="tg://resolve?domain=NamwarnJarern_bot&start=comeback_{promo_code}">กดปุ่มต่ออายุเลย (ไม่ต้องกรอกโค้ด)</a>'
    )


# ─── Scheduler Job ───────────────────────────────────────────────────────────

async def run_retention_alert_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Scheduled job: send retention alerts with discounts."""
    bot = context.bot
    now_th = datetime.now(TH_TZ)
    logger.info("🔔 Retention alert v2 job started at %s", now_th.strftime("%Y-%m-%d %H:%M"))

    total_sent = 0
    total_skipped = 0

    for days_before, discount_pct, notif_type, dm_round in DISCOUNT_TIERS:
        # Date range for this tier
        if days_before == 0:
            subs = await _get_expiring_subscriptions(days_from=-1, days_to=1)
            days_left = 0
        elif days_before == 1:
            subs = await _get_expiring_subscriptions(days_from=0, days_to=2)
            days_left = 1
        else:
            subs = await _get_expiring_subscriptions(days_from=2, days_to=4)
            days_left = 3

        for sub in subs:
            # Skip if already notified for this type
            if await _already_notified(sub["user_id"], sub["sub_id"], notif_type):
                total_skipped += 1
                continue

            # Generate promo code for this retention alert
            promo_code = _generate_promo_code()

            msg = _build_retention_message(
                first_name=sub["first_name"],
                package_name=sub["package_name"],
                package_price=sub["package_price"],
                discount_pct=discount_pct,
                days_left=days_left,
                promo_code=promo_code,
            )

            message_id = None
            try:
                sent_msg = await bot.send_message(
                    chat_id=sub["telegram_id"],
                    text=msg,
                    parse_mode="HTML",
                )
                message_id = sent_msg.message_id
                total_sent += 1

                # Save promo code to comeback_dm_log
                try:
                    await _save_retention_promo(
                        user_id=sub["user_id"],
                        telegram_id=sub["telegram_id"],
                        promo_code=promo_code,
                        discount_pct=discount_pct,
                        dm_round=dm_round,
                    )
                except Exception as promo_exc:
                    logger.error("Failed to save retention promo for user %d: %s", sub["telegram_id"], promo_exc)
            except Forbidden:
                logger.info("Cannot DM user %d — blocked bot", sub["telegram_id"])
            except Exception as exc:
                logger.error("Failed to send retention alert to %d: %s", sub["telegram_id"], exc)

            # Log regardless to prevent retries
            await _log_notification(sub["user_id"], sub["sub_id"], notif_type, message_id)

    logger.info(
        "Retention alert v2 done: sent=%d, skipped=%d",
        total_sent, total_skipped,
    )
