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

from bots.guardian_bot.group_monitor import check_and_kick_unauthorized, _log_kick_action

logger = logging.getLogger(__name__)

GUARDIAN_BOT_ID = 0  # System/bot admin ID for logging


async def _notify_discord(content: str) -> None:
    """[DEPRECATED — use shared.notify.notify(event_key, ...) instead]
    Delegates to shared.discord_alert for now to keep callers working."""
    from shared.discord_alert import notify_discord as _hub_notify
    try:
        title = locals().get("title") or locals().get("event") or "Notification"
        desc  = locals().get("description") or locals().get("body") or locals().get("msg") or ""
        if not isinstance(title, str): title = str(title)
        if not isinstance(desc, str): desc = str(desc)
        return await _hub_notify("members", title, desc, silent_on_error=True)
    except Exception:
        return False

async def kick_expired_members(bot: Bot) -> dict[str, int]:
    """Kick members whose subscriptions have expired.

    Runs every 6 hours. Skips Lifetime subscriptions (duration_days=NULL → end_date far future).

    Returns dict with counts: checked, kicked, errors.
    """
    now = datetime.utcnow()
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

        # Skip lifetime subscriptions (canonical test)
        from shared.subscription_access import is_lifetime_sub
        if is_lifetime_sub(sub, package):
            stats["skipped_lifetime"] += 1
            continue

        # Mark subscription as expired
        async with get_session() as session:
            result = await session.execute(
                select(Subscription).where(Subscription.id == sub.id)
            )
            db_sub = result.scalar_one()
            db_sub.status = SubscriptionStatus.EXPIRED

        # FIX 2026-07-05 (CRITICAL): do NOT kick from groups the user still has access to
        # via ANOTHER active subscription (e.g. GOD MODE ถาวร lifetime). A user can hold several
        # subs; when a shorter one expires we must only remove groups no active sub still covers.
        from shared.subscription_access import user_active_group_slugs
        covered_groups = await user_active_group_slugs(user.id, exclude_sub_id=sub.id)

        # Kick only from groups NOT still covered by another active subscription
        group_slugs = [s for s in (package.group_list or []) if s not in covered_groups]
        if not group_slugs:
            logger.info(
                "User %s: sub %d expired but ALL its groups still covered by another active sub — skip kick/notify",
                user.telegram_id, sub.id,
            )
            continue
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

        # Notify user — via the SAFE sender (records is_blocked_bot, backoff, skips blocked).
        # NEVER build a raw Bot here: this loop runs every 6h over all expired users and would
        # re-DM blocked users forever with the SALES token -> Telegram ban risk.
        try:
            from shared.customer_dm import send_to_customer
            await send_to_customer(
                telegram_id=user.telegram_id,
                text=(
                    "⏰ แพ็กเกจของคุณหมดอายุแล้วครับ\n\n"
                    f"📦 แพ็กเกจ: {package.name}\n"
                    f"📅 หมดอายุ: {format_datetime_thai(sub.end_date)}\n\n"
                    "หากต้องการต่ออายุ สามารถสมัครใหม่ได้ที่ @NamwarnJarern_bot ครับ"
                ),
                alert_on_fail=False,
            )
        except Exception:
            pass

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
    now = datetime.utcnow()
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
                # Skip lifetime (canonical test — end_date year>=2099 OR duration_days>=3650)
                from shared.subscription_access import is_lifetime_sub
                if is_lifetime_sub(sub, package):
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


async def check_unauthorized_members(bot: Bot, job_queue=None) -> dict[str, int]:
    """Check all groups for unauthorized members. Runs every 30 minutes.

    Delegates to group_monitor.check_and_kick_unauthorized.
    """
    return await check_and_kick_unauthorized(bot, job_queue=job_queue)


async def generate_daily_report(bot: Bot) -> str:
    """Generate and send daily report at 22:00.

    Includes: new users, payments, kicks, subscription stats.
    """
    now = datetime.utcnow()
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
                Payment.amount > 0,
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



