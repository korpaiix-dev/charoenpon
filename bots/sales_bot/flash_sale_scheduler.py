"""Flash Sale Scheduler - ตั้งเวลาเปิด/ปิด Flash Friday อัตโนมัติ.

- ทุกวันศุกร์ 21:00 ไทย: สร้าง flash_sale record + เปิด + โปรโมทพร้อมภาพ Flash Sale → 11 กลุ่มฟรี
- ทุกวันศุกร์ 22:00, 23:00 ไทย: remind flash sale พร้อมภาพใหม่ (ถ้ายัง active)
- ทุกวันเสาร์ 00:00 ไทย: ปิด flash sale + log analytics
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select, update as sa_update, text
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
    -1003733093219,
    -1003772512123,
    -1003706880995,
    -1003740382332,
    -1003861673687,
    -1003841389411,
    -1003723154612,
    -1003805660760,
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

# ─── DB Migration: flash_sale_analytics table ────────────────────────────────

ANALYTICS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS flash_sale_analytics (
    id SERIAL PRIMARY KEY,
    flash_sale_id INTEGER NOT NULL,
    total_clicks INTEGER NOT NULL DEFAULT 0,
    total_orders INTEGER NOT NULL DEFAULT 0,
    total_revenue NUMERIC(12, 2) NOT NULL DEFAULT 0,
    conversion_rate NUMERIC(5, 4) NOT NULL DEFAULT 0,
    analyzed_at TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_fsa_flash_sale_id ON flash_sale_analytics(flash_sale_id);
"""


async def _ensure_analytics_table() -> None:
    """Create flash_sale_analytics table if not exists."""
    try:
        async with get_session() as session:
            await session.execute(text(ANALYTICS_TABLE_SQL))
            await session.commit()
    except Exception as exc:
        logger.warning("flash_sale_analytics migration (may already exist): %s", exc)


# ─── Flash Sale Analytics ────────────────────────────────────────────────────

async def analyze_flash_sale_performance() -> dict:
    """วิเคราะห์ performance ของ Flash Sales ทั้งหมด.

    Returns dict:
    {
        "sales": [
            {"id": 1, "name": "...", "sold": 10, "total": 30, "revenue": 1990,
             "starts_at": ..., "day_of_week": "Friday", "hour": 21},
            ...
        ],
        "best_day": "Friday",
        "best_hour": 21,
        "avg_conversion": 0.33,
        "total_revenue": 5970,
    }
    """
    await _ensure_analytics_table()

    async with get_session() as session:
        result = await session.execute(
            text("""
                SELECT id, name, sold_slots, total_slots, flash_price, original_price,
                       starts_at, ends_at, is_active
                FROM flash_sales
                ORDER BY starts_at DESC
                LIMIT 20
            """)
        )
        rows = result.fetchall()

    if not rows:
        return {"sales": [], "best_day": None, "best_hour": None, "avg_conversion": 0, "total_revenue": 0}

    sales = []
    day_revenue: dict[str, float] = {}
    hour_revenue: dict[int, float] = {}
    total_revenue = 0
    total_sold = 0
    total_slots = 0

    for row in rows:
        revenue = float(row.flash_price) * row.sold_slots
        starts_utc = row.starts_at
        # Convert to Thai time for day/hour analysis
        if starts_utc:
            starts_th = starts_utc.replace(tzinfo=timezone.utc).astimezone(TH_TZ) if starts_utc.tzinfo is None else starts_utc.astimezone(TH_TZ)
            day_name = starts_th.strftime("%A")
            hour = starts_th.hour
        else:
            day_name = "Unknown"
            hour = 0

        sales.append({
            "id": row.id,
            "name": row.name,
            "sold": row.sold_slots,
            "total": row.total_slots,
            "revenue": revenue,
            "starts_at": starts_utc,
            "day_of_week": day_name,
            "hour": hour,
        })

        day_revenue[day_name] = day_revenue.get(day_name, 0) + revenue
        hour_revenue[hour] = hour_revenue.get(hour, 0) + revenue
        total_revenue += revenue
        total_sold += row.sold_slots
        total_slots += row.total_slots

    best_day = max(day_revenue, key=day_revenue.get) if day_revenue else None
    best_hour = max(hour_revenue, key=hour_revenue.get) if hour_revenue else None
    avg_conversion = total_sold / total_slots if total_slots > 0 else 0

    return {
        "sales": sales,
        "best_day": best_day,
        "best_hour": best_hour,
        "avg_conversion": round(avg_conversion, 4),
        "total_revenue": total_revenue,
    }


async def _log_flash_sale_analytics(flash_sale_id: int, sold: int, total: int, revenue: float) -> None:
    """Log analytics เมื่อ flash sale จบ."""
    await _ensure_analytics_table()
    conv_rate = sold / total if total > 0 else 0

    try:
        async with get_session() as session:
            await session.execute(
                text("""
                    INSERT INTO flash_sale_analytics (flash_sale_id, total_clicks, total_orders, total_revenue, conversion_rate)
                    VALUES (:fsid, 0, :orders, :revenue, :conv)
                """),
                {"fsid": flash_sale_id, "orders": sold, "revenue": revenue, "conv": round(conv_rate, 4)},
            )
            await session.commit()
        logger.info("Flash sale analytics logged: flash_sale_id=%d sold=%d revenue=%.2f", flash_sale_id, sold, revenue)
    except Exception as exc:
        logger.error("Failed to log flash sale analytics: %s", exc)


# ─── Start Flash Sale ────────────────────────────────────────────────────────

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
                await admin_bot.initialize()
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
    """Job: ปิด Flash Sale ทุกวันเสาร์ 00:00 ไทย + log analytics."""
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
            flash_id = flash.id if flash else None
            flash_price = float(flash.flash_price) if flash else 199

            # Deactivate all flash sales
            await session.execute(
                sa_update(FlashSale).where(FlashSale.is_active == True).values(is_active=False)  # noqa: E712
            )

        # Log analytics
        if flash_id:
            revenue = sold * flash_price
            await _log_flash_sale_analytics(flash_id, sold, total, revenue)

        # Notify admin with results
        admin_group_id = int(os.environ.get("ADMIN_GROUP_CHAT_ID", "-1003830920430"))
        admin_token = os.environ.get("ADMIN_BOT_TOKEN", "")
        if admin_token:
            try:
                admin_bot = Bot(token=admin_token)
                await admin_bot.initialize()
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
