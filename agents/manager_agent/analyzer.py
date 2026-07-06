"""Manager Analyzer — ดึงข้อมูลภาพรวมจาก DB สำหรับ daily/weekly reports."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select, cast, Date

from shared.database import get_session
from shared.models import (
    Broadcast,
    AdminLog,
    GroupMigration,
    Payment,
    PaymentStatus,
    Subscription,
    SubscriptionStatus,
    TeaserClick,
    User,
)

logger = logging.getLogger(__name__)

from shared.tz import TH_TZ, th_day_start_utc


async def get_daily_stats() -> dict[str, Any]:
    """ดึงข้อมูลภาพรวมประจำวัน."""
    today_start = th_day_start_utc()
    today_end = today_start + timedelta(days=1)
    expiry_7d = today_start + timedelta(days=7)

    async with get_session() as session:
        # --- Users ---
        total_users = (await session.execute(
            select(func.count(User.id))
        )).scalar() or 0

        # --- Subscriptions ---
        active_subs = (await session.execute(
            select(func.count(Subscription.id)).where(
                Subscription.status == SubscriptionStatus.ACTIVE,
            )
        )).scalar() or 0

        expiring_7d = (await session.execute(
            select(func.count(Subscription.id)).where(
                Subscription.status == SubscriptionStatus.ACTIVE,
                Subscription.end_date >= today_start,
                Subscription.end_date < expiry_7d,
            )
        )).scalar() or 0

        expired_today = (await session.execute(
            select(func.count(Subscription.id)).where(
                Subscription.status == SubscriptionStatus.EXPIRED,
                Subscription.end_date >= today_start,
                Subscription.end_date < today_end,
            )
        )).scalar() or 0

        # --- Payments ---
        revenue_today = float((await session.execute(
            select(func.coalesce(func.sum(Payment.amount), 0)).where(
                Payment.status == PaymentStatus.CONFIRMED,
                Payment.created_at >= today_start,
                Payment.created_at < today_end,
            )
        )).scalar() or 0)

        month_start = today_start.replace(day=1)
        revenue_month = float((await session.execute(
            select(func.coalesce(func.sum(Payment.amount), 0)).where(
                Payment.status == PaymentStatus.CONFIRMED,
                Payment.created_at >= month_start,
                Payment.created_at < today_end,
            )
        )).scalar() or 0)

        pending_payments = (await session.execute(
            select(func.count(Payment.id)).where(
                Payment.status == PaymentStatus.PENDING,
            )
        )).scalar() or 0

        # --- Teaser Clicks ---
        clicks_today = (await session.execute(
            select(func.count(TeaserClick.id)).where(
                TeaserClick.created_at >= today_start,
                TeaserClick.created_at < today_end,
            )
        )).scalar() or 0

        # Top group
        top_group_row = (await session.execute(
            select(
                TeaserClick.group_index,
                func.count(TeaserClick.id).label("cnt"),
            ).where(
                TeaserClick.created_at >= today_start,
                TeaserClick.created_at < today_end,
            ).group_by(TeaserClick.group_index)
            .order_by(func.count(TeaserClick.id).desc())
            .limit(1)
        )).first()
        top_group = f"กลุ่ม #{top_group_row.group_index}" if top_group_row else "-"

        # Best time
        best_time_row = (await session.execute(
            select(
                TeaserClick.round_time,
                func.count(TeaserClick.id).label("cnt"),
            ).where(
                TeaserClick.created_at >= today_start,
                TeaserClick.created_at < today_end,
            ).group_by(TeaserClick.round_time)
            .order_by(func.count(TeaserClick.id).desc())
            .limit(1)
        )).first()
        best_time = best_time_row.round_time if best_time_row else "-"

        # --- Admin Logs (bans) ---
        bans_today = (await session.execute(
            select(func.count(AdminLog.id)).where(
                AdminLog.created_at >= today_start,
                AdminLog.created_at < today_end,
                AdminLog.action.ilike("%kick%") | AdminLog.action.ilike("%ban%"),
            )
        )).scalar() or 0

        # --- Group Migrations ---
        migrations_today = (await session.execute(
            select(func.count(GroupMigration.id)).where(
                GroupMigration.created_at >= today_start,
                GroupMigration.created_at < today_end,
            )
        )).scalar() or 0

        # --- Broadcasts ---
        broadcast_rows = (await session.execute(
            select(
                func.count(Broadcast.id).label("total"),
                func.coalesce(func.sum(Broadcast.success_count), 0).label("success"),
                func.coalesce(func.sum(Broadcast.failed_count), 0).label("failed"),
            ).where(
                Broadcast.started_at >= today_start,
                Broadcast.started_at < today_end,
            )
        )).first()
        broadcasts_today = broadcast_rows.total if broadcast_rows else 0
        broadcast_success = int(broadcast_rows.success) if broadcast_rows else 0
        broadcast_failed = int(broadcast_rows.failed) if broadcast_rows else 0

    return {
        "date": datetime.now(TH_TZ).strftime("%d/%m/%Y"),
        "total_users": total_users,
        "active_subs": active_subs,
        "expiring_7d": expiring_7d,
        "expired_today": expired_today,
        "revenue_today": revenue_today,
        "revenue_month": revenue_month,
        "pending_payments": pending_payments,
        "clicks_today": clicks_today,
        "top_group": top_group,
        "best_time": best_time,
        "bans_today": bans_today,
        "migrations_today": migrations_today,
        "broadcasts_today": broadcasts_today,
        "broadcast_success": broadcast_success,
        "broadcast_failed": broadcast_failed,
    }


async def get_weekly_stats() -> dict[str, Any]:
    """ดึงข้อมูล 7 วันย้อนหลังสำหรับ weekly analysis."""
    week_end = th_day_start_utc()
    week_start = week_end - timedelta(days=7)

    async with get_session() as session:
        # New users
        new_users = (await session.execute(
            select(func.count(User.id)).where(
                User.created_at >= week_start,
                User.created_at < week_end,
            )
        )).scalar() or 0

        # Weekly revenue
        weekly_revenue = float((await session.execute(
            select(func.coalesce(func.sum(Payment.amount), 0)).where(
                Payment.status == PaymentStatus.CONFIRMED,
                Payment.created_at >= week_start,
                Payment.created_at < week_end,
            )
        )).scalar() or 0)

        # Weekly clicks
        weekly_clicks = (await session.execute(
            select(func.count(TeaserClick.id)).where(
                TeaserClick.created_at >= week_start,
                TeaserClick.created_at < week_end,
            )
        )).scalar() or 0

        # Conversions (teaser clicks that converted)
        weekly_conversions = (await session.execute(
            select(func.count(TeaserClick.id)).where(
                TeaserClick.created_at >= week_start,
                TeaserClick.created_at < week_end,
                TeaserClick.converted == True,
            )
        )).scalar() or 0
        conversion_rate = round(
            (weekly_conversions / weekly_clicks * 100) if weekly_clicks > 0 else 0.0, 2
        )

        # Active subs
        active_subs = (await session.execute(
            select(func.count(Subscription.id)).where(
                Subscription.status == SubscriptionStatus.ACTIVE,
            )
        )).scalar() or 0

        # Expired this week
        expired_week = (await session.execute(
            select(func.count(Subscription.id)).where(
                Subscription.status == SubscriptionStatus.EXPIRED,
                Subscription.end_date >= week_start,
                Subscription.end_date < week_end,
            )
        )).scalar() or 0

        # Broadcasts
        broadcast_rows = (await session.execute(
            select(
                func.count(Broadcast.id).label("total"),
                func.coalesce(func.sum(Broadcast.success_count), 0).label("success"),
                func.coalesce(func.sum(Broadcast.failed_count), 0).label("failed"),
            ).where(
                Broadcast.started_at >= week_start,
                Broadcast.started_at < week_end,
            )
        )).first()

        # Bans
        bans_week = (await session.execute(
            select(func.count(AdminLog.id)).where(
                AdminLog.created_at >= week_start,
                AdminLog.created_at < week_end,
                AdminLog.action.ilike("%kick%") | AdminLog.action.ilike("%ban%"),
            )
        )).scalar() or 0

    now_th = datetime.now(TH_TZ)
    week_start_th = now_th - timedelta(days=7)

    return {
        "period_start": week_start_th.strftime("%d/%m"),
        "period_end": now_th.strftime("%d/%m"),
        "new_users": new_users,
        "weekly_revenue": weekly_revenue,
        "weekly_clicks": weekly_clicks,
        "conversion_rate": conversion_rate,
        "active_subs": active_subs,
        "expired_week": expired_week,
        "broadcasts_total": broadcast_rows.total if broadcast_rows else 0,
        "broadcast_success": int(broadcast_rows.success) if broadcast_rows else 0,
        "broadcast_failed": int(broadcast_rows.failed) if broadcast_rows else 0,
        "bans_week": bans_week,
    }
