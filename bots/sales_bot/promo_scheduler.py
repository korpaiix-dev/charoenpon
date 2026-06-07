"""Promo Scheduler — Scheduled broadcast for Trial & Referral promos.

Schedule (เวลาไทย):
- เสาร์ 21 มี.ค. 14:00 → Trial ฿99 โปรโมท 11 กลุ่มฟรี
- อาทิตย์ 22 มี.ค. 14:00 → Referral โปรโมท 11 กลุ่มฟรี
- ทุกศุกร์ 14:00 → Flash Sale (มีอยู่แล้ว)

ใช้ Content Bot token ส่ง (เหมือน Flash Sale)
"""

from __future__ import annotations

import asyncio
import io
import logging
import os
from datetime import datetime, timedelta, timezone

from telegram import Bot
from telegram.ext import ContextTypes

from shared.songkran_promo import is_songkran_promo_window

logger = logging.getLogger(__name__)

TH_TZ = timezone(timedelta(hours=7))

CONTENT_BOT_TOKEN = os.environ.get("CONTENT_BOT_TOKEN", "")

# 11 กลุ่มฟรี (same as content_bot/flash_sale_scheduler)
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

TRIAL_PROMO_TEXT = (
    '🆕 <b>VIP เจริญพร — ทดลอง 24 ชม.</b>\n'
    '\n'
    'ยังไม่เคยลอง VIP? ทดลองก่อนได้!\n'
    'แค่ ฿99 ดูคลิปเต็มไม่เบลอ 24 ชม.\n'
    '\n'
    '✅ คลิปเต็มไม่เบลอ\n'
    '✅ รวมกว่า 10,000 คลิป\n'
    '✅ ไม่ผูกมัด ไม่ต่ออัตโนมัติ\n'
    '\n'
    '━━━━━━━━━━━━━━━━━━\n'
    '📩 <b>สมัครเลย 👇</b>\n'
    '👉 <a href="tg://resolve?domain=NamwarnJarern_bot&start=trial">⚡ ทดลอง VIP เจริญพร ฿99 ⚡</a>\n'
    '━━━━━━━━━━━━━━━━━━\n'
    '\n'
    '⚠️ จำกัด 1 ครั้ง / 30 วัน!'
)

REFERRAL_PROMO_TEXT = (
    '🎁 <b>VIP เจริญพร — ชวนเพื่อนได้ VIP ฟรี!</b>\n'
    '\n'
    'สมาชิก VIP ชวนเพื่อนมาสมัคร\n'
    'ชวน 1 คน = ได้ VIP ฟรี 7 วัน!\n'
    'ชวน 5 คน = ได้ VIP ฟรี 30 วัน!\n'
    '\n'
    '✅ คลิปเต็มไม่เบลอ ทุกวัน\n'
    '✅ รวมกว่า 10,000 คลิป\n'
    '\n'
    '━━━━━━━━━━━━━━━━━━\n'
    '📩 <b>สมัคร VIP แล้วชวนเพื่อนเลย 👇</b>\n'
    '👉 <a href="tg://resolve?domain=NamwarnJarern_bot&start=packages">⚡ สมัคร VIP เจริญพร ⚡</a>\n'
    '━━━━━━━━━━━━━━━━━━'
)

SONGKRAN_PROMO_TEXT = (
    '💦 <b>โปรโมชั่นสงกรานต์ 7 วันเท่านั้น!</b>\n'
    '\n'
    'ซื้อ GOD MODE 3 เดือน ฿1,299 ในช่วงโปรนี้\n'
    'รับสิทธิ์เข้ากลุ่ม <b>โปรโมชั่นสงกรานต์</b> เพิ่มทันที\n'
    '\n'
    '✅ ครบ 7 ห้องหลัก + หนังซีรีส์\n'
    '✅ แถมกลุ่มโปรโมชั่นสงกรานต์สำหรับคนซื้อช่วงนี้\n'
    '✅ สิทธิ์ปกติ 90 วัน\n'
    '\n'
    '━━━━━━━━━━━━━━━━━━\n'
    '📩 <b>สมัครเลย 👇</b>\n'
    '👉 <a href="tg://resolve?domain=NamwarnJarern_bot&start=packages">💎 สมัคร GOD MODE 1,299</a>\n'
    '━━━━━━━━━━━━━━━━━━\n'
    '\n'
    '⚠️ โปรนี้เฉพาะคนที่ซื้อในช่วง 7 วันโปรเท่านั้น'
)


