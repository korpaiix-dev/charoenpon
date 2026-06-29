"""Discord notification helper — call from any Python code to post into a marketing feed channel.

Uses Discord HTTP API directly (no library dep). Designed to fail silently —
never crash the calling code.
"""
from __future__ import annotations
import asyncio
import logging
import os
from datetime import datetime, timezone
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
    """Post a message with Discord embed.

    `embed` may contain any of the standard Discord embed fields:
    title, description, color, fields (list of {name, value, inline}),
    footer ({text, icon_url}), author, thumbnail, image, timestamp, url.

    Returns True on HTTP 200/201.
    """
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


def _platform_display(platform: str) -> str:
    """Return "{emoji} {platform}" with a friendly emoji."""
    plat = (platform or "").lower()
    emoji = _PLATFORM_EMOJI.get(plat, "🔗")
    return f"{emoji} {platform or 'อื่นๆ'}"


def _customer_label(
    tg_first_name: Optional[str],
    tg_username: Optional[str],
    telegram_id: int,
) -> str:
    """e.g. 'Kit (@kit_pn)' or 'Kit (123456789)' or 'tg_123456789'."""
    name = tg_first_name or tg_username or f"tg_{telegram_id}"
    if tg_username:
        return f"{name} (@{tg_username})"
    return f"{name} ({telegram_id})"


# ---------------------------------------------------------------------------
# Public notification API
# ---------------------------------------------------------------------------

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
    """Notify marketer's feed channel that someone joined via their link.

    Renders a green embed with structured fields (easier to scan than the
    old flat └ list).
    """
    ch = _FEED_CHANNELS.get(marketer)
    if not ch:
        logger.warning("notify_marketer_join: no channel for marketer=%s", marketer)
        return False
    customer = _customer_label(tg_first_name, tg_username, telegram_id)
    source = f"{_platform_display(platform)} · ลิ้ง `#{link_id}`"
    embed = {
        "color": 0x10b981,  # green
        "title": "🔥 คนเข้าใหม่!",
        "fields": [
            {"name": "👤 ลูกค้า", "value": customer, "inline": False},
            {"name": "📥 จาก", "value": source, "inline": True},
            {"name": "📊 รวมลิ้งนี้", "value": f"**{total_joins_for_link}** คน",
             "inline": True},
        ],
        "footer": {"text": f"{marketer} • {group_title}"} if group_title else
                  {"text": marketer},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    return await post_embed(ch, embed)


# Tier label map — shared across renderers
_TIER_LABELS = {
    "TIER_100": "VIP 100 (ห้องชัก)",
    "TIER_300": "VIP 300 (ทั่วไป)",
    "TIER_500": "VIP 500 (OnlyFans + หายาก)",
    "TIER_1299": "VIP 1,299 (พรีเมียม)",
    "TIER_2499": "VIP 2,499 (Storage + Summer)",
    "TIER_FREE": "Free Trial",
    "GACHA": "Gacha Pack",
}


def _tier_label(tier: str) -> str:
    tier_up = str(tier or "").upper()
    return _TIER_LABELS.get(tier_up, str(tier).replace("TIER_", "VIP "))


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
    # NEW: pass a MarketerStats object so the leaderboard data stays
    # consistent with the rest of the system. Falls back to scalar args
    # for backward compat with old callers.
    stats=None,
    marketer_month_count: Optional[int] = None,
    marketer_month_revenue: Optional[float] = None,
) -> bool:
    """Notify marketer that one of their leads paid → gold embed celebration.

    Args:
        stats:                  shared.marketing_stats.MarketerStats — preferred.
                                If provided, marketer_month_* are ignored.
        marketer_month_count:   legacy fallback (deprecated, kept for compat)
        marketer_month_revenue: legacy fallback (deprecated, kept for compat)
    """
    ch = _FEED_CHANNELS.get(marketer)
    if not ch:
        logger.warning("notify_marketer_conversion: no channel for marketer=%s", marketer)
        return False

    customer = _customer_label(tg_first_name, tg_username, telegram_id)
    tier_lbl = _tier_label(tier)
    source = f"{_platform_display(platform)} · ลิ้ง `#{link_id}` · {days_since_join} วันก่อน"

    # Prefer MarketerStats (single source of truth); fall back to scalars.
    if stats is not None:
        period_label = getattr(stats, "window_desc", "เดือนนี้")
        m_cnt = int(getattr(stats, "conversions", 0) or 0)
        m_rev = float(getattr(stats, "revenue_thb", 0) or 0)
    else:
        period_label = "เดือนนี้"
        m_cnt = int(marketer_month_count or 0)
        m_rev = float(marketer_month_revenue or 0)

    embed = {
        "color": 0xFFD700,  # gold
        "title": f"💰 ขายได้! +฿{amount:,.0f}",
        "fields": [
            {"name": "👤 ลูกค้า", "value": customer, "inline": False},
            {"name": "💎 แพ็คเกจ", "value": tier_lbl, "inline": True},
            {"name": "📥 ลิ้ง", "value": source, "inline": True},
            # Spacer (Discord field separator) — empty field with zero-width chars
            {"name": "​", "value": "​", "inline": False},
            {
                "name": f"🏆 {marketer} {period_label}",
                "value": f"**{m_cnt}** ขาย · **฿{m_rev:,.0f}**",
                "inline": False,
            },
        ],
        "footer": {"text": f"เก่งมาก {marketer}! 🎉"},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    return await post_embed(ch, embed)


async def notify_overview(content: str) -> None:
    """Post to #marketing-รวม (daily digest, leaderboard, etc.)"""
    if _OVERVIEW_CHANNEL:
        await post_to_channel(_OVERVIEW_CHANNEL, content)


__all__ = [
    "post_to_channel",
    "post_embed",
    "notify_marketer_join",
    "notify_marketer_conversion",
    "notify_overview",
]
