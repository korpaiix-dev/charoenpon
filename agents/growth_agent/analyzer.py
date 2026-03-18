"""Growth Analyzer (แมน) - Weekly Report + Audience Brief + Content Brief.

Model: google/gemini-2.5-flash ผ่าน OpenRouter
- Weekly Report จันทร์ 08:00
- Audience Brief → เจมส์ (Marketing)
- Content Brief → มิน (Content)
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

import httpx

from shared.api_cost_tracker import call_openrouter
from shared.utils import TH_TZ, format_thb

from agents.growth_agent.collector import collect_all

logger = logging.getLogger(__name__)

MODEL = "google/gemini-2.5-flash"
CALLER = "growth_agent/analyzer"

DISCORD_BOT_TOKEN_ENV: str = os.environ.get("DISCORD_BOT_TOKEN", "")
DISCORD_CH_AD_PERFORMANCE: str = os.environ.get("DISCORD_CH_AD_PERFORMANCE", "")


def _format_data_for_ai(data: dict[str, Any]) -> str:
    """Format collected data เป็น text สำหรับส่งให้ AI วิเคราะห์."""
    marketing = data.get("marketing", {})
    sales = data.get("sales", {})
    retention = data.get("retention", {})
    content = data.get("content", {})
    finance = data.get("finance", {})

    lines = [
        "=== MARKETING DATA ===",
        f"Campaigns: {len(marketing.get('campaigns', []))}",
    ]
    for c in marketing.get("campaigns", [])[:5]:
        lines.append(
            f"  • {c['name']}: CTR={c['ctr']}%, CPC=฿{c['cpc']:.2f}, "
            f"Conversions={c['conversions']}, Spent=฿{c['spent']:.2f}"
        )

    lines.append(f"Leads by source: {json.dumps(marketing.get('leads_by_source', {}), ensure_ascii=False)}")

    lines.extend([
        "",
        "=== SALES DATA ===",
        f"New subs: {sales.get('new_subscriptions', 0)}",
        f"Revenue: {format_thb(sales.get('total_revenue', 0))}",
        f"Conversion rate: {sales.get('conversion_rate', 0)}%",
    ])
    for pkg in sales.get("sales_by_package", [])[:5]:
        lines.append(f"  • {pkg['name']}: {pkg['sales']} sales, {format_thb(pkg['revenue'])}")

    lines.extend([
        "",
        "=== RETENTION DATA ===",
        f"Active subs: {retention.get('active_subscribers', 0)}",
        f"Expired: {retention.get('expired', 0)}",
        f"Churn rate: {retention.get('churn_rate', 0)}%",
        f"Renewals: {retention.get('renewals', 0)}",
    ])

    lines.extend([
        "",
        "=== CONTENT DATA ===",
    ])
    for g in content.get("content_by_group", []):
        lines.append(f"  • {g['group']}: {g['sent']}/{g['total']} sent, {g['errors']} errors")

    lines.extend([
        "",
        "=== FINANCE DATA ===",
        f"Revenue: {format_thb(finance.get('revenue', 0))}",
        f"Ads spend: {format_thb(finance.get('ads_spend', 0))}",
        f"API cost: {format_thb(finance.get('api_cost', 0))}",
        f"Profit: {format_thb(finance.get('profit', 0))}",
        f"Margin: {finance.get('margin', 0)}%",
    ])

    return "\n".join(lines)


async def generate_weekly_growth_report(data: dict[str, Any] | None = None) -> dict[str, Any]:
    """สร้าง Weekly Growth Report (จันทร์ 08:00)."""
    if data is None:
        data = await collect_all(days=7)

    formatted = _format_data_for_ai(data)

    messages = [
        {"role": "system", "content": (
            "คุณคือ 'แมน' Growth Analyst ของบริษัทเจริญพร\n"
            "วิเคราะห์ข้อมูลรายสัปดาห์แล้วสร้าง Growth Report\n\n"
            "รูปแบบ report:\n"
            "1. Executive Summary (3 บรรทัด)\n"
            "2. Key Wins (สิ่งที่ทำได้ดี 2-3 ข้อ)\n"
            "3. Areas of Concern (สิ่งที่ต้องปรับปรุง 2-3 ข้อ)\n"
            "4. Action Items (สิ่งที่ต้องทำสัปดาห์หน้า 3-5 ข้อ)\n"
            "5. KPI Targets สัปดาห์หน้า\n\n"
            "เขียนภาษาไทย กระชับ ใช้ตัวเลขจริง ห้ามแต่งข้อมูลเอง"
        )},
        {"role": "user", "content": f"ข้อมูลสัปดาห์นี้:\n{formatted}"},
    ]

    response = await call_openrouter(
        model=MODEL,
        messages=messages,
        caller=CALLER,
        temperature=0.5,
        max_tokens=1500,
        metadata={"report_type": "weekly_growth"},
    )

    report_text = response["choices"][0]["message"]["content"].strip()

    report = {
        "type": "weekly_growth",
        "raw_data": data,
        "report_text": report_text,
        "generated_at": datetime.now(TH_TZ).isoformat(),
    }

    logger.info("Weekly growth report generated")
    return report


async def generate_audience_brief(data: dict[str, Any] | None = None) -> dict[str, Any]:
    """สร้าง Audience Brief สำหรับเจมส์ (Marketing Agent)."""
    if data is None:
        data = await collect_all(days=7)

    formatted = _format_data_for_ai(data)

    messages = [
        {"role": "system", "content": (
            "คุณคือ 'แมน' Growth Analyst ของบริษัทเจริญพร\n"
            "สร้าง Audience Brief สำหรับทีม Marketing (เจมส์)\n\n"
            "รูปแบบ brief:\n"
            "1. Target Audience Profile (ใครคือลูกค้าหลัก)\n"
            "2. Best Performing Channels (ช่องทางไหนดีที่สุด)\n"
            "3. Ad Copy Recommendations (แนะนำโทนเสียง/สไตล์)\n"
            "4. Budget Allocation (แนะนำการแบ่งงบ)\n"
            "5. Audiences to Avoid (กลุ่มที่ไม่ควรเจาะ)\n\n"
            "สำคัญ: ห้ามส่งข้อมูล 18+ ไม่ว่าจะรูปแบบใด\n"
            "ให้ข้อมูลเชิง insight เท่านั้น ไม่ต้องส่ง raw data\n"
            "เขียนภาษาไทย กระชับ actionable"
        )},
        {"role": "user", "content": f"ข้อมูลสัปดาห์นี้:\n{formatted}"},
    ]

    response = await call_openrouter(
        model=MODEL,
        messages=messages,
        caller=CALLER,
        temperature=0.6,
        max_tokens=1000,
        metadata={"brief_type": "audience"},
    )

    brief_text = response["choices"][0]["message"]["content"].strip()

    brief = {
        "type": "audience_brief",
        "target": "marketing_agent",
        "brief_text": brief_text,
        "generated_at": datetime.now(TH_TZ).isoformat(),
    }

    logger.info("Audience brief generated for Marketing Agent")
    return brief


async def generate_content_brief(data: dict[str, Any] | None = None) -> dict[str, Any]:
    """สร้าง Content Brief สำหรับมิน (Content Agent)."""
    if data is None:
        data = await collect_all(days=7)

    formatted = _format_data_for_ai(data)

    messages = [
        {"role": "system", "content": (
            "คุณคือ 'แมน' Growth Analyst ของบริษัทเจริญพร\n"
            "สร้าง Content Brief สำหรับทีม Content (มิน)\n\n"
            "รูปแบบ brief:\n"
            "1. Content Performance Summary (กลุ่มไหนทำได้ดี)\n"
            "2. Top Content Types (ประเภทคอนเทนต์ยอดนิยม)\n"
            "3. Posting Time Analysis (เวลาไหนได้ engagement ดี)\n"
            "4. Content Recommendations สัปดาห์หน้า\n"
            "5. Groups ที่ต้องเพิ่มคอนเทนต์\n\n"
            "ข้อมูลที่ให้เป็น insight เท่านั้น\n"
            "เขียนภาษาไทย กระชับ actionable"
        )},
        {"role": "user", "content": f"ข้อมูลสัปดาห์นี้:\n{formatted}"},
    ]

    response = await call_openrouter(
        model=MODEL,
        messages=messages,
        caller=CALLER,
        temperature=0.6,
        max_tokens=1000,
        metadata={"brief_type": "content"},
    )

    brief_text = response["choices"][0]["message"]["content"].strip()

    brief = {
        "type": "content_brief",
        "target": "content_agent",
        "brief_text": brief_text,
        "generated_at": datetime.now(TH_TZ).isoformat(),
    }

    logger.info("Content brief generated for Content Agent")
    return brief


def format_growth_report_discord(report: dict[str, Any]) -> str:
    """Format growth report เป็น Discord message."""
    lines = [
        "📊 **Weekly Growth Report**",
        f"📅 Generated: {report.get('generated_at', '-')}",
        "━━━━━━━━━━━━━━━━━━━━",
        "",
        report.get("report_text", "-"),
    ]
    return "\n".join(lines)


async def send_discord_growth_report(content: str) -> bool:
    """ส่ง growth report ไป Discord #ad-performance via Bot API."""
    token = DISCORD_BOT_TOKEN_ENV
    ch = DISCORD_CH_AD_PERFORMANCE
    if not token or not ch:
        logger.warning("No Discord Bot token/channel configured for growth reports")
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
        logger.error("Failed to send growth report to Discord: %s", exc)
        return False


