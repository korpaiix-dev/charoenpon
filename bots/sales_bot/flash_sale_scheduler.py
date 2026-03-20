"""Flash Sale Scheduler - ตั้งเวลาเปิด/ปิด Flash Friday อัตโนมัติ.

- ทุกวันศุกร์ 21:00 ไทย: สร้าง flash_sale record + เปิด + โปรโมทพร้อมภาพ Flash Sale → 11 กลุ่มฟรี
- ทุกวันศุกร์ 22:00, 23:00 ไทย: remind flash sale พร้อมภาพใหม่ (ถ้ายัง active)
- ทุกวันเสาร์ 00:00 ไทย: ปิด flash sale
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select, update as sa_update
from telegram import Bot
from telegram.ext import ContextTypes

from shared.database import get_session
from shared.models import FlashSale, Package, PackageTier

# Lazy import to avoid circular deps
_create_flash_sale_image = None
_fetch_latest_vip_content = None


def _get_flash_image_deps():
    global _create_flash_sale_image, _fetch_latest_vip_content
    if _create_flash_sale_image is None:
        from bots.content_bot.main import create_flash_sale_image, fetch_latest_vip_content
        _create_flash_sale_image = create_flash_sale_image
        _fetch_latest_vip_content = fetch_latest_vip_content
    return _create_flash_sale_image, _fetch_latest_vip_content

logger = logging.getLogger(__name__)

TH_TZ = timezone(timedelta(hours=7))

# 11 กลุ่มฟรี (same as content_bot)
FREE_GROUPS = [
    -1003540998287,
    -1003777838783,
    -1003733093219,
    -1003772512123,
    -1003706880995,
    -1003740382332,
    -1003861673687,
    -1003841389411,
    -1003876840312,
    -1003723154612,
    -1003789621076,
]

FLASH_SALE_PROMO = (
    "⚡ <b>VIP เจริญพร — FLASH FRIDAY</b> ⚡\n"
    "\n"
    "สมาชิก VIP 30 วัน ลดเหลือ ฿199 (ปกติ ฿300)\n"
    "⏰ คืนนี้เท่านั้น 21:00 - 23:59\n"
    "🔥 จำกัด 30 คนเท่านั้น!\n"
    "\n"
    "✅ คลิปเต็มไม่เบลอ ทุกวัน\n"
    "✅ คลิป Exclusive ก่อนใคร\n"
    "✅ รวมกว่า 10,000 คลิป\n"
    "\n"
    "━━━━━━━━━━━━━━━━━━\n"
    '📩 <b>สมัครเลย 👇</b>\n'
    '👉 <a href="tg://resolve?domain=NamwarnJarern_bot&start=flashsale">⚡ สมัคร VIP เจริญพร ฿199 ⚡</a>\n'
    "━━━━━━━━━━━━━━━━━━\n"
    "\n"
    "เมื่อหมดก็หมด ไม่มีรอบสอง!"
)


async def start_flash_sale(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Job: เปิด Flash Sale ทุกวันศุกร์ 21:00 ไทย."""
    logger.info("🔥 Starting Flash Friday!")

    now_th = datetime.now(TH_TZ)

    try:
        # Get VIP 30 วัน package (id=1, tier=300)
        async with get_session() as session:
            pkg_result = await session.execute(
                select(Package).where(Package.tier == PackageTier.TIER_300)
            )
            package = pkg_result.scalar_one_or_none()
            if not package:
                logger.error("Package TIER_300 not found!")
                return

            # Deactivate any existing active flash sales
            await session.execute(
                sa_update(FlashSale).where(FlashSale.is_active == True).values(is_active=False)  # noqa: E712
            )

            # Create new flash sale record
            starts_at = now_th.replace(hour=21, minute=0, second=0, microsecond=0).astimezone(timezone.utc).replace(tzinfo=None)
            ends_at = (now_th.replace(hour=23, minute=59, second=59, microsecond=0)).astimezone(timezone.utc).replace(tzinfo=None)

            flash = FlashSale(
                name="VIP 30 วัน (Flash Friday)",
                package_id=package.id,
                flash_price=Decimal("199"),
                original_price=package.price,
                total_slots=30,
                sold_slots=0,
                starts_at=starts_at,
                ends_at=ends_at,
                is_active=True,
            )
            session.add(flash)

        logger.info("Flash sale record created, broadcasting to %d free groups", len(FREE_GROUPS))

        # ลองสร้างภาพ Flash Sale จากรูปใน content_queue
        bot = context.bot
        flash_image = None
        try:
            create_img, fetch_content = _get_flash_image_deps()
            content = await fetch_content()
            if content:
                flash_image = await create_img(bot, content["file_id"])
                logger.info("Flash sale image created from content_id=%d", content["id"])
            else:
                logger.info("No content in queue for flash sale image, sending text-only")
        except Exception as exc:
            logger.warning("Failed to create flash sale image: %s — falling back to text-only", exc)

        # Broadcast promo to all free groups
        success = 0
        failed = 0
        for group_id in FREE_GROUPS:
            try:
                if flash_image:
                    flash_image.seek(0)
                    await bot.send_photo(
                        chat_id=group_id,
                        photo=flash_image,
                        caption=FLASH_SALE_PROMO,
                        parse_mode="HTML",
                    )
                else:
                    await bot.send_message(
                        chat_id=group_id,
                        text=FLASH_SALE_PROMO,
                        parse_mode="HTML",
                    )
                success += 1
                await asyncio.sleep(1)  # Rate limit: 1 msg/sec between groups
            except Exception as exc:
                logger.error("Failed to send flash sale promo to %s: %s", group_id, exc)
                failed += 1

        logger.info("Flash sale promo broadcast: %d success, %d failed (with_image=%s)", success, failed, flash_image is not None)

        # Notify admin group
        admin_group_id = int(os.environ.get("ADMIN_GROUP_CHAT_ID", "-1003830920430"))
        admin_token = os.environ.get("ADMIN_BOT_TOKEN", "")
        if admin_token:
            try:
                admin_bot = Bot(token=admin_token)
                await admin_bot.send_message(
                    chat_id=admin_group_id,
                    text=(
                        "⚡ <b>Flash Friday เปิดแล้ว!</b>\n\n"
                        "📦 VIP 30 วัน ฿199 (ปกติ ฿300)\n"
                        "🔥 จำกัด 30 slot\n"
                        f"📡 โปรโมทไป {success}/{len(FREE_GROUPS)} กลุ่มฟรี\n\n"
                        "⏰ เปิด 21:00 - 23:59 น."
                    ),
                    parse_mode="HTML",
                )
            except Exception as exc:
                logger.error("Failed to notify admin about flash sale start: %s", exc)

    except Exception as exc:
        logger.error("Failed to start flash sale: %s", exc)


