"""Trial Promo DM System — ส่ง DM โปรโมท Trial ฿99 ให้ลูกค้าใหม่ที่ไม่เคยจ่าย.

- ดึงลูกค้าที่ไม่มี subscription + ยังไม่เคย DM trial promo
- Rate limit: 30 DM/วัน, delay 3 วินาที
- ห้ามส่งซ้ำ (เช็ค trial_dm_log)
- ทำงานทุกวัน 00:30 ไทย (17:30 UTC) — หลัง Flash Sale ปิด 30 นาที
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select, exists
from telegram import Bot
from telegram.error import Forbidden
from telegram.ext import ContextTypes

from shared.database import get_session
from shared.models import (
    Subscription,
    SubscriptionStatus,
    TrialDmLog,
    User,
)

logger = logging.getLogger(__name__)

TH_TZ = timezone(timedelta(hours=7))

# Config
MAX_DM_PER_DAY = 30
DM_DELAY_SECONDS = 3

ADMIN_GROUP_ID = int(os.environ.get("ADMIN_GROUP_CHAT_ID", "-1003830920430"))


def _build_trial_promo_message(first_name: str) -> str:
    """สร้างข้อความ DM โปรโมท Trial ฿99."""
    return (
        f"สวัสดีค่ะ คุณ {first_name} 💕\n"
        f"\n"
        f"ยังไม่เคยลอง VIP เจริญพร?\n"
        f"\n"
        f"🔥 ทดลอง 24 ชม. แค่ ฿99!\n"
        f"ดูคลิปเต็มไม่เบลอก่อนตัดสินใจ\n"
        f"\n"
        f"✅ คลิปเต็มไม่เบลอ 24 ชม.\n"
        f"✅ รวมกว่า 10,000 คลิป\n"
        f"✅ ไม่ผูกมัด ไม่ต่ออัตโนมัติ\n"
        f"\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f'📩 <b>ลองเลย 👇</b>\n'
        f'👉 <a href="tg://resolve?domain=jarernAD1_bot&start=trial">⚡ ทดลอง VIP เจริญพร ฿99 ⚡</a>\n'
        f"━━━━━━━━━━━━━━━━━━\n"
        f"\n"
        f"⚠️ สิทธิ์นี้จำกัด 1 ครั้ง / 30 วัน!"
    )


async def get_new_customers_no_trial_dm(limit: int = MAX_DM_PER_DAY) -> list[dict]:
    """ดึงลูกค้าที่ไม่มี subscription + ยังไม่เคย DM trial promo.

    - ไม่มี subscription ใดๆ เลย (ไม่ว่า status ไหน)
    - ไม่อยู่ใน trial_dm_log
    - ไม่ถูกแบน
    - ORDER BY created_at ASC (เก่าสุดก่อน)
    """
    async with get_session() as session:
        # Subquery: user_ids ที่มี subscription (ไม่ว่า status ไหน)
        has_subscription = (
            select(Subscription.user_id)
            .scalar_subquery()
        )

        # Subquery: user_ids ที่เคย DM trial promo แล้ว
        already_dm = (
            select(TrialDmLog.user_id)
            .scalar_subquery()
        )

        result = await session.execute(
            select(User)
            .where(
                User.id.notin_(has_subscription),
                User.id.notin_(already_dm),
                User.is_banned == False,  # noqa: E712
            )
            .order_by(User.created_at.asc())
            .limit(limit)
        )
        users = result.scalars().all()

    return [
        {
            "user_id": u.id,
            "telegram_id": u.telegram_id,
            "first_name": u.first_name or u.username or "ลูกค้า",
            "username": u.username,
        }
        for u in users
    ]


async def send_trial_promo_dm(bot: Bot, user: dict) -> bool:
    """ส่ง DM โปรโมท Trial ฿99.

    Returns True if sent successfully, False if failed.
    """
    message = _build_trial_promo_message(user["first_name"])

    try:
        await bot.send_message(
            chat_id=user["telegram_id"],
            text=message,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
    except Forbidden:
        logger.info(
            "Cannot DM user %s (tg:%d) — never started bot or blocked",
            user.get("username", "?"), user["telegram_id"],
        )
        return False
    except Exception as exc:
        logger.error(
            "Failed to DM user %s (tg:%d): %s",
            user.get("username", "?"), user["telegram_id"], exc,
        )
        return False

    # Log to DB
    async with get_session() as session:
        log_entry = TrialDmLog(
            user_id=user["user_id"],
            telegram_id=user["telegram_id"],
        )
        session.add(log_entry)

    logger.info(
        "TRIAL DM sent: user_id=%d tg=%d name=%s",
        user["user_id"], user["telegram_id"], user["first_name"],
    )
    return True


async def _notify_discord_system_log(message: str) -> None:
    """ส่ง log ไป Discord #system-logs."""
    discord_token = os.environ.get("DISCORD_BOT_TOKEN", "")
    discord_ch = os.environ.get("DISCORD_CH_SYSTEM_LOGS", "")
    if not discord_token or not discord_ch:
        return
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(
                f"https://discord.com/api/v10/channels/{discord_ch}/messages",
                headers={
                    "Authorization": f"Bot {discord_token}",
                    "Content-Type": "application/json",
                },
                json={"content": message},
            )
    except Exception as exc:
        logger.warning("Discord system log failed: %s", exc)