async def analyze_teaser_performance() -> str:
    """วิเคราะห์ performance ของ teaser clicks ใน 7 วันที่ผ่านมา."""
    from datetime import timedelta
    from sqlalchemy import text
    from shared.database import get_session

    now = datetime.now(TH_TZ)
    week_ago = now - timedelta(days=7)

    try:
        async with get_session() as session:
            # Query by round_time
            round_result = await session.execute(
                text(
                    "SELECT round_time, COUNT(*) as clicks, SUM(CASE WHEN converted THEN 1 ELSE 0 END) as conversions "
                    "FROM teaser_clicks WHERE created_at >= :since GROUP BY round_time ORDER BY clicks DESC"
                ),
                {"since": week_ago.replace(tzinfo=None)},
            )
            by_round = round_result.fetchall()

            # Query by group_index
            group_result = await session.execute(
                text(
                    "SELECT group_index, COUNT(*) as clicks, SUM(CASE WHEN converted THEN 1 ELSE 0 END) as conversions "
                    "FROM teaser_clicks WHERE created_at >= :since GROUP BY group_index ORDER BY clicks DESC"
                ),
                {"since": week_ago.replace(tzinfo=None)},
            )
            by_group = group_result.fetchall()

            # Total stats
            total_result = await session.execute(
                text(
                    "SELECT COUNT(*) as total_clicks, SUM(CASE WHEN converted THEN 1 ELSE 0 END) as total_conversions "
                    "FROM teaser_clicks WHERE created_at >= :since"
                ),
                {"since": week_ago.replace(tzinfo=None)},
            )
            totals = total_result.fetchone()

    except Exception as exc:
        logger.error("analyze_teaser_performance DB error: %s", exc)
        return ""

    total_clicks = totals.total_clicks or 0
    total_conversions = totals.total_conversions or 0
    overall_cvr = (total_conversions / total_clicks * 100) if total_clicks > 0 else 0.0

    lines = [
        "📊 **Teaser Performance (7 วันที่ผ่านมา)**",
        "━━━━━━━━━━━━━━━━━━━━",
        f"🔢 Total Clicks: **{total_clicks}** | Conversions: **{total_conversions}** | CVR: **{overall_cvr:.1f}%**",
        "",
        "**⏰ แยกตามรอบเวลา:**",
    ]

    for row in by_round:
        clicks = row.clicks or 0
        convs = row.conversions or 0
        cvr = (convs / clicks * 100) if clicks > 0 else 0.0
        lines.append(f"• รอบ {row.round_time}: {clicks} คลิก → {convs} สมัคร ({cvr:.1f}%)")

    if not by_round:
        lines.append("• ยังไม่มีข้อมูล")

    lines.extend(["", "**👥 แยกตามกลุ่ม (Top 5):**"])
    for row in list(by_group)[:5]:
        clicks = row.clicks or 0
        convs = row.conversions or 0
        cvr = (convs / clicks * 100) if clicks > 0 else 0.0
        lines.append(f"• กลุ่ม #{row.group_index}: {clicks} คลิก → {convs} สมัคร ({cvr:.1f}%)")

    if not by_group:
        lines.append("• ยังไม่มีข้อมูล")

    return "\n".join(lines)


async def run_weekly_analysis() -> dict[str, Any]:
    """รัน weekly analysis ครบทุก output (จันทร์ 08:00)."""
    data = await collect_all(days=7)

    growth_report = await generate_weekly_growth_report(data)
    audience_brief = await generate_audience_brief(data)
    content_brief = await generate_content_brief(data)

    report_msg = format_growth_report_discord(growth_report)
    await send_discord_growth_report(report_msg)

    # Teaser performance analysis
    teaser_msg = await analyze_teaser_performance()
    if teaser_msg:
        await send_discord_growth_report(teaser_msg)
        logger.info("Teaser performance analysis sent to Discord")

    logger.info("Weekly analysis completed: growth report + audience brief + content brief + teaser performance")

    return {
        "growth_report": growth_report,
        "audience_brief": audience_brief,
        "content_brief": content_brief,
    }
