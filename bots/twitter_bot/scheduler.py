"""Twitter Auto-Post Scheduler — โพสต์อัตโนมัติวันละ 4 ครั้ง.

Schedule (เวลาไทย):
- 12:00 — โพสต์ 1 (teaser + ลิงก์)
- 18:00 — โพสต์ 2 (teaser + ลิงก์)
- 21:00 — โพสต์ 3 (teaser + ลิงก์)
- 23:00 — โพสต์ 4 (teaser + ลิงก์)

ดึงรูปจาก content_queue → รูปเต็มไม่เบลอ + watermark → โพสต์ Twitter
สร้าง caption AI สั้น ≤ 280 chars + hashtag + ลิงก์ Telegram
"""

from __future__ import annotations

import asyncio
import io
import logging
import os
import random
import re
import tempfile
from datetime import datetime, time as dt_time, timedelta, timezone
from pathlib import Path

import httpx
from PIL import Image, ImageDraw, ImageFilter, ImageFont

logging.basicConfig(
    format="[%(asctime)s] [TWITTER_BOT] [%(levelname)s] %(message)s",
    level=logging.INFO,
    datefmt="%Y-%m-%d %H:%M:%S",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("tweepy").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

TH_TZ = timezone(timedelta(hours=7))

# Telegram bot link (NamwarnJarern_bot ไม่ใช่ jarernAD1_bot!)
TELEGRAM_LINK = "https://t.me/NamwarnJarern_bot"

# Hashtags
HASHTAGS = "#VIPเจริญพร #18plus #คลิปไทย #VIPTelegram"

# AI Model
AI_MODEL = "google/gemini-2.0-flash-lite-001"

# Temp directory for blurred images
TEMP_DIR = Path(tempfile.gettempdir()) / "charoenpon_twitter"
TEMP_DIR.mkdir(exist_ok=True)


# ──────────────────────────────────────────────
# AI Caption Generator (สั้น ≤ 280 chars)
# ──────────────────────────────────────────────

async def generate_twitter_caption() -> str:
    """AI สร้าง caption สำหรับ Twitter — สั้น กระชับ ≤ 140 chars (เหลือที่ให้ hashtag+link)."""
    try:
        from shared.api_cost_tracker import call_openrouter

        prompt = (
            "เขียน caption ภาษาไทยสั้นมากๆ 1 บรรทัด สำหรับ tweet โปรโมท VIP เจริญพร\n\n"
            "กฎเหล็ก:\n"
            "- ตอบแค่ caption 1 อันเท่านั้น ห้ามให้ตัวเลือก\n"
            "- ห้ามขึ้นต้นด้วย 'นี่คือ' 'ตัวเลือก' 'แคปชั่น:'\n"
            "- ความยาวไม่เกิน 100 ตัวอักษร\n"
            "- ใช้อีโมจิ 1-2 ตัว\n"
            "- ห้ามใส่ลิงก์ ห้ามใส่ราคา ห้ามใส่ hashtag\n"
            "- ห้ามใช้คำว่า 'ซื้อ' ให้ใช้ 'สมัคร'\n"
            "- เร้าใจ สร้างความอยากรู้ อยากดู"
        )

        data = await call_openrouter(
            model=AI_MODEL,
            messages=[{"role": "user", "content": prompt}],
            caller="twitter_bot/caption",
            temperature=0.9,
            max_tokens=80,
        )
        caption = data["choices"][0]["message"]["content"].strip().strip('"')

        # Post-processing
        caption = re.sub(
            r'^(ตัวเลือก(ที่\s*)?\d+[:.]\s*|แคปชั่น[:.]\s*|caption[:.]\s*|นี่คือ\s*)',
            '', caption, flags=re.IGNORECASE,
        )
        lines = [
            l.strip() for l in caption.split('\n')
            if l.strip() and not l.strip().startswith(('1.', '2.', '3.', 'ตัวเลือก'))
        ]
        caption = lines[0] if lines else caption.split('\n')[0]

        # ตัดให้ไม่เกิน 120 chars (เหลือที่ให้ link + hashtag)
        if len(caption) > 120:
            caption = caption[:117] + "..."

        return caption.strip()
    except Exception as exc:
        logger.error("AI caption failed: %s", exc)

    # Fallback captions
    fallbacks = [
        "🔥 ของดีมาแล้ว คลิปเต็มไม่เบลอ ทุกวัน",
        "😈 แอบดูนิดนึง... อยากดูต่อ สมัคร VIP เจริญพร",
        "🔞 คลิปเด็ดวันนี้ ดูฟรีได้แค่นี้~",
        "💦 คลิปเต็มกว่า 10,000 คลิป VIP เจริญพร",
        "🫣 แค่ตัวอย่าง... ของจริงอยู่ใน VIP",
    ]
    return random.choice(fallbacks)


def build_tweet_text(caption: str) -> str:
    """รวม caption + link + hashtag ให้ ≤ 280 chars."""
    # Twitter นับ URL เป็น 23 chars เสมอ (t.co)
    # Format: caption\n\nสมัครเลย 👇\nlink\n\nhashtags
    tweet = f"{caption}\n\nสมัครเลย 👇\n{TELEGRAM_LINK}\n\n{HASHTAGS}"

    # Safety check
    if len(tweet) > 280:
        # ตัด hashtag ลง
        tweet = f"{caption}\n\nสมัคร 👇 {TELEGRAM_LINK}\n{HASHTAGS}"
    if len(tweet) > 280:
        tweet = f"{caption}\n\n{TELEGRAM_LINK}\n#VIPเจริญพร #18plus"
    if len(tweet) > 280:
        tweet = f"{caption}\n{TELEGRAM_LINK}"

    return tweet


# ──────────────────────────────────────────────
# Image Processing (ไม่เบลอ + watermark เท่านั้น)
# ──────────────────────────────────────────────

async def prepare_image_for_twitter(file_id: str) -> str | None:
    """ดาวน์โหลดรูปจาก Telegram file_id → ไม่เบลอ — X อนุญาต 18+ + watermark → save temp file.

    Returns: local file path หรือ None ถ้าพัง
    """
    from telegram import Bot

    bot_token = os.environ.get("CONTENT_BOT_TOKEN", "")
    if not bot_token:
        logger.error("CONTENT_BOT_TOKEN not set")
        return None

    try:
        bot = Bot(token=bot_token)
        file = await bot.get_file(file_id)
        buf = io.BytesIO()
        await file.download_to_memory(buf)
        buf.seek(0)

        img = Image.open(buf)
        # ไม่เบลอ — X อนุญาต 18+ แค่ใส่ watermark
        # ไม่เบลอ — X อนุญาต 18+
        blurred = img

        # Watermark
        blurred = blurred.convert("RGBA")
        wm_text = "VIP เจริญพร"

        font_size = max(blurred.width // 14, 24)
        font = None
        font_paths = [
            "/usr/share/fonts/truetype/thai-tlwg/Garuda-Bold.ttf",
            "/usr/share/fonts/truetype/thai-tlwg/Sarabun-Bold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        ]
        for fp in font_paths:
            try:
                font = ImageFont.truetype(fp, size=font_size)
                break
            except (OSError, IOError):
                continue
        if font is None:
            font = ImageFont.load_default()
            wm_text = "VIP Charoenpon"

        # Tile watermark
        w, h = blurred.size
        tmp = Image.new("RGBA", (w * 2, h * 2), (0, 0, 0, 0))
        tmp_draw = ImageDraw.Draw(tmp)

        bbox = tmp_draw.textbbox((0, 0), wm_text, font=font)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]

        spacing_x = tw + max(tw // 2, 60)
        spacing_y = th + max(th * 3, 120)

        for y_pos in range(-h, h * 2, spacing_y):
            for x_pos in range(-w, w * 2, spacing_x):
                tmp_draw.text(
                    (x_pos, y_pos), wm_text, font=font,
                    fill=(255, 255, 255, 70),
                )

        rotated = tmp.rotate(30, resample=Image.BICUBIC, expand=False)
        cx, cy = rotated.width // 2, rotated.height // 2
        half_w, half_h = w // 2, h // 2
        watermark = rotated.crop((cx - half_w, cy - half_h, cx + half_w, cy + half_h))

        if watermark.size != blurred.size:
            watermark = watermark.resize(blurred.size, Image.LANCZOS)

        blurred = Image.alpha_composite(blurred, watermark)
        blurred = blurred.convert("RGB")

        # Save to temp file
        filepath = TEMP_DIR / f"twitter_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
        blurred.save(str(filepath), format="JPEG", quality=75)
        logger.info("Blurred image saved: %s", filepath)
        return str(filepath)

    except Exception as exc:
        logger.error("Failed to prepare image: %s", exc)
        return None


# ──────────────────────────────────────────────
# Discord Logging
# ──────────────────────────────────────────────

async def _send_discord_log(content: str) -> None:
    """Send log to Discord."""
    token = os.environ.get("DISCORD_BOT_TOKEN", "")
    ch = os.environ.get("DISCORD_CH_CONTENT_LOG", "")
    if not token or not ch:
        return
    try:
        now_th = datetime.now(TH_TZ)
        embed = {
            "title": "🐦 Twitter Bot",
            "description": content,
            "color": 0x1DA1F2,
            "footer": {"text": f"⊙ เจริญพร | {now_th.strftime('%d/%m/%Y %H:%M')}"},
        }
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(
                f"https://discord.com/api/v10/channels/{ch}/messages",
                headers={"Authorization": f"Bot {token}", "Content-Type": "application/json"},
                json={"embeds": [embed]},
            )
    except Exception as e:
        logger.error("Failed to send Discord log: %s", e)


# ──────────────────────────────────────────────
# Scheduled Post Function
# ──────────────────────────────────────────────

async def scheduled_twitter_post(context=None) -> dict | None:
    """โพสต์ Twitter อัตโนมัติ — ดึงรูปจาก content_queue → เบลอ → โพสต์.

    สามารถเรียกจาก APScheduler job หรือ telegram job_queue ก็ได้.

    Returns:
        tweet data dict on success, None on failure.
    """
    from bots.twitter_bot.poster import post_tweet, post_tweet_with_image

    now_th = datetime.now(TH_TZ)
    round_label = now_th.strftime("%H:%M")
    logger.info("🐦 Twitter scheduled post triggered (round=%s)", round_label)

    # Generate caption
    caption = await generate_twitter_caption()
    tweet_text = build_tweet_text(caption)
    logger.info("Tweet text (%d chars): %s", len(tweet_text), tweet_text[:100])

    # ลองดึงรูปจาก content_queue
    image_path = None
    content_id = None
    try:
        from shared.database import get_session
        from shared.models import ContentQueue
        from sqlalchemy import select

        async with get_session() as session:
            result = await session.execute(
                select(ContentQueue)
                .where(ContentQueue.is_used == False)
                .order_by(ContentQueue.created_at.asc())
                .limit(1)
            )
            row = result.scalar_one_or_none()

        if row:
            content_id = row.id
            file_id = row.file_id
            logger.info("Found content_id=%d for Twitter post", content_id)
            image_path = await prepare_image_for_twitter(file_id)
    except Exception as exc:
        logger.error("Failed to get content from queue: %s", exc)

    # โพสต์
    tweet_data = None
    if image_path:
        tweet_data = post_tweet_with_image(tweet_text, image_path)

        # Cleanup temp file
        try:
            os.remove(image_path)
        except Exception:
            pass

        # ไม่ mark as used — ใช้คนละ pool กับมิน (มินดึงจาก queue เหมือนกันแต่คนละรอบ)
        # ถ้าต้องการ mark ให้เปิด comment ข้างล่าง:
        # if tweet_data and content_id:
        #     from bots.content_bot.main import mark_content_used
        #     await mark_content_used(content_id)
    else:
        # ไม่มีรูป → โพสต์แค่ข้อความ
        logger.info("No image available, posting text-only tweet")
        tweet_data = post_tweet(tweet_text)

    # Log result
    if tweet_data:
        tweet_id = tweet_data.get("id", "?")
        logger.info("✅ Twitter post success: tweet_id=%s (round=%s)", tweet_id, round_label)
        await _send_discord_log(
            f"✅ **Twitter Post Success** [round={round_label}]\n"
            f"Tweet ID: `{tweet_id}`\n"
            f"Text: {tweet_text[:200]}{'...' if len(tweet_text) > 200 else ''}\n"
            f"Image: {'Yes' if image_path else 'No'}"
        )
    else:
        logger.error("❌ Twitter post failed (round=%s)", round_label)
        await _send_discord_log(
            f"❌ **Twitter Post Failed** [round={round_label}]\n"
            f"Text: {tweet_text[:200]}"
        )

    return tweet_data


# ──────────────────────────────────────────────
# Standalone Runner (สำหรับ run แยก service)
# ──────────────────────────────────────────────

async def run_scheduler() -> None:
    """Run Twitter scheduler as standalone async service ด้วย APScheduler."""
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from shared.database import init_db

    await init_db()
    logger.info("DB initialized for Twitter Bot")

    scheduler = AsyncIOScheduler(timezone="Asia/Bangkok")

    # Schedule 4 รอบต่อวัน (เวลาไทย)
    schedule_config = [
        {"hour": 12, "minute": 0, "id": "twitter_12"},
        {"hour": 18, "minute": 0, "id": "twitter_18"},
        {"hour": 21, "minute": 0, "id": "twitter_21"},
        {"hour": 23, "minute": 0, "id": "twitter_23"},
    ]

    for conf in schedule_config:
        scheduler.add_job(
            scheduled_twitter_post,
            "cron",
            hour=conf["hour"],
            minute=conf["minute"],
            id=conf["id"],
            misfire_grace_time=300,
        )
        logger.info("Scheduled Twitter post at %02d:%02d", conf["hour"], conf["minute"])

    scheduler.start()
    logger.info("🐦 Twitter Bot Scheduler started — 4 posts/day")

    # Keep running
    try:
        while True:
            await asyncio.sleep(3600)
    except (KeyboardInterrupt, SystemExit):
        scheduler.shutdown()
        logger.info("Twitter Bot Scheduler stopped")


def main() -> None:
    """Entry point."""
    asyncio.run(run_scheduler())


if __name__ == "__main__":
    main()
