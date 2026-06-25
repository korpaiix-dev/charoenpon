"""Discord notification helper — call from any Python code to post into a marketing feed channel.

Uses Discord HTTP API directly (no library dep). Designed to fail silently —
never crash the calling code.
"""
from __future__ import annotations
import asyncio
import logging
import os
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

DISCORD_TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "")
API = "https://discord.com/api/v10"

# Marketer → feed channel ID
_FEED_CHANNELS = {
    "Ivy":  os.environ.get("DISCORD_MARKETING_FEED_IVY_CHANNEL_ID", ""),
    "Wasu": os.environ.get("DISCORD_MARKETING_FEED_WASU_CHANNEL_ID", ""),
    "Pai":  os.environ.get("DISCORD_MARKETING_FEED_PAI_CHANNEL_ID", ""),
}

# #marketing-รวม (daily digest + leaderboard)
_OVERVIEW_CHANNEL = os.environ.get("DISCORD_MARKETING_OVERVIEW_CHANNEL_ID", "")


async def post_to_channel(channel_id: str, content: str) -> bool:
    """Post a message to a Discord channel. Returns True on success."""
    if not channel_id or not DISCORD_TOKEN:
        logger.warning("discord_notify: missing channel_id or token")
        return False
    # Discord limit: 2000 chars
    if len(content) > 1990:
        content = content[:1990] + "..."
    try:
        async with httpx.AsyncClient(timeout=10.0) as cli:
            r = await cli.post(
                f"{API}/channels/{channel_id}/messages",
                json={"content": content},
                headers={"Authorization": f"Bot {DISCORD_TOKEN}"},
            )
            if r.status_code not in (200, 201):
                logger.warning("discord_notify: status %s: %s", r.status_code, r.text[:200])
                return False
        return True
    except Exception as exc:
        logger.exception("discord_notify post failed: %s", exc)
        return False


async def post_embed(channel_id: str, embed: dict, content: str = "") -> bool:
    """Post a message with Discord embed."""
    if not channel_id or not DISCORD_TOKEN:
        return False
    try:
        async with httpx.AsyncClient(timeout=10.0) as cli:
            r = await cli.post(
                f"{API}/channels/{channel_id}/messages",
                json={"content": content, "embeds": [embed]},
                headers={"Authorization": f"Bot {DISCORD_TOKEN}"},
            )
            if r.status_code not in (200, 201):
                logger.warning("post_embed: status %s: %s", r.status_code, r.text[:200])
                return False
        return True
    except Exception as exc:
        logger.exception("post_embed failed: %s", exc)
        return False


_PLATFORM_EMOJI = {
    "telegram": "✈️", "twitter": "🐦", "x": "🐦",
    "facebook": "📘", "tiktok": "🎵", "instagram": "📸",
    "youtube": "▶️", "line": "💬",
}


async def notify_marketer_join(
    marketer: str,
    platform: str,
    group_title: str,
    telegram_id: int,
    tg_username: Optional[str],
    tg_first_name: Optional[str],
    link_id: int,
    total_joins_for_link: int,
) -> bool:
    """Notify marketer's feed channel that someone joined via their link."""
    ch = _FEED_CHANNELS.get(marketer)
    if not ch:
        logger.warning("notify_marketer_join: no channel for marketer=%s", marketer)
        return False
    name = tg_first_name or tg_username or f"tg_{telegram_id}"
    handle = f"@{tg_username}" if tg_username else str(telegram_id)
    plat_emoji = _PLATFORM_EMOJI.get(platform.lower(), "🔗")
    embed = {
        "color": 0x10b981,  # green
        "title": f"🔥 {name}",
        "description": f"{plat_emoji} **{platform}** · ลิ้ง `#{link_id}` · รวม **{total_joins_for_link}** คน",
        "footer": {"text": f"@{tg_username}" if tg_username else f"tg: {telegram_id}"},
    }
    return await post_embed(ch, embed)


async def notify_marketer_conversion(
    marketer: str,
    platform: str,
    telegram_id: int,
    tg_username: Optional[str],
    tg_first_name: Optional[str],
    amount: float,
    tier: str,
    days_since_join: int,
    link_id: int,
    marketer_month_count: int,
    marketer_month_revenue: float,
) -> None:
    """Notify marketer that one of their leads paid → big celebration."""
    ch = _FEED_CHANNELS.get(marketer)
    if not ch:
        return
    # Friendly tier label
    tier_str = str(tier).upper()
    tier_map = {
        "TIER_100": "VIP 100 (ห้องชัก)",
        "TIER_300": "VIP 300 (ทั่วไป)",
        "TIER_500": "VIP 500 (OnlyFans + หายาก)",
        "TIER_1299": "VIP 1,299 (พรีเมียม)",
        "TIER_2499": "VIP 2,499 (Storage + Summer)",
        "TIER_FREE": "Free Trial",
        "GACHA": "Gacha Pack",
    }
    tier_label = tier_map.get(tier_str, str(tier).replace("TIER_", "VIP "))
    
    name = tg_first_name or tg_username or f"tg_{telegram_id}"
    handle = f"@{tg_username}" if tg_username else f"`{telegram_id}`"
    msg = (
        f"💰💰💰 **CONVERSION!** 💰💰💰\n"
        f"└ 👤 {name} ({handle})\n"
        f"└ 💎 ซื้อ **{tier_label}** = **฿{amount:,.0f}**\n"
        f"└ 📥 มาจากลิ้ง #{link_id} ({platform}) — {days_since_join} วันก่อน\n"
        f"└ 🏆 {marketer} เดือนนี้: **{marketer_month_count}** conversions, **฿{marketer_month_revenue:,.0f}**\n"
        f"\n"
        f"เก่งมาก {marketer}! 🎉✨"
    )
    await post_to_channel(ch, msg)


async def notify_overview(content: str) -> None:
    """Post to #marketing-รวม (daily digest, leaderboard, etc.)"""
    if _OVERVIEW_CHANNEL:
        await post_to_channel(_OVERVIEW_CHANNEL, content)
