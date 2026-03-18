"""Discord Commands - ทุกคำสั่งสำหรับศูนย์บัญชาการเจ้าของ บริษัทเจริญพร."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import discord
from discord.ext import commands
from sqlalchemy import func, select

from shared.database import get_session
from shared.models import (
    AdCampaign,
    AdPerformance,
    CampaignStatus,
    Payment,
    PaymentStatus,
    Subscription,
    SubscriptionStatus,
)
from shared.utils import format_thb
from shared.api_cost_tracker import daily_summary

from discord_bot.channels import SHEETS_LINKS, get_channel_id

logger = logging.getLogger(__name__)


# ─── Approval Views (Button UI) ──────────────────────────────────────────────

class AdApprovalView(discord.ui.View):
    """Discord Buttons สำหรับอนุมัติ/ไม่อนุมัติแอดโฆษณา."""

    def __init__(self, campaign_id: int) -> None:
        super().__init__(timeout=None)
        self.campaign_id = campaign_id

    @discord.ui.button(label="✅ อนุมัติ", style=discord.ButtonStyle.success, custom_id="ad_approve")
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        async with get_session() as session:
            campaign = await session.get(AdCampaign, self.campaign_id)
            if not campaign:
                await interaction.response.send_message("❌ ไม่พบ Campaign", ephemeral=True)
                return
            if campaign.status != CampaignStatus.DRAFT:
                await interaction.response.send_message(
                    f"⚠️ Campaign สถานะเป็น {campaign.status.value} แล้ว", ephemeral=True
                )
                return
            campaign.status = CampaignStatus.ACTIVE
            campaign.start_date = datetime.now(timezone.utc)
            await session.flush()

        await interaction.response.send_message(
            f"✅ **อนุมัติ Campaign #{self.campaign_id}**\n"
            f"โดย: {interaction.user.display_name}",
        )
        # Disable buttons after action
        for item in self.children:
            item.disabled = True
        await interaction.message.edit(view=self)

        logger.info(
            "[%s] [DISCORD] [APPROVE_AD] [%s] [campaign_id=%d]",
            datetime.now(timezone.utc).isoformat(),
            interaction.user.id,
            self.campaign_id,
        )

    @discord.ui.button(label="❌ ไม่อนุมัติ", style=discord.ButtonStyle.danger, custom_id="ad_reject")
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        async with get_session() as session:
            campaign = await session.get(AdCampaign, self.campaign_id)
            if not campaign:
                await interaction.response.send_message("❌ ไม่พบ Campaign", ephemeral=True)
                return
            if campaign.status != CampaignStatus.DRAFT:
                await interaction.response.send_message(
                    f"⚠️ Campaign สถานะเป็น {campaign.status.value} แล้ว", ephemeral=True
                )
                return
            campaign.status = CampaignStatus.PAUSED
            await session.flush()

        await interaction.response.send_message(
            f"❌ **ไม่อนุมัติ Campaign #{self.campaign_id}**\n"
            f"โดย: {interaction.user.display_name}",
        )
        for item in self.children:
            item.disabled = True
        await interaction.message.edit(view=self)

        logger.info(
            "[%s] [DISCORD] [REJECT_AD] [%s] [campaign_id=%d]",
            datetime.now(timezone.utc).isoformat(),
            interaction.user.id,
            self.campaign_id,
        )

    @discord.ui.button(label="✏️ แก้ไข", style=discord.ButtonStyle.secondary, custom_id="ad_edit")
    async def edit(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_message(
            f"✏️ กรุณาแก้ไข Campaign #{self.campaign_id} แล้วส่งมาใหม่",
            ephemeral=True,
        )
        logger.info(
            "[%s] [DISCORD] [EDIT_AD] [%s] [campaign_id=%d]",
            datetime.now(timezone.utc).isoformat(),
            interaction.user.id,
            self.campaign_id,
        )


class BroadcastApprovalView(discord.ui.View):
    """Discord Buttons สำหรับอนุมัติ broadcast."""

    def __init__(self, broadcast_id: int) -> None:
        super().__init__(timeout=None)
        self.broadcast_id = broadcast_id

    @discord.ui.button(label="✅ อนุมัติ", style=discord.ButtonStyle.success, custom_id="bc_approve")
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        from shared.models import BroadcastLog

        async with get_session() as session:
            broadcast = await session.get(BroadcastLog, self.broadcast_id)
            if not broadcast:
                await interaction.response.send_message("❌ ไม่พบ Broadcast", ephemeral=True)
                return
            if broadcast.total_sent != 0:
                await interaction.response.send_message("⚠️ Broadcast นี้ถูกดำเนินการแล้ว", ephemeral=True)
                return
            broadcast.total_sent = -1  # Signal: approved, ready to send
            await session.flush()

        await interaction.response.send_message(
            f"✅ **อนุมัติ Broadcast #{self.broadcast_id}**\n"
            f"📢 ระบบจะเริ่มส่งอัตโนมัติ\n"
            f"โดย: {interaction.user.display_name}",
        )
        for item in self.children:
            item.disabled = True
        await interaction.message.edit(view=self)

        logger.info(
            "[%s] [DISCORD] [APPROVE_BROADCAST] [%s] [broadcast_id=%d]",
            datetime.now(timezone.utc).isoformat(),
            interaction.user.id,
            self.broadcast_id,
        )

    @discord.ui.button(label="❌ ไม่อนุมัติ", style=discord.ButtonStyle.danger, custom_id="bc_reject")
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        from shared.models import BroadcastLog

        async with get_session() as session:
            broadcast = await session.get(BroadcastLog, self.broadcast_id)
            if not broadcast:
                await interaction.response.send_message("❌ ไม่พบ Broadcast", ephemeral=True)
                return
            if broadcast.total_sent != 0:
                await interaction.response.send_message("⚠️ Broadcast นี้ถูกดำเนินการแล้ว", ephemeral=True)
                return
            broadcast.total_failed = -1  # Signal: rejected
            await session.flush()

        await interaction.response.send_message(
            f"❌ **ไม่อนุมัติ Broadcast #{self.broadcast_id}**\n"
            f"โดย: {interaction.user.display_name}",
        )
        for item in self.children:
            item.disabled = True
        await interaction.message.edit(view=self)

        logger.info(
            "[%s] [DISCORD] [REJECT_BROADCAST] [%s] [broadcast_id=%d]",
            datetime.now(timezone.utc).isoformat(),
            interaction.user.id,
            self.broadcast_id,
        )

    @discord.ui.button(label="✏️ แก้ไข", style=discord.ButtonStyle.secondary, custom_id="bc_edit")
    async def edit(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_message(
            f"✏️ กรุณาแก้ไข Broadcast #{self.broadcast_id} แล้วส่งมาใหม่",
            ephemeral=True,
        )


# ─── Bot Commands Cog ────────────────────────────────────────────────────────

class CharoenponCommands(commands.Cog):
    """คำสั่งทั้งหมดสำหรับศูนย์บัญชาการ Discord."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.command(name="approve")
    async def approve_cmd(self, ctx: commands.Context, target: str = "", *, reason: str = "") -> None:
        """!approve ad — อนุมัติแอดโฆษณา / !approve broadcast — อนุมัติ broadcast"""
        if target == "ad":
            await self._approve_ad(ctx)
        elif target == "broadcast":
            await self._approve_broadcast(ctx)
        else:
            await ctx.send("❓ ใช้: `!approve ad` หรือ `!approve broadcast`")

    async def _approve_ad(self, ctx: commands.Context) -> None:
        """แสดงรายการ ad campaigns ที่รออนุมัติพร้อมปุ่ม."""
        async with get_session() as session:
            result = await session.execute(
                select(AdCampaign)
                .where(AdCampaign.status == CampaignStatus.DRAFT)
                .order_by(AdCampaign.created_at.asc())
            )
            campaigns = result.scalars().all()

        if not campaigns:
            await ctx.send("✅ ไม่มี Ad Campaign ที่รออนุมัติ")
            return

        for campaign in campaigns:
            embed = discord.Embed(
                title=f"📢 Ad Campaign #{campaign.id}: {campaign.name}",
                color=discord.Color.gold(),
            )
            embed.add_field(name="Platform", value=campaign.platform, inline=True)
            embed.add_field(name="Budget", value=format_thb(campaign.budget), inline=True)
            embed.add_field(name="Spent", value=format_thb(campaign.spent), inline=True)
            if campaign.target_audience:
                embed.add_field(name="Target", value=campaign.target_audience[:200], inline=False)
            if campaign.message_template:
                embed.add_field(name="Message", value=campaign.message_template[:300], inline=False)

            view = AdApprovalView(campaign.id)
            await ctx.send(embed=embed, view=view)

        logger.info(
            "[%s] [DISCORD] [LIST_ADS] [%s] [%d campaigns pending]",
            datetime.now(timezone.utc).isoformat(),
            ctx.author.id,
            len(campaigns),
        )

    async def _approve_broadcast(self, ctx: commands.Context) -> None:
        """แสดงรายการ broadcast ที่รออนุมัติพร้อมปุ่ม."""
        from shared.models import BroadcastLog

        async with get_session() as session:
            result = await session.execute(
                select(BroadcastLog)
                .where(BroadcastLog.total_sent == 0, BroadcastLog.total_failed == 0)
                .order_by(BroadcastLog.created_at.asc())
            )
            broadcasts = result.scalars().all()

        if not broadcasts:
            await ctx.send("✅ ไม่มี Broadcast ที่รออนุมัติ")
            return

        for bc in broadcasts:
            tier_text = bc.target_tier.value if bc.target_tier else "ทั้งหมด"
            group_text = bc.target_group.value if bc.target_group else "ทุกกลุ่ม"
            msg_preview = (bc.message_text[:200] + "...") if bc.message_text and len(bc.message_text) > 200 else (bc.message_text or "(ไม่มีข้อความ)")

            embed = discord.Embed(
                title=f"📢 Broadcast #{bc.id}",
                description=msg_preview,
                color=discord.Color.blue(),
            )
            embed.add_field(name="Tier", value=tier_text, inline=True)
            embed.add_field(name="Group", value=group_text, inline=True)
            if bc.media_file_id:
                embed.add_field(name="Media", value="มีสื่อแนบ", inline=True)

            view = BroadcastApprovalView(bc.id)
            await ctx.send(embed=embed, view=view)

        logger.info(
            "[%s] [DISCORD] [LIST_BROADCASTS] [%s] [%d broadcasts pending]",
            datetime.now(timezone.utc).isoformat(),
            ctx.author.id,
            len(broadcasts),
        )

    @commands.command(name="reject")
    async def reject_cmd(self, ctx: commands.Context, target: str = "", *, reason: str = "") -> None:
        """!reject ad [เหตุผล] — ไม่อนุมัติแอดล่าสุด"""
        if target == "ad":
            await self._reject_ad(ctx, reason)
        else:
            await ctx.send("❓ ใช้: `!reject ad [เหตุผล]`")

    async def _reject_ad(self, ctx: commands.Context, reason: str) -> None:
        """Reject the most recent draft ad campaign."""
        async with get_session() as session:
            result = await session.execute(
                select(AdCampaign)
                .where(AdCampaign.status == CampaignStatus.DRAFT)
                .order_by(AdCampaign.created_at.desc())
                .limit(1)
            )
            campaign = result.scalar_one_or_none()

            if not campaign:
                await ctx.send("✅ ไม่มี Ad Campaign ที่รออนุมัติ")
                return

            campaign.status = CampaignStatus.PAUSED
            await session.flush()

        reject_text = reason if reason else "ไม่ระบุเหตุผล"
        embed = discord.Embed(
            title=f"❌ Rejected Campaign #{campaign.id}: {campaign.name}",
            description=f"**เหตุผล:** {reject_text}",
            color=discord.Color.red(),
        )
        embed.set_footer(text=f"โดย {ctx.author.display_name}")
        await ctx.send(embed=embed)

        logger.info(
            "[%s] [DISCORD] [REJECT_AD] [%s] [campaign_id=%d reason=%s]",
            datetime.now(timezone.utc).isoformat(),
            ctx.author.id,
            campaign.id,
            reject_text,
        )

    @commands.command(name="status")
    async def status_cmd(self, ctx: commands.Context) -> None:
        """!status — สถานะทุก bot"""
        now = datetime.now(timezone.utc)
        from discord_bot.channels import get_all_channel_ids

        channels = get_all_channel_ids()
        channel_status = "\n".join(
            f"  #{slug}: {'✅ configured' if cid else '❌ not set'}"
            for slug, cid in channels.items()
        ) if channels else "  (ไม่มี channel ที่ configure)"

        embed = discord.Embed(
            title="🏢 สถานะระบบ — บริษัทเจริญพร",
            color=discord.Color.green(),
            timestamp=now,
        )
        embed.add_field(
            name="🤖 Discord Bot",
            value=f"✅ Online\nLatency: {round(self.bot.latency * 1000)}ms",
            inline=True,
        )
        embed.add_field(
            name="📡 Channels",
            value=f"{len(channels)} configured",
            inline=True,
        )
        embed.add_field(
            name="🔗 Channel List",
            value=channel_status or "N/A",
            inline=False,
        )
        await ctx.send(embed=embed)

        logger.info(
            "[%s] [DISCORD] [STATUS] [%s] [latency=%dms]",
            datetime.now(timezone.utc).isoformat(),
            ctx.author.id,
            round(self.bot.latency * 1000),
        )

    @commands.command(name="revenue")
    async def revenue_cmd(self, ctx: commands.Context, period: str = "today") -> None:
        """!revenue today — ยอดวันนี้"""
        now = datetime.now(timezone.utc)

        if period == "today":
            start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            period_label = "วันนี้"
        elif period == "week":
            start = now - timedelta(days=now.weekday())
            start = start.replace(hour=0, minute=0, second=0, microsecond=0)
            period_label = "สัปดาห์นี้"
        elif period == "month":
            start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            period_label = "เดือนนี้"
        else:
            await ctx.send("❓ ใช้: `!revenue today`, `!revenue week`, หรือ `!revenue month`")
            return

        async with get_session() as session:
            result = await session.execute(
                select(
                    func.count(Payment.id).label("count"),
                    func.coalesce(func.sum(Payment.amount), 0).label("total"),
                ).where(
                    Payment.status == PaymentStatus.CONFIRMED,
                    Payment.verified_at >= start,
                )
            )
            row = result.one()

            pending_q = await session.execute(
                select(
                    func.count(Payment.id).label("count"),
                    func.coalesce(func.sum(Payment.amount), 0).label("total"),
                ).where(Payment.status == PaymentStatus.PENDING)
            )
            pending = pending_q.one()

        embed = discord.Embed(
            title=f"💰 รายได้ {period_label}",
            color=discord.Color.green(),
            timestamp=now,
        )
        embed.add_field(name="ยอดรวม", value=format_thb(row.total), inline=True)
        embed.add_field(name="จำนวน", value=f"{row.count} รายการ", inline=True)
        embed.add_field(name="⏳ รออนุมัติ", value=f"{pending.count} ({format_thb(pending.total)})", inline=False)
        await ctx.send(embed=embed)

        logger.info(
            "[%s] [DISCORD] [REVENUE] [%s] [period=%s total=%s]",
            datetime.now(timezone.utc).isoformat(),
            ctx.author.id,
            period,
            row.total,
        )

    @commands.command(name="members")
    async def members_cmd(self, ctx: commands.Context) -> None:
        """!members — จำนวน active"""
        now = datetime.now(timezone.utc)

        async with get_session() as session:
            active_q = await session.execute(
                select(func.count(Subscription.id)).where(
                    Subscription.status == SubscriptionStatus.ACTIVE,
                    Subscription.end_date > now,
                )
            )
            active = active_q.scalar()

            expiring_q = await session.execute(
                select(func.count(Subscription.id)).where(
                    Subscription.status == SubscriptionStatus.ACTIVE,
                    Subscription.end_date >= now,
                    Subscription.end_date <= now + timedelta(days=3),
                )
            )
            expiring = expiring_q.scalar()

            expired_q = await session.execute(
                select(func.count(Subscription.id)).where(
                    Subscription.status == SubscriptionStatus.EXPIRED,
                )
            )
            expired = expired_q.scalar()

        embed = discord.Embed(
            title="👥 สมาชิก",
            color=discord.Color.blue(),
            timestamp=now,
        )
        embed.add_field(name="✅ Active", value=f"{active:,}", inline=True)
        embed.add_field(name="⚠️ หมดอายุใน 3 วัน", value=f"{expiring:,}", inline=True)
        embed.add_field(name="❌ Expired", value=f"{expired:,}", inline=True)
        await ctx.send(embed=embed)

        logger.info(
            "[%s] [DISCORD] [MEMBERS] [%s] [active=%d expiring=%d]",
            datetime.now(timezone.utc).isoformat(),
            ctx.author.id,
            active,
            expiring,
        )

    @commands.command(name="costs")
    async def costs_cmd(self, ctx: commands.Context, period: str = "today") -> None:
        """!costs today — ค่า API วันนี้"""
        summary = await daily_summary()

        embed = discord.Embed(
            title="🤖 ค่า API วันนี้",
            color=discord.Color.orange(),
            timestamp=datetime.now(timezone.utc),
        )
        embed.add_field(
            name="Total",
            value=f"${summary['total_usd']:.4f} (฿{summary['total_thb']:.2f})",
            inline=True,
        )
        embed.add_field(name="Calls", value=f"{summary['total_calls']}", inline=True)
        embed.add_field(
            name="Tokens",
            value=f"{summary['total_prompt_tokens']:,} in / {summary['total_completion_tokens']:,} out",
            inline=False,
        )

        if summary["by_model"]:
            model_lines = []
            for m in summary["by_model"]:
                model_lines.append(
                    f"`{m['model']}`: {m['calls']} calls — "
                    f"${m['cost_usd']:.4f} (฿{m['cost_thb']:.2f})"
                )
            embed.add_field(name="Per Model", value="\n".join(model_lines), inline=False)

        await ctx.send(embed=embed)

        logger.info(
            "[%s] [DISCORD] [COSTS] [%s] [total_usd=%s calls=%d]",
            datetime.now(timezone.utc).isoformat(),
            ctx.author.id,
            summary["total_usd"],
            summary["total_calls"],
        )

    @commands.command(name="sheet")
    async def sheet_cmd(self, ctx: commands.Context, name: str = "") -> None:
        """!sheet [ชื่อ] — ลิ้งค์ Google Sheet"""
        if not name:
            lines = ["📊 **Google Sheets ที่มี:**\n"]
            for key, url in SHEETS_LINKS.items():
                status = f"[{url[:50]}...]({url})" if url else "❌ ยังไม่ตั้งค่า"
                lines.append(f"• `{key}`: {status}")
            lines.append("\nใช้: `!sheet [ชื่อ]` เช่น `!sheet revenue`")
            await ctx.send("\n".join(lines))
            return

        url = SHEETS_LINKS.get(name.lower())
        if url is None:
            available = ", ".join(f"`{k}`" for k in SHEETS_LINKS)
            await ctx.send(f"❓ ไม่พบ Sheet ชื่อ `{name}`\nที่มี: {available}")
            return

        if not url:
            await ctx.send(f"⚠️ Sheet `{name}` ยังไม่ได้ตั้งค่า URL (env: SHEET_{name.upper()})")
            return

        embed = discord.Embed(
            title=f"📊 Google Sheet: {name}",
            description=f"[เปิด Google Sheet]({url})",
            color=discord.Color.green(),
        )
        await ctx.send(embed=embed)

        logger.info(
            "[%s] [DISCORD] [SHEET] [%s] [name=%s]",
            datetime.now(timezone.utc).isoformat(),
            ctx.author.id,
            name,
        )


