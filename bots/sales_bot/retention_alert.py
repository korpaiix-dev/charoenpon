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
                INSERT INTO expiry_notifications (user_id, subscription_id, notification_type, message_id, acknowledged)
                VALUES (:uid, :sid, :ntype, :mid, FALSE)
                ON CONFLICT (user_id, subscription_id, notification_type) DO NOTHING
            """),
            {"uid": user_id, "sid": sub_id, "ntype": notif_type.value, "mid": message_id},
        )
        await session.commit()


# ─── Message Builder ─────────────────────────────────────────────────────────


# ── Retention discount amounts per tier (match shared/pricing.py) ──
RETENTION_PRICES = {
    # tier_callback_str -> {discount_pct: discounted_amount}
    "300":  {10: 269, 15: 255, 20: 240},
    "500":  {10: 450, 15: 425, 20: 400},
    "1299": {10: 1169, 15: 1104, 20: 1039},
    "2499": {10: 2249, 15: 2124, 20: 1998},
}


def _retention_discounted_amount(package_tier: str, discount_pct: int) -> int:
    """Return concrete amount the customer should transfer."""
    tier_map = RETENTION_PRICES.get(str(package_tier), {})
    return tier_map.get(discount_pct, 0)


def _build_retention_message_v2(
    first_name: str,
    package_name: str,
    package_tier: str,
    package_price,
    discount_pct: int,
    days_left: int,
    promo_code: str,
    top_room: str | None = None,
    new_clips_week: int | None = None,
) -> str:
    """Build retention message based on round (3d / 1d / today).

    Uses loss aversion + content reminder + concrete bonus.
    Real discounted amount comes from RETENTION_PRICES table.
    """
    from decimal import Decimal as _D
    discounted = _retention_discounted_amount(package_tier, discount_pct)
    if discounted <= 0:
        # Fallback to %-based math
        discounted = int(_D(str(package_price)) * (100 - discount_pct) / 100)

    fname = first_name or "คุณ"
    deep_link = f'tg://resolve?domain=NamwarnJarern_bot&start=comeback_{promo_code}'

    # Round 1 — 3 days left (10% off, no bonus)
    if days_left >= 2:
        room_line = ""
        if top_room and new_clips_week and new_clips_week > 0:
            room_line = f"📦 ห้อง <b>{top_room}</b> → อัปคลิปใหม่ <b>{new_clips_week}</b> ตอนสัปดาห์นี้\n"
        return (
            f"สวัสดีคุณ {fname}~ 👋\n\n"
            f"⏳ อีก <b>{days_left} วัน</b> สมาชิก {package_name} ของคุณจะหมดอายุ\n"
            f"\n"
            f"อยากบอกก่อน — เพราะเตรียมไว้ให้แล้ว 👇\n"
            f"{room_line}"
            f"🎬 คลิป exclusive อัปทุกวัน\n"
            f"✨ Summer Fest อยู่ในแพ็คเกจถาวร\n"
            f"\n"
            f"⚠️ ถ้าหมดอายุ — ลิงก์เข้ากลุ่มหยุดทันที\n"
            f"ระบบเก็บประวัติให้แค่ <b>7 วัน</b>\n"
            f"\n"
            f"━━━━━━━━━━━━━━━\n"
            f"🎁 ต่ออายุก่อนหมด — ลด <b>10%</b>\n"
            f"💰 จ่ายแค่ <b>฿{discounted:,}</b> (ปกติ ฿{int(package_price):,})\n"
            f"⏰ ใช้ได้ 48 ชม.\n"
            f"\n"
            f'👉 <a href="{deep_link}">ต่ออายุ {package_name} — ลด 10%</a>'
        )

    # Round 2 — 1 day left (15% off + 1 gachapon spin bonus)
    if days_left == 1:
        return (
            f"⏰ พรุ่งนี้คุณจะออกจากเราแล้วนะคะ 😢\n"
            f"\n"
            f"คุณ {fname} อยู่กับเรามาเกือบ 30 วันเต็ม — ขอบคุณนะคะ 🙏\n"
            f"\n"
            f"อยากให้คุณอยู่ต่อ — จัดพิเศษ:\n"
            f"\n"
            f"🎁 ลด <b>15%</b> (ปกติ 10%)\n"
            f"🎁 + กาชาปอง <b>1 หมุน</b> ฟรี ลุ้นรางวัล (ปกติ ฿99)\n"
            f"\n"
            f"💰 จ่ายแค่ <b>฿{discounted:,}</b> (จาก ฿{int(package_price):,})\n"
            f"⏰ ใช้ได้แค่ <b>24 ชม.</b> — พรุ่งนี้กลับเป็น 10%\n"
            f"\n"
            f"ไม่อยากเสียคุณไปจริง ๆ ค่ะ\n"
            f"\n"
            f'👉 <a href="{deep_link}">ต่ออายุ + รับกาชาปอง 1 หมุน</a>'
        )

    # Round 3 — today (20% off + 3 spins + 1 shaker number)
    return (
        f"😢 <b>วันสุดท้ายแล้ว คุณ {fname}</b>\n"
        f"\n"
        f"วันนี้คือวันสุดท้ายที่คุณยังเข้าห้องได้\n"
        f"— พรุ่งนี้ลิงก์เข้ากลุ่มจะหยุดทำงาน\n"
        f"\n"
        f"ก่อนคุณไปจริง ๆ จัดให้สุดที่เคยมี:\n"
        f"\n"
        f"🎁 ลด <b>20%</b> (ดีที่สุดของเรา)\n"
        f"🎁 + กาชาปอง <b>3 หมุน</b> ฟรี (ปกติ ฿270)\n"
        f"🎁 + เลขห้องมีคนชัก <b>1 ใบ</b> ลุ้น GOD ถาวร (ปกติ ฿100)\n"
        f"\n"
        f"💰 จ่ายแค่ <b>฿{discounted:,}</b> (จาก ฿{int(package_price):,})\n"
        f"💎 รวมของแถม มูลค่า ฿{370 + (int(package_price) - discounted):,}\n"
        f"\n"
        f"หลังคืนนี้ ราคา discount จะหายไป\n"
        f"ต้องสมัครใหม่ราคาเต็ม\n"
        f"\n"
        f"ขอบคุณที่อยู่ด้วยกันมาตลอด 🙏\n"
        f"\n"
        f'👉 <a href="{deep_link}">ต่ออายุ + รับของแถมทั้งหมด</a>'
    )


# ─── Legacy wrapper for backward compat ──────────────────────────────────
def _build_retention_message(
    first_name: str,
    package_name: str,
    package_price,
    discount_pct: int,
    days_left: int,
    promo_code: str,
) -> str:
    """Legacy 6-arg signature — delegates to v2."""
    # Infer tier from package_name
    if "GOD MODE ถาวร" in package_name or "2,499" in package_name or "2499" in package_name:
        tier = "2499"
    elif "GOD MODE" in package_name or "1,299" in package_name or "1299" in package_name:
        tier = "1299"
    elif "OnlyFans" in package_name or "OF" in package_name or "500" in package_name:
        tier = "500"
    else:
        tier = "300"
    return _build_retention_message_v2(
        first_name=first_name,
        package_name=package_name,
        package_tier=tier,
        package_price=package_price,
        discount_pct=discount_pct,
        days_left=days_left,
        promo_code=promo_code,
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