async def end_flash_sale(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Job: ปิด Flash Sale ทุกวันเสาร์ 00:00 ไทย."""
    logger.info("Ending Flash Friday")

    try:
        async with get_session() as session:
            # Get active flash sale for reporting
            result = await session.execute(
                select(FlashSale).where(FlashSale.is_active == True).order_by(FlashSale.id.desc()).limit(1)  # noqa: E712
            )
            flash = result.scalar_one_or_none()

            sold = flash.sold_slots if flash else 0
            total = flash.total_slots if flash else 30

            # Deactivate all flash sales
            await session.execute(
                sa_update(FlashSale).where(FlashSale.is_active == True).values(is_active=False)  # noqa: E712
            )

        # Notify admin with results
        admin_group_id = int(os.environ.get("ADMIN_GROUP_CHAT_ID", "-1003830920430"))
        admin_token = os.environ.get("ADMIN_BOT_TOKEN", "")
        if admin_token:
            try:
                admin_bot = Bot(token=admin_token)
                revenue = sold * 199
                await admin_bot.send_message(
                    chat_id=admin_group_id,
                    text=(
                        "🔒 <b>Flash Friday ปิดแล้ว!</b>\n\n"
                        f"📊 ผลการขาย: <b>{sold}/{total}</b> slot\n"
                        f"💰 รายได้: <b>฿{revenue:,}</b>\n"
                        f"{'🎉 ขายหมด!' if sold >= total else f'เหลือ {total - sold} slot'}\n\n"
                        "ไว้ศุกร์หน้ามาใหม่! 🔥"
                    ),
                    parse_mode="HTML",
                )
            except Exception as exc:
                logger.error("Failed to notify admin about flash sale end: %s", exc)

    except Exception as exc:
        logger.error("Failed to end flash sale: %s", exc)


FLASH_SALE_REMIND = (
    "⚡ <b>VIP เจริญพร — FLASH FRIDAY ยังไม่หมด!</b> ⚡\n"
    "\n"
    "สมาชิก VIP 30 วัน เหลือ ฿199 เท่านั้น!\n"
    "⏰ เหลือเวลาอีกไม่นาน ปิด 23:59!\n"
    "\n"
    "━━━━━━━━━━━━━━━━━━\n"
    '📩 <b>สมัครเลย 👇</b>\n'
    '👉 <a href="tg://resolve?domain=NamwarnJarern_bot&start=flashsale">⚡ สมัคร VIP เจริญพร ฿199 ⚡</a>\n'
    "━━━━━━━━━━━━━━━━━━\n"
    "\n"
    "หมดแล้วหมดเลย! 🔥"
)


async def remind_flash_sale(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Job: ส่ง remind flash sale ทุกชั่วโมง (22:00, 23:00) พร้อมภาพ Flash Sale ใหม่."""
    logger.info("🔔 Flash sale reminder triggered")

    # เช็คว่ายังมี flash sale active อยู่ไหม
    try:
        async with get_session() as session:
            result = await session.execute(
                select(FlashSale).where(FlashSale.is_active == True).limit(1)  # noqa: E712
            )
            flash = result.scalar_one_or_none()
            if not flash:
                logger.info("No active flash sale, skipping reminder")
                return
            remaining = flash.total_slots - flash.sold_slots
    except Exception as exc:
        logger.error("Failed to check flash sale for reminder: %s", exc)
        return

    if remaining <= 0:
        logger.info("Flash sale sold out, skipping reminder")
        return

    # สร้างภาพ Flash Sale ใหม่ (ใช้รูปคนละรูปจากรอบก่อน)
    bot = context.bot
    flash_image = None
    try:
        create_img, fetch_content = _get_flash_image_deps()
        content = await fetch_content()
        if content:
            flash_image = await create_img(bot, content["file_id"])
            logger.info("Flash sale reminder image created from content_id=%d", content["id"])
    except Exception as exc:
        logger.warning("Failed to create reminder image: %s", exc)

    remind_text = (
        f"⚡ FLASH FRIDAY ยังไม่หมด! ⚡\n"
        f"\n"
        f"VIP 30 วัน เหลือ ฿199 เท่านั้น!\n"
        f"🔥 เหลืออีก {remaining} slot!\n"
        f"⏰ ปิด 23:59 คืนนี้!\n"
        f"\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f'📩 <b>กดสมัครเลย 👇</b>\n'
        f'👉 <a href="tg://resolve?domain=NamwarnJarern_bot&start=flashsale">⚡ สมัคร VIP เจริญพร ฿199 ⚡</a>\n'
        f"━━━━━━━━━━━━━━━━━━\n"
        f"\n"
        f"หมดแล้วหมดเลย! 🔥"
    )

    success = 0
    for group_id in FREE_GROUPS:
        try:
            if flash_image:
                flash_image.seek(0)
                await bot.send_photo(
                    chat_id=group_id,
                    photo=flash_image,
                    caption=remind_text,
                    parse_mode="HTML",
                )
            else:
                await bot.send_message(
                    chat_id=group_id,
                    text=remind_text,
                    parse_mode="HTML",
                )
            success += 1
            await asyncio.sleep(1)
        except Exception as exc:
            logger.error("Failed to send flash reminder to %s: %s", group_id, exc)

    logger.info("Flash sale reminder sent: %d/%d groups (with_image=%s)", success, len(FREE_GROUPS), flash_image is not None)