# ─────────────────────────────────────────────────────────────────────────
# Marketing Daily Digest (post to #marketing-รวม at 09:00 BKK)
# Added 2026-06-24
# ─────────────────────────────────────────────────────────────────────────
async def marketing_daily_digest() -> str:
    """Generate yesterday's marketing stats summary + post to Discord #marketing-รวม.
    
    Returns the summary text (also logged).
    """
    import logging as _lg
    _logger = _lg.getLogger(__name__)
    
    try:
        from shared.database import get_session as _gs
        from shared.discord_notify import notify_overview
        from sqlalchemy import text as _t
        
        async with _gs() as s:
            # Yesterday's join count + paid users per marketer/platform (BKK timezone)
            rows = (await s.execute(_t("""
                WITH y AS (
                  SELECT
                    l.marketer, l.platform,
                    j.telegram_id, j.joined_at
                  FROM marketing_invite_links l
                  JOIN marketing_invite_joins j ON j.link_id = l.id
                  WHERE (j.joined_at AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Bangkok')::date
                        = (now() AT TIME ZONE 'Asia/Bangkok')::date - 1
                ),
                paid AS (
                  SELECT y.marketer, y.platform, y.telegram_id,
                         (SELECT MIN(p.created_at) FROM payments p
                          JOIN users u ON u.id = p.user_id
                          WHERE u.telegram_id = y.telegram_id
                            AND p.status = 'CONFIRMED'
                            AND p.amount > 0
                            AND p.created_at >= y.joined_at
                            AND (p.created_at - y.joined_at) <= interval '30 days'
                         ) AS pay_at,
                         (SELECT COALESCE(SUM(p.amount),0) FROM payments p
                          JOIN users u ON u.id = p.user_id
                          WHERE u.telegram_id = y.telegram_id
                            AND p.status = 'CONFIRMED'
                            AND p.amount > 0
                            AND p.created_at >= y.joined_at
                            AND (p.created_at - y.joined_at) <= interval '30 days'
                         ) AS paid_amt
                  FROM y
                )
                SELECT marketer, platform,
                       COUNT(*)::int AS joins,
                       COUNT(*) FILTER (WHERE pay_at IS NOT NULL)::int AS paid_count,
                       COALESCE(SUM(paid_amt), 0)::int AS revenue
                FROM paid
                GROUP BY marketer, platform
                ORDER BY revenue DESC, joins DESC
            """))).fetchall()
            
            # Also conversion from joins in past 30d that paid yesterday (catches delayed conversions)
            delayed_rows = (await s.execute(_t("""
                SELECT
                  l.marketer, l.platform,
                  COUNT(DISTINCT p.id)::int AS conv_count,
                  COALESCE(SUM(p.amount), 0)::int AS conv_revenue
                FROM payments p
                JOIN users u ON u.id = p.user_id
                JOIN marketing_invite_joins j ON j.telegram_id = u.telegram_id
                JOIN marketing_invite_links l ON l.id = j.link_id
                WHERE p.status = 'CONFIRMED'
                  AND p.amount > 0
                  AND u.telegram_id < 9000000000
                  AND (p.created_at AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Bangkok')::date
                      = (now() AT TIME ZONE 'Asia/Bangkok')::date - 1
                  AND p.created_at >= j.joined_at
                  AND (p.created_at - j.joined_at) <= interval '30 days'
                GROUP BY l.marketer, l.platform
                ORDER BY conv_revenue DESC
            """))).fetchall()

        # Format
        from datetime import datetime, timezone, timedelta
        bkk = timezone(timedelta(hours=7))
        yesterday = (datetime.now(bkk) - timedelta(days=1)).strftime("%-d %b %Y")
        
        lines = [f"📊 **สรุปเมื่อวาน ({yesterday})**", "─" * 30, ""]
        
        if rows:
            total_j = sum(int(r.joins) for r in rows)
            total_p = sum(int(r.paid_count) for r in rows)
            total_r = sum(int(r.revenue) for r in rows)
            lines.append(f"👥 **คนเข้ามาใหม่:** {total_j} คน")
            lines.append(f"💰 **จ่ายเงินใน 30 วัน:** {total_p} คน (฿{total_r:,})")
            lines.append("")
            lines.append("**แยกตามทีม/ช่อง:**")
            for r in rows[:10]:
                lines.append(f"• {r.marketer}/{r.platform}: {r.joins} joins → {r.paid_count} paid (฿{r.revenue:,})")
        else:
            lines.append("👥 เมื่อวานไม่มีคนเข้าผ่านลิ้ง marketing เลย")
            lines.append("")

        # Delayed conversions
        if delayed_rows:
            d_total_c = sum(int(r.conv_count) for r in delayed_rows)
            d_total_r = sum(int(r.conv_revenue) for r in delayed_rows)
            if d_total_c > 0:
                lines.append("")
                lines.append(f"🔥 **Conversions เมื่อวาน:** {d_total_c} คน (฿{d_total_r:,})")
                for r in delayed_rows[:5]:
                    lines.append(f"  • {r.marketer}/{r.platform}: {r.conv_count} conv → ฿{r.conv_revenue:,}")

        summary = "\n".join(lines)
        await notify_overview(summary)
        _logger.info("marketing_daily_digest posted: %d marketers reported", len(rows))
        return summary
    except Exception as exc:
        _logger.exception("marketing_daily_digest failed: %s", exc)
        return f"ERROR: {exc}"



