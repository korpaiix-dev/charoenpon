"""Guardian Bot (ยาม) - Scheduler.

ไม่ใช้ AI — Python + SQL ล้วน

Scheduled tasks:
- ทุก 6 ชม.: เตะสมาชิกหมดอายุ
- ทุกวัน 09:00: ส่งรายชื่อใกล้หมดอายุ (1/3/7 วัน) ให้ sales bot
- ทุก 30 นาที: ตรวจคนเข้ากลุ่มไม่มีสิทธิ์
- ทุกวัน 22:00: daily report

กฎ:
- ส่งเตือนแต่ละ tier ได้ครั้งเดียวต่อรอบ
- Lifetime (duration_days=NULL) ห้ามแตะ
- บันทึก log ทุก action
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from sqlalchemy import and_, func, or_, select
from telegram import Bot
from telegram.error import BadRequest, Forbidden

from shared.database import get_session
from shared.models import (
    AdminLog,
    ExpiryNotification,
    GroupRegistry,
    NotificationType,
    Package,
    Payment,
    PaymentStatus,
    Subscription,
    SubscriptionStatus,
    User,
)
from shared.utils import format_datetime_thai, format_thb, log_admin_action

from bots.guardian_bot.group_monitor import check_and_kick_unauthorized

logger = logging.getLogger(__name__)

DISCORD_WEBHOOK_URL: str = os.environ.get("DISCORD_WEBHOOK_URL", "")
GUARDIAN_BOT_ID = 0  # System/bot admin ID for logging


async def _notify_discord(content: str) -> None:
    """Send notification to Discord."""
    if not DISCORD_WEBHOOK_URL:
        return
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(DISCORD_WEBHOOK_URL, json={"content": content})
    except Exception as exc:
        logger.error("Discord notification failed: %s", exc)


async def kick_expired_members(bot: Bot) -> dict[str, int]:
    """Kick members whose subscriptions have expired.

    Runs every 6 hours. Skips Lifetime subscriptions (duration_days=NULL → end_date far future).

    Returns dict with counts: checked, kicked, errors.
    """
    now = datetime.now(timezone.utc)
    stats = {"checked": 0, "kicked": 0, "errors": 0, "skipped_lifetime": 0}

    async with get_session() as session:
        # Find expired active subscriptions
        expired_result = await session.execute(
            select(Subscription, User, Package)
            .join(User, Subscription.user_id == User.id)
            .join(Package, Subscription.package_id == Package.id)
            .where(
                Subscription.status == SubscriptionStatus.ACTIVE,
                Subscription.end_date <= now,
            )
        )
        expired_rows = expired_result.all()

        # Get all active groups
        groups_result = await session.execute(
            select(GroupRegistry).where(GroupRegistry.is_active == True)  # noqa: E712
        )
        groups = {g.slug: g for g in groups_result.scalars().all()}

    for sub, user, package in expired_rows:
        stats["checked"] += 1

        # Skip lifetime subscriptions (duration_days is NULL or very large)
        if package.duration_days is None:
            stats["skipped_lifetime"] += 1
            continue

        # Mark subscription as expired
        async with get_session() as session:
            result = await session.execute(
                select(Subscription).where(Subscription.id == sub.id)
            )
            db_sub = result.scalar_one()
            db_sub.status = SubscriptionStatus.EXPIRED

        # Kick from all groups this package grants access to
        group_slugs = package.group_list
        for slug in group_slugs:
            group = groups.get(slug)
            if not group:
                continue

            try:
                await bot.ban_chat_member(
                    chat_id=group.chat_id,
                    user_id=user.telegram_id,
                )
                # Immediately unban so they can rejoin if they renew
                await bot.unban_chat_member(
                    chat_id=group.chat_id,
                    user_id=user.telegram_id,
                    only_if_banned=True,
                )
                stats["kicked"] += 1

                await log_admin_action(
                    admin_id=GUARDIAN_BOT_ID,
                    action="kick_expired",
                    target_type="user",
                    target_id=user.id,
                    details=f"tg={user.telegram_id} group={slug} sub_id={sub.id} expired={sub.end_date.isoformat()}",
                )

                logger.info(
                    "Kicked expired user %s from group %s (sub %d)",
                    user.telegram_id,
                    slug,
                    sub.id,
                )

            except Forbidden:
                logger.warning(
                    "No permission to kick %s from group %s",
                    user.telegram_id,
                    slug,
                )
                stats["errors"] += 1
            except BadRequest as e:
                if "user not found" in str(e).lower():
                    logger.info("User %s already not in group %s", user.telegram_id, slug)
                else:
                    logger.error("Error kicking %s from %s: %s", user.telegram_id, slug, e)
                    stats["errors"] += 1
            except Exception as exc:
                logger.error("Unexpected error kicking %s: %s", user.telegram_id, exc)
                stats["errors"] += 1

        # Notify user
        try:
            await bot.send_message(
                chat_id=user.telegram_id,
                text=(
                    "⏰ แพ็กเกจของคุณหมดอายุแล้วครับ\n\n"
                    f"📦 แพ็กเกจ: {package.name}\n"
                    f"📅 หมดอายุ: {format_datetime_thai(sub.end_date)}\n\n"
                    "หากต้องการต่ออายุ สามารถสมัครใหม่ได้ที่ @CharoenponBot ครับ"
                ),
                parse_mode="HTML",
            )
        except Exception:
            pass  # User may have blocked bot

    logger.info(
        "Kick expired: checked=%d kicked=%d lifetime=%d errors=%d",
        stats["checked"],
        stats["kicked"],
        stats["skipped_lifetime"],
        stats["errors"],
    )

    if stats["kicked"] > 0:
        await _notify_discord(
            f"🔒 **Guardian: Kicked Expired Members**\n"
            f"Checked: {stats['checked']}\n"
            f"Kicked: {stats['kicked']}\n"
            f"Lifetime (skipped): {stats['skipped_lifetime']}\n"
            f"Errors: {stats['errors']}"
        )

    return stats


async def send_expiring_list(bot: Bot) -> dict[str, Any]:
    """Send list of users with expiring subscriptions to Discord/admin.

    Runs daily at 09:00.
    Tiers: 1 day, 3 days, 7 days.
    Each tier notification is sent only once per subscription per cycle.

    Returns summary dict.
    """
    now = datetime.now(timezone.utc)
    summary: dict[str, Any] = {"1d": [], "3d": [], "7d": []}

    tiers = [
        (1, NotificationType.PRE_EXPIRY_1D, "1d"),
        (3, NotificationType.PRE_EXPIRY_3D, "3d"),
        (7, NotificationType.RENEWAL_REMINDER, "7d"),
    ]

    for days, notif_type, key in tiers:
        cutoff = now + timedelta(days=days)

        async with get_session() as session:
            # Find active subs expiring within this tier
            result = await session.execute(
                select(Subscription, User, Package)
                .join(User, Subscription.user_id == User.id)
                .join(Package, Subscription.package_id == Package.id)
                .where(
                    Subscription.status == SubscriptionStatus.ACTIVE,
                    Subscription.end_date >= now,
                    Subscription.end_date <= cutoff,
                )
                .order_by(Subscription.end_date.asc())
            )
            rows = result.all()

            for sub, user, package in rows:
                # Skip lifetime
                if package.duration_days is None:
                    continue

                # Check if already notified for this tier
                existing = await session.execute(
                    select(ExpiryNotification).where(
                        ExpiryNotification.user_id == user.id,
                        ExpiryNotification.subscription_id == sub.id,
                        ExpiryNotification.notification_type == notif_type,
                    )
                )
                if existing.scalar_one_or_none():
                    continue  # Already sent for this tier

                days_left = (sub.end_date - now).total_seconds() / 86400

                summary[key].append({
                    "user_id": user.id,
                    "telegram_id": user.telegram_id,
                    "username": user.username,
                    "package": package.name,
                    "end_date": sub.end_date,
                    "days_left": round(days_left, 1),
                    "subscription_id": sub.id,
                })

                # Record notification
                notif = ExpiryNotification(
                    user_id=user.id,
                    subscription_id=sub.id,
                    notification_type=notif_type,
                )
                session.add(notif)

    # Build Discord report
    lines = [f"📋 **Guardian: Expiry Report ({now.strftime('%Y-%m-%d')})**\n"]

    for key, label in [("1d", "⚠️ หมดอายุใน 1 วัน"), ("3d", "📢 หมดอายุใน 3 วัน"), ("7d", "📝 หมดอายุใน 7 วัน")]:
        users = summary[key]
        lines.append(f"\n**{label}** ({len(users)} คน)")
        if not users:
            lines.append("  (ไม่มี)")
        for u in users[:20]:  # Limit to 20 per tier in Discord
            name = f"@{u['username']}" if u["username"] else str(u["telegram_id"])
            lines.append(
                f"  • {name} — {u['package']} "
                f"(หมด {format_datetime_thai(u['end_date'])})"
            )
        if len(users) > 20:
            lines.append(f"  ... และอีก {len(users) - 20} คน")

    await _notify_discord("\n".join(lines))

    await log_admin_action(
        admin_id=GUARDIAN_BOT_ID,
        action="send_expiring_list",
        details=f"1d={len(summary['1d'])} 3d={len(summary['3d'])} 7d={len(summary['7d'])}",
    )

    logger.info(
        "Expiry report sent: 1d=%d, 3d=%d, 7d=%d",
        len(summary["1d"]),
        len(summary["3d"]),
        len(summary["7d"]),
    )

    return summary


async def check_unauthorized_members(bot: Bot) -> dict[str, int]:
    """Check all groups for unauthorized members. Runs every 30 minutes.

    Delegates to group_monitor.check_and_kick_unauthorized.
    """
    return await check_and_kick_unauthorized(bot)


async def generate_daily_report(bot: Bot) -> str:
    """Generate and send daily report at 22:00.

    Includes: new users, payments, kicks, subscription stats.
    """
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    async with get_session() as session:
        # New users today
        new_users_result = await session.execute(
            select(func.count(User.id)).where(User.created_at >= today_start)
        )
        new_users = new_users_result.scalar() or 0

        # Payments today
        payments_result = await session.execute(
            select(
                func.count(Payment.id).label("total"),
                func.coalesce(func.sum(Payment.amount), 0).label("total_amount"),
            )
            .where(
                Payment.created_at >= today_start,
                Payment.status == PaymentStatus.CONFIRMED,
            )
        )
        payment_row = payments_result.one()
        total_payments = payment_row.total
        total_revenue = payment_row.total_amount

        # Rejected payments
        rejected_result = await session.execute(
            select(func.count(Payment.id)).where(
                Payment.created_at >= today_start,
                Payment.status == PaymentStatus.REJECTED,
            )
        )
        rejected_payments = rejected_result.scalar() or 0

        # Active subscriptions
        active_subs_result = await session.execute(
            select(func.count(Subscription.id)).where(
                Subscription.status == SubscriptionStatus.ACTIVE,
            )
        )
        active_subs = active_subs_result.scalar() or 0

        # Kicks today
        kicks_result = await session.execute(
            select(func.count(AdminLog.id)).where(
                AdminLog.created_at >= today_start,
                AdminLog.action.in_(["kick_expired", "kick_unauthorized"]),
            )
        )
        kicks_today = kicks_result.scalar() or 0

        # Expiring in next 3 days
        expiring_3d_result = await session.execute(
            select(func.count(Subscription.id)).where(
                Subscription.status == SubscriptionStatus.ACTIVE,
                Subscription.end_date >= now,
                Subscription.end_date <= now + timedelta(days=3),
            )
        )
        expiring_3d = expiring_3d_result.scalar() or 0

    report = (
        f"📊 **Daily Report — {now.strftime('%Y-%m-%d')}**\n\n"
        f"👥 สมาชิกใหม่: **{new_users}** คน\n"
        f"💰 รายได้วันนี้: **{format_thb(total_revenue)}** ({total_payments} รายการ)\n"
        f"❌ สลิปถูกปฏิเสธ: **{rejected_payments}** รายการ\n"
        f"📦 Subscription ที่ Active: **{active_subs}**\n"
        f"🔒 เตะออกวันนี้: **{kicks_today}** คน\n"
        f"⏳ หมดอายุใน 3 วัน: **{expiring_3d}** คน\n"
    )

    await _notify_discord(report)

    await log_admin_action(
        admin_id=GUARDIAN_BOT_ID,
        action="daily_report",
        details=f"new={new_users} revenue={total_revenue} kicks={kicks_today}",
    )

    logger.info("Daily report generated and sent to Discord")
    return report
