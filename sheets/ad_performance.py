"""Sheet 'Facebook Ads Performance' - อัปเดตทุก 24h/48h."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import func, select

from shared.database import get_session
from shared.models import AdCampaign, AdPerformance, CampaignStatus, Lead, LeadStatus
from sheets.manager import SheetsManager

logger = logging.getLogger(__name__)

TH_TZ = timezone(timedelta(hours=7))


class AdPerformanceSheet:
    """Manages the 'Facebook Ads Performance' worksheet."""

    SHEET_NAME = "Facebook Ads Performance"

    @classmethod
    async def get_campaign_data(
        cls, date: datetime | None = None
    ) -> list[dict]:
        """Query ad performance data from the database for a given date."""
        if date is None:
            date = datetime.now(TH_TZ)

        day_start = date.replace(hour=0, minute=0, second=0, microsecond=0)
        day_end = day_start + timedelta(days=1)

        day_start_utc = day_start.astimezone(timezone.utc)
        day_end_utc = day_end.astimezone(timezone.utc)

        async with get_session() as session:
            result = await session.execute(
                select(AdPerformance, AdCampaign)
                .join(AdCampaign, AdPerformance.campaign_id == AdCampaign.id)
                .where(
                    AdPerformance.date >= day_start_utc,
                    AdPerformance.date < day_end_utc,
                )
                .order_by(AdPerformance.spend.desc())
            )
            rows = result.all()

            campaigns = []
            for perf, campaign in rows:
                # Count leads for this campaign on this date
                leads_q = await session.execute(
                    select(func.count(Lead.id)).where(
                        Lead.campaign_id == campaign.id,
                        Lead.created_at >= day_start_utc,
                        Lead.created_at < day_end_utc,
                    )
                )
                leads_count = leads_q.scalar() or 0

                spend = float(perf.spend)
                revenue = float(perf.revenue)
                impressions = perf.impressions
                clicks = perf.clicks
                conversions = perf.conversions

                ctr = (clicks / impressions * 100) if impressions > 0 else 0.0
                cpl = (spend / leads_count) if leads_count > 0 else 0.0
                roas = (revenue / spend) if spend > 0 else 0.0

                # Generate AI recommendation based on metrics
                recommendation = cls._generate_recommendation(
                    ctr=ctr, cpl=cpl, roas=roas, spend=spend,
                    budget=float(campaign.budget), leads=leads_count,
                )

                campaigns.append({
                    "date": day_start.strftime("%Y-%m-%d"),
                    "campaign": campaign.name,
                    "budget": float(campaign.budget),
                    "spent": spend,
                    "reach": impressions,
                    "impressions": impressions,
                    "clicks": clicks,
                    "ctr": ctr,
                    "leads": leads_count,
                    "cpl": cpl,
                    "conversions": conversions,
                    "revenue": revenue,
                    "roas": roas,
                    "recommendation": recommendation,
                })

        return campaigns

    @classmethod
    def _generate_recommendation(
        cls,
        ctr: float,
        cpl: float,
        roas: float,
        spend: float,
        budget: float,
        leads: int,
    ) -> str:
        """Generate a simple recommendation based on ad metrics."""
        notes = []

        if roas >= 3.0:
            notes.append("ROAS ดีมาก เพิ่มงบได้")
        elif roas >= 1.5:
            notes.append("ROAS พอใช้ ปรับ creative")
        elif roas > 0 and spend > 0:
            notes.append("ROAS ต่ำ พิจารณาปิดแอด")

        if ctr >= 3.0:
            notes.append("CTR สูง creative ดี")
        elif ctr >= 1.0:
            notes.append("CTR ปานกลาง")
        elif spend > 0:
            notes.append("CTR ต่ำ เปลี่ยน creative")

        if leads > 0 and cpl > 0:
            if cpl <= 50:
                notes.append(f"CPL ดี ฿{cpl:.0f}")
            elif cpl <= 150:
                notes.append(f"CPL ปานกลาง ฿{cpl:.0f}")
            else:
                notes.append(f"CPL สูง ฿{cpl:.0f} ปรับ targeting")

        if spend > budget * 0.9:
            notes.append("ใกล้หมดงบ")

        return " | ".join(notes) if notes else "รอข้อมูลเพิ่ม"

    @classmethod
    async def update(cls, date: datetime | None = None) -> None:
        """Update ad performance rows in Google Sheets."""
        campaigns = await cls.get_campaign_data(date)

        if not campaigns:
            logger.info("No ad performance data to update")
            return

        try:
            ws = SheetsManager.get_sheet(cls.SHEET_NAME)

            for data in campaigns:
                row = [
                    data["date"],
                    data["campaign"],
                    f"{data['budget']:,.2f}",
                    f"{data['spent']:,.2f}",
                    f"{data['reach']:,}",
                    f"{data['impressions']:,}",
                    f"{data['clicks']:,}",
                    f"{data['ctr']:.2f}%",
                    data["leads"],
                    f"{data['cpl']:,.2f}",
                    data["conversions"],
                    f"{data['revenue']:,.2f}",
                    f"{data['roas']:.2f}x",
                    data["recommendation"],
                ]

                # Check if this campaign+date already exists
                all_values = ws.get_all_values()
                found_row = None
                for idx, existing in enumerate(all_values, start=1):
                    if (
                        len(existing) >= 2
                        and existing[0] == data["date"]
                        and existing[1] == data["campaign"]
                    ):
                        found_row = idx
                        break

                if found_row:
                    SheetsManager.update_row(ws, found_row, row)
                else:
                    SheetsManager.append_row(ws, row)

            logger.info("Updated %d ad performance rows", len(campaigns))

        except Exception as exc:
            logger.error("Failed to update ad performance sheet: %s", exc)
            SheetsManager.reset_client()
            raise
