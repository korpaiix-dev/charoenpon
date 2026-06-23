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
    Payment,
    PaymentStatus,
    Subscription,
    SubscriptionStatus,
)
from shared.utils import format_thb, get_expiring_users
from shared.api_cost_tracker import daily_summary

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
    # Start AFK sweep loop (2026-06-22)
    if not afk_sweep_task.is_running():
        afk_sweep_task.start()
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
    logger.info("Marketing report tasks disabled by config change")

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



# ─────────────────────────────────────────────────────────────────────────
# AFK AUTO-MOVE (added 2026-06-22)
# Moves user to AFK voice channel after 15 minutes of mute+deafen
# ─────────────────────────────────────────────────────────────────────────
from datetime import datetime, timedelta

AFK_CHANNEL_ID = int(os.environ.get("DISCORD_AFK_CHANNEL_ID", "0") or 0)
AFK_TIMEOUT_MIN = 15  # minutes — boss spec

# Track when user entered "muted + deafened" state
_afk_tracker: dict[int, datetime] = {}  # user_id → since (UTC naive)


@bot.event
async def on_voice_state_update(member: discord.Member, before: discord.VoiceState,
                                 after: discord.VoiceState) -> None:
    """Track when user becomes mute+deaf in voice."""
    if member.bot:
        return  # ignore bots
    
    # Determine current AFK-eligible state
    is_muted_and_deaf = (
        after.channel is not None
        and after.channel.id != AFK_CHANNEL_ID  # already in AFK = skip
        and (after.self_mute or after.mute)
        and (after.self_deaf or after.deaf)
    )
    
    if is_muted_and_deaf:
        if member.id not in _afk_tracker:
            _afk_tracker[member.id] = datetime.utcnow()
            logger.info("AFK track start: %s @ %s", member.name, _afk_tracker[member.id])
    else:
        # Either left voice, unmuted, or undeafened → clear
        if member.id in _afk_tracker:
            del _afk_tracker[member.id]


@tasks.loop(seconds=60)
async def afk_sweep_task() -> None:
    """Every 60s — check trackers, move expired users to AFK channel."""
    if not AFK_CHANNEL_ID:
        return
    
    if not _afk_tracker:
        return  # no one to check
    
    now = datetime.utcnow()
    threshold = now - timedelta(minutes=AFK_TIMEOUT_MIN)
    expired_ids = [uid for uid, since in _afk_tracker.items() if since <= threshold]
    
    if not expired_ids:
        return
    
    for guild in bot.guilds:
        afk_ch = guild.get_channel(AFK_CHANNEL_ID)
        if not afk_ch:
            continue
        for uid in expired_ids:
            try:
                member = guild.get_member(uid)
                if not member or not member.voice or not member.voice.channel:
                    _afk_tracker.pop(uid, None)
                    continue
                if member.voice.channel.id == AFK_CHANNEL_ID:
                    _afk_tracker.pop(uid, None)
                    continue
                logger.info("AFK MOVE: %s -> #%s (idle %d min)",
                            member.name, afk_ch.name, AFK_TIMEOUT_MIN)
                await member.move_to(afk_ch, reason=f"AFK {AFK_TIMEOUT_MIN}+ min")
                _afk_tracker.pop(uid, None)
            except Exception as exc:
                logger.warning("AFK move fail uid=%s: %s", uid, exc)


@afk_sweep_task.before_loop
async def _afk_sweep_wait() -> None:
    await bot.wait_until_ready()




