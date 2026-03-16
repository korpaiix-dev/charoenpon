"""Data Collector (แมน) - เก็บข้อมูลทุกส่วนเพื่อวิเคราะห์.

Model: google/gemini-2.5-flash ผ่าน OpenRouter
เก็บข้อมูล: Marketing FB, Sales TG, Retention, Content, Finance
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select

from shared.database import get_session
from shared.models import (
    AdCampaign,
    AdPerformance,
    CampaignStatus,
    ContentSchedule,
    ExpiryNotification,
    GroupRegistry,
    GroupSlug,
    Lead,
    LeadStatus,
    Package,
    Payment,
    PaymentMethod,
    PaymentStatus,
    Subscription,
    SubscriptionStatus,
    User,
)
from shared.utils import TH_TZ

logger = logging.getLogger(__name__)


async def collect_marketing_data(
    days: int = 7,
) -> dict[str, Any]:
    """เก็บข้อมูล Marketing (Facebook Ads performance)."""
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=days)

    async with get_session() as session:
        campaigns_q = await session.execute(
            select(AdCampaign).where(
                AdCampaign.status.in_([CampaignStatus.ACTIVE, CampaignStatus.COMPLETED]),
            )
        )
        campaigns = campaigns_q.scalars().all()

        perf_q = await session.execute(
            select(
                AdPerformance.campaign_id,
                func.sum(AdPerformance.impressions).label("impressions"),
                func.sum(AdPerformance.clicks).label("clicks"),
                func.sum(AdPerformance.conversions).label("conversions"),
                func.sum(AdPerformance.spend).label("spend"),
                func.sum(AdPerformance.revenue).label("revenue"),
            ).where(
                AdPerformance.date >= since,
            ).group_by(AdPerformance.campaign_id)
        )
        perf_data = {row.campaign_id: row for row in perf_q.all()}

        leads_q = await session.execute(
            select(
                Lead.source,
                Lead.status,
                func.count(Lead.id).label("count"),
            ).where(
                Lead.created_at >= since,
            ).group_by(Lead.source, Lead.status)
        )
        leads_by_source = {}
        for row in leads_q.all():
            source = row.source or "unknown"
            if source not in leads_by_source:
                leads_by_source[source] = {}
            leads_by_source[source][row.status.value] = row.count

    campaign_data = []
    for camp in campaigns:
        perf = perf_data.get(camp.id)
        impressions = int(perf.impressions) if perf else 0
        clicks = int(perf.clicks) if perf else 0
        spend = float(perf.spend) if perf else 0.0

        ctr = (clicks / impressions * 100) if impressions > 0 else 0.0
        cpc = (spend / clicks) if clicks > 0 else 0.0

        campaign_data.append({
            "id": camp.id,
            "name": camp.name,
            "platform": camp.platform,
            "status": camp.status.value,
            "budget": float(camp.budget),
            "spent": float(camp.spent),
            "impressions": impressions,
            "clicks": clicks,
            "conversions": int(perf.conversions) if perf else 0,
            "ctr": round(ctr, 2),
            "cpc": round(cpc, 2),
        })

    return {
        "period_days": days,
        "campaigns": campaign_data,
        "leads_by_source": leads_by_source,
        "collected_at": now.isoformat(),
    }


async def collect_sales_data(
    days: int = 7,
) -> dict[str, Any]:
    """เก็บข้อมูล Sales (Telegram subscriptions, payments)."""
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=days)

    async with get_session() as session:
        new_subs_q = await session.execute(
            select(func.count(Subscription.id)).where(
                Subscription.created_at >= since,
            )
        )
        new_subs = new_subs_q.scalar() or 0

        payments_q = await session.execute(
            select(
                Payment.method,
                func.count(Payment.id).label("count"),
                func.sum(Payment.amount).label("total"),
            ).where(
                Payment.status == PaymentStatus.CONFIRMED,
                Payment.created_at >= since,
            ).group_by(Payment.method)
        )
        payments_by_method = [
            {
                "method": row.method.value,
                "count": row.count,
                "total": float(row.total),
            }
            for row in payments_q.all()
        ]

        total_revenue_q = await session.execute(
            select(func.coalesce(func.sum(Payment.amount), 0)).where(
                Payment.status == PaymentStatus.CONFIRMED,
                Payment.created_at >= since,
            )
        )
        total_revenue = float(total_revenue_q.scalar() or 0)

        conversion_q = await session.execute(
            select(
                func.count(Lead.id).filter(Lead.status == LeadStatus.CONVERTED).label("converted"),
                func.count(Lead.id).label("total"),
            ).where(Lead.created_at >= since)
        )
        conv_row = conversion_q.one()
        conversion_rate = (conv_row.converted / conv_row.total * 100) if conv_row.total > 0 else 0.0

        by_package_q = await session.execute(
            select(
                Package.name,
                Package.tier,
                func.count(Payment.id).label("sales"),
                func.sum(Payment.amount).label("revenue"),
            ).join(Payment, Payment.package_id == Package.id)
            .where(
                Payment.status == PaymentStatus.CONFIRMED,
                Payment.created_at >= since,
            ).group_by(Package.id, Package.name, Package.tier)
            .order_by(func.sum(Payment.amount).desc())
        )
        sales_by_package = [
            {
                "name": row.name,
                "tier": row.tier.value,
                "sales": row.sales,
                "revenue": float(row.revenue),
            }
            for row in by_package_q.all()
        ]

    return {
        "period_days": days,
        "new_subscriptions": new_subs,
        "total_revenue": total_revenue,
        "payments_by_method": payments_by_method,
        "conversion_rate": round(conversion_rate, 2),
        "sales_by_package": sales_by_package,
        "collected_at": now.isoformat(),
    }


async def collect_retention_data(
    days: int = 7,
) -> dict[str, Any]:
    """เก็บข้อมูล Retention (churn, renewals, notifications)."""
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=days)

    async with get_session() as session:
        active_q = await session.execute(
            select(func.count(Subscription.id)).where(
                Subscription.status == SubscriptionStatus.ACTIVE,
            )
        )
        active_subs = active_q.scalar() or 0

        expired_q = await session.execute(
            select(func.count(Subscription.id)).where(
                Subscription.status == SubscriptionStatus.EXPIRED,
                Subscription.end_date >= since,
                Subscription.end_date <= now,
            )
        )
        expired = expired_q.scalar() or 0

        renewals_q = await session.execute(
            select(func.count(Subscription.id)).where(
                Subscription.auto_renew.is_(True),
                Subscription.created_at >= since,
            )
        )
        renewals = renewals_q.scalar() or 0

        notif_q = await session.execute(
            select(
                ExpiryNotification.notification_type,
                func.count(ExpiryNotification.id).label("sent"),
                func.count(ExpiryNotification.id).filter(
                    ExpiryNotification.acknowledged.is_(True)
                ).label("acked"),
            ).where(
                ExpiryNotification.sent_at >= since,
            ).group_by(ExpiryNotification.notification_type)
        )
        notifications = [
            {
                "type": row.notification_type.value,
                "sent": row.sent,
                "acknowledged": row.acked,
            }
            for row in notif_q.all()
        ]

    churn_rate = (expired / (active_subs + expired) * 100) if (active_subs + expired) > 0 else 0.0

    return {
        "period_days": days,
        "active_subscribers": active_subs,
        "expired": expired,
        "renewals": renewals,
        "churn_rate": round(churn_rate, 2),
        "notifications": notifications,
        "collected_at": now.isoformat(),
    }


async def collect_content_data(
    days: int = 7,
) -> dict[str, Any]:
    """เก็บข้อมูล Content (posts per group, engagement)."""
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=days)

    async with get_session() as session:
        by_group_q = await session.execute(
            select(
                ContentSchedule.group_slug,
                func.count(ContentSchedule.id).label("total"),
                func.count(ContentSchedule.id).filter(
                    ContentSchedule.is_sent.is_(True)
                ).label("sent"),
                func.count(ContentSchedule.id).filter(
                    ContentSchedule.error.isnot(None)
                ).label("errors"),
            ).where(
                ContentSchedule.created_at >= since,
            ).group_by(ContentSchedule.group_slug)
        )
        by_group = [
            {
                "group": row.group_slug.value,
                "total": row.total,
                "sent": row.sent,
                "errors": row.errors,
            }
            for row in by_group_q.all()
        ]

        groups_q = await session.execute(
            select(GroupRegistry).where(GroupRegistry.is_active.is_(True))
        )
        groups_info = [
            {
                "slug": g.slug.value,
                "title": g.title,
                "member_count": g.member_count,
            }
            for g in groups_q.scalars().all()
        ]

    return {
        "period_days": days,
        "content_by_group": by_group,
        "groups_info": groups_info,
        "collected_at": now.isoformat(),
    }


async def collect_finance_data(
    days: int = 7,
) -> dict[str, Any]:
    """เก็บข้อมูล Finance (revenue, costs, profit)."""
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=days)

    async with get_session() as session:
        revenue_q = await session.execute(
            select(func.coalesce(func.sum(Payment.amount), 0)).where(
                Payment.status == PaymentStatus.CONFIRMED,
                Payment.created_at >= since,
            )
        )
        revenue = float(revenue_q.scalar() or 0)

        ads_q = await session.execute(
            select(func.coalesce(func.sum(AdPerformance.spend), 0)).where(
                AdPerformance.date >= since,
            )
        )
        ads_spend = float(ads_q.scalar() or 0)

        from shared.models import ApiCostLog
        api_q = await session.execute(
            select(func.coalesce(func.sum(ApiCostLog.cost_thb), 0)).where(
                ApiCostLog.created_at >= since,
            )
        )
        api_cost = float(api_q.scalar() or 0)

    total_expenses = ads_spend + api_cost
    profit = revenue - total_expenses
    margin = (profit / revenue * 100) if revenue > 0 else 0.0

    return {
        "period_days": days,
        "revenue": revenue,
        "ads_spend": ads_spend,
        "api_cost": api_cost,
        "total_expenses": total_expenses,
        "profit": profit,
        "margin": round(margin, 2),
        "collected_at": now.isoformat(),
    }


async def collect_all(days: int = 7) -> dict[str, Any]:
    """เก็บข้อมูลทุกส่วนรวมกัน."""
    marketing = await collect_marketing_data(days)
    sales = await collect_sales_data(days)
    retention = await collect_retention_data(days)
    content = await collect_content_data(days)
    finance = await collect_finance_data(days)

    combined = {
        "period_days": days,
        "marketing": marketing,
        "sales": sales,
        "retention": retention,
        "content": content,
        "finance": finance,
        "collected_at": datetime.now(timezone.utc).isoformat(),
    }

    logger.info("Collected all data for %d days", days)
    return combined
