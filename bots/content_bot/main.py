"""Content Bot (มิน) — ดึงคอนเทนต์จาก VIP แล้วโพสต์ teaser ลงกลุ่มฟรี.

Bot: @jarernAD4_bot "นักแจกทีเด็ด ไม่เด็ดไม่แจก"
Schedule: 12:30 / 18:00 / 21:00 / 23:00 / 01:00 (เวลาไทย)
Source: authorized users ส่งรูปใน DM
Target: 11 กลุ่มฟรี
"""

from __future__ import annotations

import asyncio
import io
import logging
import os
import random
import re
from datetime import datetime, time as dt_time, timedelta, timezone

import httpx
from PIL import Image, ImageDraw, ImageFilter, ImageFont
from sqlalchemy import select, update
from telegram import Bot, InputMediaPhoto, Update
from telegram.ext import Application, ContextTypes, MessageHandler, filters

from shared.database import get_session, init_db
from shared.models import ContentQueue

logging.basicConfig(
    format="[%(asctime)s] [CONTENT_BOT] [%(levelname)s] %(message)s",
    level=logging.INFO,
    datefmt="%Y-%m-%d %H:%M:%S",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

CONTENT_BOT_TOKEN = os.environ.get("CONTENT_BOT_TOKEN", "")
VIP_GROUP_ID = int(os.environ.get("VIP_SOURCE_GROUP_ID", "-1003765565847"))

TH_TZ = timezone(timedelta(hours=7))

# Authorized users ที่ส่งรูปให้ bot ได้ (from env: ADMIN_TELEGRAM_IDS)
AUTHORIZED_SENDERS = [int(x.strip()) for x in os.environ.get("ADMIN_TELEGRAM_IDS", "8502597269").split(",") if x.strip()]

# 11 กลุ่มฟรี
FREE_GROUPS = [
    -1003540998287,  # แจกกลุ่มฟรี
    -1003777838783,  # งานไทยสบายตัว
    -1003733093219,  # ไทยเอามัน
    -1003772512123,  # เย็ดมัน
    -1003706880995,  # วุ่ยหนุ่ม
    -1003740382332,  # นักตำแตก
    -1003861673687,  # ตรงนี้มีกี
    -1003841389411,  # มาดูไรกัน
    -1003876840312,  # หลุมหลบภัยบิน
    -1003723154612,  # โห่โห่ซ้อ
    -1003789621076,  # เจริญพร
]

AI_MODEL = "google/gemini-2.0-flash-lite-001"
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")


async def _send_discord_content_log(content: str) -> None:
    """Send log to Discord #มิน-คอนเทนต์ via Bot API."""
    token = os.environ.get("DISCORD_BOT_TOKEN", "")
    ch = os.environ.get("DISCORD_CH_CONTENT_LOG", "")
    if not token or not ch:
        return
    try:
        now_th = datetime.now(TH_TZ)
        embed = {
            "title": "📝 Content Bot — มิน",
            "description": content,
            "color": 0x9B59B6,
            "footer": {"text": f"⊙ เจริญพร | {now_th.strftime('%d/%m/%Y %H:%M')}"},
        }
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(
                f"https://discord.com/api/v10/channels/{ch}/messages",
                headers={"Authorization": f"Bot {token}", "Content-Type": "application/json"},
                json={"embeds": [embed]},
            )
    except Exception as e:
        logger.error("Failed to send Discord content log: %s", e)


async def generate_teaser_caption() -> str:
    """AI สร้าง caption teaser เสียวๆ ล่อใจ."""
    from shared.api_cost_tracker import call_openrouter

    prompt = (
        "เขียน caption ภาษาไทยสั้นๆ 1-2 บรรทัดสำหรับโพสต์ teaser 18+ "
        "ให้คนอยากดูเต็มๆ แล้วสมัคร VIP เจริญพร\n\n"
        "กฎเหล็ก:\n"
        "- ตอบแค่ caption 1 อันเท่านั้น ห้ามให้ตัวเลือก ห้ามมีข้อ 1. 2. 3.\n"
        "- ห้ามขึ้นต้นด้วย 'นี่คือ' 'ตัวเลือก' 'แคปชั่น:' หรือคำนำใดๆ\n"
        "- ตอบแค่ข้อความ caption ตรงๆ เลย\n"
        "- ใช้อีโมจิ 1-2 ตัว\n"
        "- ห้ามใส่ลิงก์ ห้ามใส่ราคา\n"
        "- ห้ามใช้คำว่า 'คลิป' ให้ใช้ 'คลิป' แทน\n"
        "- ห้ามใช้คำว่า 'ซื้อ' หรือ 'สั่งซื้อ' ให้ใช้ 'สมัคร' แทน\n"
        "- ห้ามใช้คำว่า 'ทดลองฟรี'\n"
        "- เร้าใจแต่ไม่หยาบคาย สร้างความอยากรู้"
    )

    try:
        data = await call_openrouter(
            model=AI_MODEL,
            messages=[{"role": "user", "content": prompt}],
            caller="content_bot/teaser_caption",
            temperature=0.9,
            max_tokens=100,
        )
        caption = data["choices"][0]["message"]["content"].strip().strip('"')

        # Post-processing: ตัดคำนำ / ตัวเลือกที่ AI อาจใส่มา
        caption = re.sub(
            r'^(ตัวเลือก(ที่\s*)?\d+[:.]\s*|แคปชั่น[:.]\s*|caption[:.]\s*|นี่คือ\s*)',
            '', caption, flags=re.IGNORECASE,
        )
        # ถ้ามีหลายบรรทัด เอาแค่บรรทัดแรกที่มีเนื้อหาจริง
        lines = [
            l.strip() for l in caption.split('\n')
            if l.strip() and not l.strip().startswith(('1.', '2.', '3.', 'ตัวเลือก'))
        ]
        caption = lines[0] if lines else caption.split('\n')[0]
        return caption.strip()
    except Exception as exc:
        logger.error("AI caption failed: %s", exc)

    # Fallback captions
    fallbacks = [
        "🔥 ของดีมาแล้ว สมัคร VIP ดูเต็มๆ",
        "😈 แอบดูนิดนึง... อยากดูต่อ สมัคร VIP เจริญพร",
        "🔞 คลิปเด็ดวันนี้ ดูฟรีได้แค่นี้~",
        "💦 น้องคนนี้ ของดีจริงๆ ดูเต็มใน VIP เจริญพร",
        "🫣 แค่ตัวอย่าง... ของจริงอยู่ใน VIP เจริญพร",
    ]
    return random.choice(fallbacks)


async def blur_image(bot: Bot, file_id: str) -> io.BytesIO:
    """ดาวน์โหลดรูปจาก Telegram แล้วเบลอ + watermark."""
    file = await bot.get_file(file_id)
    buf = io.BytesIO()
    await file.download_to_memory(buf)
    buf.seek(0)

    img = Image.open(buf)
    # Blur แบบหวาบหวิว — เห็นรูปร่างแต่ไม่ชัด
    blurred = img.filter(ImageFilter.GaussianBlur(radius=12))

    # === Watermark "VIP เจริญพร" แนวทแยง tile pattern ===
    blurred = blurred.convert("RGBA")
    text = "VIP เจริญพร"

    # โหลดฟอนต์ — ลองฟอนต์ไทยก่อน, fallback เป็น DejaVu
    font_size = max(blurred.width // 12, 28)
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
        text = "VIP Charoenpon"  # fallback ภาษาอังกฤษถ้าไม่มีฟอนต์ไทย

    # สร้าง single text stamp แล้วหมุน
    tmp = Image.new("RGBA", (blurred.width * 2, blurred.height * 2), (0, 0, 0, 0))
    tmp_draw = ImageDraw.Draw(tmp)

    # วัดขนาดข้อความ
    bbox = tmp_draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]

    # Tile pattern — วาดข้อความซ้ำทั่วทั้ง canvas
    spacing_x = tw + max(tw // 2, 60)
    spacing_y = th + max(th * 3, 120)

    for y_pos in range(-blurred.height, blurred.height * 2, spacing_y):
        for x_pos in range(-blurred.width, blurred.width * 2, spacing_x):
            tmp_draw.text(
                (x_pos, y_pos), text, font=font,
                fill=(255, 255, 255, 90),  # opacity ~35%
            )

    # หมุน -30 องศา แล้ว crop กลับขนาดเดิม
    rotated = tmp.rotate(30, resample=Image.BICUBIC, expand=False)
    # Crop center ให้ได้ขนาดเท่ารูปต้นฉบับ
    cx = rotated.width // 2
    cy = rotated.height // 2
    half_w = blurred.width // 2
    half_h = blurred.height // 2
    watermark = rotated.crop((cx - half_w, cy - half_h, cx + half_w, cy + half_h))

    # ปรับขนาดให้ตรง (กันพิกเซลคี่)
    if watermark.size != blurred.size:
        watermark = watermark.resize(blurred.size, Image.LANCZOS)

    blurred = Image.alpha_composite(blurred, watermark)
    blurred = blurred.convert("RGB")

    out = io.BytesIO()
    blurred.save(out, format="JPEG", quality=65)
    out.seek(0)
    return out


async def create_flash_sale_image(bot: Bot, file_id: str) -> io.BytesIO:
    """สร้างภาพ Flash Sale จากรูป VIP — เบลอ + overlay ราคา + watermark."""
    # 1) ดาวน์โหลดรูปจาก Telegram
    file = await bot.get_file(file_id)
    buf = io.BytesIO()
    await file.download_to_memory(buf)
    buf.seek(0)

    img = Image.open(buf).convert("RGBA")
    w, h = img.size

    # 2) เบลอ radius=6 (เห็นเค้าโครง)
    blurred = img.filter(ImageFilter.GaussianBlur(radius=6))

    # === โหลดฟอนต์ ===
    font_paths = [
        "/usr/share/fonts/truetype/thai-tlwg/Garuda-Bold.ttf",
        "/usr/share/fonts/truetype/thai-tlwg/Sarabun-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ]

    def _load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
        for fp in font_paths:
            try:
                return ImageFont.truetype(fp, size=size)
            except (OSError, IOError):
                continue
        return ImageFont.load_default()

    # ขนาดฟอนต์สัมพันธ์กับความกว้างรูป
    font_title = _load_font(max(w // 8, 48))       # FLASH FRIDAY
    font_vip = _load_font(max(w // 14, 30))         # VIP 30 วัน
    font_old_price = _load_font(max(w // 18, 24))   # ฿300 ขีดฆ่า
    font_new_price = _load_font(max(w // 9, 44))    # ฿199 ตัวใหญ่
    font_detail = _load_font(max(w // 22, 20))      # จำกัด 30 คน | 14:00-00:00


    # 3) วาด overlay
    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    # แถบดำ semi-transparent บน (22%)
    top_h = int(h * 0.22)
    draw.rectangle([(0, 0), (w, top_h)], fill=(0, 0, 0, 170))

    # แถบดำ semi-transparent ล่าง (30%)
    bot_h = int(h * 0.30)
    bot_y = h - bot_h
    draw.rectangle([(0, bot_y), (w, h)], fill=(0, 0, 0, 180))

    # --- บน: "FLASH FRIDAY" สีทอง กลาง ---
    title_text = "FLASH FRIDAY"
    title_bbox = draw.textbbox((0, 0), title_text, font=font_title)
    title_tw = title_bbox[2] - title_bbox[0]
    title_th = title_bbox[3] - title_bbox[1]
    title_x = (w - title_tw) // 2
    title_y = (top_h - title_th) // 2
    draw.text((title_x, title_y), title_text, font=font_title, fill=(255, 215, 0, 255))

    # --- ล่าง: หลายบรรทัด ---
    # บรรทัด 1: "VIP 30 วัน" สีขาว
    line1 = "VIP 30 วัน"
    l1_bbox = draw.textbbox((0, 0), line1, font=font_vip)
    l1_tw = l1_bbox[2] - l1_bbox[0]
    line_gap = max(h // 80, 6)
    cursor_y = bot_y + line_gap + 4

    l1_x = (w - l1_tw) // 2
    draw.text((l1_x, cursor_y), line1, font=font_vip, fill=(255, 255, 255, 255))
    cursor_y += (l1_bbox[3] - l1_bbox[1]) + line_gap

    # บรรทัด 2: "฿300" ขีดฆ่าสีเทา + "฿199" สีแดง/ชมพูตัวใหญ่
    old_price = "฿300"
    new_price = "฿199"
    old_bbox = draw.textbbox((0, 0), old_price, font=font_old_price)
    new_bbox = draw.textbbox((0, 0), new_price, font=font_new_price)
    old_tw = old_bbox[2] - old_bbox[0]
    old_th = old_bbox[3] - old_bbox[1]
    new_tw = new_bbox[2] - new_bbox[0]
    new_th = new_bbox[3] - new_bbox[1]

    gap_between = max(w // 30, 12)
    total_price_w = old_tw + gap_between + new_tw
    price_x = (w - total_price_w) // 2

    # วาง ฿300 (สีเทา) + ขีดฆ่า — จัดให้อยู่กลางแนวตั้งเทียบกับ ฿199
    old_y = cursor_y + (new_th - old_th) // 2
    draw.text((price_x, old_y), old_price, font=font_old_price, fill=(160, 160, 160, 255))
    # เส้นขีดฆ่า
    strike_y = old_y + old_th // 2
    draw.line([(price_x, strike_y), (price_x + old_tw, strike_y)], fill=(160, 160, 160, 255), width=max(2, w // 250))

    # วาง ฿199 สีแดง/ชมพู
    new_x = price_x + old_tw + gap_between
    draw.text((new_x, cursor_y), new_price, font=font_new_price, fill=(255, 60, 100, 255))
    cursor_y += new_th + line_gap

    # บรรทัด 3: "จำกัด 30 คน | 14:00-00:00"
    line3 = "จำกัด 30 คน | 14:00-00:00"
    l3_bbox = draw.textbbox((0, 0), line3, font=font_detail)
    l3_tw = l3_bbox[2] - l3_bbox[0]
    draw.text(((w - l3_tw) // 2, cursor_y), line3, font=font_detail, fill=(255, 255, 255, 220))
    cursor_y += (l3_bbox[3] - l3_bbox[1]) + line_gap

    # Composite overlay ลงบนรูปเบลอ
    result = Image.alpha_composite(blurred, overlay)

    # 4) Watermark "VIP เจริญพร" tile pattern หมุน 30° opacity 60
    wm_text = "VIP เจริญพร"
    wm_font_size = max(w // 12, 28)
    wm_font = _load_font(wm_font_size)

    # ถ้า fallback font ไม่รองรับไทย ใช้ภาษาอังกฤษ
    if isinstance(wm_font, ImageFont.ImageFont):
        wm_text = "VIP Charoenpon"

    # สร้าง canvas ใหญ่สำหรับ tile
    tmp = Image.new("RGBA", (w * 2, h * 2), (0, 0, 0, 0))
    tmp_draw = ImageDraw.Draw(tmp)
    wm_bbox = tmp_draw.textbbox((0, 0), wm_text, font=wm_font)
    tw = wm_bbox[2] - wm_bbox[0]
    th = wm_bbox[3] - wm_bbox[1]

    spacing_x = tw + max(tw // 2, 60)
    spacing_y = th + max(th * 3, 120)

    for y_pos in range(-h, h * 2, spacing_y):
        for x_pos in range(-w, w * 2, spacing_x):
            tmp_draw.text(
                (x_pos, y_pos), wm_text, font=wm_font,
                fill=(255, 255, 255, 60),  # opacity 60
            )

    # หมุน 30° แล้ว crop กลับขนาดเดิม
    rotated = tmp.rotate(30, resample=Image.BICUBIC, expand=False)
    cx, cy = rotated.width // 2, rotated.height // 2
    half_w, half_h = w // 2, h // 2
    watermark = rotated.crop((cx - half_w, cy - half_h, cx + half_w, cy + half_h))

    if watermark.size != result.size:
        watermark = watermark.resize(result.size, Image.LANCZOS)

    result = Image.alpha_composite(result, watermark)

    # 5) Output JPEG quality=80
    result = result.convert("RGB")
    out = io.BytesIO()
    out.name = "flash_sale.jpg"
    result.save(out, format="JPEG", quality=80)
    out.seek(0)
    return out


async def fetch_latest_vip_content() -> dict | None:
    """ดึงรูปล่าสุดจาก content_queue ที่ยังไม่ได้ใช้.

    Returns dict with keys: id, file_id, file_type
    หรือ None ถ้าไม่มีรูปในคิว
    """
    try:
        async with get_session() as session:
            result = await session.execute(
                select(ContentQueue)
                .where(ContentQueue.is_used == False)
                .order_by(ContentQueue.created_at.desc())
                .limit(1)
            )
            row = result.scalar_one_or_none()
            if row is None:
                return None
            return {
                "id": row.id,
                "file_id": row.file_id,
                "file_type": row.file_type,
            }
    except Exception as exc:
        logger.error("fetch_latest_vip_content failed: %s", exc)
        return None


async def fetch_multiple_content(limit: int = 5) -> list[dict]:
    """ดึง content ที่ยังไม่ได้ใช้ หลายคลิป สำหรับส่งเป็น album.

    Returns list of dicts with keys: id, file_id, file_type
    """
    try:
        async with get_session() as session:
            result = await session.execute(
                select(ContentQueue)
                .where(ContentQueue.is_used == False)
                .order_by(ContentQueue.created_at.asc())
                .limit(limit)
            )
            rows = result.scalars().all()
            return [
                {"id": row.id, "file_id": row.file_id, "file_type": row.file_type}
                for row in rows
            ]
    except Exception as exc:
        logger.error("fetch_multiple_content failed: %s", exc)
        return []


async def mark_content_used(content_id: int) -> None:
    """Mark content queue item as used."""
    try:
        async with get_session() as session:
            await session.execute(
                update(ContentQueue)
                .where(ContentQueue.id == content_id)
                .values(is_used=True, used_at=datetime.now(tz=timezone.utc))
            )
    except Exception as exc:
        logger.error("mark_content_used failed: %s", exc)


async def recycle_old_content() -> int:
    """Recycle content ที่ใช้แล้ว > 7 วัน กลับมาใช้ซ้ำ (reset is_used=False).

    Returns จำนวน content ที่ recycle ได้.
    """
    try:
        cutoff = datetime.now(tz=timezone.utc) - timedelta(days=7)
        async with get_session() as session:
            result = await session.execute(
                update(ContentQueue)
                .where(ContentQueue.is_used == True)
                .where(ContentQueue.used_at < cutoff)
                .values(is_used=False, used_at=None)
            )
            recycled = result.rowcount or 0
            if recycled > 0:
                logger.info("♻️ Recycled %d old content items (used > 7 days ago)", recycled)
            return recycled
    except Exception as exc:
        logger.error("recycle_old_content failed: %s", exc)
        return 0


# --- Handler: รับรูปจาก authorized users ใน DM ---

async def handle_authorized_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """รับรูปจาก authorized users แล้วบันทึกลง DB."""
    msg = update.message
    if not msg:
        return

    sender_id = msg.from_user.id if msg.from_user else None
    if sender_id not in AUTHORIZED_SENDERS:
        # ไม่ใช่ authorized user → เงียบ
        return

    # ต้องเป็น DM (private chat) เท่านั้น
    if msg.chat.type != "private":
        return

    file_id = None
    file_type = "photo"

    if msg.photo:
        file_id = msg.photo[-1].file_id
        file_type = "photo"
    elif msg.document and msg.document.mime_type and msg.document.mime_type.startswith("image/"):
        file_id = msg.document.file_id
        file_type = "photo"

    if not file_id:
        return

    try:
        async with get_session() as session:
            item = ContentQueue(
                file_id=file_id,
                file_type=file_type,
                sent_by=sender_id,
            )
            session.add(item)

        logger.info("Content queued from user %d: file_id=%s", sender_id, file_id[:20])
        await msg.reply_text("✅ รับรูปแล้ว จะโพสต์รอบถัดไป")

    except Exception as exc:
        logger.error("Failed to save content from user %d: %s", sender_id, exc)
        await msg.reply_text("❌ เกิดข้อผิดพลาด ลองใหม่อีกครั้ง")


SCHEDULE_TIMES = ["1230", "1800", "2100", "2300", "0100"]


def get_round_time() -> str:
    """Determine the current round_time based on Thai time (closest scheduled slot)."""
    now = datetime.now(TH_TZ)
    current_minutes = now.hour * 60 + now.minute
    slots = [
        (12 * 60 + 30, "1230"),
        (18 * 60 + 0,  "1800"),
        (21 * 60 + 0,  "2100"),
        (23 * 60 + 0,  "2300"),
        (1 * 60 + 0,   "0100"),
    ]
    best = min(slots, key=lambda s: min(abs(current_minutes - s[0]), abs(current_minutes - s[0] + 1440), abs(current_minutes - s[0] - 1440)))
    return best[1]


def build_caption(base_caption: str, round_time: str, group_index: int) -> str:
    """Build full caption with unique tracking deep link per group."""
    return (
        f"🔥 <b>VIP เจริญพร</b> 🔥\n\n"
        f"{base_caption}\n\n"
        f"✅ คลิปเต็มไม่เบลอ ทุกวัน\n"
        f"✅ รวมกว่า 10,000 คลิป\n\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f'📩 <b>สมัครเลย 👇</b>\n'
        f'👉 <a href="tg://resolve?domain=NamwarnJarern_bot&start=t_{round_time}_g{group_index}">⚡ สมัคร VIP เจริญพร ⚡</a>\n'
        f"━━━━━━━━━━━━━━━━━━"
    )


async def post_teaser_to_free_groups(context: ContextTypes.DEFAULT_TYPE) -> None:
    """โพสต์ teaser (ข้อความอย่างเดียว) ไปทุกกลุ่มฟรี."""
    bot = context.bot
    round_time = get_round_time()
    logger.info("Starting text-only teaser post round (round=%s)...", round_time)

    base_caption = await generate_teaser_caption()

    success = 0
    failed = 0

    for group_index, group_id in enumerate(FREE_GROUPS):
        full_caption = build_caption(base_caption, round_time, group_index)
        try:
            await bot.send_message(
                chat_id=group_id,
                text=full_caption,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
            success += 1
            await asyncio.sleep(1)
        except Exception as exc:
            logger.error("Failed to post to group %d: %s", group_id, exc)
            failed += 1

    logger.info("Teaser round done: %d success, %d failed", success, failed)
    await _send_discord_content_log(
        f"📢 **Content Bot: Teaser Round Complete** [round={round_time}]\n"
        f"✅ Success: {success} / ❌ Failed: {failed} / Total: {len(FREE_GROUPS)} groups"
    )


async def post_teaser_with_image(context: ContextTypes.DEFAULT_TYPE, content_id: int, file_id: str) -> None:
    """โพสต์ teaser พร้อมรูปเบลอไปทุกกลุ่มฟรี แล้ว mark content ว่าใช้แล้ว."""
    bot = context.bot
    round_time = get_round_time()
    logger.info("Starting teaser post with image (round=%s, content_id=%d)...", round_time, content_id)

    base_caption = await generate_teaser_caption()

    # Blur image
    try:
        blurred_buf = await blur_image(bot, file_id)
    except Exception as exc:
        logger.error("Failed to blur image: %s", exc)
        # Fallback to text-only
        await post_teaser_to_free_groups(context)
        return

    success = 0
    for group_index, group_id in enumerate(FREE_GROUPS):
        full_caption = build_caption(base_caption, round_time, group_index)
        try:
            blurred_buf.seek(0)
            await bot.send_photo(
                chat_id=group_id,
                photo=blurred_buf,
                caption=full_caption,
                parse_mode="HTML",
            )
            success += 1
            await asyncio.sleep(1)
        except Exception as exc:
            logger.error("Failed to post image to group %d: %s", group_id, exc)

    failed_img = len(FREE_GROUPS) - success
    logger.info("Image teaser round done: %d/%d", success, len(FREE_GROUPS))

    # Mark content as used หลังโพสต์สำเร็จ (ถ้ามีอย่างน้อย 1 กลุ่มสำเร็จ)
    if success > 0:
        await mark_content_used(content_id)
        logger.info("Marked content_id=%d as used", content_id)

    await _send_discord_content_log(
        f"🖼️ **Content Bot: Image Teaser Round Complete** [round={round_time}]\n"
        f"✅ Success: {success} / ❌ Failed: {failed_img} / Total: {len(FREE_GROUPS)} groups"
    )


async def post_teaser_album(context: ContextTypes.DEFAULT_TYPE, contents: list[dict]) -> None:
    """โพสต์ teaser เป็น album (media group) ไปทุกกลุ่มฟรี แล้ว mark ทุก content ว่าใช้แล้ว."""
    bot = context.bot
    round_time = get_round_time()
    content_ids = [c["id"] for c in contents]
    logger.info(
        "Starting album teaser post (round=%s, %d images, content_ids=%s)...",
        round_time, len(contents), content_ids,
    )

    base_caption = await generate_teaser_caption()

    # เบลอรูปทั้งหมดล่วงหน้า
    blurred_items: list[tuple[int, io.BytesIO]] = []
    for c in contents:
        try:
            blurred_buf = await blur_image(bot, c["file_id"])
            blurred_items.append((c["id"], blurred_buf))
        except Exception as exc:
            logger.error("Failed to blur image content_id=%d: %s", c["id"], exc)

    if len(blurred_items) < 2:
        # ถ้าเบลอได้น้อยกว่า 2 รูป → fallback เป็นทีละรูป
        if blurred_items:
            c = contents[0]
            await post_teaser_with_image(context, c["id"], c["file_id"])
        else:
            await post_teaser_to_free_groups(context)
        return

    success = 0
    for group_index, group_id in enumerate(FREE_GROUPS):
        full_caption = build_caption(base_caption, round_time, group_index)

        # สร้าง media group ใหม่ทุกกลุ่ม (ต้อง seek(0) ทุกรอบ)
        media_group = []
        for i, (content_id, blurred_buf) in enumerate(blurred_items):
            blurred_buf.seek(0)
            caption = full_caption if i == 0 else None
            media_group.append(InputMediaPhoto(
                media=blurred_buf,
                caption=caption,
                parse_mode="HTML" if caption else None,
            ))

        try:
            await bot.send_media_group(chat_id=group_id, media=media_group)
            success += 1
            await asyncio.sleep(1.5)  # ช้าลงนิดเพราะ album ใช้ bandwidth มากกว่า
        except Exception as exc:
            logger.error("Failed to post album to group %d: %s", group_id, exc)
            # Fallback: ลองส่งทีละรูปสำหรับกลุ่มนี้
            try:
                blurred_items[0][1].seek(0)
                await bot.send_photo(
                    chat_id=group_id,
                    photo=blurred_items[0][1],
                    caption=full_caption,
                    parse_mode="HTML",
                )
                success += 1
                logger.info("Fallback single photo sent to group %d", group_id)
            except Exception as exc2:
                logger.error("Fallback single photo also failed for group %d: %s", group_id, exc2)

    failed_count = len(FREE_GROUPS) - success
    logger.info("Album teaser round done: %d/%d groups", success, len(FREE_GROUPS))

    # Mark ทุก content ที่ใช้แล้วเป็น is_used=True
    if success > 0:
        for content_id, _ in blurred_items:
            await mark_content_used(content_id)
        logger.info("Marked %d content items as used", len(blurred_items))

    await _send_discord_content_log(
        f"🖼️ **Content Bot: Album Teaser Round Complete** [round={round_time}]\n"
        f"📸 Album: {len(blurred_items)} images\n"
        f"✅ Success: {success} / ❌ Failed: {failed_count} / Total: {len(FREE_GROUPS)} groups"
    )


async def _check_content_queue_alert() -> None:
    """แจ้งเตือนกลุ่ม Admin เมื่อรูปในคิวเหลือน้อยกว่า 5."""
    import os
    try:
        async with get_session() as session:
            from sqlalchemy import func as sqlfunc, select as sa_select
            result = await session.execute(
                sa_select(sqlfunc.count(ContentQueue.id)).where(ContentQueue.is_used == False)
            )
            remaining = result.scalar() or 0

        if remaining < 5:
            ADMIN_BOT_TOKEN = os.environ.get("ADMIN_BOT_TOKEN", "")
            ADMIN_GROUP_ID = int(os.environ.get("ADMIN_GROUP_CHAT_ID", os.environ.get("TG_GROUP_ADMIN", "-1003830920430")))
            if ADMIN_BOT_TOKEN:
                from telegram import Bot as _Bot
                admin_bot = _Bot(token=ADMIN_BOT_TOKEN)
                await admin_bot.send_message(
                    chat_id=ADMIN_GROUP_ID,
                    text=(
                        f"⚠️ <b>แจ้งเตือน: รูปในคิว Content เหลือ {remaining} รูป!</b>\n\n"
                        f"{'🔴 หมดแล้ว! โพสต์รอบถัดไปจะไม่มีรูป' if remaining == 0 else f'🟡 เหลืออีก {remaining} รอบ'}\n\n"
                        f"📷 ส่งรูปเพิ่มที่ @jarernAD4_bot ได้เลยค่ะ"
                    ),
                    parse_mode="HTML",
                )
                logger.info("Content queue alert sent: %d remaining", remaining)
    except Exception as exc:
        logger.error("Content queue alert failed: %s", exc)


async def scheduled_teaser(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Scheduled job: โพสต์ teaser — ดึง 3-5 รูปส่งเป็น album, ถ้าน้อยกว่า 3 ส่งทีละรูป."""
    logger.info("scheduled_teaser triggered")

    # ดึง content 3-5 คลิปจาก queue
    contents = await fetch_multiple_content(limit=5)

    # ถ้าไม่มี content ใหม่เลย → ลอง recycle content เก่า (ใช้แล้ว > 7 วัน)
    if not contents:
        recycled = await recycle_old_content()
        if recycled > 0:
            contents = await fetch_multiple_content(limit=5)

    if len(contents) >= 3:
        # >= 3 คลิป → ส่งเป็น album
        logger.info("Found %d content items → sending as album", len(contents))
        await post_teaser_album(context, contents)
    elif len(contents) >= 1:
        # 1-2 คลิป → ส่งทีละรูปเหมือนเดิม (ใช้คลิปแรก)
        c = contents[0]
        logger.info("Found %d content items (< 3) → sending single image (id=%d)", len(contents), c["id"])
        await post_teaser_with_image(context, c["id"], c["file_id"])
    else:
        # ไม่มีเลย → text-only
        logger.info("No content in queue (even after recycle), posting text-only teaser")
        await post_teaser_to_free_groups(context)

    # เช็คและแจ้งเตือนหลังโพสต์ทุกรอบ
    await _check_content_queue_alert()


# --- Auto-fetch scheduled job ---

async def _scheduled_auto_fetch(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Scheduled job: ดึงรูปใหม่จากแหล่งภายนอกทุก 6 ชั่วโมง."""
    logger.info("🔄 Auto-fetch content triggered")
    try:
        from bots.content_bot.content_fetcher import fetch_new_content
        count = await fetch_new_content()
        logger.info("🔄 Auto-fetch done: %d new images added to content_queue", count)
    except Exception as exc:
        logger.error("Auto-fetch failed: %s", exc)
        await _send_discord_content_log(
            f"❌ **Content Auto-Fetch Failed**\nError: {exc}"
        )


# --- Entry point ---

def main() -> None:
    if not CONTENT_BOT_TOKEN:
        logger.error("CONTENT_BOT_TOKEN not set")
        return

    app = Application.builder().token(CONTENT_BOT_TOKEN).build()

    # Post-init: สร้างตาราง DB ถ้ายังไม่มี
    async def post_init(application: Application) -> None:
        await init_db()
        logger.info("DB initialized (content_queue table ready)")

    app.post_init = post_init

    # Handler: รับรูปใน DM จาก authorized users
    app.add_handler(MessageHandler(
        (filters.PHOTO | filters.Document.IMAGE) & filters.ChatType.PRIVATE,
        handle_authorized_photo,
    ))

    # Schedule teaser posts (เวลาไทย)
    job_queue = app.job_queue
    schedule_times = [
        dt_time(hour=12, minute=30, tzinfo=TH_TZ),  # 12:30
        dt_time(hour=18, minute=0, tzinfo=TH_TZ),   # 18:00
        dt_time(hour=21, minute=0, tzinfo=TH_TZ),   # 21:00
        dt_time(hour=23, minute=0, tzinfo=TH_TZ),   # 23:00
    ]
    for i, t in enumerate(schedule_times):
        job_queue.run_daily(scheduled_teaser, time=t, name=f"teaser_{i}")

    # 01:00 next day
    job_queue.run_daily(
        scheduled_teaser,
        time=dt_time(hour=1, minute=0, tzinfo=TH_TZ),
        name="teaser_late",
    )

    # Schedule auto-fetch content ทุก 6 ชั่วโมง (04:00, 10:00, 16:00, 22:00 เวลาไทย)
    # auto_fetch ปิดชั่วคราว — Reddit/Nitter block จาก server
    # auto_fetch_times = [
    #     dt_time(hour=4, minute=0, tzinfo=TH_TZ),
    #     dt_time(hour=10, minute=0, tzinfo=TH_TZ),
    #     dt_time(hour=16, minute=0, tzinfo=TH_TZ),
    #     dt_time(hour=22, minute=0, tzinfo=TH_TZ),
    # ]
    # for i, t in enumerate(auto_fetch_times):
    #     job_queue.run_daily(_scheduled_auto_fetch, time=t, name=f"auto_fetch_{i}")

    logger.info("Content Bot (มิน) starting — 5 rounds/day to %d groups", len(FREE_GROUPS))
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