async def marketing_monthly_leaderboard() -> str:
    """Post LAST month's leaderboard to #marketing-รวม.
    
    Runs 1st of each month at 09:30 BKK (after daily digest at 09:00).
    """
    import logging as _lg
    _logger = _lg.getLogger(__name__)
    
    try:
        from shared.database import get_session as _gs
        from shared.discord_notify import notify_overview
        from sqlalchemy import text as _t
        
        async with _gs() as s:
            # Last completed month — joins + conversions + revenue per marketer
            rows = (await s.execute(_t("""
                WITH last_month_joins AS (
                  SELECT l.marketer, j.telegram_id, j.joined_at, j.link_id, l.platform
                  FROM marketing_invite_links l
                  JOIN marketing_invite_joins j ON j.link_id = l.id
                  WHERE date_trunc('month', (j.joined_at AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Bangkok'))
                        = date_trunc('month', (now() AT TIME ZONE 'Asia/Bangkok')) - interval '1 month'
                ),
                with_paid AS (
                  SELECT mj.marketer, mj.telegram_id, mj.platform,
                         (SELECT MIN(p.created_at) FROM payments p
                          JOIN users u ON u.id = p.user_id
                          WHERE u.telegram_id = mj.telegram_id
                            AND p.status = 'CONFIRMED' AND p.amount > 0
                            AND p.created_at >= mj.joined_at
                            AND (p.created_at - mj.joined_at) <= interval '30 days'
                         ) AS pay_at,
                         (SELECT COALESCE(SUM(p.amount), 0) FROM payments p
                          JOIN users u ON u.id = p.user_id
                          WHERE u.telegram_id = mj.telegram_id
                            AND p.status = 'CONFIRMED' AND p.amount > 0
                            AND p.created_at >= mj.joined_at
                            AND (p.created_at - mj.joined_at) <= interval '30 days'
                         ) AS revenue
                  FROM last_month_joins mj
                )
                SELECT marketer,
                       COUNT(*)::int AS joins,
                       COUNT(*) FILTER (WHERE pay_at IS NOT NULL)::int AS paid_count,
                       COALESCE(SUM(revenue), 0)::int AS revenue
                FROM with_paid
                GROUP BY marketer
                ORDER BY revenue DESC, paid_count DESC, joins DESC
            """))).fetchall()
        
        if not rows:
            await notify_overview("🏆 **Leaderboard เดือนที่แล้ว** — ไม่มีข้อมูล (ทีมยังไม่ได้สร้างลิ้งเลย)")
            return "no data"
        
        from datetime import datetime, timezone, timedelta
        bkk = timezone(timedelta(hours=7))
        last_month_dt = datetime.now(bkk).replace(day=1) - timedelta(days=1)
        month_label = last_month_dt.strftime("%B %Y")  # e.g. "May 2026"
        
        medals = ["🥇", "🥈", "🥉"]
        lines = [
            f"🏆 **LEADERBOARD เดือน {month_label}** 🏆",
            "─" * 30,
            "",
        ]
        for i, r in enumerate(rows):
            medal = medals[i] if i < 3 else f"{i+1}."
            cvr = (r.paid_count / r.joins * 100) if r.joins > 0 else 0
            lines.append(f"{medal} **{r.marketer}** — ฿{r.revenue:,}")
            lines.append(f"   ↳ {r.joins} joins → {r.paid_count} paid ({cvr:.1f}%)")
            lines.append("")
        
        total_rev = sum(int(r.revenue) for r in rows)
        total_joins = sum(int(r.joins) for r in rows)
        total_paid = sum(int(r.paid_count) for r in rows)
        lines.append("─" * 30)
        lines.append(f"💰 **รวมทั้งทีม:** ฿{total_rev:,} ({total_paid}/{total_joins} = {(total_paid/total_joins*100 if total_joins else 0):.1f}%)")
        if rows:
            lines.append(f"\n🏆 ยินดีกับ **{rows[0].marketer}** ที่เป็นแชมป์เดือนนี้! 🎉")
        
        summary = "\n".join(lines)
        await notify_overview(summary)
        _logger.info("monthly leaderboard posted: %d marketers", len(rows))
        return summary
    except Exception as exc:
        _logger.exception("monthly_leaderboard failed: %s", exc)
        return f"ERROR: {exc}"