async def _create_promo_image(bot: Bot, overlay_title: str, overlay_subtitle: str) -> io.BytesIO | None:
    """สร้างภาพโปรโมท Flash Sale style จาก content_queue."""
    try:
        from bots.content_bot.main import fetch_latest_vip_content
        from PIL import Image, ImageDraw, ImageFilter, ImageFont

        content = await fetch_latest_vip_content()
        if not content:
            logger.info("No content in queue for promo image")
            return None

        # Download image
        file = await bot.get_file(content["file_id"])
        buf = io.BytesIO()
        await file.download_to_memory(buf)
        buf.seek(0)

        img = Image.open(buf).convert("RGBA")
        w, h = img.size

        # Blur
        blurred = img.filter(ImageFilter.GaussianBlur(radius=6))

        # Load font
        font_paths = [
            "/usr/share/fonts/truetype/thai-tlwg/Garuda-Bold.ttf",
            "/usr/share/fonts/truetype/thai-tlwg/Sarabun-Bold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        ]

        def _load_font(size: int):
            for fp in font_paths:
                try:
                    return ImageFont.truetype(fp, size=size)
                except (OSError, IOError):
                    continue
            return ImageFont.load_default()

        font_title = _load_font(max(w // 10, 40))
        font_sub = _load_font(max(w // 16, 26))

        # Overlay
        overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)

        # Top bar
        top_h = int(h * 0.22)
        draw.rectangle([(0, 0), (w, top_h)], fill=(0, 0, 0, 170))

        # Bottom bar
        bot_h = int(h * 0.22)
        bot_y = h - bot_h
        draw.rectangle([(0, bot_y), (w, h)], fill=(0, 0, 0, 180))

        # Title text (top center)
        title_bbox = draw.textbbox((0, 0), overlay_title, font=font_title)
        title_tw = title_bbox[2] - title_bbox[0]
        title_th = title_bbox[3] - title_bbox[1]
        draw.text(
            ((w - title_tw) // 2, (top_h - title_th) // 2),
            overlay_title, font=font_title, fill=(255, 215, 0, 255),
        )

        # Subtitle text (bottom center)
        sub_bbox = draw.textbbox((0, 0), overlay_subtitle, font=font_sub)
        sub_tw = sub_bbox[2] - sub_bbox[0]
        sub_th = sub_bbox[3] - sub_bbox[1]
        draw.text(
            ((w - sub_tw) // 2, bot_y + (bot_h - sub_th) // 2),
            overlay_subtitle, font=font_sub, fill=(255, 255, 255, 240),
        )

        # Composite
        result = Image.alpha_composite(blurred, overlay)

        # Watermark
        wm_text = "VIP เจริญพร"
        wm_font = _load_font(max(w // 12, 28))
        if isinstance(wm_font, ImageFont.ImageFont):
            wm_text = "VIP Charoenpon"
        tmp = Image.new("RGBA", (w * 2, h * 2), (0, 0, 0, 0))
        tmp_draw = ImageDraw.Draw(tmp)
        wm_bbox = tmp_draw.textbbox((0, 0), wm_text, font=wm_font)
        tw = wm_bbox[2] - wm_bbox[0]
        th = wm_bbox[3] - wm_bbox[1]
        spacing_x = tw + max(tw // 2, 60)
        spacing_y = th + max(th * 3, 120)
        for y_pos in range(-h, h * 2, spacing_y):
            for x_pos in range(-w, w * 2, spacing_x):
                tmp_draw.text((x_pos, y_pos), wm_text, font=wm_font, fill=(255, 255, 255, 60))
        rotated = tmp.rotate(30, resample=Image.BICUBIC, expand=False)
        cx, cy = rotated.width // 2, rotated.height // 2
        half_w, half_h = w // 2, h // 2
        watermark = rotated.crop((cx - half_w, cy - half_h, cx + half_w, cy + half_h))
        if watermark.size != result.size:
            watermark = watermark.resize(result.size, Image.LANCZOS)
        result = Image.alpha_composite(result, watermark)

        result = result.convert("RGB")
        out = io.BytesIO()
        out.name = "promo.jpg"
        result.save(out, format="JPEG", quality=80)
        out.seek(0)
        return out

    except Exception as exc:
        logger.warning("Failed to create promo image: %s", exc)
        return None


async def _broadcast_to_free_groups(bot: Bot, text: str, image: io.BytesIO | None) -> tuple[int, int]:
    """Broadcast ข้อความไปยัง 11 กลุ่มฟรี. Returns (success, failed)."""
    success = 0
    failed = 0
    for group_id in FREE_GROUPS:
        try:
            if image:
                image.seek(0)
                await bot.send_photo(
                    chat_id=group_id,
                    photo=image,
                    caption=text,
                    parse_mode="HTML",
                )
            else:
                await bot.send_message(
                    chat_id=group_id,
                    text=text,
                    parse_mode="HTML",
                    disable_web_page_preview=True,
                )
            success += 1
            await asyncio.sleep(1)
        except Exception as exc:
            logger.error("Failed to send promo to group %d: %s", group_id, exc)
            failed += 1
    return success, failed


async def broadcast_trial_promo(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Scheduled job: Broadcast Trial ฿99 promo to free groups."""
    logger.info("Broadcasting Trial promo to free groups...")

    token = CONTENT_BOT_TOKEN
    if not token:
        logger.error("CONTENT_BOT_TOKEN not set")
        return

    bot = Bot(token=token)
    await bot.initialize()
    image = await _create_promo_image(bot, "TRIAL VIP", "ทดลอง 24 ชม. แค่ ฿99")

    success, failed = await _broadcast_to_free_groups(bot, TRIAL_PROMO_TEXT, image)
    logger.info("Trial promo broadcast: %d success, %d failed", success, failed)


async def broadcast_referral_promo(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Scheduled job: Broadcast Referral promo to free groups."""
    logger.info("Broadcasting Referral promo to free groups...")

    token = CONTENT_BOT_TOKEN
    if not token:
        logger.error("CONTENT_BOT_TOKEN not set")
        return

    bot = Bot(token=token)
    await bot.initialize()
    image = await _create_promo_image(bot, "INVITE FRIENDS", "ชวนเพื่อน = VIP ฟรี!")

    success, failed = await _broadcast_to_free_groups(bot, REFERRAL_PROMO_TEXT, image)
    logger.info("Referral promo broadcast: %d success, %d failed", success, failed)


async def broadcast_songkran_promo(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Scheduled job: Broadcast Songkran 1299 promo to free groups during promo window only."""
    if not is_songkran_promo_window():
        logger.info("Songkran promo window inactive, skipping broadcast")
        return

    token = CONTENT_BOT_TOKEN
    if not token:
        logger.error("CONTENT_BOT_TOKEN not set")
        return

    logger.info("Broadcasting Songkran promo to free groups...")
    bot = Bot(token=token)
    await bot.initialize()
    image = await _create_promo_image(bot, "SONGKRAN 1299", "แถมกลุ่มโปรสงกรานต์ 7 วัน")

    success, failed = await _broadcast_to_free_groups(bot, SONGKRAN_PROMO_TEXT, image)
    logger.info("Songkran promo broadcast: %d success, %d failed", success, failed)
