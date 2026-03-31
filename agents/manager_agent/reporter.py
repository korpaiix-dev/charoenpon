"""Manager Reporter — สร้าง report + ส่ง Discord embed."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from agents.manager_agent.analyzer import get_daily_stats, get_weekly_stats
from shared.api_cost_tracker import call_openrouter

logger = logging.getLogger(__name__)

TH_TZ = timezone(timedelta(hours=7))

DISCORD_BOT_TOKEN: str = os.environ.get("DISCORD_BOT_TOKEN", "")
DISCORD_CH_MANAGER: str = os.environ.get("DISCORD_CH_MANAGER", "")

AI_MODEL = "anthropic/claude-sonnet-4-20250514"
CALLER = "manager_agent/reporter"


def _format_thb(amount: float) -> str:
    return f"฿{amount:,.2f}"


async def _send_discord_embed(channel_id: str, embed: dict[str, Any]) -> bool:
    """ส่ง embed ไป Discord channel via Bot API."""
    if not DISCORD_BOT_TOKEN or not channel_id:
        logger.warning("Discord credentials not configured for manager agent")
        return False
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"https://discord.com/api/v10/channels/{channel_id}/messages",
                headers={
                    "Authorization": f"Bot {DISCORD_BOT_TOKEN}",
                    "Content-Type": "application/json",
                },
                json={"embeds": [embed]},
            )
            resp.raise_for_status()
        logger.info("Discord embed sent to channel %s", channel_id)
        return True
    except Exception as exc:
        logger.error("Failed to send Discord embed: %s", exc)
        return False


async def send_daily_report() -> None:
    """ดึงข้อมูล + ส่ง daily report embed สีทอง."""
    stats = await get_daily_stats()

    description = (
        f"📈 **ภาพรวมวันนี้ ({stats['date']})**\n\n"
        f"👥 **สมาชิก**\n"
        f"├ ทั้งหมด: **{stats['total_users']:,}**\n"
        f"├ Active: **{stats['active_subs']:,}**\n"
        f"├ ใกล้หมดอายุ (7 วัน): **{stats['expiring_7d']:,}**\n"
        f"└ หมดอายุวันนี้: **{stats['expired_today']:,}**\n\n"
        f"💰 **รายได้**\n"
        f"├ วันนี้: **{_format_thb(stats['revenue_today'])}**\n"
        f"├ เดือนนี้: **{_format_thb(stats['revenue_month'])}**\n"
        f"└ Payments รอตรวจ: **{stats['pending_payments']:,}**\n\n"
        f"📢 **Content**\n"
        f"├ Teaser คลิกวันนี้: **{stats['clicks_today']:,}**\n"
        f"├ กลุ่มที่คลิกเยอะสุด: **{stats['top_group']}**\n"
        f"└ รอบเวลาที่ดีสุด: **{stats['best_time']}**\n\n"
        f"🛡️ **Moderation**\n"
        f"├ แบนวันนี้: **{stats['bans_today']:,}**\n"
        f"└ กลุ่มบิน: **{stats['migrations_today']:,}**\n\n"
        f"📊 **Broadcast**\n"
        f"├ ส่งวันนี้: **{stats['broadcasts_today']:,}**\n"
        f"└ สำเร็จ/ล้มเหลว: **{stats['broadcast_success']:,}/{stats['broadcast_failed']:,}**"
    )

    embed = {
        "title": "📊 Daily Report — Manager",
        "description": description,
        "color": 0xF1C40F,  # สีทอง
        "footer": {"text": f"Generated at {datetime.now(TH_TZ).strftime('%H:%M %d/%m/%Y')}"},
    }

    await _send_discord_embed(DISCORD_CH_MANAGER, embed)
    logger.info("Daily manager report sent")


async def send_weekly_analysis() -> None:
    """ดึงข้อมูล 7 วัน + AI วิเคราะห์ + ส่ง weekly embed สีม่วง."""
    stats = await get_weekly_stats()

    # --- ส่งให้ AI วิเคราะห์ ---
    data_summary = (
        f"ช่วง: {stats['period_start']} - {stats['period_end']}\n"
        f"สมาชิกใหม่: +{stats['new_users']}\n"
        f"รายได้รวม: {_format_thb(stats['weekly_revenue'])}\n"
        f"Teaser คลิก: {stats['weekly_clicks']}\n"
        f"Conversion: {stats['conversion_rate']}%\n"
        f"Active subs: {stats['active_subs']}\n"
        f"หมดอายุสัปดาห์นี้: {stats['expired_week']}\n"
        f"Broadcasts: {stats['broadcasts_total']} (สำเร็จ {stats['broadcast_success']}, ล้มเหลว {stats['broadcast_failed']})\n"
        f"แบน: {stats['bans_week']}"
    )

    # โหลด knowledge file
    knowledge = ""
    try:
        with open("/app/data/manager_knowledge.md") as f:
            knowledge = f.read()
    except Exception:
        pass

    ai_prompt = (
        "คุณเป็น Business Manager ของธุรกิจขายสมาชิก VIP Telegram 18+ ชื่อเจริญพร "
        "วิเคราะห์ข้อมูลนี้แล้วเสนอ 3 action items ที่ควรทำ, จุดที่ต้องระวัง, และโอกาสที่ควรคว้า "
        "ตอบเป็นภาษาไทย กระชับ\n\n"
        "ตอบในรูปแบบ JSON:\n"
        '{"actions": ["...", "...", "..."], "warnings": ["...", "..."], "opportunities": ["...", "..."]}\n\n'
        f"ข้อมูลสัปดาห์นี้:\n{data_summary}\n\n"
        f"ข้อมูลอ้างอิงกลยุทธ์:\n{knowledge[:2000]}"
    )

    try:
        response = await call_openrouter(
            model=AI_MODEL,
            messages=[{"role": "user", "content": ai_prompt}],
            caller=CALLER,
            temperature=0.5,
            max_tokens=800,
            metadata={"report_type": "weekly_manager"},
        )
        ai_text = response["choices"][0]["message"]["content"].strip()

        # Parse JSON from AI response
        # Try to extract JSON from response (may be wrapped in ```json ... ```)
        json_str = ai_text
        if "```json" in json_str:
            json_str = json_str.split("```json")[1].split("```")[0].strip()
        elif "```" in json_str:
            json_str = json_str.split("```")[1].split("```")[0].strip()

        ai_data = json.loads(json_str)
        actions = ai_data.get("actions", ["ไม่มีข้อเสนอ"])
        warnings = ai_data.get("warnings", ["ไม่มี"])
        opportunities = ai_data.get("opportunities", ["ไม่มี"])

    except Exception as exc:
        logger.error("AI analysis failed: %s", exc)
        # Fallback: use raw text or defaults
        actions = ["ไม่สามารถวิเคราะห์ได้ (AI error)"]
        warnings = ["-"]
        opportunities = ["-"]

    # --- Build embed ---
    actions_text = "\n".join(f"{i+1}. {a}" for i, a in enumerate(actions[:3]))
    warnings_text = "\n".join(f"- {w}" for w in warnings[:3])
    opportunities_text = "\n".join(f"- {o}" for o in opportunities[:3])

    description = (
        f"📅 **สัปดาห์ที่ผ่านมา ({stats['period_start']} - {stats['period_end']})**\n\n"
        f"📈 **สรุปตัวเลข**\n"
        f"├ สมาชิกใหม่: **+{stats['new_users']:,}**\n"
        f"├ รายได้รวม: **{_format_thb(stats['weekly_revenue'])}**\n"
        f"├ Teaser คลิกรวม: **{stats['weekly_clicks']:,}**\n"
        f"└ อัตรา conversion: **{stats['conversion_rate']}%**\n\n"
        f"🎯 **Action Items**\n{actions_text}\n\n"
        f"⚠️ **จุดที่ต้องระวัง**\n{warnings_text}\n\n"
        f"💡 **โอกาส**\n{opportunities_text}"
    )

    embed = {
        "title": "🧠 Weekly Analysis — Manager",
        "description": description,
        "color": 0x9B59B6,  # สีม่วง
        "footer": {"text": f"Generated at {datetime.now(TH_TZ).strftime('%H:%M %d/%m/%Y')}"},
    }

    await _send_discord_embed(DISCORD_CH_MANAGER, embed)
    logger.info("Weekly manager analysis sent")