async def marketing_stale_link_check() -> str:
    """Check for marketing links >30d old with 0 joins → post warning in marketer's channel.
    
    Runs daily 10:00 BKK.
    Posts in #ivy / #wasu / #pai (not feed channel — boss wants chat channel).
    """
    import logging as _lg
    import os as _os
    _logger = _lg.getLogger(__name__)
    
    try:
        from shared.database import get_session as _gs
        from shared.discord_notify import post_to_channel
        from sqlalchemy import text as _t
        
        # Marketer → chat channel (not feed)
        chat_channels = {
            "Ivy": _os.environ.get("DISCORD_MARKETING_IVY_CHANNEL_ID", ""),
            "Wasu": _os.environ.get("DISCORD_MARKETING_WASU_CHANNEL_ID", ""),
            "Pai": _os.environ.get("DISCORD_MARKETING_PAI_CHANNEL_ID", ""),
        }
        
        async with _gs() as s:
            # Find stale links: created >30 days ago, is_revoked=false, 0 joins, not already warned
            rows = (await s.execute(_t("""
                SELECT l.id, l.marketer, l.platform, l.invite_link, l.created_at,
                       EXTRACT(DAY FROM (now() - l.created_at))::int AS age_days
                FROM marketing_invite_links l
                WHERE l.is_revoked = false
                  AND l.created_at < now() - interval '30 days'
                  AND NOT EXISTS (
                    SELECT 1 FROM marketing_invite_joins j WHERE j.link_id = l.id
                  )
                  AND NOT EXISTS (
                    SELECT 1 FROM admin_logs
                    WHERE action = 'marketing_stale_warned'
                      AND details LIKE '%link_id=' || l.id || '%'
                  )
                ORDER BY l.marketer, l.created_at
            """))).fetchall()
            
            if not rows:
                _logger.info("stale_link_check: no stale links")
                return "no stale links"
            
            # Group by marketer
            from collections import defaultdict
            by_marketer = defaultdict(list)
            for r in rows:
                by_marketer[r.marketer].append(r)
            
            posted = 0
            for marketer, links in by_marketer.items():
                ch = chat_channels.get(marketer)
                if not ch:
                    continue
                lines = [
                    f"👋 หวัดดี {marketer}! แพรเช็คให้แล้วนะ มีลิ้ง **{len(links)} อัน** ที่เก่ากว่า 30 วันแล้วยังไม่มีใครเข้าเลย:\n"
                ]
                for r in links[:10]:
                    lines.append(f"• ลิ้ง **#{r.id}** ({r.platform}) — สร้างมา {r.age_days} วันแล้ว")
                    lines.append(f"  └ {r.invite_link}")
                if len(links) > 10:
                    lines.append(f"... + อีก {len(links)-10} อัน")
                lines.append("\n💡 ถ้าไม่ใช้แล้วพิมพ์ **'revoke <link_id>'** เพื่อลบ (เช่น `revoke 5`)")
                lines.append("ถ้าจะเก็บไว้ก็ไม่เป็นไรนะคะ 💕")
                
                ok = await post_to_channel(ch, "\n".join(lines))
                if ok:
                    posted += 1
                    # Mark as warned (per-link, idempotent)
                    for r in links:
                        await s.execute(_t(
                            "INSERT INTO admin_logs (admin_id, action, details, created_at) "
                            "VALUES (0, :a, :d, now())"
                        ), {"a": "marketing_stale_warned", "d": f"link_id={r.id} marketer={marketer}"})
                    await s.commit()
            
            _logger.info("stale_link_check: warned %d marketers about %d stale links", posted, len(rows))
            return f"warned {posted} marketers, {len(rows)} links"
    except Exception as exc:
        _logger.exception("marketing_stale_link_check failed: %s", exc)
        return f"ERROR: {exc}"



