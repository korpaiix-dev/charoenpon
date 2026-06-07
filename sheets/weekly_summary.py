"""Sheet 'Weekly Summary' - อัปเดตทุกจันทร์ 07:00."""

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

from shared.tz import TH_TZ


class WeeklySummarySheet:
    """Manages the 'Weekly Summary' worksheet."""

    SHEET_NAME = "Weekly Summary"

    @classmethod
    async def get_weekly_data(
        cls, week_start: datetime | None = None
    ) -> dict:
        """Query weekly summary data. week_start should be Monday 00:00 TH time."""
        now = datetime.now(TH_TZ)

        if week_start is None:
            # Find last Monday
            days_since_monday = now.weekday()
            week_start = (now - timedelta(days=days_since_monday)).replace(
                hour=0, minute=0, second=0, microsecond=0
            )

        week_end = week_start + timedelta(days=7)

        week_start_utc = week_start.astimezone(timezone.utc)
        week_end_utc = week_end.astimezone(timezone.utc)

        async with get_session() as session:
            # Revenue
            revenue_q = await session.execute(
                select(func.coalesce(func.sum(Payment.amount), 0)).where(
                    Payment.status == PaymentStatus.CONFIRMED,
                    Payment.created_at >= week_start_utc,
                    Payment.created_at < week_end_utc,
                )
            )
            revenue = float(revenue_q.scalar() or 0)

            # Expenses: API costs
            api_cost_q = await session.execute(
                select(func.coalesce(func.sum(ApiCostLog.cost_thb), 0)).where(
                    ApiCostLog.created_at >= week_start_utc,
                    ApiCostLog.created_at < week_end_utc,
                )
            )
            api_cost = float(api_cost_q.scalar() or 0)

            # Expenses: Ad spend
            ad_spend_q = await session.execute(
                select(func.coalesce(func.sum(AdPerformance.spend), 0)).where(
                    AdPerformance.date >= week_start_utc,
                    AdPerformance.date < week_end_utc,
                )
            )
            ad_spend = float(ad_spend_q.scalar() or 0)

            total_expenses = api_cost + ad_spend
            profit = revenue - total_expenses

            # New members
            new_members_q = await session.execute(
                select(func.count(Subscription.id)).where(
                    Subscription.created_at >= week_start_utc,
                    Subscription.created_at < week_end_utc,
                )
            )
            new_members = new_members_q.scalar() or 0

            # Churn
            churn_q = await session.execute(
                select(func.count(Subscription.id)).where(
                    Subscription.status == SubscriptionStatus.EXPIRED,
                    Subscription.end_date >= week_start_utc,
                    Subscription.end_date < week_end_utc,
                )
            )
            churn = churn_q.scalar() or 0

            # Active
            active_q = await session.execute(
                select(func.count(Subscription.id)).where(
                    Subscription.status == SubscriptionStatus.ACTIVE,
                )
            )
            active = active_q.scalar() or 0

            # CAC
            cac = (ad_spend / new_members) if new_members > 0 else 0.0

            # Best CPL campaign
            best_cpl_q = await session.execute(
                select(
                    AdCampaign.name,
                    func.sum(AdPerformance.spend).label("spend"),
                    func.count(Lead.id).label("leads"),
                )
                .join(AdPerformance, AdPerformance.campaign_id == AdCampaign.id)
                .outerjoin(Lead, Lead.campaign_id == AdCampaign.id)
                .where(
                    AdPerformance.date >= week_start_utc,
                    AdPerformance.date < week_end_utc,
                )
                .group_by(AdCampaign.id, AdCampaign.name)
                .having(func.count(Lead.id) > 0)
                .order_by((func.sum(AdPerformance.spend) / func.count(Lead.id)).asc())
                .limit(1)
            )
            best_cpl_row = best_cpl_q.first()

            if best_cpl_row and best_cpl_row.leads > 0:
                best_cpl = float(best_cpl_row.spend) / best_cpl_row.leads
                best_cpl_str = f"฿{best_cpl:,.2f}"
                best_campaign = best_cpl_row.name
            else:
                best_cpl_str = "-"
                best_campaign = "-"

            # Best ad by ROAS
            best_roas_q = await session.execute(
                select(
                    AdCampaign.name,
                    func.sum(AdPerformance.revenue).label("revenue"),
                    func.sum(AdPerformance.spend).label("spend"),
                )
                .join(AdPerformance, AdPerformance.campaign_id == AdCampaign.id)
                .where(
                    AdPerformance.date >= week_start_utc,
                    AdPerformance.date < week_end_utc,
                )
                .group_by(AdCampaign.id, AdCampaign.name)
                .having(func.sum(AdPerformance.spend) > 0)
                .order_by(
                    (func.sum(AdPerformance.revenue) / func.sum(AdPerformance.spend)).desc()
                )
                .limit(1)
            )
            best_roas_row = best_roas_q.first()
            best_ad = best_roas_row.name if best_roas_row else "-"

        # Generate insight
        insight = cls._generate_insight(
            revenue=revenue, expenses=total_expenses, profit=profit,
            new_members=new_members, churn=churn, cac=cac,
        )

        # Generate action plan
        action_plan = cls._generate_action_plan(
            revenue=revenue, profit=profit, new_members=new_members,
            churn=churn, cac=cac,
        )

        week_num = week_start.isocalendar()[1]

        return {
            "week": f"W{week_num}",
            "week_start": week_start.strftime("%Y-%m-%d"),
            "week_end": (week_end - timedelta(days=1)).strftime("%Y-%m-%d"),
            "revenue": revenue,
            "expenses": total_expenses,
            "profit": profit,
            "new_members": new_members,
            "churn": churn,
            "active": active,
            "cac": cac,
            "best_cpl": best_cpl_str,
            "best_ad": best_ad,
            "insight": insight,
            "action_plan": action_plan,
        }

    @classmethod
    def _generate_insight(
        cls,
        revenue: float,
        expenses: float,
        profit: float,
        new_members: int,
        churn: int,
        cac: float,
    ) -> str:
        """Generate weekly insight text based on metrics."""
        notes = []

        if profit > 0:
            margin = (profit / revenue * 100) if revenue > 0 else 0
            notes.append(f"กำไร ฿{profit:,.0f} (margin {margin:.0f}%)")
        else:
            notes.append(f"ขาดทุน ฿{abs(profit):,.0f}")

        net_growth = new_members - churn
        if net_growth > 0:
            notes.append(f"สมาชิกเพิ่มสุทธิ +{net_growth}")
        elif net_growth < 0:
            notes.append(f"สมาชิกลดสุทธิ {net_growth}")
        else:
            notes.append("สมาชิกคงที่")

        if churn > 0 and new_members > 0:
            churn_ratio = churn / new_members
            if churn_ratio > 0.5:
                notes.append("Churn สูง ควรปรับ retention")

        return " | ".join(notes)

    @classmethod
    def _generate_action_plan(
        cls,
        revenue: float,
        profit: float,
        new_members: int,
        churn: int,
        cac: float,
    ) -> str:
        """Generate weekly action plan based on metrics."""
        actions = []

        if churn > new_members:
            actions.append("เพิ่มแคมเปญ retention / ส่ง promo ต่ออายุ")

        if cac > 200:
            actions.append("ปรับ targeting ลด CAC")
        elif cac > 100:
            actions.append("ทดสอบ creative ใหม่เพื่อลด CAC")

        if new_members < 5:
            actions.append("เพิ่มงบโฆษณา/ขยาย channel")

        if profit < 0:
            actions.append("ลดรายจ่าย API / ปรับแพ็กเกจราคา")

        if not actions:
            actions.append("รักษาแนวทางปัจจุบัน ทดสอบ A/B creative")

        return " → ".join(actions)

    @classmethod
    async def update(cls, week_start: datetime | None = None) -> None:
        """Update or insert weekly summary row in Google Sheets."""
        data = await cls.get_weekly_data(week_start)

        try:
            ws = SheetsManager.get_sheet(cls.SHEET_NAME)

            row = [
                data["week"],
                data["week_start"],
                data["week_end"],
                f"{data['revenue']:,.2f}",
                f"{data['expenses']:,.2f}",
                f"{data['profit']:,.2f}",
                data["new_members"],
                data["churn"],
                data["active"],
                f"{data['cac']:,.2f}",
                data["best_cpl"],
                data["best_ad"],
                data["insight"],
                data["action_plan"],
            ]

            existing_row = SheetsManager.find_row_by_value(ws, 1, data["week"])
            if existing_row:
                SheetsManager.update_row(ws, existing_row, row)
                logger.info("Updated weekly summary for %s", data["week"])
            else:
                SheetsManager.append_row(ws, row)
                logger.info("Appended weekly summary for %s", data["week"])

        except Exception as exc:
            logger.error("Failed to update weekly summary sheet: %s", exc)
            SheetsManager.reset_client()
            raise
