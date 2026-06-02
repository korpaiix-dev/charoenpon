"""Referral Broadcast - ส่ง DM + โพสต์กลุ่มโปรโมท Referral System.

ส่ง 3 ช่องทาง:
1. DM VIP Active ทุกคน (rate limit 30/วัน, delay 3 วิ)
2. โพสต์ 11 กลุ่มฟรี (พร้อมภาพ)
3. โพสต์กลุ่ม VIP

รันครั้งเดียว: docker exec charoenpon-sales-bot python -m bots.sales_bot.referral_broadcast
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, text
from telegram import Bot
from telegram.error import BadRequest, Forbidden, RetryAfter

# Add project root to path
sys.path.insert(0, "/app")

from shared.database import get_session, init_db, close_db
from shared.models import (
    Subscription,
    SubscriptionStatus,
    User,
)

logging.basicConfig(
    format="%(asctime)s [REFERRAL_BROADCAST] %(levelname)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

SALES_BOT_TOKEN = os.environ.get("SALES_BOT_TOKEN", "")

# 11 กลุ่มฟรี
FREE_GROUPS = [
    -1003733093219,
    -1003772512123,
    -1003706880995,
    -1003740382332,
    -1003861673687,
    -1003841389411,
    -1003723154612,
]

# VIP Groups
VIP_GROUPS = [
    -1003765565847,   # G300
    -1003785888021,   # G500
    -1003625687303,   # VGOD
    -1003873486550,   # INTER
    -1003829556450,   # SSS
    -1003760176676,   # SERIES
]

DM_TEXT = (
    "🎁 ข่าวดี! VIP เจริญพร เปิดระบบชวนเพื่อนแล้ว!\n\n"
    "ชวนเพื่อนมาสมัคร VIP = ได้วันฟรีเพิ่ม!\n\n"
    "🎯 ชวน 1 คน = +7 วัน VIP ฟรี\n"
    "🎯 ชวน 5 คน = +30 วัน VIP ฟรี!\n\n"
    "━━━━━━━━━━━━━━━━━━\n"
    '📩 <b>รับลิงก์ชวนเพื่อนเลย 👇</b>\n'
    '👉 <a href="tg://resolve?domain=NamwarnJarern_bot&start=invite">🎁 กดรับลิงก์ชวนเพื่อน</a>\n'
    "━━━━━━━━━━━━━━━━━━"
)

FREE_GROUP_TEXT = (
    '🎁 <b>VIP เจริญพร — ชวนเพื่อนได้ VIP ฟรี!</b>\n\n'
    "สมาชิก VIP ชวนเพื่อนมาสมัคร\n"
    "ชวน 1 คน = ได้ VIP ฟรี 7 วัน!\n"
    "ชวน 5 คน = ได้ VIP ฟรี 30 วัน!\n\n"
    "✅ คลิปเต็มไม่เบลอ ทุกวัน\n"
    "✅ รวมกว่า 10,000 คลิป\n\n"
    "━━━━━━━━━━━━━━━━━━\n"
    '📩 <b>สมัคร VIP แล้วชวนเพื่อนเลย 👇</b>\n'
    '👉 <a href="tg://resolve?domain=NamwarnJarern_bot&start=packages">⚡ สมัคร VIP เจริญพร ⚡</a>\n'
    "━━━━━━━━━━━━━━━━━━"
)

VIP_GROUP_TEXT = (
    "🎁 ระบบชวนเพื่อนเปิดแล้ว!\n\n"
    "กด /invite เพื่อรับลิงก์ชวนเพื่อน\n"
    "ชวน 1 คน = +7 วัน ฟรี!\n"
    "ชวน 5 คน = +30 วัน ฟรี!"
)

DM_DAILY_LIMIT = 30
DM_DELAY = 3  # seconds


async def _get_vip_active_telegram_ids() -> list[int]:
    """Get telegram_ids of all VIP Active users."""
    async with get_session() as session:
        result = await session.execute(
            select(User.telegram_id)
            .join(Subscription, Subscription.user_id == User.id)
            .where(
                Subscription.status == SubscriptionStatus.ACTIVE,
                Subscription.end_date > datetime.utcnow(),
            )
            .distinct()
        )
        return [row[0] for row in result.fetchall()]


async def _check_already_sent(telegram_id: int) -> bool:
    """Check if referral broadcast was already sent to this user."""
    async with get_session() as session:
        result = await session.execute(
            text("SELECT 1 FROM referral_broadcast_log WHERE telegram_id = :tg_id LIMIT 1"),
            {"tg_id": telegram_id},
        )
        return result.fetchone() is not None


async def _mark_sent(telegram_id: int) -> None:
    """Mark referral broadcast as sent."""
    async with get_session() as session:
        await session.execute(
            text("INSERT INTO referral_broadcast_log (telegram_id) VALUES (:tg_id) ON CONFLICT DO NOTHING"),
            {"tg_id": telegram_id},
        )
        await session.commit()


async def _create_broadcast_log_table():
    """Create tracking table if not exists."""
    async with get_session() as session:
        await session.execute(
            text("""
                CREATE TABLE IF NOT EXISTS referral_broadcast_log (
                    telegram_id BIGINT PRIMARY KEY,
                    sent_at TIMESTAMP DEFAULT NOW()
                )
            """)
        )
        await session.commit()


async def _create_referral_promo_image() -> bytes | None:
    """Return the 02_referral.png banner bytes (new branding)."""
    from pathlib import Path as _P
    img_path = _P("/root/charoenpon/assets/campaigns/02_referral.png")
    if not img_path.exists():
        logger.warning("Referral campaign image not found at %s", img_path)
        return None
    try:
        return img_path.read_bytes()
    except Exception as exc:
        logger.warning("Failed to read referral promo image: %s", exc)
        return None



async def broadcast_dm_vip_active(bot: Bot) -> dict:
    """Send DM to all VIP Active users (rate limited)."""
    vip_ids = await _get_vip_active_telegram_ids()
    logger.info("Found %d VIP Active users", len(vip_ids))

    sent = 0
    failed = 0
    skipped = 0

    for tg_id in vip_ids:
        if sent >= DM_DAILY_LIMIT:
            logger.info("Daily DM limit reached (%d), stopping", DM_DAILY_LIMIT)
            break

        # Check if already sent
        if await _check_already_sent(tg_id):
            skipped += 1
            continue

        try:
            await bot.send_message(
                chat_id=tg_id,
                text=DM_TEXT,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
            await _mark_sent(tg_id)
            sent += 1
            logger.info("DM sent to %d (%d/%d)", tg_id, sent, DM_DAILY_LIMIT)
            await asyncio.sleep(DM_DELAY)

        except RetryAfter as e:
            logger.warning("Rate limited, waiting %d seconds", e.retry_after)
            await asyncio.sleep(e.retry_after)
        except (Forbidden, BadRequest) as e:
            logger.warning("Cannot DM %d: %s", tg_id, e)
            await _mark_sent(tg_id)  # Don't retry
            failed += 1
        except Exception as e:
            logger.error("DM failed for %d: %s", tg_id, e)
            failed += 1

    return {"sent": sent, "failed": failed, "skipped": skipped, "remaining": len(vip_ids) - sent - skipped - failed}


async def broadcast_free_groups(bot: Bot) -> dict:
    """Post referral promo to 11 free groups with image."""
    # Try to create promo image
    image_bytes = await _create_referral_promo_image()

    sent = 0
    failed = 0

    for group_id in FREE_GROUPS:
        try:
            if image_bytes:
                import io
                await bot.send_photo(
                    chat_id=group_id,
                    photo=io.BytesIO(image_bytes),
                    caption=FREE_GROUP_TEXT,
                    parse_mode="HTML",
                )
            else:
                await bot.send_message(
                    chat_id=group_id,
                    text=FREE_GROUP_TEXT,
                    parse_mode="HTML",
                    disable_web_page_preview=True,
                )
            sent += 1
            await asyncio.sleep(1)
        except Exception as e:
            logger.error("Failed to post to group %d: %s", group_id, e)
            failed += 1

    return {"sent": sent, "failed": failed, "total": len(FREE_GROUPS)}


async def broadcast_vip_groups(bot: Bot) -> dict:
    """Post referral announcement to VIP groups."""
    sent = 0
    failed = 0

    for group_id in VIP_GROUPS:
        try:
            await bot.send_message(
                chat_id=group_id,
                text=VIP_GROUP_TEXT,
                parse_mode="HTML",
            )
            sent += 1
            await asyncio.sleep(1)
        except Exception as e:
            logger.error("Failed to post to VIP group %d: %s", group_id, e)
            failed += 1

    return {"sent": sent, "failed": failed, "total": len(VIP_GROUPS)}


async def main():
    """Run the full referral broadcast."""
    if not SALES_BOT_TOKEN:
        logger.error("SALES_BOT_TOKEN not set")
        return

    await init_db()
    await _create_broadcast_log_table()

    bot = Bot(token=SALES_BOT_TOKEN)
    await bot.initialize()

    logger.info("=" * 50)
    logger.info("Starting Referral Broadcast")
    logger.info("=" * 50)

    # 1. DM VIP Active
    logger.info("--- Phase 1: DM VIP Active ---")
    dm_result = await broadcast_dm_vip_active(bot)
    logger.info("DM Result: sent=%d, failed=%d, skipped=%d, remaining=%d",
                dm_result["sent"], dm_result["failed"], dm_result["skipped"], dm_result["remaining"])

    # 2. Post to free groups
    logger.info("--- Phase 2: Free Groups ---")
    free_result = await broadcast_free_groups(bot)
    logger.info("Free Groups: sent=%d, failed=%d", free_result["sent"], free_result["failed"])

    # 3. Post to VIP groups
    logger.info("--- Phase 3: VIP Groups ---")
    vip_result = await broadcast_vip_groups(bot)
    logger.info("VIP Groups: sent=%d, failed=%d", vip_result["sent"], vip_result["failed"])

    # Notify admin
    try:
        import telegram as tg
        admin_bot = tg.Bot(token=os.environ.get("ADMIN_BOT_TOKEN", ""))
        await admin_bot.initialize()
        admin_group = int(os.environ.get("ADMIN_GROUP_CHAT_ID", "-1003830920430"))
        await admin_bot.send_message(
            chat_id=admin_group,
            text=(
                "📢 <b>Referral Broadcast สรุป</b>\n\n"
                f"📩 DM VIP Active: ส่ง {dm_result['sent']} | ซ้ำ {dm_result['skipped']} | พลาด {dm_result['failed']}\n"
                f"  📋 เหลือ: {dm_result['remaining']} (ส่งเพิ่มรอบหน้า)\n"
                f"🏘 กลุ่มฟรี: {free_result['sent']}/{free_result['total']}\n"
                f"⭐ กลุ่ม VIP: {vip_result['sent']}/{vip_result['total']}"
            ),
            parse_mode="HTML",
        )
    except Exception as e:
        logger.error("Failed to notify admin: %s", e)

    await close_db()
    logger.info("Broadcast complete!")


if __name__ == "__main__":
    asyncio.run(main())
