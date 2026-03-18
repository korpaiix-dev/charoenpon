"""Discord Bot - ศูนย์บัญชาการเจ้าของ บริษัทเจริญพร.

discord.py v2
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, timedelta, timezone

import discord
from discord.ext import commands, tasks
from sqlalchemy import func, select

from shared.database import close_db, init_db, get_session
from shared.models import (
    AdCampaign,
    CampaignStatus,
    Payment,
    PaymentStatus,
    Subscription,
    SubscriptionStatus,
)
from shared.utils import format_thb, get_expiring_users
from shared.api_cost_tracker import daily_summary, format_daily_summary_discord

from discord_bot.channels import get_channel_id, CHANNEL_DEFS
from discord_bot.commands import (
    AdApprovalView,
    BroadcastApprovalView,
    PaymentApprovalView,
    setup as setup_commands,
)

logging.basicConfig(
    format="[%(asctime)s] [DISCORD_BOT] [%(levelname)s] %(message)s",
    level=logging.INFO,
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

DISCORD_BOT_TOKEN: str = os.environ.get("DISCORD_BOT_TOKEN", "")

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(
    command_prefix="!",
    intents=intents,
    help_command=None,
)


# ─── Events ──────────────────────────────────────────────────────────────────

@bot.event
async def on_ready() -> None:
    """Bot is ready — initialize DB, register views, start tasks."""
    await init_db()
    logger.info("Database initialized")

    # Register persistent views for button callbacks
    bot.add_view(AdApprovalView(0))
    bot.add_view(BroadcastApprovalView(0))
    bot.add_view(PaymentApprovalView())

    # Load commands cog
    await setup_commands(bot)

    # Start scheduled tasks
    if not daily_report_task.is_running():
        daily_report_task.start()
    if not expiring_members_task.is_running():
        expiring_members_task.start()
    if not ad_performance_task.is_running():
        ad_performance_task.start()

    await bot.change_presence(
        activity=discord.Activity(
            type=discord.ActivityType.watching,
            name="บริษัทเจริญพร 🏢",
        )
    )

    logger.info("Discord Bot ready as %s (ID: %s)", bot.user.name, bot.user.id)
    logger.info("Connected to %d guild(s)", len(bot.guilds))

    # Send startup notification to system-logs
    ch_id = get_channel_id("system-logs")
    if ch_id:
        channel = bot.get_channel(ch_id)
        if channel:
            embed = discord.Embed(
                title="🟢 Discord Bot Online",
                description=(
                    f"Bot: {bot.user.name}\n"
                    f"Guilds: {len(bot.guilds)}\n"
                    f"Latency: {round(bot.latency * 1000)}ms"
                ),
                color=discord.Color.green(),
                timestamp=datetime.now(timezone.utc),
            )
            await channel.send(embed=embed)


@bot.event
async def on_command_error(ctx: commands.Context, error: commands.CommandError) -> None:
    """Global error handler for commands."""
    if isinstance(error, commands.CommandNotFound):
        await ctx.send("❓ ไม่รู้จักคำสั่งนี้ พิมพ์ `!help` เพื่อดูคำสั่งทั้งหมด")
    elif isinstance(error, commands.MissingPermissions):
        await ctx.send("⛔ คุณไม่มีสิทธิ์ใช้คำสั่งนี้")
    else:
        logger.error("Command error in %s: %s", ctx.command, error)
        await ctx.send(f"❌ เกิดข้อผิดพลาด: {error}")

        # Log to system-logs channel
        ch_id = get_channel_id("system-logs")
        if ch_id:
            channel = bot.get_channel(ch_id)
            if channel:
                embed = discord.Embed(
                    title="🔴 Command Error",
                    description=f"Command: `{ctx.message.content}`\nError: `{error}`",
                    color=discord.Color.red(),
                    timestamp=datetime.now(timezone.utc),
                )
                embed.set_footer(text=f"User: {ctx.author}")
                await channel.send(embed=embed)


@bot.command(name="help")
async def help_cmd(ctx: commands.Context) -> None:
    """Custom help command."""
    embed = discord.Embed(
        title="📖 คำสั่ง — บริษัทเจริญพร",
        color=discord.Color.blue(),
    )
    embed.add_field(
        name="📢 Ad Management",
        value=(
            "`!approve ad` — อนุมัติแอด\n"
            "`!reject ad [เหตุผล]` — ไม่อนุมัติ\n"
        ),
        inline=False,
    )
    embed.add_field(
        name="📢 Broadcast",
        value="`!approve broadcast` — อนุมัติ broadcast\n",
        inline=False,
    )
    embed.add_field(
        name="📊 Reports",
        value=(
            "`!status` — สถานะทุก bot\n"
            "`!revenue today` — ยอดวันนี้\n"
            "`!revenue week` — ยอดสัปดาห์\n"
            "`!revenue month` — ยอดเดือน\n"
            "`!members` — จำนวน active\n"
            "`!costs today` — ค่า API วันนี้\n"
        ),
        inline=False,
    )
    embed.add_field(
        name="📊 Google Sheets",
        value=(
            "`!sheet` — ดูรายชื่อ Sheet\n"
            "`!sheet [ชื่อ]` — เปิด Sheet\n"
        ),
        inline=False,
    )
    await ctx.send(embed=embed)


# ─── Scheduled Tasks ─────────────────────────────────────────────────────────

@tasks.loop(hours=24)
async def daily_report_task() -> None:
    """ส่งรายงานประจำวันไปที่ #daily-report เวลา 08:00 UTC+7 (01:00 UTC)."""
    now = datetime.now(timezone.utc)
    # DB uses naive datetime, so strip tz for queries
    now_naive = now.replace(tzinfo=None)
    today_start = now_naive.replace(hour=0, minute=0, second=0, microsecond=0)
    month_start = now_naive.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    async with get_session() as session:
        # Revenue today
        rev_q = await session.execute(
            select(
                func.count(Payment.id).label("count"),
                func.coalesce(func.sum(Payment.amount), 0).label("total"),
            ).where(
                Payment.status == PaymentStatus.CONFIRMED,
                Payment.verified_at >= today_start,
            )
        )
        rev = rev_q.one()

        # Revenue month
        rev_month_q = await session.execute(
            select(
                func.coalesce(func.sum(Payment.amount), 0).label("total"),
            ).where(
                Payment.status == PaymentStatus.CONFIRMED,
                Payment.verified_at >= month_start,
            )
        )
        rev_month = rev_month_q.scalar()

        # Active members
        active_q = await session.execute(
            select(func.count(Subscription.id)).where(
                Subscription.status == SubscriptionStatus.ACTIVE,
                Subscription.end_date > now_naive,
            )
        )
        active = active_q.scalar()

    # API costs
    cost_summary = await daily_summary()

    ch_id = get_channel_id("daily-report")
    if not ch_id:
        logger.warning("daily-report channel not configured")
        return

    channel = bot.get_channel(ch_id)
    if not channel:
        logger.warning("Cannot find daily-report channel %d", ch_id)
        return

    embed = discord.Embed(
        title="📊 รายงานประจำวัน — บริษัทเจริญพร",
        color=discord.Color.gold(),
        timestamp=now,
    )
    embed.add_field(name="💰 รายได้วันนี้", value=f"{format_thb(rev.total)} ({rev.count} รายการ)", inline=True)
    embed.add_field(name="📆 รายได้เดือนนี้", value=format_thb(rev_month), inline=True)
    embed.add_field(name="👥 Active Members", value=f"{active:,}", inline=True)
    embed.add_field(
        name="🤖 ค่า API",
        value=f"${cost_summary['total_usd']:.4f} (฿{cost_summary['total_thb']:.2f}) — {cost_summary['total_calls']} calls",
        inline=False,
    )
    await channel.send(embed=embed)

    logger.info(
        "[%s] [DISCORD] [DAILY_REPORT] [SYSTEM] [revenue=%s active=%d]",
        now.isoformat(),
        rev.total,
        active,
    )


