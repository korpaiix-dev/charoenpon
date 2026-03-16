"""Sheet 'รายได้รายเดือน' - อัปเดตวันที่ 1 + running total."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import func, select

from shared.database import get_session
from shared.models import (
    AdCampaign,
    AdPerformance,
    ApiCostLog,
    Lead,
    LeadStatus,
    Payment,
    PaymentStatus,
    Subscription,
    SubscriptionStatus,
)
from sheets.manager import SheetsManager

logger = logging.getLogger(__name__)

TH_TZ = timezone(timedelta(hours=7))


class MonthlySummarySheet:
    """Manages the 'รายได้รายเดือน' worksheet."""

    SHEET_NAME = "รายได้รายเดือน"

    @classmethod
    async def get_monthly_data(cls, year: int | None = None, month: int | None = None) -> dict:
        """Query monthly summary data from the database."""
        now = datetime.now(TH_TZ)
        if year is None:
            year = now.year
        if month is None:
            month = now.month

        month_start = datetime(year, month, 1, tzinfo=TH_TZ)
        if month == 12:
            month_end = datetime(year + 1, 1, 1, tzinfo=TH_TZ)
        else:
            month_end = datetime(year, month + 1, 1, tzinfo=TH_TZ)

        month_start_utc = month_start.astimezone(timezone.utc)
        month_end_utc = month_end.astimezone(timezone.utc)

        async with get_session() as session:
            # Total revenue
            revenue_q = await session.execute(
                select(func.coalesce(func.sum(Payment.amount), 0)).where(
                    Payment.status == PaymentStatus.CONFIRMED,
                    Payment.created_at >= month_start_utc,
                    Payment.created_at < month_end_utc,
                )
            )
            revenue = float(revenue_q.scalar() or 0)

            # Total expenses (API costs)
            expense_q = await session.execute(
                select(func.coalesce(func.sum(ApiCostLog.cost_thb), 0)).where(
                    ApiCostLog.created_at >= month_start_utc,
                    ApiCostLog.created_at < month_end_utc,
                )
            )
            api_expenses = float(expense_q.scalar() or 0)

            # Ad spend
            ad_spend_q = await session.execute(
                select(func.coalesce(func.sum(AdPerformance.spend), 0)).where(
                    AdPerformance.date >= month_start_utc,
                    AdPerformance.date < month_end_utc,
                )
            )
            ad_spend = float(ad_spend_q.scalar() or 0)

            total_expenses = api_expenses + ad_spend
            profit = revenue - total_expenses
            margin = (profit / revenue * 100) if revenue > 0 else 0.0

            # MRR (active subscriptions * their package price)
            mrr_q = await session.execute(
                select(func.coalesce(func.sum(Payment.amount), 0))
                .join(Subscription, Subscription.payment_id == Payment.id)
                .where(
                    Subscription.status == SubscriptionStatus.ACTIVE,
                )
            )
            mrr = float(mrr_q.scalar() or 0)

            # New members this month
            new_members_q = await session.execute(
                select(func.count(Subscription.id)).where(
                    Subscription.created_at >= month_start_utc,
                    Subscription.created_at < month_end_utc,
                )
            )
            new_members = new_members_q.scalar() or 0

            # Churn this month
            churn_q = await session.execute(
                select(func.count(Subscription.id)).where(
                    Subscription.status == SubscriptionStatus.EXPIRED,
                    Subscription.end_date >= month_start_utc,
                    Subscription.end_date < month_end_utc,
                )
            )
            churn = churn_q.scalar() or 0

            # Active subscriptions
            active_q = await session.execute(
                select(func.count(Subscription.id)).where(
                    Subscription.status == SubscriptionStatus.ACTIVE,
                )
            )
            active = active_q.scalar() or 0

            # CAC (Customer Acquisition Cost) = ad spend / new members
            cac = (ad_spend / new_members) if new_members > 0 else 0.0

            # ROAS = revenue from ad leads / ad spend
            ad_revenue_q = await session.execute(
                select(func.coalesce(func.sum(AdPerformance.revenue), 0)).where(
                    AdPerformance.date >= month_start_utc,
                    AdPerformance.date < month_end_utc,
                )
            )
            ad_revenue = float(ad_revenue_q.scalar() or 0)
            roas = (ad_revenue / ad_spend) if ad_spend > 0 else 0.0

        month_label = f"{year}-{month:02d}"

        return {
            "month": month_label,
            "revenue": revenue,
            "expenses": total_expenses,
            "profit": profit,
            "margin": margin,
            "mrr": mrr,
            "new_members": new_members,
            "churn": churn,
            "active": active,
            "cac": cac,
            "roas": roas,
        }

    @classmethod
    async def update(cls, year: int | None = None, month: int | None = None) -> None:
        """Update or insert monthly summary row in Google Sheets."""
        data = await cls.get_monthly_data(year, month)

        try:
            ws = SheetsManager.get_sheet(cls.SHEET_NAME)

            row = [
                data["month"],
                f"{data['revenue']:,.2f}",
                f"{data['expenses']:,.2f}",
                f"{data['profit']:,.2f}",
                f"{data['margin']:.1f}%",
                f"{data['mrr']:,.2f}",
                data["new_members"],
                data["churn"],
                data["active"],
                f"{data['cac']:,.2f}",
                f"{data['roas']:.2f}x",
            ]

            existing_row = SheetsManager.find_row_by_value(ws, 1, data["month"])
            if existing_row:
                SheetsManager.update_row(ws, existing_row, row)
                logger.info("Updated monthly summary for %s", data["month"])
            else:
                SheetsManager.append_row(ws, row)
                logger.info("Appended monthly summary for %s", data["month"])

        except Exception as exc:
            logger.error("Failed to update monthly summary sheet: %s", exc)
            SheetsManager.reset_client()
            raise
