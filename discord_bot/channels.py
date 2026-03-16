"""Channel Registry - mapping ทุกห้องใน Discord Server บริษัทเจริญพร."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class ChannelInfo:
    """Discord channel metadata."""
    name: str
    env_key: str
    description: str


# Channel definitions — ทุกห้องในระบบ
CHANNEL_DEFS: dict[str, ChannelInfo] = {
    "daily-report": ChannelInfo(
        name="daily-report",
        env_key="DISCORD_CH_DAILY_REPORT",
        description="รายงานประจำวัน — revenue, members, costs",
    ),
    "ad-approval": ChannelInfo(
        name="ad-approval",
        env_key="DISCORD_CH_AD_APPROVAL",
        description="อนุมัติ/reject แอดโฆษณา",
    ),
    "alerts": ChannelInfo(
        name="alerts",
        env_key="DISCORD_CH_ALERTS",
        description="แจ้งเตือนเหตุการณ์สำคัญ",
    ),
    "member-expiring": ChannelInfo(
        name="member-expiring",
        env_key="DISCORD_CH_MEMBER_EXPIRING",
        description="สมาชิกที่ใกล้หมดอายุ",
    ),
    "broadcast-approve": ChannelInfo(
        name="broadcast-approve",
        env_key="DISCORD_CH_BROADCAST_APPROVE",
        description="อนุมัติ broadcast ก่อนส่ง",
    ),
    "finance": ChannelInfo(
        name="finance",
        env_key="DISCORD_CH_FINANCE",
        description="รายงานการเงิน — payment, refund",
    ),
    "growth-insights": ChannelInfo(
        name="growth-insights",
        env_key="DISCORD_CH_GROWTH_INSIGHTS",
        description="วิเคราะห์การเติบโต — new members, churn, conversion",
    ),
    "system-logs": ChannelInfo(
        name="system-logs",
        env_key="DISCORD_CH_SYSTEM_LOGS",
        description="Log ระบบ — errors, bot status, deploy",
    ),
    "ad-performance": ChannelInfo(
        name="ad-performance",
        env_key="DISCORD_CH_AD_PERFORMANCE",
        description="ผลลัพธ์โฆษณา — impressions, clicks, conversions",
    ),
    "sheets-updates": ChannelInfo(
        name="sheets-updates",
        env_key="DISCORD_CH_SHEETS_UPDATES",
        description="อัปเดต Google Sheets — sync status",
    ),
}


def get_channel_id(slug: str) -> int | None:
    """Get Discord channel ID from environment variable by slug name.

    Returns None if the channel is not configured.
    """
    info = CHANNEL_DEFS.get(slug)
    if not info:
        return None
    raw = os.environ.get(info.env_key, "")
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def get_all_channel_ids() -> dict[str, int]:
    """Get all configured channel IDs as {slug: channel_id}."""
    result = {}
    for slug in CHANNEL_DEFS:
        cid = get_channel_id(slug)
        if cid:
            result[slug] = cid
    return result


# Google Sheets links — ใช้กับคำสั่ง !sheet
SHEETS_LINKS: dict[str, str] = {
    "revenue": os.environ.get("SHEET_REVENUE", ""),
    "members": os.environ.get("SHEET_MEMBERS", ""),
    "costs": os.environ.get("SHEET_COSTS", ""),
    "ads": os.environ.get("SHEET_ADS", ""),
    "leads": os.environ.get("SHEET_LEADS", ""),
}