@daily_report_task.before_loop
async def daily_report_before() -> None:
    """Wait until bot is ready before starting daily report loop."""
    await bot.wait_until_ready()


@tasks.loop(hours=12)
async def expiring_members_task() -> None:
    """ส่งรายชื่อสมาชิกใกล้หมดอายุไปที่ #member-expiring."""
    expiring = await get_expiring_users(days=3)
    if not expiring:
        return

    ch_id = get_channel_id("member-expiring")
    if not ch_id:
        return

    channel = bot.get_channel(ch_id)
    if not channel:
        return

    embed = discord.Embed(
        title=f"⚠️ สมาชิกใกล้หมดอายุ ({len(expiring)} คน)",
        color=discord.Color.orange(),
        timestamp=datetime.now(timezone.utc),
    )

    lines = []
    for u in expiring[:20]:  # Limit to 20 to avoid embed size limit
        username = f"@{u['username']}" if u['username'] else f"TG:{u['telegram_id']}"
        lines.append(f"• {username} — เหลือ {u['days_left']} วัน")

    if len(expiring) > 20:
        lines.append(f"\n...และอีก {len(expiring) - 20} คน")

    embed.description = "\n".join(lines)
    await channel.send(embed=embed)

    logger.info(
        "[%s] [DISCORD] [EXPIRING_MEMBERS] [SYSTEM] [count=%d]",
        datetime.now(timezone.utc).isoformat(),
        len(expiring),
    )


@expiring_members_task.before_loop
async def expiring_before() -> None:
    await bot.wait_until_ready()


