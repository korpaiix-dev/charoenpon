"""Preview Generator — สร้างรูปเบลอจาก content_queue สำหรับโปรโมท.

- ดาวน์โหลดรูปจาก Telegram file_id
- Blur ล่าง 60%, บน 40% คมชัด
- เพิ่ม watermark "🔒 สมัคร VIP ดูเต็ม" (fallback: "VIP ONLY")
- อัพโหลดกลับ Telegram → เก็บ preview_file_id ใน content_previews table
"""

from __future__ import annotations

import asyncio
import io
import logging
import os
from datetime import datetime, timedelta, timezone

from PIL import Image, ImageDraw, ImageFilter, ImageFont
from sqlalchemy import text
from telegram import Bot
from telegram.ext import ContextTypes

from shared.database import get_session

logger = logging.getLogger(__name__)

from shared.tz import TH_TZ

SALES_BOT_TOKEN: str = os.environ.get("SALES_BOT_TOKEN", "")
ADMIN_GROUP_ID = int(os.environ.get("ADMIN_GROUP_CHAT_ID", ""))

# ─── DB Migration ────────────────────────────────────────────────────────────

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS content_previews (
    id SERIAL PRIMARY KEY,
    content_id INTEGER NOT NULL REFERENCES content_queue(id),
    preview_file_id TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
)
"""

CREATE_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS ix_content_previews_content_id ON content_previews(content_id)
"""


async def ensure_tables() -> None:
    """Create content_previews table if not exists."""
    async with get_session() as session:
        await session.execute(text(CREATE_TABLE_SQL))
        await session.execute(text(CREATE_INDEX_SQL))
        await session.commit()


# ─── Image Processing ────────────────────────────────────────────────────────

def _add_blur_and_watermark(img_bytes: bytes) -> bytes:
    """Blur bottom 60% + add watermark text.

    Returns processed image as bytes (JPEG).
    """
    img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    width, height = img.size

    # Split: top 40% stays clear, bottom 60% gets blurred
    split_y = int(height * 0.4)

    top = img.crop((0, 0, width, split_y))
    bottom = img.crop((0, split_y, width, height))

    # Apply GaussianBlur to bottom part
    bottom_blurred = bottom.filter(ImageFilter.GaussianBlur(radius=15))

    # Paste back
    result = img.copy()
    result.paste(top, (0, 0))
    result.paste(bottom_blurred, (0, split_y))

    # Add watermark
    draw = ImageDraw.Draw(result)

    # Try Thai watermark first, fallback to ASCII
    watermark_text = "VIP ONLY"
    font_size = max(30, width // 12)

    try:
        # Try to load a system font that supports Thai
        for font_path in [
            "/usr/share/fonts/truetype/noto/NotoSansThai-Bold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
        ]:
            if os.path.exists(font_path):
                font = ImageFont.truetype(font_path, font_size)
                # Test if font supports Thai
                try:
                    draw.textbbox((0, 0), "🔒 สมัคร VIP ดูเต็ม", font=font)
                    watermark_text = "🔒 สมัคร VIP ดูเต็ม"
                except Exception:
                    watermark_text = "VIP ONLY"
                break
        else:
            font = ImageFont.load_default()
    except Exception:
        font = ImageFont.load_default()

    # Calculate text position (center)
    bbox = draw.textbbox((0, 0), watermark_text, font=font)
    text_width = bbox[2] - bbox[0]
    text_height = bbox[3] - bbox[1]
    x = (width - text_width) // 2
    y = (height - text_height) // 2

    # Draw semi-transparent white text with shadow
    draw.text((x + 2, y + 2), watermark_text, fill=(0, 0, 0, 128), font=font)
    draw.text((x, y), watermark_text, fill=(255, 255, 255, 200), font=font)

    # Save as JPEG
    output = io.BytesIO()
    result.save(output, format="JPEG", quality=85)
    output.seek(0)
    return output.read()


# ─── Core Functions ──────────────────────────────────────────────────────────

async def generate_preview(bot: Bot, content_id: int) -> str | None:
    """Generate a blurred preview for a content_queue item.

    Returns preview_file_id or None on failure.
    """
    async with get_session() as session:
        # Check if preview already exists
        existing = await session.execute(
            text("SELECT preview_file_id FROM content_previews WHERE content_id = :cid LIMIT 1"),
            {"cid": content_id},
        )
        row = existing.fetchone()
        if row:
            return row.preview_file_id

        # Get content info
        content = await session.execute(
            text("SELECT id, file_id, file_type FROM content_queue WHERE id = :cid"),
            {"cid": content_id},
        )
        content_row = content.fetchone()
        if not content_row:
            logger.warning("Content %d not found", content_id)
            return None

        if content_row.file_type != "photo":
            logger.info("Content %d is %s, skipping preview", content_id, content_row.file_type)
            return None

    # Download photo from Telegram
    try:
        tg_file = await bot.get_file(content_row.file_id)
        file_bytes = await tg_file.download_as_bytearray()
    except Exception as exc:
        logger.error("Failed to download file for content %d: %s", content_id, exc)
        return None

    # Process image
    try:
        preview_bytes = _add_blur_and_watermark(bytes(file_bytes))
    except Exception as exc:
        logger.error("Failed to process image for content %d: %s", content_id, exc)
        return None

    # Upload preview back to Telegram (send to admin group, then get file_id)
    try:
        msg = await bot.send_photo(
            chat_id=ADMIN_GROUP_ID,
            photo=preview_bytes,
            caption=f"🖼 Preview generated for content #{content_id}",
        )
        preview_file_id = msg.photo[-1].file_id
    except Exception as exc:
        logger.error("Failed to upload preview for content %d: %s", content_id, exc)
        return None

    # Save to DB
    async with get_session() as session:
        await session.execute(
            text("INSERT INTO content_previews (content_id, preview_file_id) VALUES (:cid, :fid)"),
            {"cid": content_id, "fid": preview_file_id},
        )
        await session.commit()

    logger.info("Preview generated for content %d: %s", content_id, preview_file_id[:20])
    return preview_file_id


async def batch_generate_previews(bot: Bot, limit: int = 20) -> int:
    """Generate previews for recent photos that don't have one yet.

    Returns number of previews generated.
    """
    await ensure_tables()

    async with get_session() as session:
        result = await session.execute(
            text("""
                SELECT cq.id
                FROM content_queue cq
                LEFT JOIN content_previews cp ON cp.content_id = cq.id
                WHERE cq.file_type = 'photo'
                  AND cp.id IS NULL
                ORDER BY cq.created_at DESC
                LIMIT :lim
            """),
            {"lim": limit},
        )
        content_ids = [row.id for row in result.fetchall()]

    generated = 0
    for cid in content_ids:
        preview_id = await generate_preview(bot, cid)
        if preview_id:
            generated += 1
        await asyncio.sleep(1)  # Rate limit

    logger.info("Batch preview generation: %d/%d generated", generated, len(content_ids))
    return generated


# ─── Scheduler Job ───────────────────────────────────────────────────────────

async def run_preview_generator_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Scheduled job: batch generate previews for new content."""
    bot = context.bot
    logger.info("🖼 Preview generator job started")

    try:
        count = await batch_generate_previews(bot, limit=20)
        logger.info("Preview generator job done: %d previews created", count)
    except Exception as exc:
        logger.error("Preview generator job failed: %s", exc)
