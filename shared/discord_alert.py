"""Single helper for posting alerts to Discord channels (via webhooks).

Replaces 8 duplicate `_notify_discord*` functions across the codebase that read
4 different env names with inconsistent fallback logic.

Channels:
    "payment"   — payment approve/reject events       → DISCORD_CH_FINANCE
    "broadcast" — broadcast queue events              → DISCORD_CH_BROADCAST_APPROVE
    "system"    — bot crashes, infrastructure errors  → DISCORD_CH_SYSTEM_LOGS
    "alerts"    — general urgent alerts               → DISCORD_CH_ALERTS
    "spam"      — spam filter triggers                → DISCORD_CH_ALERTS (shared)
    "content"   — content distributor + scheduler     → DISCORD_CH_CONTENT_LOG
    "members"   — expiring members + kicks            → DISCORD_CH_MEMBER_EXPIRING
    "report"    — daily/weekly/monthly reports        → DISCORD_CH_DAILY_REPORT
    "sheets"    — Google Sheets sync events           → DISCORD_CH_SHEETS_UPDATES
    "manager"   — manager-agent oversight             → DISCORD_CH_MANAGER

Usage:
    from shared.discord_alert import notify_discord

    await notify_discord("payment", "✅ Payment Approved", "User X paid ฿500")
    await notify_discord("alerts", "⚠️ Bot down!", details_text)
"""
from __future__ import annotations

import logging
import os
from typing import Any

import httpx

logger = logging.getLogger(__name__)


# Channel → env var name. Resolved at call time so .env reload works.
_CHANNEL_ENV = {
    "payment":   "DISCORD_CH_FINANCE",
    "broadcast": "DISCORD_CH_BROADCAST_APPROVE",
    "system":    "DISCORD_CH_SYSTEM_LOGS",
    "alerts":    "DISCORD_CH_ALERTS",
    "spam":      "DISCORD_CH_ALERTS",
    "content":   "DISCORD_CH_CONTENT_LOG",
    "members":   "DISCORD_CH_MEMBER_EXPIRING",
    "report":    "DISCORD_CH_DAILY_REPORT",
    "sheets":    "DISCORD_CH_SHEETS_UPDATES",
    "manager":   "DISCORD_CH_MANAGER",
    # Generic fallback used by old code
    "default":   "DISCORD_WEBHOOK_URL",
}


def _resolve_webhook(channel: str) -> str | None:
    env_name = _CHANNEL_ENV.get(channel, _CHANNEL_ENV["default"])
    url = os.environ.get(env_name, "").strip()
    # Guard: env value must be a real webhook URL (not a channel ID)
    if url and not url.startswith(("http://", "https://")):
        url = ""
    if not url:
        # Last-resort generic fallback
        url = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
    if url and not url.startswith(("http://", "https://")):
        return None
    return url or None


async def notify_discord(
    channel: str,
    title: str,
    body: str = "",
    *,
    color: int | None = None,
    fields: list[dict[str, Any]] | None = None,
    silent_on_error: bool = True,
) -> bool:
    """Post an embed to a Discord channel.

    Args:
        channel: one of the keys in _CHANNEL_ENV (e.g. "payment", "alerts").
        title: short headline (becomes embed.title).
        body: longer text (becomes embed.description).
        color: hex int (0xRRGGBB). If None, picks from title emoji.
        fields: list of {"name": str, "value": str, "inline": bool}.

    Returns True if sent, False if webhook missing or HTTP error.
    """
    url = _resolve_webhook(channel)
    if not url:
        logger.debug("notify_discord: no webhook for channel=%s", channel)
        return False

    # Auto-pick color from leading emoji if not given
    if color is None:
        first = title.strip()[:2]
        color = (
            0x57F287 if any(c in first for c in "✅🎉") else      # green
            0xED4245 if any(c in first for c in "❌🚨🔴") else    # red
            0xFEE75C if any(c in first for c in "⚠️🟡") else      # yellow
            0x5865F2                                              # blurple
        )

    embed: dict[str, Any] = {"title": title[:256], "color": color}
    if body:
        embed["description"] = body[:4000]
    if fields:
        embed["fields"] = fields[:25]

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.post(url, json={"embeds": [embed]})
            r.raise_for_status()
            return True
    except Exception as exc:
        logger.warning("notify_discord(%s) failed: %s", channel, exc)
        if not silent_on_error:
            raise
        return False


__all__ = ["notify_discord"]