@tasks.loop(hours=6)
async def ad_performance_task() -> None:
    """ส่งรายงาน ad performance ไปที่ #ad-performance."""
    from shared.models import AdPerformance

    now = datetime.now(timezone.utc)
    yesterday = datetime.utcnow() - timedelta(days=1)  # naive for DB query

    async with get_session() as session:
        result = await session.execute(
            select(AdPerformance, AdCampaign)
            .join(AdCampaign, AdPerformance.campaign_id == AdCampaign.id)
            .where(AdPerformance.date >= yesterday)
            .order_by(AdPerformance.date.desc())
        )
        rows = result.all()

    if not rows:
        return

    ch_id = get_channel_id("ad-performance")
    if not ch_id:
        return

    channel = bot.get_channel(ch_id)
    if not channel:
        return

    embed = discord.Embed(
        title="📈 Ad Performance Report",
        color=discord.Color.purple(),
        timestamp=now,
    )

    for perf, campaign in rows[:10]:
        ctr = (perf.clicks / perf.impressions * 100) if perf.impressions > 0 else 0
        conv_rate = (perf.conversions / perf.clicks * 100) if perf.clicks > 0 else 0
        roi = ((float(perf.revenue) - float(perf.spend)) / float(perf.spend) * 100) if float(perf.spend) > 0 else 0

        embed.add_field(
            name=f"📢 {campaign.name}",
            value=(
                f"Impressions: {perf.impressions:,}\n"
                f"Clicks: {perf.clicks:,} (CTR: {ctr:.1f}%)\n"
                f"Conversions: {perf.conversions:,} ({conv_rate:.1f}%)\n"
                f"Spend: {format_thb(perf.spend)} | Revenue: {format_thb(perf.revenue)}\n"
                f"ROI: {roi:+.1f}%"
            ),
            inline=False,
        )

    await channel.send(embed=embed)

    logger.info(
        "[%s] [DISCORD] [AD_PERFORMANCE] [SYSTEM] [campaigns=%d]",
        now.isoformat(),
        len(rows),
    )


@ad_performance_task.before_loop
async def ad_performance_before() -> None:
    await bot.wait_until_ready()


# ─── Utility functions for other bots to send to Discord ─────────────────────

async def send_to_channel(slug: str, content: str = None, embed: discord.Embed = None) -> bool:
    """Send a message to a Discord channel by slug name.

    Used by other bots/services to push notifications to Discord.
    Returns True if sent successfully.
    """
    ch_id = get_channel_id(slug)
    if not ch_id:
        logger.warning("Channel '%s' not configured", slug)
        return False

    channel = bot.get_channel(ch_id)
    if not channel:
        logger.warning("Cannot find channel '%s' (ID: %d)", slug, ch_id)
        return False

    await channel.send(content=content, embed=embed)
    return True


async def send_alert(message: str, level: str = "info") -> bool:
    """Send an alert to #alerts channel."""
    color_map = {
        "info": discord.Color.blue(),
        "warning": discord.Color.orange(),
        "error": discord.Color.red(),
        "success": discord.Color.green(),
    }
    embed = discord.Embed(
        title=f"{'🔴' if level == 'error' else '🟡' if level == 'warning' else '🟢' if level == 'success' else 'ℹ️'} Alert",
        description=message,
        color=color_map.get(level, discord.Color.blue()),
        timestamp=datetime.now(timezone.utc),
    )
    return await send_to_channel("alerts", embed=embed)


async def send_finance_update(title: str, description: str) -> bool:
    """Send a finance update to #finance channel."""
    embed = discord.Embed(
        title=f"💰 {title}",
        description=description,
        color=discord.Color.green(),
        timestamp=datetime.now(timezone.utc),
    )
    return await send_to_channel("finance", embed=embed)


async def send_system_log(message: str, level: str = "info") -> bool:
    """Send a log entry to #system-logs channel."""
    now = datetime.now(timezone.utc)
    log_line = f"`[{now.strftime('%Y-%m-%d %H:%M:%S')}]` {message}"
    return await send_to_channel("system-logs", content=log_line)


async def send_sheets_update(sheet_name: str, action: str, details: str = "") -> bool:
    """Send a Google Sheets update notification to #sheets-updates."""
    embed = discord.Embed(
        title=f"📊 Sheets Update: {sheet_name}",
        description=f"**Action:** {action}\n{details}" if details else f"**Action:** {action}",
        color=discord.Color.teal(),
        timestamp=datetime.now(timezone.utc),
    )
    return await send_to_channel("sheets-updates", embed=embed)


# ─── Entry Point ─────────────────────────────────────────────────────────────

def main() -> None:
    """Entry point for Discord Bot."""
    if not DISCORD_BOT_TOKEN:
        logger.error("DISCORD_BOT_TOKEN environment variable is not set")
        sys.exit(1)

    logger.info("Starting Discord Bot...")
    bot.run(DISCORD_BOT_TOKEN, log_handler=None)


if __name__ == "__main__":
    main()
