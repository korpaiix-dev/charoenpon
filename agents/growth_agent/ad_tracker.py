"""Ad Tracker (แมน) - ติดตามและวิเคราะห์ผลโฆษณา.

Model: google/gemini-2.5-flash ผ่าน OpenRouter
TRIGGER วิเคราะห์:
- หลังแอดรัน 24h/48h
- CPL เกิน threshold
- CTR < 1%
- Budget ใช้ 50%/100%
- ใช้งบ 30% ยัง 0 lead → แจ้งทันที
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import httpx
from sqlalchemy import func, select

from shared.api_cost_tracker import call_openrouter
from shared.database import get_session
from shared.models import (
    AdCampaign,
    AdPerformance,
    CampaignStatus,
    Lead,
    LeadStatus,
)
from shared.utils import TH_TZ, format_thb

logger = logging.getLogger(__name__)

MODEL = "google/gemini-2.5-flash"
CALLER = "growth_agent/ad_tracker"

DISCORD_WEBHOOK_ALERTS: str = os.environ.get("DISCORD_WEBHOOK_ALERTS", "")
DISCORD_WEBHOOK_GROWTH: str = os.environ.get("DISCORD_WEBHOOK_GROWTH", "")

DEFAULT_CPL_THRESHOLD = 150.0
DEFAULT_CTR_THRESHOLD = 1.0
BUDGET_MILESTONES = [0.50, 1.00]
ZERO_LEAD_BUDGET_THRESHOLD = 0.30


async def get_campaign_metrics(campaign_id: int) -> dict[str, Any] | None:
    """ดึง metrics ของ campaign."""
    async with get_session() as session:
        camp_q = await session.execute(
            select(AdCampaign).where(AdCampaign.id == campaign_id)
        )
        campaign = camp_q.scalar_one_or_none()
        if not campaign:
            return None

        perf_q = await session.execute(
            select(
                func.sum(AdPerformance.impressions).label("impressions"),
                func.sum(AdPerformance.clicks).label("clicks"),
                func.sum(AdPerformance.conversions).label("conversions"),
                func.sum(AdPerformance.spend).label("spend"),
                func.sum(AdPerformance.revenue).label("revenue"),
                func.min(AdPerformance.date).label("first_date"),
                func.max(AdPerformance.date).label("last_date"),
            ).where(AdPerformance.campaign_id == campaign_id)
        )
        perf = perf_q.one()

        leads_q = await session.execute(
            select(func.count(Lead.id)).where(
                Lead.campaign_id == campaign_id,
            )
        )
        total_leads = leads_q.scalar() or 0

        converted_q = await session.execute(
            select(func.count(Lead.id)).where(
                Lead.campaign_id == campaign_id,
                Lead.status == LeadStatus.CONVERTED,
            )
        )
        converted_leads = converted_q.scalar() or 0

    impressions = int(perf.impressions or 0)
    clicks = int(perf.clicks or 0)
    spend = float(perf.spend or 0)
    conversions = int(perf.conversions or 0)

    ctr = (clicks / impressions * 100) if impressions > 0 else 0.0
    cpc = (spend / clicks) if clicks > 0 else 0.0
    cpl = (spend / total_leads) if total_leads > 0 else 0.0
    budget_used = (float(campaign.spent) / float(campaign.budget) * 100) if campaign.budget > 0 else 0.0

    first_date = perf.first_date
    running_hours = 0.0
    if first_date:
        running_hours = (datetime.now(timezone.utc) - first_date).total_seconds() / 3600

    return {
        "campaign_id": campaign.id,
        "name": campaign.name,
        "status": campaign.status.value,
        "budget": float(campaign.budget),
        "spent": float(campaign.spent),
        "budget_used_pct": round(budget_used, 1),
        "impressions": impressions,
        "clicks": clicks,
        "conversions": conversions,
        "total_leads": total_leads,
        "converted_leads": converted_leads,
        "ctr": round(ctr, 2),
        "cpc": round(cpc, 2),
        "cpl": round(cpl, 2),
        "spend": spend,
        "running_hours": round(running_hours, 1),
    }


async def check_triggers(campaign_id: int) -> list[dict[str, Any]]:
    """ตรวจสอบ triggers ทั้งหมดสำหรับ campaign."""
    metrics = await get_campaign_metrics(campaign_id)
    if not metrics:
        return []

    triggers: list[dict[str, Any]] = []

    # 1. หลังแอดรัน 24h
    if 23.5 <= metrics["running_hours"] <= 24.5:
        analysis = await _analyze_campaign(metrics, "24h_check")
        triggers.append({
            "type": "24h_check",
            "severity": "info",
            "campaign_id": campaign_id,
            "message": f"⏰ Campaign '{metrics['name']}' รัน 24 ชม.แล้ว",
            "metrics": metrics,
            "analysis": analysis,
        })

    # 2. หลังแอดรัน 48h
    if 47.5 <= metrics["running_hours"] <= 48.5:
        analysis = await _analyze_campaign(metrics, "48h_check")
        triggers.append({
            "type": "48h_check",
            "severity": "info",
            "campaign_id": campaign_id,
            "message": f"⏰ Campaign '{metrics['name']}' รัน 48 ชม.แล้ว",
            "metrics": metrics,
            "analysis": analysis,
        })

    # 3. CPL เกิน threshold
    if metrics["cpl"] > DEFAULT_CPL_THRESHOLD and metrics["total_leads"] > 0:
        triggers.append({
            "type": "high_cpl",
            "severity": "warning",
            "campaign_id": campaign_id,
            "message": (
                f"⚠️ Campaign '{metrics['name']}' CPL สูง!\n"
                f"CPL: {format_thb(metrics['cpl'])} (threshold: {format_thb(DEFAULT_CPL_THRESHOLD)})\n"
                f"Leads: {metrics['total_leads']}, Spend: {format_thb(metrics['spend'])}"
            ),
            "metrics": metrics,
        })

    # 4. CTR < 1%
    if metrics["ctr"] < DEFAULT_CTR_THRESHOLD and metrics["impressions"] > 100:
        triggers.append({
            "type": "low_ctr",
            "severity": "warning",
            "campaign_id": campaign_id,
            "message": (
                f"⚠️ Campaign '{metrics['name']}' CTR ต่ำ!\n"
                f"CTR: {metrics['ctr']}% (threshold: {DEFAULT_CTR_THRESHOLD}%)\n"
                f"Impressions: {metrics['impressions']:,}, Clicks: {metrics['clicks']:,}"
            ),
            "metrics": metrics,
        })

    # 5. Budget milestones (50%, 100%)
    for milestone in BUDGET_MILESTONES:
        milestone_pct = milestone * 100
        if abs(metrics["budget_used_pct"] - milestone_pct) < 2:
            triggers.append({
                "type": f"budget_{int(milestone_pct)}pct",
                "severity": "info" if milestone < 1.0 else "warning",
                "campaign_id": campaign_id,
                "message": (
                    f"💰 Campaign '{metrics['name']}' ใช้งบ {metrics['budget_used_pct']:.0f}%\n"
                    f"Spent: {format_thb(metrics['spent'])} / Budget: {format_thb(metrics['budget'])}"
                ),
                "metrics": metrics,
            })

    # 6. ใช้งบ 30% ยัง 0 lead → แจ้งทันที
    if (
        metrics["budget_used_pct"] >= ZERO_LEAD_BUDGET_THRESHOLD * 100
        and metrics["total_leads"] == 0
    ):
        triggers.append({
            "type": "zero_leads_warning",
            "severity": "critical",
            "campaign_id": campaign_id,
            "message": (
                f"🚨 CRITICAL: Campaign '{metrics['name']}' ใช้งบ {metrics['budget_used_pct']:.0f}% "
                f"แต่ยัง 0 leads!\n"
                f"Spent: {format_thb(metrics['spent'])} / Budget: {format_thb(metrics['budget'])}\n"
                f"ควรพิจารณาหยุดแอดและ review ทันที"
            ),
            "metrics": metrics,
        })

    return triggers


async def _analyze_campaign(
    metrics: dict[str, Any],
    check_type: str,
) -> str:
    """ใช้ AI วิเคราะห์ campaign performance."""
    messages = [
        {"role": "system", "content": (
            "คุณคือ 'แมน' Growth Analyst ของบริษัทเจริญพร\n"
            "วิเคราะห์ผลการรันโฆษณาแล้วให้ recommendation\n\n"
            "ตอบสั้นๆ 3-5 บรรทัด:\n"
            "1. สรุปผลรวม\n"
            "2. จุดที่ดี/ต้องปรับ\n"
            "3. Recommendation (ควรทำอะไรต่อ)\n\n"
            "ภาษาไทย กระชับ actionable ใช้ตัวเลขจริง"
        )},
        {"role": "user", "content": (
            f"วิเคราะห์ campaign '{metrics['name']}' ({check_type}):\n"
            f"Running: {metrics['running_hours']:.0f} ชม.\n"
            f"Budget: {format_thb(metrics['budget'])} (used {metrics['budget_used_pct']:.0f}%)\n"
            f"Impressions: {metrics['impressions']:,}\n"
            f"Clicks: {metrics['clicks']:,} (CTR: {metrics['ctr']}%)\n"
            f"CPC: {format_thb(metrics['cpc'])}\n"
            f"Leads: {metrics['total_leads']} (CPL: {format_thb(metrics['cpl'])})\n"
            f"Conversions: {metrics['converted_leads']}\n"
            f"Spend: {format_thb(metrics['spend'])}"
        )},
    ]

    response = await call_openrouter(
        model=MODEL,
        messages=messages,
        caller=CALLER,
        temperature=0.4,
        max_tokens=500,
        metadata={
            "campaign_id": metrics["campaign_id"],
            "check_type": check_type,
        },
    )

    return response["choices"][0]["message"]["content"].strip()


async def send_trigger_alert(trigger: dict[str, Any]) -> bool:
    """ส่ง trigger alert ไป Discord."""
    webhook_url = DISCORD_WEBHOOK_ALERTS or DISCORD_WEBHOOK_GROWTH
    if not webhook_url:
        logger.warning("No Discord webhook for ad tracker alerts")
        return False

    severity_map = {
        "critical": "🔴",
        "warning": "🟡",
        "info": "🔵",
    }
    emoji = severity_map.get(trigger.get("severity", "info"), "🔵")

    content = (
        f"{emoji} **Ad Tracker Alert** [{trigger['type']}]\n"
        f"{trigger.get('message', '-')}"
    )

    analysis = trigger.get("analysis")
    if analysis:
        content += f"\n\n📊 **AI Analysis:**\n{analysis}"

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(webhook_url, json={"content": content})
            resp.raise_for_status()
        logger.info("Ad tracker alert sent: %s", trigger["type"])
        return True
    except Exception as exc:
        logger.error("Failed to send ad tracker alert: %s", exc)
        return False


async def check_all_active_campaigns() -> list[dict[str, Any]]:
    """ตรวจสอบ triggers ทุก active campaign."""
    async with get_session() as session:
        q = await session.execute(
            select(AdCampaign.id).where(
                AdCampaign.status == CampaignStatus.ACTIVE,
            )
        )
        campaign_ids = [row[0] for row in q.all()]

    all_triggers = []
    for cid in campaign_ids:
        triggers = await check_triggers(cid)
        for trigger in triggers:
            all_triggers.append(trigger)
            await send_trigger_alert(trigger)

    if all_triggers:
        logger.info("Ad tracker: %d triggers from %d campaigns", len(all_triggers), len(campaign_ids))
    else:
        logger.info("Ad tracker: no triggers from %d campaigns", len(campaign_ids))

    return all_triggers
