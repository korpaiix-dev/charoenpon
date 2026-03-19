"""Report handlers - รายงาน revenue, members, costs สำหรับแอดมิน."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import func, select
from telegram import Update
from telegram.ext import ContextTypes

from shared.database import get_session
from shared.models import (
    ApiCostLog,
    Payment,
    PaymentStatus,
    Subscription,
    SubscriptionStatus,
    TeaserClick,
    User,
)
from shared.utils import format_thb
from shared.api_cost_tracker import daily_summary, format_daily_summary_discord

logger = logging.getLogger(__name__)


def _admin_ids() -> list[int]:
    return [
        int(x.strip())
        for x in os.environ.get("ADMIN_TELEGRAM_IDS", "").split(",")
        if x.strip()
    ]


def _is_admin(user_id: int) -> bool:
    return user_id in _admin_ids()


async def cmd_revenue(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """รายงานรายได้วันนี้และเดือนนี้."""
    if not update.effective_user or not _is_admin(update.effective_user.id):
        await update.effective_message.reply_text("⛔ คุณไม่มีสิทธิ์ใช้งาน")
        return

    now = datetime.utcnow()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    async with get_session() as session:
        # Revenue today
        today_q = await session.execute(
            select(
                func.count(Payment.id).label("count"),
                func.coalesce(func.sum(Payment.amount), 0).label("total"),
            ).where(
                Payment.status == PaymentStatus.CONFIRMED,
                Payment.verified_at >= today_start,
            )
        )
        today_row = today_q.one()

        # Revenue this month
        month_q = await session.execute(
            select(
                func.count(Payment.id).label("count"),
                func.coalesce(func.sum(Payment.amount), 0).label("total"),
            ).where(
                Payment.status == PaymentStatus.CONFIRMED,
                Payment.verified_at >= month_start,
            )
        )
        month_row = month_q.one()

        # Pending payments
        pending_q = await session.execute(
            select(
                func.count(Payment.id).label("count"),
                func.coalesce(func.sum(Payment.amount), 0).label("total"),
            ).where(Payment.status == PaymentStatus.PENDING)
        )
        pending_row = pending_q.one()

    text = (
        "💰 <b>รายงานรายได้</b>\n\n"
        f"📅 <b>วันนี้:</b>\n"
        f"   รายการ: {today_row.count}\n"
        f"   ยอด: {format_thb(today_row.total)}\n\n"
        f"📆 <b>เดือนนี้:</b>\n"
        f"   รายการ: {month_row.count}\n"
        f"   ยอด: {format_thb(month_row.total)}\n\n"
        f"⏳ <b>รออนุมัติ:</b>\n"
        f"   รายการ: {pending_row.count}\n"
        f"   ยอด: {format_thb(pending_row.total)}"
    )

    await update.effective_message.reply_text(text, parse_mode="HTML")

    logger.info(
        "[%s] [ADMIN_BOT] [REVENUE_REPORT] [%s] [today=%s month=%s]",
        datetime.utcnow().isoformat(),
        update.effective_user.id,
        today_row.total,
        month_row.total,
    )


async def cmd_members(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """รายงานจำนวนสมาชิก active."""
    if not update.effective_user or not _is_admin(update.effective_user.id):
        await update.effective_message.reply_text("⛔ คุณไม่มีสิทธิ์ใช้งาน")
        return

    now = datetime.utcnow()

    async with get_session() as session:
        # Total users
        total_users_q = await session.execute(
            select(func.count(User.id))
        )
        total_users = total_users_q.scalar()

        # Active subscriptions
        active_subs_q = await session.execute(
            select(func.count(Subscription.id)).where(
                Subscription.status == SubscriptionStatus.ACTIVE,
                Subscription.end_date > now,
            )
        )
        active_subs = active_subs_q.scalar()

        # Expired subscriptions
        expired_subs_q = await session.execute(
            select(func.count(Subscription.id)).where(
                Subscription.status == SubscriptionStatus.EXPIRED,
            )
        )
        expired_subs = expired_subs_q.scalar()

        # Expiring in 3 days
        cutoff_3d = now + timedelta(days=3)
        expiring_q = await session.execute(
            select(func.count(Subscription.id)).where(
                Subscription.status == SubscriptionStatus.ACTIVE,
                Subscription.end_date >= now,
                Subscription.end_date <= cutoff_3d,
            )
        )
        expiring_3d = expiring_q.scalar()

        # New members today
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        new_today_q = await session.execute(
            select(func.count(User.id)).where(User.created_at >= today_start)
        )
        new_today = new_today_q.scalar()

    text = (
        "👥 <b>รายงานสมาชิก</b>\n\n"
        f"📊 สมาชิกทั้งหมด: <b>{total_users:,}</b>\n"
        f"✅ Active subscriptions: <b>{active_subs:,}</b>\n"
        f"❌ Expired: <b>{expired_subs:,}</b>\n"
        f"⚠️ หมดอายุใน 3 วัน: <b>{expiring_3d:,}</b>\n"
        f"🆕 สมาชิกใหม่วันนี้: <b>{new_today:,}</b>"
    )

    await update.effective_message.reply_text(text, parse_mode="HTML")

    logger.info(
        "[%s] [ADMIN_BOT] [MEMBERS_REPORT] [%s] [active=%d total=%d]",
        datetime.utcnow().isoformat(),
        update.effective_user.id,
        active_subs,
        total_users,
    )


async def cmd_costs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """รายงานค่า API วันนี้."""
    if not update.effective_user or not _is_admin(update.effective_user.id):
        await update.effective_message.reply_text("⛔ คุณไม่มีสิทธิ์ใช้งาน")
        return

    summary = await daily_summary()

    lines = [
        "🤖 <b>ค่า API วันนี้</b>\n",
        f"💰 Total: <b>${summary['total_usd']:.4f}</b> (฿{summary['total_thb']:.2f})",
        f"📞 Calls: <b>{summary['total_calls']}</b>",
        f"🔤 Tokens: {summary['total_prompt_tokens']:,} in / {summary['total_completion_tokens']:,} out",
        "",
        "<b>แยกตาม Model:</b>",
    ]
    for m in summary["by_model"]:
        lines.append(
            f"• <code>{m['model']}</code>: {m['calls']} calls — "
            f"${m['cost_usd']:.4f} (฿{m['cost_thb']:.2f})"
        )

    if not summary["by_model"]:
        lines.append("  (ยังไม่มีการใช้งานวันนี้)")

    await update.effective_message.reply_text("\n".join(lines), parse_mode="HTML")

    logger.info(
        "[%s] [ADMIN_BOT] [COSTS_REPORT] [%s] [total_usd=%s calls=%d]",
        datetime.utcnow().isoformat(),
        update.effective_user.id,
        summary["total_usd"],
        summary["total_calls"],
    )


async def cmd_teaser(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/teaser [week|best] — สถิติ teaser clicks."""
    if not update.effective_user or not _is_admin(update.effective_user.id):
        await update.effective_message.reply_text("⛔ คุณไม่มีสิทธิ์ใช้งาน")
        return

    args = context.args or []
    mode = args[0].lower() if args else "today"

    from sqlalchemy import text

    now = datetime.utcnow()

    if mode == "week":
        since = now - timedelta(days=7)
        period_label = "7 วันที่ผ่านมา"
    else:
        since = now.replace(hour=0, minute=0, second=0, microsecond=0)
        period_label = "วันนี้"

    async with get_session() as session:
        # Totals
        total_q = await session.execute(
            text(
                "SELECT COUNT(*) as clicks, SUM(CASE WHEN converted THEN 1 ELSE 0 END) as conversions "
                "FROM teaser_clicks WHERE created_at >= :since"
            ),
            {"since": since.replace(tzinfo=None)},
        )
        totals = total_q.fetchone()

        if mode == "best":
            # Best round
            round_q = await session.execute(
                text(
                    "SELECT round_time, COUNT(*) as clicks, "
                    "SUM(CASE WHEN converted THEN 1 ELSE 0 END) as conversions "
                    "FROM teaser_clicks WHERE created_at >= :since "
                    "GROUP BY round_time ORDER BY clicks DESC LIMIT 3"
                ),
                {"since": since.replace(tzinfo=None)},
            )
            best_rounds = round_q.fetchall()

            # Best group
            group_q = await session.execute(
                text(
                    "SELECT group_index, COUNT(*) as clicks, "
                    "SUM(CASE WHEN converted THEN 1 ELSE 0 END) as conversions "
                    "FROM teaser_clicks WHERE created_at >= :since "
                    "GROUP BY group_index ORDER BY clicks DESC LIMIT 3"
                ),
                {"since": since.replace(tzinfo=None)},
            )
            best_groups = group_q.fetchall()
        else:
            # By round for today/week
            round_q = await session.execute(
                text(
                    "SELECT round_time, COUNT(*) as clicks, "
                    "SUM(CASE WHEN converted THEN 1 ELSE 0 END) as conversions "
                    "FROM teaser_clicks WHERE created_at >= :since "
                    "GROUP BY round_time ORDER BY round_time"
                ),
                {"since": since.replace(tzinfo=None)},
            )
            best_rounds = round_q.fetchall()
            best_groups = []

    total_clicks = totals.clicks or 0
    total_convs = totals.conversions or 0
    overall_cvr = (total_convs / total_clicks * 100) if total_clicks > 0 else 0.0

    lines = [
        f"📊 <b>Teaser Stats — {period_label}</b>\n",
        f"🔢 Clicks: <b>{total_clicks}</b> | Conversions: <b>{total_convs}</b> | CVR: <b>{overall_cvr:.1f}%</b>",
    ]

    if mode == "best":
        lines.append("\n<b>🏆 รอบที่ดีที่สุด:</b>")
        for r in best_rounds:
            cvr = (r.conversions / r.clicks * 100) if r.clicks else 0.0
            lines.append(f"  • รอบ {r.round_time}: {r.clicks} คลิก ({cvr:.1f}%)")
        lines.append("\n<b>🏆 กลุ่มที่ดีที่สุด:</b>")
        for g in best_groups:
            cvr = (g.conversions / g.clicks * 100) if g.clicks else 0.0
            lines.append(f"  • กลุ่ม #{g.group_index}: {g.clicks} คลิก ({cvr:.1f}%)")
    else:
        lines.append("\n<b>⏰ แยกตามรอบ:</b>")
        for r in best_rounds:
            cvr = (r.conversions / r.clicks * 100) if r.clicks else 0.0
            lines.append(f"  • รอบ {r.round_time}: {r.clicks} คลิก → {r.conversions} สมัคร ({cvr:.1f}%)")
        if not best_rounds:
            lines.append("  (ยังไม่มีข้อมูล)")

    await update.effective_message.reply_text("\n".join(lines), parse_mode="HTML")

    logger.info(
        "[%s] [ADMIN_BOT] [TEASER_REPORT] [%s] [mode=%s total_clicks=%d]",
        datetime.utcnow().isoformat(),
        update.effective_user.id,
        mode,
        total_clicks,
    )


