"""Daily report of แพร v2 — sends to Discord at 22:00.

Stats:
- Total conversations
- Unique users
- Intent breakdown
- Handoff rate
- Avg confidence
- Cost (USD + THB est)
- Top intents
- Recent error count
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta

from sqlalchemy import text as _t
from shared.database import get_session

logger = logging.getLogger(__name__)


async def build_daily_report(days: int = 1) -> str:
    """Build a markdown report for the last N day(s)."""
    async with get_session() as s:
        # Totals
        r = await s.execute(_t("""
            SELECT
              count(*) FILTER (WHERE role='user') AS user_msgs,
              count(*) FILTER (WHERE role='assistant') AS bot_msgs,
              count(DISTINCT telegram_id) AS unique_users,
              COALESCE(SUM(cost_usd), 0) AS cost_usd
            FROM prae_conversations
            WHERE created_at >= NOW() - make_interval(days => :d)
        """), {"d": days})
        row = r.fetchone()
        total_user = int(row.user_msgs or 0)
        total_bot = int(row.bot_msgs or 0)
        users = int(row.unique_users or 0)
        cost_usd = float(row.cost_usd or 0)
        cost_thb = cost_usd * 35.0

        # Handoffs (we logged via tools_used = ['handoff_keyword'])
        r2 = await s.execute(_t("""
            SELECT count(*) FROM prae_conversations
            WHERE role='assistant' AND created_at >= NOW() - make_interval(days => :d)
              AND tools_used::text ILIKE '%handoff%'
        """), {"d": days})
        handoffs = int(r2.scalar() or 0)

        # Tool usage breakdown
        r3 = await s.execute(_t("""
            SELECT tool, count(*) AS uses FROM (
              SELECT jsonb_array_elements_text(tools_used) AS tool
              FROM prae_conversations
              WHERE role='assistant'
                AND created_at >= NOW() - make_interval(days => :d)
                AND tools_used IS NOT NULL
            ) t
            GROUP BY tool ORDER BY uses DESC LIMIT 10
        """), {"d": days})
        tool_rows = r3.fetchall()

    handoff_rate = (handoffs / max(total_bot, 1)) * 100

    lines = [
        f"🎀 **แพร v2 — Daily Report ({days}d)**",
        "━━━━━━━━━━━━━━━",
        f"💬 User msgs: **{total_user}**",
        f"🤖 Bot replies: **{total_bot}**",
        f"👥 Unique users: **{users}**",
        f"🤝 Handoffs: **{handoffs}** ({handoff_rate:.1f}%)",
        f"💰 Cost: **${cost_usd:.3f}** (≈ ฿{cost_thb:.0f})",
        "",
        "**Tool usage:**",
    ]
    for r in tool_rows:
        lines.append(f"  • {r.tool}: {r.uses}")

    return "\n".join(lines)


async def send_daily_prae_report():
    """Scheduled job — send report to Discord at 22:00 BKK."""
    try:
        report = await build_daily_report(days=1)
        from shared.discord_alert import notify_discord
        await notify_discord("prae_daily", "Daily Report", report)
        logger.info("prae_v2 daily report sent")
    except Exception as e:
        logger.exception("prae_daily_report failed: %s", e)


__all__ = ["build_daily_report", "send_daily_prae_report"]