@tasks.loop(hours=24)
async def daily_report_task() -> None:
    """ส่งรายงานประจำวันไปที่ #daily-report เวลา 08:00 UTC+7 (01:00 UTC)."""
    # FIX 2026-06-22: ใช้ BKK timezone กำหนดขอบเขต "วันนี้" / "เดือนนี้"
    # เพราะลูกค้าอยู่ไทย — payment 06-22 BKK 00:54 = 06-21 UTC 17:54 ในฐาน
    # ถ้า filter UTC midnight จะตกหล่น payment ของเช้ามืดไทย
    from zoneinfo import ZoneInfo
    BKK = ZoneInfo("Asia/Bangkok")
    UTC = timezone.utc
    now = datetime.now(UTC)
    now_bkk = datetime.now(BKK)
    now_naive = now.replace(tzinfo=None)
    # คำนวณขอบเขตใน BKK ก่อน แล้วแปลงเป็น naive UTC (DB เก็บแบบ naive UTC)
    today_bkk = now_bkk.replace(hour=0, minute=0, second=0, microsecond=0)
    month_bkk = now_bkk.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    today_start = today_bkk.astimezone(UTC).replace(tzinfo=None)
    month_start = month_bkk.astimezone(UTC).replace(tzinfo=None)

    async with get_session() as session:
        # Revenue today
        rev_q = await session.execute(
            select(
                func.count(Payment.id).label("count"),
                func.coalesce(func.sum(Payment.amount), 0).label("total"),
            ).where(
                Payment.status == PaymentStatus.CONFIRMED,
                Payment.amount > 0,
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
                Payment.amount > 0,
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

    # ── Sync Google Sheets: รายได้รายวัน ──
    try:
        from sheets.daily_revenue import DailyRevenueSheet
        await DailyRevenueSheet.update()
        from sheets.daily_summary import DailySummarySheet
        await DailySummarySheet.update()
        logger.info("Daily revenue sheet synced")
    except Exception as exc:
        logger.warning("Daily revenue sheet sync failed: %s", exc)

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



# ─────────────────────────────────────────────────────────────────────────
# AI PRAE DISCORD (added 2026-06-22)
# Listen for messages in #คุย-กับ-แพร channel → reply via shared/prae_engine
# Boss spec: dedicated channel only, team context
# ─────────────────────────────────────────────────────────────────────────
PRAE_CHANNEL_ID = int(os.environ.get("DISCORD_PRAE_CHANNEL_ID", "0") or 0)

# Use negative TG IDs to namespace team members (avoid collision with real customers)
# Format: -1_<discord_user_id> — so prae_engine memory tables track team chat separately
def _discord_to_tg(discord_uid: int) -> int:
    return -(discord_uid)  # negative = team context




def _html_to_discord_md(text: str) -> str:
    """Convert Telegram HTML to Discord Markdown.
    
    <b>x</b> → **x**
    <i>x</i> → *x*
    <code>x</code> → `x`
    <pre>x</pre> → ```x```
    <u>x</u> → __x__
    <s>x</s> → ~~x~~
    <a href='url'>x</a> → [x](url)
    """
    import re
    if not text:
        return text
    # Bold
    text = re.sub(r"<b>(.*?)</b>", r"**\1**", text, flags=re.DOTALL)
    text = re.sub(r"<strong>(.*?)</strong>", r"**\1**", text, flags=re.DOTALL)
    # Italic
    text = re.sub(r"<i>(.*?)</i>", r"*\1*", text, flags=re.DOTALL)
    text = re.sub(r"<em>(.*?)</em>", r"*\1*", text, flags=re.DOTALL)
    # Code (inline)
    text = re.sub(r"<code>(.*?)</code>", r"`\1`", text, flags=re.DOTALL)
    # Code (block)
    text = re.sub(r"<pre>(.*?)</pre>", r"```\1```", text, flags=re.DOTALL)
    # Underline
    text = re.sub(r"<u>(.*?)</u>", r"__\1__", text, flags=re.DOTALL)
    # Strikethrough
    text = re.sub(r"<s>(.*?)</s>", r"~~\1~~", text, flags=re.DOTALL)
    text = re.sub(r"<del>(.*?)</del>", r"~~\1~~", text, flags=re.DOTALL)
    # Links: <a href='url'>text</a> or <a href="url">text</a>
    text = re.sub(r'<a\s+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', r"[\2](\1)", text, flags=re.DOTALL)
    # Remove any leftover tags (safety)
    text = re.sub(r"<[^>]+>", "", text)
    # Decode HTML entities
    import html
    text = html.unescape(text)
    return text



@bot.event
async def on_message(message: discord.Message) -> None:
    """Listen to team chat in #คุย-กับ-แพร."""
    # DEBUG: log every message
    try:
        logger.info(
            "on_message: ch=%s/%s author=%s bot=%s text_len=%d",
            message.channel.id,
            getattr(message.channel, "name", "?"),
            message.author.name,
            message.author.bot,
            len(message.content or ""),
        )
    except Exception:
        pass
    # Skip bots + non-target channels
    if message.author.bot:
        return
    if not PRAE_CHANNEL_ID or message.channel.id != PRAE_CHANNEL_ID:
        # Still allow commands to work in other channels
        await bot.process_commands(message)
        return
    
    # Skip empty / commands
    text = (message.content or "").strip()
    if not text or text.startswith("/") or text.startswith("!"):
        await bot.process_commands(message)
        return
    
    # Show typing indicator while waiting
    async with message.channel.typing():
        try:
            # FIX 2026-06-23: use team-specific engine instead of customer-facing prae_engine
            from shared.prae_team_engine import team_reply
            reply_text = await team_reply(text, user_name=message.author.name)
            result = {"cost_usd": 0.0}  # team engine doesnt track cost yet
            if not reply_text:
                reply_text = "(ขออภัย แพรไม่ได้คำตอบจาก AI ลองพิมพ์ใหม่)"
            # Team engine outputs Discord markdown natively, but safety-clean HTML if any
            reply_text = _html_to_discord_md(reply_text)
            # Truncate to Discord 2000-char limit
            if len(reply_text) > 1900:
                reply_text = reply_text[:1900] + "…"
            await message.reply(reply_text, mention_author=False)
            logger.info("Prae-Discord: user=%s text_len=%d cost=$%.4f",
                        message.author.name, len(text), result.get("cost_usd", 0))
        except Exception as exc:
            logger.exception("Prae-Discord reply failed: %s", exc)
            await message.reply("ขออภัย แพรเกิดปัญหา ลองใหม่นะคะ 🙏", mention_author=False)
    
    # Still process commands if any (allows future /commands in same channel)
    await bot.process_commands(message)



def main() -> None:
    """Entry point for Discord Bot."""
    if not DISCORD_BOT_TOKEN:
        logger.error("DISCORD_BOT_TOKEN environment variable is not set")
        sys.exit(1)

    logger.info("Starting Discord Bot...")
    bot.run(DISCORD_BOT_TOKEN, log_handler=None)


if __name__ == "__main__":
    main()