# ─────────────────────────────────────────────────────────────────────────
# Team Discord daily/weekly posts — 2026-06-24
# ─────────────────────────────────────────────────────────────────────────
async def team_morning_briefing() -> str:
    """09:00 BKK — post to #รายงานประจำวัน."""
    import logging as _lg
    import os as _os
    _logger = _lg.getLogger(__name__)
    try:
        from shared.team_discord_features import morning_briefing
        from shared.discord_notify import post_to_channel
        text = await morning_briefing()
        ch = _os.environ.get("DISCORD_TEAM_REPORT_CHANNEL_ID", "")
        if ch:
            await post_to_channel(ch, text)
        _logger.info("morning_briefing posted")
        return text
    except Exception as exc:
        _logger.exception("morning_briefing failed: %s", exc)
        return f"ERROR: {exc}"


async def team_weekly_mvp() -> str:
    """Friday 18:00 BKK — post weekly wrap-up + MVP."""
    import logging as _lg
    import os as _os
    import datetime as _dt
    _logger = _lg.getLogger(__name__)
    try:
        # Guard: only Friday
        bkk = _dt.timezone(_dt.timedelta(hours=7))
        if _dt.datetime.now(bkk).weekday() != 4:  # Friday = 4
            return "skipped (not friday)"
        from shared.team_discord_features import weekly_mvp
        from shared.discord_notify import post_to_channel
        text = await weekly_mvp()
        ch = _os.environ.get("DISCORD_TEAM_REPORT_CHANNEL_ID", "")
        if ch:
            await post_to_channel(ch, text)
        _logger.info("weekly_mvp posted")
        return text
    except Exception as exc:
        _logger.exception("weekly_mvp failed: %s", exc)
        return f"ERROR: {exc}"


async def team_streak_ranking() -> str:
    """Monday 09:30 BKK — post weekly streak leaderboard."""
    import logging as _lg
    import os as _os
    import datetime as _dt
    _logger = _lg.getLogger(__name__)
    try:
        # Guard: only Monday
        bkk = _dt.timezone(_dt.timedelta(hours=7))
        if _dt.datetime.now(bkk).weekday() != 0:  # Monday = 0
            return "skipped (not monday)"
        from shared.team_discord_features import streak_ranking_text
        from shared.discord_notify import post_to_channel
        text = await streak_ranking_text()
        if not text:
            return "no data"
        ch = _os.environ.get("DISCORD_TEAM_REPORT_CHANNEL_ID", "")
        if ch:
            await post_to_channel(ch, text)
        _logger.info("streak_ranking posted")
        return text
    except Exception as exc:
        _logger.exception("streak_ranking failed: %s", exc)
        return f"ERROR: {exc}"