# ─── Payment Approval View ────────────────────────────────────────────────────

class PaymentApprovalView(discord.ui.View):
    """Discord Buttons สำหรับอนุมัติ/ไม่อนุมัติ payment."""

    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(label="อนุมัติ", emoji="✅", style=discord.ButtonStyle.success, custom_id="pay_approve")
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        # Extract payment_id from embed
        payment_id = _extract_payment_id(interaction)
        if not payment_id:
            await interaction.response.send_message("❌ ไม่พบ Payment ID", ephemeral=True)
            return

        try:
            from bots.sales_bot.handlers.payment import _approve_payment
            from shared.models import Package, User

            async with get_session() as session:
                payment = await session.get(Payment, payment_id)
                if not payment:
                    await interaction.response.send_message("❌ ไม่พบ Payment", ephemeral=True)
                    return
                if payment.status == PaymentStatus.CONFIRMED:
                    await interaction.response.send_message("⚠️ อนุมัติไปแล้ว", ephemeral=True)
                    return

                # Get user telegram_id
                user = await session.get(User, payment.user_id)
                if not user:
                    await interaction.response.send_message("❌ ไม่พบ User", ephemeral=True)
                    return
                user_tg_id = user.telegram_id

            # Approve payment & generate invite links
            import telegram as tg
            import os
            sales_bot = tg.Bot(token=os.environ.get("SALES_BOT_TOKEN", ""))
            invite_links = await _approve_payment(payment, user_tg_id, sales_bot)
            links_text = "\n".join(invite_links) if invite_links else "ไม่สามารถสร้างลิงก์ได้"

            # Send invite links to customer via Sales Bot
            try:
                await sales_bot.send_message(
                    chat_id=user_tg_id,
                    text=(
                        f"✅ <b>ชำระเงินสำเร็จค่ะ!</b>\n\n"
                        f"🔗 <b>ลิงก์เข้ากลุ่ม VIP:</b>\n{links_text}\n\n"
                        f"⚠️ ลิงก์แต่ละลิงก์ใช้ได้ 1 ครั้ง หมดอายุ 24 ชม.\n"
                        f"กรุณากดเข้าร่วมโดยเร็วนะคะ 🙏"
                    ),
                    parse_mode="HTML",
                )
            except Exception as exc:
                logger.warning("Failed to send invite to user: %s", exc)

            # Update embed
            embed = interaction.message.embeds[0] if interaction.message.embeds else None
            if embed:
                embed.title = "✅ APPROVED — อนุมัติแล้ว"
                embed.color = discord.Color.green()
                embed.set_footer(text=f"อนุมัติโดย {interaction.user.display_name}")

            await interaction.response.edit_message(embed=embed, view=None)
            await interaction.followup.send(
                f"✅ อนุมัติ #PAY{payment_id} แล้ว — ส่งลิงก์ให้ลูกค้าเรียบร้อย",
                ephemeral=True,
            )
        except Exception as exc:
            logger.error("Payment approve error: %s", exc)
            await interaction.response.send_message(f"❌ Error: {exc}", ephemeral=True)

    @discord.ui.button(label="ไม่อนุมัติ", emoji="❌", style=discord.ButtonStyle.danger, custom_id="pay_reject")
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        payment_id = _extract_payment_id(interaction)
        if not payment_id:
            await interaction.response.send_message("❌ ไม่พบ Payment ID", ephemeral=True)
            return

        try:
            async with get_session() as session:
                payment = await session.get(Payment, payment_id)
                if not payment:
                    await interaction.response.send_message("❌ ไม่พบ Payment", ephemeral=True)
                    return
                payment.status = PaymentStatus.REJECTED
                payment.reject_reason = f"ไม่อนุมัติโดย {interaction.user.display_name}"

            # Notify customer
            from shared.models import User
            import telegram as tg
            import os

            async with get_session() as session:
                user = await session.get(User, payment.user_id)
                if user:
                    try:
                        sales_bot = tg.Bot(token=os.environ.get("SALES_BOT_TOKEN", ""))
                        await sales_bot.send_message(
                            chat_id=user.telegram_id,
                            text=(
                                f"❌ <b>สลิปไม่ผ่านการตรวจสอบค่ะ</b>\n\n"
                                f"กรุณาส่งสลิปใหม่ หรือติดต่อแอดมินค่ะ\n"
                                f"หมายเลข: #PAY{payment_id}"
                            ),
                            parse_mode="HTML",
                        )
                    except Exception as exc:
                        logger.warning("Failed to notify user rejection: %s", exc)

            # Update embed
            embed = interaction.message.embeds[0] if interaction.message.embeds else None
            if embed:
                embed.title = "❌ REJECTED — ไม่อนุมัติ"
                embed.color = discord.Color.red()
                embed.set_footer(text=f"ไม่อนุมัติโดย {interaction.user.display_name}")

            await interaction.response.edit_message(embed=embed, view=None)
        except Exception as exc:
            logger.error("Payment reject error: %s", exc)
            await interaction.response.send_message(f"❌ Error: {exc}", ephemeral=True)

    @discord.ui.button(label="ตรวจเพิ่ม", emoji="🔍", style=discord.ButtonStyle.secondary, custom_id="pay_inspect")
    async def inspect(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        payment_id = _extract_payment_id(interaction)
        if not payment_id:
            await interaction.response.send_message("❌ ไม่พบ Payment ID", ephemeral=True)
            return

        async with get_session() as session:
            payment = await session.get(Payment, payment_id)
            if not payment:
                await interaction.response.send_message("❌ ไม่พบ Payment", ephemeral=True)
                return

            from shared.models import User, Package
            user = await session.get(User, payment.user_id)
            package = await session.get(Package, payment.package_id)

        embed = discord.Embed(
            title=f"🔍 รายละเอียด #PAY{payment_id}",
            color=discord.Color.blue(),
        )
        embed.add_field(name="สถานะ", value=payment.status.value, inline=True)
        embed.add_field(name="ยอด", value=format_thb(payment.amount), inline=True)
        embed.add_field(name="วิธีชำระ", value=payment.method.value, inline=True)
        if user:
            embed.add_field(name="ลูกค้า", value=f"@{user.username or user.first_name} (TG: {user.telegram_id})", inline=False)
        if package:
            embed.add_field(name="แพ็กเกจ", value=f"{package.name} ({format_thb(package.price)})", inline=False)
        embed.add_field(name="สร้างเมื่อ", value=str(payment.created_at)[:19], inline=True)

        await interaction.response.send_message(embed=embed, ephemeral=True)


def _extract_payment_id(interaction: discord.Interaction) -> int | None:
    """Extract payment_id from embed description (#PAYxx)."""
    import re
    if interaction.message and interaction.message.embeds:
        desc = interaction.message.embeds[0].description or ""
        match = re.search(r"#PAY(\d+)", desc)
        if match:
            return int(match.group(1))
    # Try from custom_id
    if hasattr(interaction, "data") and interaction.data:
        cid = interaction.data.get("custom_id", "")
        match = re.search(r"(\d+)$", cid)
        if match:
            return int(match.group(1))
    return None


async def setup(bot: commands.Bot) -> None:
    """Add the commands cog and persistent views to the bot."""
    await bot.add_cog(CharoenponCommands(bot))
    bot.add_view(PaymentApprovalView())
