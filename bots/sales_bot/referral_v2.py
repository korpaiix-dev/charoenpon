"""Referral v2 — ระบบชวนเพื่อนรับรางวัล tier ใหม่.

รางวัล:
- 1 referral = +7 วัน
- 3 referrals = +30 วัน
- 5 referrals = +90 วัน

Weekly DM: ทุกวันจันทร์ 15:00 ไทย ส่ง DM VIP active แจ้งสถิติ referral
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from telegram import Bot
from telegram.error import Forbidden
from telegram.ext import ContextTypes

from shared.database import get_session

logger = logging.getLogger(__name__)

from shared.tz import TH_TZ
from shared.admin_alert import _admin_group_id

ADMIN_GROUP_ID = _admin_group_id()
MAX_DM_PER_RUN = 50
DM_DELAY_SECONDS = 2

# Reward tiers: (required_count, reward_days)
REWARD_TIERS = [
    (5, 90),
    (3, 30),
    (1, 7),
]


# ─── Reward Logic ────────────────────────────────────────────────────────────



# ─── Weekly Reminder DM ─────────────────────────────────────────────────────

def _next_tier_info(current_count: int) -> str:
    """Get info about next reward tier."""
    if current_count >= 5:
        return "🏆 คุณได้รับรางวัลสูงสุดแล้ว! ชวนต่อได้อีก +7 วัน/คน"
    elif current_count >= 3:
        remaining = 5 - current_count
        return f"🎯 ชวนอีก {remaining} คน รับ +90 วันฟรี!"
    elif current_count >= 1:
        remaining = 3 - current_count
        return f"🎯 ชวนอีก {remaining} คน รับ +30 วันฟรี!"
    else:
        return "🎯 ชวน 1 คน รับ +7 วันฟรี!"


async def _get_active_vip_users() -> list[dict]:
    """Get active VIP users for referral reminder."""
    async with get_session() as session:
        result = await session.execute(
            text("""
                SELECT DISTINCT u.id as user_id, u.telegram_id, u.first_name, u.username, u.referral_code
                FROM users u
                JOIN subscriptions s ON s.user_id = u.id
                WHERE s.status = 'ACTIVE'
                  AND s.end_date > NOW()
                  AND u.is_banned = false
                ORDER BY u.id
            """)
        )
        return [
            {
                "user_id": row.user_id,
                "telegram_id": row.telegram_id,
                "first_name": row.first_name or row.username or "คุณ",
                "referral_code": row.referral_code,
            }
            for row in result.fetchall()
        ]


async def _get_referral_count(user_id: int) -> int:
    """Get completed referral count for a user."""
    async with get_session() as session:
        result = await session.execute(
            text("""
                SELECT COUNT(*) FROM referrals
                WHERE referrer_user_id = :uid AND status IN ('COMPLETED', 'REWARDED')
            """),
            {"uid": user_id},
        )
        return result.scalar() or 0


async def send_referral_reminder_v2(context: ContextTypes.DEFAULT_TYPE) -> None:
    return  # AUDIT: referral ปิด
    """Weekly DM to active VIP users about referral program."""
    bot = context.bot
    now_th = datetime.now(TH_TZ)
    logger.info("🔗 Referral reminder v2 job started at %s", now_th.strftime("%Y-%m-%d %H:%M"))

    users = await _get_active_vip_users()
    sent_count = 0
    dm_budget = MAX_DM_PER_RUN

    for user in users:
        if dm_budget <= 0:
            break

        ref_count = await _get_referral_count(user["user_id"])
        tier_info = _next_tier_info(ref_count)

        ref_code = user["referral_code"] or "ยังไม่มี (พิมพ์ /referral)"

        msg = (
            f"🔗 <b>Referral Update</b>\n"
            f"\n"
            f"คุณ {user['first_name']} ชวนเพื่อนมา VIP แล้ว <b>{ref_count}</b> คน\n"
            f"\n"
            f"📊 <b>รางวัลที่ได้:</b>\n"
        )

        if ref_count >= 5:
            msg += f"  ✅ 1 คน = +7 วัน\n  ✅ 3 คน = +30 วัน\n  ✅ 5 คน = +90 วัน\n"
        elif ref_count >= 3:
            msg += f"  ✅ 1 คน = +7 วัน\n  ✅ 3 คน = +30 วัน\n  ⬜ 5 คน = +90 วัน\n"
        elif ref_count >= 1:
            msg += f"  ✅ 1 คน = +7 วัน\n  ⬜ 3 คน = +30 วัน\n  ⬜ 5 คน = +90 วัน\n"
        else:
            msg += f"  ⬜ 1 คน = +7 วัน\n  ⬜ 3 คน = +30 วัน\n  ⬜ 5 คน = +90 วัน\n"

        msg += (
            f"\n"
            f"{tier_info}\n"
            f"\n"
            f"🔑 โค้ดชวนเพื่อน: <code>{ref_code}</code>\n"
            f'👉 <a href="tg://resolve?domain=NamwarnJarern_bot&start=referral">ดูรายละเอียด</a>'
        )

        try:
            await bot.send_message(
                chat_id=user["telegram_id"],
                text=msg,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
            sent_count += 1
            dm_budget -= 1
        except Forbidden:
            logger.info("Cannot DM user %d — blocked bot", user["telegram_id"])
        except Exception as exc:
            logger.error("Failed to send referral reminder to %d: %s", user["telegram_id"], exc)

        await asyncio.sleep(DM_DELAY_SECONDS)

    logger.info("Referral reminder v2 done: sent %d/%d", sent_count, len(users))
