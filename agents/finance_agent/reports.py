"""Finance Reports (นัท) - รายงานการเงิน daily/weekly + alerts อัตโนมัติ.

Model: openai/gpt-4o-mini ผ่าน OpenRouter
- รายงานประจำวัน 23:00: รายรับ/รายจ่าย/กำไร/margin แยกวิธีชำระ
- รายงานประจำสัปดาห์ จันทร์ 07:00: MRR/CAC/Churn/ARPU/แพ็กเกจขายดี
- ALERT อัตโนมัติ: รายรับต่ำ>30%, Ads เกิน>20%, Churn>10คน/24h
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import httpx
from sqlalchemy import func, select

from shared.api_cost_tracker import call_openrouter, daily_summary as api_daily_summary
from shared.database import get_session
from shared.models import (
    AdCampaign,
    AdPerformance,
    ApiCostLog,
    CampaignStatus,
    Package,
    Payment,
    PaymentMethod,
    PaymentStatus,
    Subscription,
    SubscriptionStatus,
    User,
)
from shared.utils import TH_TZ, format_thb

logger = logging.getLogger(__name__)

MODEL = "openai/gpt-4o-mini"
CALLER = "finance_agent/reports"

DISCORD_BOT_TOKEN: str = os.environ.get("DISCORD_BOT_TOKEN", "")
DISCORD_CH_FINANCE: str = os.environ.get("DISCORD_CH_FINANCE", "")
DISCORD_CH_ALERTS: str = os.environ.get("DISCORD_CH_ALERTS", "")

ALERT_REVENUE_DROP_THRESHOLD = 0.30
ALERT_ADS_OVERSPEND_THRESHOLD = 0.20
ALERT_CHURN_THRESHOLD = 10


async def _get_daily_revenue(date: datetime) -> dict[str, Any]:
    """ดึงรายรับประจำวัน แยกตามวิธีชำระ."""
    _th = date + timedelta(hours=7)  # FIX: Thai calendar day, not UTC (created_at naive-UTC; TH=UTC+7)
    day_start = _th.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(hours=7)
    day_end = day_start + timedelta(days=1)

    async with get_session() as session:
        total_q = await session.execute(
            select(
                func.coalesce(func.sum(Payment.amount), 0).label("total"),
                func.count(Payment.id).label("count"),
            )
            .join(User, User.id == Payment.user_id)
            .where(
                Payment.status == PaymentStatus.CONFIRMED,
                Payment.amount > 0,
                User.telegram_id < 9000000000,
                Payment.created_at >= day_start,
                Payment.created_at < day_end,
            )
        )
        totals = total_q.one()

        by_method_q = await session.execute(
            select(
                Payment.method,
                func.sum(Payment.amount).label("amount"),
                func.count(Payment.id).label("count"),
            )
            .join(User, User.id == Payment.user_id)
            .where(
                Payment.status == PaymentStatus.CONFIRMED,
                Payment.amount > 0,
                User.telegram_id < 9000000000,
                Payment.created_at >= day_start,
                Payment.created_at < day_end,
            ).group_by(Payment.method)
        )
        by_method = [
            {
                "method": row.method.value,
                "amount": float(row.amount),
                "count": row.count,
            }
            for row in by_method_q.all()
        ]

    return {
        "date": day_start.strftime("%Y-%m-%d"),
        "total_revenue": float(totals.total),
        "total_transactions": totals.count,
        "by_payment_method": by_method,
    }


async def _get_daily_expenses(date: datetime) -> dict[str, Any]:
    """ดึงรายจ่ายประจำวัน (Ads + API costs)."""
    _th = date + timedelta(hours=7)  # FIX: Thai calendar day, not UTC (created_at naive-UTC; TH=UTC+7)
    day_start = _th.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(hours=7)
    day_end = day_start + timedelta(days=1)

    async with get_session() as session:
        ads_q = await session.execute(
            select(
                func.coalesce(func.sum(AdPerformance.spend), 0).label("ads_spend"),
            ).where(
                AdPerformance.date >= day_start,
                AdPerformance.date < day_end,
            )
        )
        ads_spend = float(ads_q.one().ads_spend)

        api_q = await session.execute(
            select(
                func.coalesce(func.sum(ApiCostLog.cost_thb), 0).label("api_cost"),
            ).where(
                ApiCostLog.created_at >= day_start,
                ApiCostLog.created_at < day_end,
            )
        )
        api_cost = float(api_q.one().api_cost)

    return {
        "date": day_start.strftime("%Y-%m-%d"),
        "ads_spend": ads_spend,
        "api_cost": api_cost,
        "total_expenses": ads_spend + api_cost,
    }


async def generate_daily_report() -> dict[str, Any]:
    """สร้างรายงานประจำวัน 23:00."""
    now = datetime.now(TH_TZ)
    today_utc = datetime.utcnow()

    revenue = await _get_daily_revenue(today_utc)
    expenses = await _get_daily_expenses(today_utc)

    total_revenue = revenue["total_revenue"]
    total_expenses = expenses["total_expenses"]
    profit = total_revenue - total_expenses
    margin = (profit / total_revenue * 100) if total_revenue > 0 else 0.0

    report = {
        "type": "daily",
        "date": now.strftime("%Y-%m-%d"),
        "revenue": revenue,
        "expenses": expenses,
        "profit": profit,
        "margin": round(margin, 2),
        "generated_at": now.isoformat(),
    }

    logger.info(
        "Daily report: revenue=%s, expenses=%s, profit=%s, margin=%.1f%%",
        format_thb(total_revenue), format_thb(total_expenses),
        format_thb(profit), margin,
    )

    return report


def format_daily_report_discord(report: dict[str, Any]) -> str:
    """Format daily report เป็น Discord message."""
    rev = report["revenue"]
    exp = report["expenses"]

    lines = [
        f"💰 **รายงานประจำวัน — {report['date']}**",
        "",
        "📈 **รายรับ**",
        f"  รวม: **{format_thb(rev['total_revenue'])}** ({rev['total_transactions']} รายการ)",
    ]

    for pm in rev.get("by_payment_method", []):
        lines.append(f"  • {pm['method']}: {format_thb(pm['amount'])} ({pm['count']} รายการ)")

    lines.extend([
        "",
        "📉 **รายจ่าย**",
        f"  Ads: {format_thb(exp['ads_spend'])}",
        f"  API: {format_thb(exp['api_cost'])}",
        f"  รวม: **{format_thb(exp['total_expenses'])}**",
        "",
        "💵 **สรุป**",
        f"  กำไร: **{format_thb(report['profit'])}**",
        f"  Margin: **{report['margin']:.1f}%**",
    ])

    return "\n".join(lines)


async def _get_weekly_metrics(week_start: datetime) -> dict[str, Any]:
    """คำนวณ metrics สำหรับรายงานประจำสัปดาห์."""
    week_end = week_start + timedelta(days=7)

    async with get_session() as session:
        # MRR (Monthly Recurring Revenue)
        active_subs_q = await session.execute(
            select(func.count(Subscription.id)).where(
                Subscription.status == SubscriptionStatus.ACTIVE,
            )
        )
        active_sub_count = active_subs_q.scalar() or 0

        revenue_q = await session.execute(
            select(func.coalesce(func.sum(Payment.amount), 0)).where(
                Payment.status == PaymentStatus.CONFIRMED,
                Payment.created_at >= week_start,
                Payment.created_at < week_end,
            )
        )
        weekly_revenue = float(revenue_q.scalar() or 0)

        mrr = weekly_revenue * (30 / 7)

        # ARPU (Average Revenue Per User)
        paying_users_q = await session.execute(
            select(func.count(func.distinct(Payment.user_id))).where(
                Payment.status == PaymentStatus.CONFIRMED,
                Payment.created_at >= week_start,
                Payment.created_at < week_end,
            )
        )
        paying_users = paying_users_q.scalar() or 0
        arpu = weekly_revenue / paying_users if paying_users > 0 else 0.0

        # Churn
        expired_q = await session.execute(
            select(func.count(Subscription.id)).where(
                Subscription.status == SubscriptionStatus.EXPIRED,
                Subscription.end_date >= week_start,
                Subscription.end_date < week_end,
            )
        )
        churned = expired_q.scalar() or 0
        churn_rate = (churned / active_sub_count * 100) if active_sub_count > 0 else 0.0

        # CAC (Customer Acquisition Cost)
        ads_spend_q = await session.execute(
            select(func.coalesce(func.sum(AdPerformance.spend), 0)).where(
                AdPerformance.date >= week_start,
                AdPerformance.date < week_end,
            )
        )
        ads_spend = float(ads_spend_q.scalar() or 0)

        new_users_q = await session.execute(
            select(func.count(User.id)).where(
                User.created_at >= week_start,
                User.created_at < week_end,
            )
        )
        new_users = new_users_q.scalar() or 0
        cac = ads_spend / new_users if new_users > 0 else 0.0

        # Best selling package
        best_pkg_q = await session.execute(
            select(
                Package.name,
                Package.tier,
                func.count(Payment.id).label("sales"),
                func.sum(Payment.amount).label("revenue"),
            )
            .join(Payment, Payment.package_id == Package.id)
            .where(
                Payment.status == PaymentStatus.CONFIRMED,
                Payment.created_at >= week_start,
                Payment.created_at < week_end,
            )
            .group_by(Package.id, Package.name, Package.tier)
            .order_by(func.count(Payment.id).desc())
            .limit(5)
        )
        best_packages = [
            {
                "name": row.name,
                "tier": row.tier.value,
                "sales": row.sales,
                "revenue": float(row.revenue),
            }
            for row in best_pkg_q.all()
        ]

    return {
        "period": f"{week_start.strftime('%Y-%m-%d')} — {week_end.strftime('%Y-%m-%d')}",
        "mrr": round(mrr, 2),
        "arpu": round(arpu, 2),
        "cac": round(cac, 2),
        "churn_rate": round(churn_rate, 2),
        "churned_count": churned,
        "active_subscribers": active_sub_count,
        "new_users": new_users,
        "weekly_revenue": weekly_revenue,
        "ads_spend": ads_spend,
        "paying_users": paying_users,
        "best_packages": best_packages,
    }


async def generate_weekly_report() -> dict[str, Any]:
    """สร้างรายงานประจำสัปดาห์ (จันทร์ 07:00)."""
    now = datetime.now(TH_TZ)
    week_start_utc = (datetime.utcnow() - timedelta(days=7)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )

    metrics = await _get_weekly_metrics(week_start_utc)

    messages = [
        {"role": "system", "content": (
            "คุณคือ 'นัท' Finance Agent ของบริษัทเจริญพร\n"
            "วิเคราะห์ข้อมูลการเงินประจำสัปดาห์แล้วให้ insight สั้นๆ 3-5 ข้อ\n"
            "ภาษาไทย กระชับ เน้น actionable insights\n"
            "ห้ามแต่งตัวเลขเอง ใช้เฉพาะข้อมูลที่ได้รับ"
        )},
        {"role": "user", "content": f"ข้อมูลสัปดาห์นี้:\n{_format_metrics_for_ai(metrics)}"},
    ]

    response = await call_openrouter(
        model=MODEL,
        messages=messages,
        caller=CALLER,
        temperature=0.5,
        max_tokens=600,
        metadata={"report_type": "weekly"},
    )

    ai_insights = response["choices"][0]["message"]["content"].strip()

    report = {
        "type": "weekly",
        "metrics": metrics,
        "ai_insights": ai_insights,
        "generated_at": now.isoformat(),
    }

    logger.info("Weekly report generated: MRR=%s, Churn=%.1f%%",
                format_thb(metrics["mrr"]), metrics["churn_rate"])

    return report


def _format_metrics_for_ai(metrics: dict[str, Any]) -> str:
    """Format metrics เป็น text สำหรับส่งให้ AI วิเคราะห์."""
    lines = [
        f"ช่วงเวลา: {metrics['period']}",
        f"MRR: {format_thb(metrics['mrr'])}",
        f"ARPU: {format_thb(metrics['arpu'])}",
        f"CAC: {format_thb(metrics['cac'])}",
        f"Churn Rate: {metrics['churn_rate']:.1f}% ({metrics['churned_count']} คน)",
        f"Active Subscribers: {metrics['active_subscribers']}",
        f"New Users: {metrics['new_users']}",
        f"Weekly Revenue: {format_thb(metrics['weekly_revenue'])}",
        f"Ads Spend: {format_thb(metrics['ads_spend'])}",
        f"Paying Users: {metrics['paying_users']}",
        "",
        "แพ็กเกจขายดี:",
    ]
    for pkg in metrics.get("best_packages", []):
        lines.append(f"  • {pkg['name']} ({pkg['tier']}): {pkg['sales']} ยอดขาย, {format_thb(pkg['revenue'])}")

    return "\n".join(lines)


def format_weekly_report_discord(report: dict[str, Any]) -> str:
    """Format weekly report เป็น Discord message."""
    m = report["metrics"]

    lines = [
        f"📊 **รายงานประจำสัปดาห์ — {m['period']}**",
        "",
        "📈 **Key Metrics**",
        f"  MRR: **{format_thb(m['mrr'])}**",
        f"  ARPU: **{format_thb(m['arpu'])}**",
        f"  CAC: **{format_thb(m['cac'])}**",
        f"  Churn: **{m['churn_rate']:.1f}%** ({m['churned_count']} คน)",
        "",
        f"👥 Active: **{m['active_subscribers']}** | New: **{m['new_users']}** | Paying: **{m['paying_users']}**",
        f"💰 Revenue: **{format_thb(m['weekly_revenue'])}** | Ads: **{format_thb(m['ads_spend'])}**",
        "",
        "🏆 **แพ็กเกจขายดี**",
    ]

    for pkg in m.get("best_packages", []):
        lines.append(f"  {pkg['name']}: {pkg['sales']} ยอด ({format_thb(pkg['revenue'])})")

    lines.extend(["", "🤖 **AI Insights**", report.get("ai_insights", "-")])

    return "\n".join(lines)


async def check_alerts() -> list[dict[str, Any]]:
    """ตรวจสอบ alert conditions อัตโนมัติ (BKK timezone)."""
    alerts: list[dict[str, Any]] = []
    # BKK timezone — convert to naive UTC for DB compare
    now_bkk = datetime.now(TH_TZ)
    today_bkk = now_bkk.replace(hour=0, minute=0, second=0, microsecond=0)
    today_start = today_bkk.astimezone(timezone.utc).replace(tzinfo=None)
    yesterday_start = today_start - timedelta(days=1)

    # 1. Revenue drop > 30%
    today_rev = await _get_daily_revenue(today_start)
    yesterday_rev = await _get_daily_revenue(yesterday_start)

    if yesterday_rev["total_revenue"] > 0:
        drop = 1 - (today_rev["total_revenue"] / yesterday_rev["total_revenue"])
        if drop > ALERT_REVENUE_DROP_THRESHOLD:
            alerts.append({
                "type": "revenue_drop",
                "severity": "high",
                "message": (
                    f"🚨 รายรับลดลง {drop*100:.1f}% จากเมื่อวาน!\n"
                    f"วันนี้: {format_thb(today_rev['total_revenue'])} "
                    f"vs เมื่อวาน: {format_thb(yesterday_rev['total_revenue'])}"
                ),
            })

    # 2. Ads overspend > 20% of budget
    async with get_session() as session:
        campaigns_q = await session.execute(
            select(AdCampaign).where(
                AdCampaign.status == CampaignStatus.ACTIVE,
                AdCampaign.budget > 0,
            )
        )
        active_campaigns = campaigns_q.scalars().all()

    for camp in active_campaigns:
        if camp.budget > 0:
            overspend_ratio = float(camp.spent) / float(camp.budget)
            if overspend_ratio > (1 + ALERT_ADS_OVERSPEND_THRESHOLD):
                alerts.append({
                    "type": "ads_overspend",
                    "severity": "medium",
                    "campaign_id": camp.id,
                    "message": (
                        f"⚠️ Campaign '{camp.name}' ใช้งบเกิน {(overspend_ratio-1)*100:.0f}%\n"
                        f"ใช้ไป: {format_thb(camp.spent)} / งบ: {format_thb(camp.budget)}"
                    ),
                })

    # 3. Churn > 10 people in 24 hours
    async with get_session() as session:
        churn_24h_q = await session.execute(
            select(func.count(Subscription.id)).where(
                Subscription.status == SubscriptionStatus.EXPIRED,
                Subscription.end_date >= now - timedelta(hours=24),
                Subscription.end_date <= now,
            )
        )
        churn_24h = churn_24h_q.scalar() or 0

    if churn_24h > ALERT_CHURN_THRESHOLD:
        alerts.append({
            "type": "high_churn",
            "severity": "high",
            "message": (
                f"🚨 Churn สูงผิดปกติ! {churn_24h} คนหมดอายุใน 24 ชม.\n"
                f"(threshold: {ALERT_CHURN_THRESHOLD} คน)"
            ),
        })

    if alerts:
        logger.warning("Finance alerts triggered: %d alerts", len(alerts))
    else:
        logger.info("No finance alerts")

    return alerts


async def _send_discord(channel_env_value: str, content: str) -> bool:
    """ส่งข้อความไป Discord channel via Bot API."""
    token = DISCORD_BOT_TOKEN
    ch = channel_env_value
    if not token or not ch:
        return False
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"https://discord.com/api/v10/channels/{ch}/messages",
                headers={"Authorization": f"Bot {token}", "Content-Type": "application/json"},
                json={"content": content},
            )
            resp.raise_for_status()
        return True
    except Exception as exc:
        logger.error("Failed to send Discord message: %s", exc)
        return False


async def send_discord_report(
    content: str,
    channel_id: str | None = None,
) -> bool:
    """ส่งรายงานไป Discord #finance."""
    ch = channel_id or DISCORD_CH_FINANCE
    if not ch:
        logger.warning("No Discord channel configured for finance reports")
        return False
    return await _send_discord(ch, content)


async def send_alert(alert: dict[str, Any]) -> bool:
    """ส่ง alert ไป Discord #alerts."""
    ch = DISCORD_CH_ALERTS or DISCORD_CH_FINANCE
    if not ch:
        logger.warning("No Discord channel configured for alerts")
        return False

    severity_emoji = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(
        alert.get("severity", "medium"), "🟡"
    )

    content = (
        f"{severity_emoji} **Finance Alert** [{alert.get('type', 'unknown')}]\n"
        f"{alert.get('message', '-')}"
    )

    return await _send_discord(ch, content)


async def run_daily_routine() -> None:
    """รัน routine ประจำวัน 23:00."""
    report = await generate_daily_report()
    message = format_daily_report_discord(report)
    await send_discord_report(message)

    alerts = await check_alerts()
    for alert in alerts:
        await send_alert(alert)

    logger.info("Daily finance routine completed")


async def run_weekly_routine() -> None:
    """รัน routine ประจำสัปดาห์ จันทร์ 07:00."""
    report = await generate_weekly_report()
    message = format_weekly_report_discord(report)
    await send_discord_report(message)

    logger.info("Weekly finance routine completed")