async def cmd_summary(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """สรุปภาพรวมทั้งหมด — revenue + members + costs."""
    if not update.effective_user or not _is_admin(update.effective_user.id):
        await update.effective_message.reply_text("⛔ คุณไม่มีสิทธิ์ใช้งาน")
        return

    now = datetime.utcnow()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    async with get_session() as session:
        # Revenue today
        rev_today_q = await session.execute(
            select(
                func.count(Payment.id).label("count"),
                func.coalesce(func.sum(Payment.amount), 0).label("total"),
            ).where(
                Payment.status == PaymentStatus.CONFIRMED,
                Payment.verified_at >= today_start,
            )
        )
        rev_today = rev_today_q.one()

        # Revenue month
        rev_month_q = await session.execute(
            select(
                func.coalesce(func.sum(Payment.amount), 0).label("total"),
            ).where(
                Payment.status == PaymentStatus.CONFIRMED,
                Payment.verified_at >= month_start,
            )
        )
        rev_month = rev_month_q.scalar()

        # Active members
        active_q = await session.execute(
            select(func.count(Subscription.id)).where(
                Subscription.status == SubscriptionStatus.ACTIVE,
                Subscription.end_date > now,
            )
        )
        active_count = active_q.scalar()

        # Pending payments
        pending_q = await session.execute(
            select(func.count(Payment.id)).where(
                Payment.status == PaymentStatus.PENDING,
            )
        )
        pending_count = pending_q.scalar()

        # Expiring in 3 days
        cutoff = now + timedelta(days=3)
        expiring_q = await session.execute(
            select(func.count(Subscription.id)).where(
                Subscription.status == SubscriptionStatus.ACTIVE,
                Subscription.end_date >= now,
                Subscription.end_date <= cutoff,
            )
        )
        expiring_count = expiring_q.scalar()

    # API costs
    cost_summary = await daily_summary()

    text = (
        "🏢 <b>สรุปภาพรวม — บริษัทเจริญพร</b>\n\n"
        f"💰 <b>รายได้วันนี้:</b> {format_thb(rev_today.total)} ({rev_today.count} รายการ)\n"
        f"📆 <b>รายได้เดือนนี้:</b> {format_thb(rev_month)}\n\n"
        f"👥 <b>Active Members:</b> {active_count:,}\n"
        f"⚠️ <b>หมดอายุใน 3 วัน:</b> {expiring_count:,}\n"
        f"⏳ <b>Payment รออนุมัติ:</b> {pending_count:,}\n\n"
        f"🤖 <b>ค่า API วันนี้:</b>\n"
        f"   ${cost_summary['total_usd']:.4f} (฿{cost_summary['total_thb']:.2f})\n"
        f"   {cost_summary['total_calls']} calls"
    )

    await update.effective_message.reply_text(text, parse_mode="HTML")

    logger.info(
        "[%s] [ADMIN_BOT] [SUMMARY_REPORT] [%s] [revenue_today=%s active=%d]",
        datetime.utcnow().isoformat(),
        update.effective_user.id,
        rev_today.total,
        active_count,
    )