async def run_trial_promo_dm_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Scheduled job: ส่ง DM Trial ฿99 ให้ลูกค้าใหม่ที่ไม่เคยจ่าย.

    - ดึง 30 คนที่ยังไม่เคย DM
    - Rate limit: delay 3 วิ ระหว่างแต่ละ DM
    - Skip คนที่ bot ส่ง DM ไม่ได้ (Forbidden)
    - Log ทุก DM → trial_dm_log + Discord #system-logs
    - สรุปส่ง admin
    """
    bot = context.bot
    now_th = datetime.now(TH_TZ)
    logger.info("🔄 TRIAL PROMO DM job started at %s", now_th.strftime("%Y-%m-%d %H:%M"))

    # ดึงลูกค้าใหม่ที่ยังไม่เคย DM
    customers = await get_new_customers_no_trial_dm(limit=MAX_DM_PER_DAY)

    if not customers:
        logger.info("TRIAL PROMO DM: ไม่มีลูกค้าใหม่ที่ต้องส่ง DM")
        return

    total_sent = 0
    total_failed = 0

    for customer in customers:
        success = await send_trial_promo_dm(bot, customer)
        if success:
            total_sent += 1
        else:
            total_failed += 1
        await asyncio.sleep(DM_DELAY_SECONDS)

    # ---- Summary ----
    summary = (
        f"📊 TRIAL PROMO DM Summary ({now_th.strftime('%d/%m/%Y')})\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"ส่งสำเร็จ: {total_sent} คน\n"
        f"ส่งไม่ได้: {total_failed} คน\n"
        f"คงเหลือ (ยังไม่เคย DM): รอ batch ถัดไป\n"
        f"━━━━━━━━━━━━━━━━━━"
    )
    logger.info(summary)

    # Discord #system-logs
    await _notify_discord_system_log(summary)

    # Admin group notification
    admin_token = os.environ.get("ADMIN_BOT_TOKEN", "")
    if admin_token and (total_sent > 0 or total_failed > 0):
        try:
            admin_bot = Bot(token=admin_token)
            admin_text = (
                f"📬 <b>Trial Promo DM Report</b>\n"
                f"📅 {now_th.strftime('%d/%m/%Y')}\n\n"
                f"วันนี้ส่ง DM Trial ฿99 ให้ <b>{total_sent}</b> คน\n"
                f"ส่งไม่ได้ (blocked/ไม่เคย /start): {total_failed} คน"
            )
            await admin_bot.send_message(
                chat_id=ADMIN_GROUP_ID,
                text=admin_text,
                parse_mode="HTML",
            )
        except Exception as exc:
            logger.error("Failed to send TRIAL PROMO admin notification: %s", exc)
