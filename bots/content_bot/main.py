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
from shared.endmonth_vip_promo import (
    PROMO_2499_IMAGE_PATH,
    get_group_2499_promo_caption,
    is_endmonth_vip_promo_active,
)
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

from shared.tz import TH_TZ

# Authorized users ที่ส่งรูปให้ bot ได้ (from env: ADMIN_TELEGRAM_IDS)
AUTHORIZED_SENDERS = [int(x.strip()) for x in os.environ.get("ADMIN_TELEGRAM_IDS", "8502597269").split(",") if x.strip()]

# กลุ่มฟรีทั้งหมด (ดู group_registry table where min_tier=FREE is_active=true)
FREE_GROUPS = [
    -1003733093219, -1003772512123, -1003706880995, -1003740382332,
    -1003861673687, -1003841389411, -1003723154612, -1003981084328,
    -1003414401674, -1003933195188, -1003831199018, -1003749804554,
]  # static fallback — main loader is _get_free_groups_async() below


async def _get_free_groups_async() -> list[int]:
    """Load target groups for content_bot from bot_group_targets matrix table.

    Source of truth: Dashboard → 🤖 จัดการบอท → Content Bot → tick groups.
    Boss can add/remove groups via UI; bot picks up changes within 5 minutes (cache TTL).

    Cascading fallback (safe):
      1. bot_group_targets WHERE bot_key='content_bot' AND target_role='distribution'
      2. (legacy) group_registry WHERE is_active AND min_tier=FREE
      3. (last resort) static FREE_GROUPS list
    """
    import time as _t
    _cache_attr = "_free_groups_cache"
    _ts_attr = "_free_groups_ts"
    state = globals()
    cache = state.get(_cache_attr)
    ts = state.get(_ts_attr, 0)
    if cache and (_t.time() - ts) < 300:
        return cache

    # Tier 1: matrix table (Dashboard-managed)
    try:
        from shared.database import get_session as _gs
        from sqlalchemy import text as _t_sql
        async with _gs() as _s:
            r = await _s.execute(_t_sql("""
                SELECT bgt.chat_id FROM bot_group_targets bgt
                JOIN group_registry g ON g.chat_id = bgt.chat_id
                WHERE bgt.bot_key = 'content_bot'
                  AND bgt.target_role = 'distribution'
                  AND bgt.is_active = TRUE
                  AND g.is_active = TRUE
                ORDER BY g.id
            """))
            groups = [row[0] for row in r.all()]
        if groups:
            state[_cache_attr] = groups
            state[_ts_attr] = _t.time()
            logger.info("Loaded %d groups for content_bot from bot_group_targets", len(groups))
            return groups
        logger.warning("bot_group_targets empty for content_bot — falling back to legacy FREE filter")
    except Exception as exc:
        logger.warning("bot_group_targets query failed: %s — falling back to legacy FREE filter", exc)

    # Tier 2: legacy group_registry FREE filter
    try:
        from shared.database import get_session as _gs
        from sqlalchemy import text as _t_sql
        async with _gs() as _s:
            r = await _s.execute(_t_sql("""
                SELECT chat_id FROM group_registry
                WHERE is_active = true AND min_tier::text = 'FREE'
                ORDER BY id
            """))
            groups = [row[0] for row in r.all()]
        if groups:
            state[_cache_attr] = groups
            state[_ts_attr] = _t.time()
            logger.warning("Using LEGACY group_registry FREE filter (%d groups) — admin should tick groups in Dashboard 🤖 จัดการบอท", len(groups))
            return groups
    except Exception as exc:
        logger.warning("legacy FREE filter failed: %s — using static fallback", exc)

    # Tier 3: hardcoded last resort
    logger.error("Using STATIC FREE_GROUPS fallback (%d groups) — DB unreachable!", len(FREE_GROUPS))
    return FREE_GROUPS

AI_MODEL = "anthropic/claude-haiku-3-5"
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")

# ── Caption Styles สำหรับความหลากหลาย ──
CAPTION_STYLES = [
    {"name": "question", "prompt": "แบบตั้งคำถามชวนสงสัย เช่น 'อยากรู้มั้ยว่า...' 'เคยเห็นแบบนี้ยัง?'"},
    {"name": "countdown", "prompt": "แบบ countdown เร่งด่วน เช่น 'เหลืออีก X ชม.!' 'วันนี้วันสุดท้าย!' สร้างความเร่งรีบ"},
    {"name": "testimonial", "prompt": "แบบ testimonial อ้างอิงตัวเลข เช่น 'เมื่อวานมีคนสมัคร XX คน' 'สมาชิกใหม่วันนี้ XX คน' (ใช้ตัวเลขสมมุติ)"},
    {"name": "curiosity_gap", "prompt": "แบบ curiosity gap สร้างช่องว่างความอยากรู้ เช่น 'คลิปนี้ถ้าไม่เบลอ...' 'ถ้าเห็นชัดๆ จะ...'"},
    {"name": "emoji_heavy", "prompt": "แบบใช้อีโมจิเยอะๆ 4-6 ตัว สลับกับข้อความสั้นๆ ดูสนุกสนาน"},
    {"name": "ultra_short", "prompt": "แบบสั้นมากแค่ 1 บรรทัด ไม่เกิน 15 คำ กระชับ ทรงพลัง"},
    {"name": "storytelling", "prompt": "แบบเล่าเรื่องสั้นๆ เปิดเรื่องให้อยากรู้ตอนจบ เช่น 'น้องคนนี้...' 'คลิปนี้มีที่มา...'"},
    {"name": "challenge", "prompt": "แบบท้าทาย/ยั่วยุ เช่น 'กล้าดูมั้ย?' 'ไม่ดูพลาดแน่' 'ถ้าไม่สมัครจะเสียใจ'"},
    {"name": "exclusive", "prompt": "แบบเน้นความ exclusive เช่น 'มีแค่ใน VIP' 'ที่อื่นหาไม่ได้' 'เฉพาะสมาชิก'"},
    {"name": "teasing", "prompt": "แบบแหย่ๆ ยั่วๆ เช่น 'เห็นแค่นี้พอมั้ย?' 'อยากดูต่อใช่ป่ะ' 'นิดเดียวพอนะ'"},
]

# Track ว่าวันนี้ใช้ style ไหนไปแล้ว (reset ทุกวัน)
_used_styles_today: list[str] = []
_used_styles_date: str = ""


def _pick_caption_style() -> dict:
    """สุ่มเลือก caption style ที่ยังไม่ใช้วันนี้."""
    global _used_styles_today, _used_styles_date

    today = datetime.now(TH_TZ).strftime("%Y-%m-%d")
    if _used_styles_date != today:
        _used_styles_today = []
        _used_styles_date = today

    available = [s for s in CAPTION_STYLES if s["name"] not in _used_styles_today]
    if not available:
        # ใช้ครบแล้ว → reset แล้วสุ่มใหม่
        _used_styles_today = []
        available = CAPTION_STYLES

    style = random.choice(available)
    _used_styles_today.append(style["name"])
    return style


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


async def generate_teaser_caption() -> tuple[str, str]:
    """AI สร้าง caption teaser เสียวๆ ล่อใจ — return (caption, style_name)."""
    from shared.api_cost_tracker import call_openrouter

    style = _pick_caption_style()
    style_name = style["name"]

    prompt = (
        "เขียน caption ภาษาไทยสั้นๆ 1-2 บรรทัดสำหรับโพสต์ teaser 18+ "
        "ให้คนอยากดูเต็มๆ แล้วสมัคร VIP เจริญพร\n\n"
        f"สไตล์ที่ต้องการ: {style['prompt']}\n\n"
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
            temperature=0.95,
            max_tokens=100,
        )
        caption = data["choices"][0]["message"]["content"].strip().strip('"')

        # Post-processing: ตัดคำนำ / ตัวเลือกที่ AI อาจใส่มา
        caption = re.sub(
            r'^(ตัวเลือก(ที่\s*)?\d+[:.]\s*|แคปชั่น[:.]\s*|caption[:.]\s*|นี่คือ\s*)',
            '', caption, flags=re.IGNORECASE,
        )
        lines = [
            l.strip() for l in caption.split('\n')
            if l.strip() and not l.strip().startswith(('1.', '2.', '3.', 'ตัวเลือก'))
        ]
        caption = lines[0] if lines else caption.split('\n')[0]
        return caption.strip(), style_name
    except Exception as exc:
        logger.error("AI caption failed: %s", exc)

    # Fallback captions — หลากหลาย 16 แบบ
    fallbacks = [
        "🔥 ของดีมาแล้ว สมัคร VIP ดูเต็มๆ",
        "😈 แอบดูนิดนึง... อยากดูต่อ สมัคร VIP เจริญพร",
        "🔞 คลิปเด็ดวันนี้ ดูฟรีได้แค่นี้~",
        "💦 น้องคนนี้ ของดีจริงๆ ดูเต็มใน VIP เจริญพร",
        "🫣 แค่ตัวอย่าง... ของจริงอยู่ใน VIP เจริญพร",
        "👀 เห็นแค่นี้พอมั้ย? ของเต็มอยู่ใน VIP",
        "🤫 ห้ามบอกใคร... คลิปนี้มีแค่ใน VIP เจริญพร",
        "⏰ เหลืออีกไม่กี่ชม.! สมัครตอนนี้ดูได้เลย",
        "🙈 ถ้าไม่เบลอ... จะร้อนแค่ไหน สมัคร VIP รู้เลย",
        "💋 น้องใหม่มาแรง ดูได้เฉพาะสมาชิก VIP เจริญพร",
        "🎯 กล้าดูมั้ย? สมัคร VIP เจริญพร แล้วจะรู้",
        "🌶️ เผ็ดมาก! ดูเต็มๆ ได้เฉพาะใน VIP",
        "😏 อยากรู้มั้ยว่าน้องทำอะไรต่อ? สมัคร VIP เลย",
        "🔒 ปลดล็อกของดี สมัคร VIP เจริญพร วันนี้",
        "💥 คลิปนี้มีคนดู 500+ แล้ว! สมัครดูเต็มใน VIP",
        "✨ ที่อื่นหาไม่ได้ มีแค่ใน VIP เจริญพร เท่านั้น",
    ]
    return random.choice(fallbacks), style_name


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
                .order_by(ContentQueue.created_at.desc())
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


async def post_endmonth_god_promo_to_free_groups(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Post end-month GOD MODE promo (2499 -> 2000) via Content Bot until promo expires."""
    if not is_endmonth_vip_promo_active():
        logger.info("GOD MODE end-month promo inactive; skip scheduled promo")
        return

    bot = context.bot
    free_groups = await _get_free_groups_async()
    success = 0
    failed = 0
    logger.info("Starting GOD MODE 2499->2000 promo post to %d groups", len(free_groups))

    for group_index, group_id in enumerate(free_groups):
        caption = get_group_2499_promo_caption(group_index)
        try:
            if os.path.exists(PROMO_2499_IMAGE_PATH):
                with open(PROMO_2499_IMAGE_PATH, "rb") as image:
                    await bot.send_photo(
                        chat_id=group_id,
                        photo=image,
                        caption=caption,
                        parse_mode="HTML",
                    )
            else:
                await bot.send_message(
                    chat_id=group_id,
                    text=caption,
                    parse_mode="HTML",
                    disable_web_page_preview=True,
                )
            success += 1
            await asyncio.sleep(1.2)
        except Exception as exc:
            failed += 1
            logger.error("Failed to post GOD MODE promo to group %d: %s", group_id, exc)

    logger.info("GOD MODE promo round done: %d success, %d failed", success, failed)
    await _send_discord_content_log(
        f"💎 **Content Bot: GOD MODE Promo Round Complete**\n"
        f"โปร 2,499 เหลือ 2,000 ถึงสิ้นเดือน\n"
        f"✅ Success: {success} / ❌ Failed: {failed} / Total: {len(free_groups)} groups"
    )




# === VIP_PROMO_V2 — Posts VIP เจริญพร promo using 01_welcome.png ===
async def post_vip_promo_to_free_groups(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Post VIP เจริญพร general promo (รูป + caption) to all free groups.

    Replaces jarern4-auto-poster role: generic VIP membership promo.
    Uses campaign image 01_welcome.png + rotated Thai captions.
    """
    bot = context.bot
    free_groups = await _get_free_groups_async()
    success = 0
    failed = 0
    logger.info("Starting VIP promo post to %d groups", len(free_groups))

    # Find welcome image (relative path inside container = /app/assets/campaigns/01_welcome.png)
    from pathlib import Path as _P
    img_path = _P(__file__).resolve().parents[2] / "assets" / "campaigns" / "01_welcome.png"

    # Rotated VIP promo captions (6 variants)
    vip_captions = [
        ("🌟 <b>VIP เจริญพร 18+</b> 🌟\n\nสมาชิก 100,000+ คน ใช้แล้วบอกต่อ\n💎 คลิป HD 10,000+ ชิ้น อัพเดททุกวัน\n🔥 VIP 30วัน ฿300 | GOD 90วัน ฿1,299 | ถาวร ฿2,499\n\n👉 <a href='https://t.me/NamwarnJarern_bot?start=packages'>กดสมัครเลย</a>"),
        ("💖 <b>เจริญพร VIP — คลิปเต็มไม่เบลอ</b> 💖\n\n✅ สมาชิก 100,000+ คน\n✅ อัพเดทใหม่ทุกวัน\n✅ ดูได้ตลอด — ราคาดีงาม\n\n🛒 <a href='https://t.me/NamwarnJarern_bot?start=packages'>ดูแพ็คเกจทั้งหมด</a>"),
        ("🔥 <b>VIP เจริญพร 👑</b>\n\nสายเซฟตัวจริง รวมที่นี่!\n10,000+ คลิป HD ทุกแนว ทุกสาย\n\n👉 <a href='https://t.me/NamwarnJarern_bot?start=packages'>สมัคร VIP เลย</a>"),
        ("💎 <b>เจริญพร VIP — ทดลองวันแรก ติดใจเลย!</b>\n\n🎬 คลิป Exclusive ทุกวัน\n🛡 ปลอดภัย • มั่นใจ\n⭐ 4.8/5 จาก 200+ รีวิว\n\n🔗 <a href='https://t.me/NamwarnJarern_bot?start=packages'>เริ่มต้นที่นี่</a>"),
        ("🌹 <b>VIP เจริญพร 👑</b>\n\nครอบครัว 100,000+ คน ที่มั่นใจเลือกเรา\nงานดี งานเด็ด ห้ามพลาด\n\n👉 <a href='https://t.me/NamwarnJarern_bot?start=packages'>เข้าร่วม VIP</a>"),
        ("⚡ <b>VIP เจริญพร — งานดี ห้ามพลาด!</b> ⚡\n\n🎁 ชวนเพื่อน รับ VIP ฟรี!\n📦 GOD MODE 90วัน ฿1,299 คุ้มสุด\n\n👉 <a href='https://t.me/NamwarnJarern_bot?start=packages'>กดเลย!</a>"),
    ]
    import random as _r
    caption = _r.choice(vip_captions)

    for group_index, group_id in enumerate(free_groups):
        try:
            if img_path.exists():
                with open(img_path, "rb") as _f:
                    await bot.send_photo(
                        chat_id=group_id, photo=_f, caption=caption,
                        parse_mode="HTML",
                    )
            else:
                await bot.send_message(
                    chat_id=group_id, text=caption, parse_mode="HTML",
                    disable_web_page_preview=True,
                )
            success += 1
            await asyncio.sleep(1.2)
        except Exception as exc:
            failed += 1
            logger.warning("VIP promo failed group %d: %s", group_id, exc)

    logger.info("VIP promo round done: %d/%d", success, len(free_groups))
    try:
        await _send_discord_content_log(
            f"💎 **VIP Promo Round Complete**\n"
            f"✅ {success} / ❌ {failed} / Total {len(free_groups)} groups"
        )
    except Exception:
        pass



async def post_shaker_promo_to_free_groups(context):
    """Post Shaker ฿100 promo (FOMO style) to all free groups.

    Caption shows live counter of seats remaining (dynamic from DB).
    """
    import asyncpg
    import os as _os
    bot = context.bot
    free_groups = await _get_free_groups_async()
    logger.info("Starting Shaker promo post to %d groups", len(free_groups))

    # Query active ticket count
    seats_left = 100
    try:
        conn = await asyncpg.connect(
            host="charoenpon-postgres", user="postgres",
            database="charoenpon", password=_os.environ.get("POSTGRES_PASSWORD", ""),
        )
        sold = await conn.fetchval(
            "SELECT count(*) FROM shaker_tickets WHERE status = 'ACTIVE'"
        )
        await conn.close()
        seats_left = max(0, 100 - int(sold or 0))
    except Exception as e:
        logger.warning("Shaker seat query failed: %s", e)

    caption = (
        "⚠️ <b>ห้องมีคนชัก — ใกล้เต็มแล้ว!</b>\n\n"
        f"🎯 เหลือเลขให้จับ: <b>{seats_left}/100</b>\n"
        "🕐 จันทร์ 21:00 — เลขออก!\n"
        "💰 รางวัล: <b>GOD MODE ถาวร</b> (มูลค่า ฿2,499)\n\n"
        "✨ จ่ายแค่ ฿100 — ลุ้นได้ตลอดชีพ!\n"
        "🔥 ที่นั่งจำกัด — คนช้าได้แค่ดู\n\n"
        "🎲 อิงผลหวยลาว 2 ตัวล่าง — โกงไม่ได้\n"
        "🔒 เลขเดียวมีคนเดียว — ห้ามชน"
    )
    kb = {
        "inline_keyboard": [[
            {"text": "🎰 รีบจับเลขเลย ฿100!",
             "url": "https://t.me/NamwarnJarern_bot?start=shaker"}
        ]]
    }

    success = 0; failed = 0
    img_path = "/app/assets/campaigns/shaker_promo.png"
    from telegram import InlineKeyboardMarkup, InlineKeyboardButton
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton(
            "🎰 รีบจับเลขเลย ฿100!",
            url="https://t.me/NamwarnJarern_bot?start=shaker",
        )
    ]])
    for group_id in free_groups:
        try:
            with open(img_path, "rb") as _f:
                await bot.send_photo(
                    chat_id=group_id, photo=_f, caption=caption,
                    parse_mode="HTML", reply_markup=kb,
                )
            success += 1
            await asyncio.sleep(1.2)
        except Exception as exc:
            failed += 1
            logger.warning("Shaker promo failed group %d: %s", group_id, exc)

    logger.info("Shaker promo round done: %d/%d (seats_left=%d)",
                success, len(free_groups), seats_left)
    try:
        await _send_discord_content_log(
            f"🎰 **Shaker Promo Round** seats_left={seats_left}\n"
            f"✅ {success} / ❌ {failed} / Total {len(free_groups)}"
        )
    except Exception:
        pass


async def post_gacha_promo_to_free_groups(context):
    """Post Gachapon promo (win-every-spin style) to all free groups."""
    bot = context.bot
    free_groups = await _get_free_groups_async()
    logger.info("Starting Gacha promo post to %d groups", len(free_groups))

    caption = (
        "🎁 <b>กาชาปอง — หมุนได้ของแน่นอน 100%</b>\n\n"
        "หมุนแล้วไม่มี \"ไม่ได้อะไรเลย\"\n"
        "ทุกครั้งได้รางวัล ✨\n\n"
        "🎫 1 หมุน <b>฿99</b>\n"
        "🎫🎫🎫 3 หมุน <b>฿270</b> <i>(ลด ฿27)</i>\n"
        "🎫×10 <b>฿890</b> <i>(ลด ฿100)</i>\n\n"
        "───── 🏆 รางวัลที่ลุ้นได้ ─────\n"
        "💰 ส่วนลด ฿50 (สะสมได้)\n"
        "🎬 ชุดคลิป A / B / C\n"
        "🎰 ห้องมีคนชัก ฿100\n"
        "💎 VIP 30 วัน\n"
        "🔥 OF+VIP 30 วัน\n"
        "👑 GOD MODE 90 วัน\n"
        "🌟 <b>GOD MODE ถาวร</b> (jackpot!)\n\n"
        "✅ หมุนปุ๊บได้ปั๊บ — ไม่มีรอ\n"
        "✅ ของซ้ำ? ระบบสุ่มใหม่ให้"
    )

    success = 0; failed = 0
    img_path = "/app/assets/campaigns/gacha_promo.png"
    from telegram import InlineKeyboardMarkup, InlineKeyboardButton
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton(
            "🎰 หมุนเลย!",
            url="https://t.me/NamwarnJarern_bot?start=gacha",
        )
    ]])
    for group_id in free_groups:
        try:
            with open(img_path, "rb") as _f:
                await bot.send_photo(
                    chat_id=group_id, photo=_f, caption=caption,
                    parse_mode="HTML", reply_markup=kb,
                )
            success += 1
            await asyncio.sleep(1.2)
        except Exception as exc:
            failed += 1
            logger.warning("Gacha promo failed group %d: %s", group_id, exc)

    logger.info("Gacha promo round done: %d/%d", success, len(free_groups))
    try:
        await _send_discord_content_log(
            f"🎰 **Gacha Promo Round**\n"
            f"✅ {success} / ❌ {failed} / Total {len(free_groups)}"
        )
    except Exception:
        pass


async def post_teaser_to_free_groups(context: ContextTypes.DEFAULT_TYPE) -> None:
    free_groups = await _get_free_groups_async()  # NEW: dynamic DB load
    """โพสต์ teaser (ข้อความอย่างเดียว) ไปทุกกลุ่มฟรี."""
    bot = context.bot
    round_time = get_round_time()
    logger.info("Starting text-only teaser post round (round=%s)...", round_time)

    base_caption, caption_style = await generate_teaser_caption()

    success = 0
    failed = 0

    for group_index, group_id in enumerate(free_groups):
        full_caption = build_caption(base_caption, round_time, group_index)
        try:
            await bot.send_message(
                chat_id=group_id,
                text=full_caption,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
            success += 1
            # Log engagement tracking
            await _log_teaser_post(round_time, group_index, caption_style, base_caption, 0)
            await asyncio.sleep(1)
        except Exception as exc:
            logger.error("Failed to post to group %d: %s", group_id, exc)
            failed += 1

    logger.info("Teaser round done: %d success, %d failed (style=%s)", success, failed, caption_style)
    await _send_discord_content_log(
        f"📢 **Content Bot: Teaser Round Complete** [round={round_time}]\n"
        f"📝 Style: {caption_style}\n"
        f"✅ Success: {success} / ❌ Failed: {failed} / Total: {len(free_groups)} groups"
    )


async def post_teaser_with_image(context: ContextTypes.DEFAULT_TYPE, content_id: int, file_id: str) -> None:
    """โพสต์ teaser พร้อมรูปเบลอไปทุกกลุ่มฟรี แล้ว mark content ว่าใช้แล้ว."""
    bot = context.bot
    round_time = get_round_time()
    logger.info("Starting teaser post with image (round=%s, content_id=%d)...", round_time, content_id)

    base_caption, caption_style = await generate_teaser_caption()

    # Blur image
    try:
        blurred_buf = await blur_image(bot, file_id)
    except Exception as exc:
        logger.error("Failed to blur image: %s", exc)
        # file_id ที่ Telegram ดึงไม่ได้ไม่ควรถูกหยิบซ้ำทุก schedule
        await mark_content_used(content_id)
        logger.warning("Marked bad content_id=%d as used after blur failure", content_id)
        await post_teaser_to_free_groups(context)
        return

    free_groups = await _get_free_groups_async()
    success = 0
    for group_index, group_id in enumerate(free_groups):
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
            await _log_teaser_post(round_time, group_index, caption_style, base_caption, 1)
            await asyncio.sleep(1)
        except Exception as exc:
            logger.error("Failed to post image to group %d: %s", group_id, exc)

    failed_img = len(free_groups) - success
    logger.info("Image teaser round done: %d/%d (style=%s)", success, len(free_groups), caption_style)

    if success > 0:
        await mark_content_used(content_id)
        logger.info("Marked content_id=%d as used", content_id)

    await _send_discord_content_log(
        f"🖼️ **Content Bot: Image Teaser Round Complete** [round={round_time}]\n"
        f"📝 Style: {caption_style}\n"
        f"✅ Success: {success} / ❌ Failed: {failed_img} / Total: {len(free_groups)} groups"
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

    base_caption, caption_style = await generate_teaser_caption()

    # เบลอรูปทั้งหมดล่วงหน้า
    blurred_items: list[tuple[int, io.BytesIO]] = []
    for c in contents:
        try:
            blurred_buf = await blur_image(bot, c["file_id"])
            blurred_items.append((c["id"], blurred_buf))
        except Exception as exc:
            logger.error("Failed to blur image content_id=%d: %s", c["id"], exc)
            # กันรูปเสียวนกลับมาใช้ซ้ำ
            await mark_content_used(c["id"])
            logger.warning("Marked bad content_id=%d as used after blur failure", c["id"])

    if len(blurred_items) < 2:
        # ถ้าเบลอได้น้อยกว่า 2 รูป → fallback เป็นทีละรูป โดยใช้รูปที่เบลอผ่านจริง
        if blurred_items:
            content_id, _ = blurred_items[0]
            matched = next((c for c in contents if c["id"] == content_id), None)
            if matched:
                await post_teaser_with_image(context, matched["id"], matched["file_id"])
            else:
                await post_teaser_to_free_groups(context)
        else:
            await post_teaser_to_free_groups(context)
        return

    free_groups = await _get_free_groups_async()
    success = 0
    for group_index, group_id in enumerate(free_groups):
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
            await _log_teaser_post(round_time, group_index, caption_style, base_caption, len(blurred_items))
            await asyncio.sleep(1.5)
        except Exception as exc:
            logger.error("Failed to post album to group %d: %s", group_id, exc)
            try:
                blurred_items[0][1].seek(0)
                await bot.send_photo(
                    chat_id=group_id,
                    photo=blurred_items[0][1],
                    caption=full_caption,
                    parse_mode="HTML",
                )
                success += 1
                await _log_teaser_post(round_time, group_index, caption_style, base_caption, 1)
                logger.info("Fallback single photo sent to group %d", group_id)
            except Exception as exc2:
                logger.error("Fallback single photo also failed for group %d: %s", group_id, exc2)

    failed_count = len(free_groups) - success
    logger.info("Album teaser round done: %d/%d groups (style=%s)", success, len(free_groups), caption_style)

    if success > 0:
        for content_id, _ in blurred_items:
            await mark_content_used(content_id)
        logger.info("Marked %d content items as used", len(blurred_items))

    await _send_discord_content_log(
        f"🖼️ **Content Bot: Album Teaser Round Complete** [round={round_time}]\n"
        f"📝 Style: {caption_style} | 📸 Album: {len(blurred_items)} images\n"
        f"✅ Success: {success} / ❌ Failed: {failed_count} / Total: {len(free_groups)} groups"
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
                await admin_bot.initialize()
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
    """Scheduled job: โพสต์ teaser — สุ่มจำนวนรูป 2-5 หรือ single photo สลับ album."""
    logger.info("scheduled_teaser triggered")

    # สุ่มโหมด: 30% single photo, 70% album
    use_single_mode = random.random() < 0.3

    # สุ่มจำนวนรูปใน album: 2-5 (ไม่ใช่ 3 ทุกรอบ)
    album_size = random.randint(2, 5)
    fetch_limit = 1 if use_single_mode else album_size

    contents = await fetch_multiple_content(limit=fetch_limit)

    # ถ้าไม่มี content ใหม่เลย → ลอง recycle content เก่า
    if not contents:
        recycled = await recycle_old_content()
        if recycled > 0:
            contents = await fetch_multiple_content(limit=fetch_limit)

    if use_single_mode and len(contents) >= 1:
        # Single photo mode — 1 รูป + caption ยาวขึ้น
        c = contents[0]
        logger.info("Single photo mode → content_id=%d", c["id"])
        await post_teaser_with_image(context, c["id"], c["file_id"])
    elif len(contents) >= 2:
        # Album mode — 2-5 รูป
        logger.info("Album mode → %d images", len(contents))
        await post_teaser_album(context, contents)
    elif len(contents) >= 1:
        c = contents[0]
        logger.info("Only 1 content available → single image (id=%d)", c["id"])
        await post_teaser_with_image(context, c["id"], c["file_id"])
    else:
        logger.info("No content in queue (even after recycle), posting text-only teaser")
        await post_teaser_to_free_groups(context)

    await _check_content_queue_alert()


# ── Task 4: Engagement Tracking — teaser_post_log ──

async def _ensure_teaser_post_log_table() -> None:
    """สร้างตาราง teaser_post_log ถ้ายังไม่มี."""
    try:
        async with get_session() as session:
            from sqlalchemy import text
            await session.execute(text("""
                CREATE TABLE IF NOT EXISTS teaser_post_log (
                    id SERIAL PRIMARY KEY,
                    round_time VARCHAR(10) NOT NULL,
                    group_index INT NOT NULL,
                    caption_style VARCHAR(50) NOT NULL,
                    caption_text TEXT NOT NULL,
                    photo_count INT NOT NULL DEFAULT 0,
                    posted_at TIMESTAMP DEFAULT NOW()
                )
            """))
            await session.execute(text("""
                CREATE INDEX IF NOT EXISTS ix_teaser_post_log_style ON teaser_post_log(caption_style)
            """))
            await session.execute(text("""
                CREATE INDEX IF NOT EXISTS ix_teaser_post_log_posted ON teaser_post_log(posted_at)
            """))
    except Exception as exc:
        logger.error("Failed to create teaser_post_log table: %s", exc)


async def _log_teaser_post(round_time: str, group_index: int, caption_style: str,
                           caption_text: str, photo_count: int) -> None:
    """Log ทุกครั้งที่ลง teaser → เก็บ style + caption + จำนวนรูป."""
    try:
        async with get_session() as session:
            from sqlalchemy import text
            await session.execute(text("""
                INSERT INTO teaser_post_log (round_time, group_index, caption_style, caption_text, photo_count)
                VALUES (:round_time, :group_index, :caption_style, :caption_text, :photo_count)
            """), {
                "round_time": round_time,
                "group_index": group_index,
                "caption_style": caption_style,
                "caption_text": caption_text[:500],
                "photo_count": photo_count,
            })
    except Exception as exc:
        logger.error("Failed to log teaser post: %s", exc)


async def get_caption_performance(days: int = 7) -> list[dict]:
    """JOIN teaser_post_log กับ teaser_clicks → หา style ไหน conversion ดีสุด."""
    try:
        async with get_session() as session:
            from sqlalchemy import text
            result = await session.execute(text("""
                SELECT
                    pl.caption_style,
                    COUNT(DISTINCT pl.id) as posts,
                    COUNT(tc.id) as clicks,
                    SUM(CASE WHEN tc.converted THEN 1 ELSE 0 END) as conversions,
                    CASE WHEN COUNT(tc.id) > 0
                        THEN ROUND(SUM(CASE WHEN tc.converted THEN 1 ELSE 0 END)::numeric / COUNT(tc.id) * 100, 1)
                        ELSE 0
                    END as conversion_rate
                FROM teaser_post_log pl
                LEFT JOIN teaser_clicks tc
                    ON pl.round_time = tc.round_time
                    AND pl.group_index = tc.group_index
                    AND tc.created_at::date = pl.posted_at::date
                WHERE pl.posted_at >= NOW() - (:days * INTERVAL '1 day')
                GROUP BY pl.caption_style
                ORDER BY clicks DESC
            """), {"days": int(days)})
            rows = result.fetchall()
            return [
                {
                    "style": r[0],
                    "posts": r[1],
                    "clicks": r[2],
                    "conversions": r[3],
                    "conversion_rate": float(r[4]),
                }
                for r in rows
            ]
    except Exception as exc:
        logger.error("get_caption_performance failed: %s", exc)
        return []


# ── Task 3: Smart Scheduling — analyze_best_rounds() ──

async def analyze_best_rounds(days: int = 7) -> list[dict]:
    """ดึง teaser_clicks 7 วันล่าสุด GROUP BY round_time → เรียงตาม clicks DESC."""
    try:
        async with get_session() as session:
            from sqlalchemy import text
            result = await session.execute(text("""
                SELECT
                    round_time,
                    COUNT(*) as clicks,
                    SUM(CASE WHEN converted THEN 1 ELSE 0 END) as conversions,
                    CASE WHEN COUNT(*) > 0
                        THEN ROUND(SUM(CASE WHEN converted THEN 1 ELSE 0 END)::numeric / COUNT(*) * 100, 1)
                        ELSE 0
                    END as conversion_rate
                FROM teaser_clicks
                WHERE created_at >= NOW() - (:days * INTERVAL '1 day')
                GROUP BY round_time
                ORDER BY clicks DESC
            """), {"days": int(days)})
            rows = result.fetchall()
            rounds = [
                {
                    "round_time": r[0],
                    "clicks": r[1],
                    "conversions": r[2],
                    "conversion_rate": float(r[3]),
                }
                for r in rows
            ]
            if rounds:
                logger.info(
                    "📊 Best rounds (last %d days): %s",
                    days,
                    " | ".join(f"{r['round_time']}: {r['clicks']} clicks ({r['conversion_rate']}%%)" for r in rounds),
                )
            return rounds
    except Exception as exc:
        logger.error("analyze_best_rounds failed: %s", exc)
        return []


# ── Task 5: Daily Report ──

async def send_daily_content_report(context: ContextTypes.DEFAULT_TYPE) -> None:
    """ส่ง daily report ไป Admin Group ทุกวัน 23:30 ไทย."""
    ADMIN_BOT_TOKEN = os.environ.get("ADMIN_BOT_TOKEN", "")
    ADMIN_GROUP_ID = int(os.environ.get("ADMIN_GROUP_CHAT_ID", os.environ.get("TG_GROUP_ADMIN", "-1003830920430")))

    if not ADMIN_BOT_TOKEN:
        logger.error("ADMIN_BOT_TOKEN not set, skipping daily report")
        return

    try:
        from telegram import Bot as _Bot
        admin_bot = _Bot(token=ADMIN_BOT_TOKEN)
        await admin_bot.initialize()

        async with get_session() as session:
            from sqlalchemy import text

            # Clicks วันนี้
            today_clicks_row = await session.execute(text("""
                SELECT COUNT(*) FROM teaser_clicks
                WHERE (created_at AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Bangkok')::date
                    = (NOW() AT TIME ZONE 'Asia/Bangkok')::date
            """))
            today_clicks = today_clicks_row.scalar() or 0

            # Clicks เมื่อวาน
            yesterday_clicks_row = await session.execute(text("""
                SELECT COUNT(*) FROM teaser_clicks
                WHERE (created_at AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Bangkok')::date
                    = (NOW() AT TIME ZONE 'Asia/Bangkok')::date - INTERVAL '1 day'
            """))
            yesterday_clicks = yesterday_clicks_row.scalar() or 0

            # % เปลี่ยนแปลง
            if yesterday_clicks > 0:
                change_pct = round((today_clicks - yesterday_clicks) / yesterday_clicks * 100, 1)
                change_str = f"+{change_pct}%" if change_pct >= 0 else f"{change_pct}%"
                change_emoji = "📈" if change_pct >= 0 else "📉"
            else:
                change_str = "N/A"
                change_emoji = "➖"

            # Best round วันนี้
            best_round_row = await session.execute(text("""
                SELECT round_time, COUNT(*) as clicks
                FROM teaser_clicks
                WHERE (created_at AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Bangkok')::date
                    = (NOW() AT TIME ZONE 'Asia/Bangkok')::date
                GROUP BY round_time ORDER BY clicks DESC LIMIT 1
            """))
            best_round = best_round_row.fetchone()
            best_round_str = f"{best_round[0]} ({best_round[1]} clicks)" if best_round else "N/A"

            # Best group วันนี้
            best_group_row = await session.execute(text("""
                SELECT group_index, COUNT(*) as clicks
                FROM teaser_clicks
                WHERE (created_at AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Bangkok')::date
                    = (NOW() AT TIME ZONE 'Asia/Bangkok')::date
                GROUP BY group_index ORDER BY clicks DESC LIMIT 1
            """))
            best_group = best_group_row.fetchone()
            best_group_str = f"Group #{best_group[0]} ({best_group[1]} clicks)" if best_group else "N/A"

            # Conversion rate วันนี้
            conv_row = await session.execute(text("""
                SELECT
                    COUNT(*) as total,
                    SUM(CASE WHEN converted THEN 1 ELSE 0 END) as conversions
                FROM teaser_clicks
                WHERE (created_at AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Bangkok')::date
                    = (NOW() AT TIME ZONE 'Asia/Bangkok')::date
            """))
            conv = conv_row.fetchone()
            conv_rate = round(conv[1] / conv[0] * 100, 1) if conv and conv[0] > 0 else 0

            # คลิปเหลือกี่ชิ้น
            from sqlalchemy import func as sqlfunc
            queue_row = await session.execute(
                select(sqlfunc.count(ContentQueue.id)).where(ContentQueue.is_used == False)
            )
            queue_remaining = queue_row.scalar() or 0

            # Caption style ที่ดีสุด (จาก engagement tracking)
            best_style_str = "N/A"
            try:
                style_row = await session.execute(text("""
                    SELECT pl.caption_style, COUNT(tc.id) as clicks
                    FROM teaser_post_log pl
                    LEFT JOIN teaser_clicks tc
                        ON pl.round_time = tc.round_time
                        AND pl.group_index = tc.group_index
                        AND tc.created_at::date = pl.posted_at::date
                    WHERE pl.posted_at >= NOW() - INTERVAL '7 days'
                    GROUP BY pl.caption_style
                    ORDER BY clicks DESC LIMIT 1
                """))
                best_style = style_row.fetchone()
                if best_style:
                    best_style_str = f"{best_style[0]} ({best_style[1]} clicks)"
            except Exception:
                pass

        # แนะนำรอบเวลาที่ดีสุด
        best_rounds = await analyze_best_rounds(days=7)
        recommended_str = "N/A"
        if best_rounds:
            top3 = best_rounds[:3]
            recommended_str = " → ".join(f"{r['round_time']} ({r['clicks']})" for r in top3)

        now_th = datetime.now(TH_TZ)
        report = (
            f"📊 <b>Content Bot Daily Report</b>\n"
            f"📅 {now_th.strftime('%d/%m/%Y')}\n"
            f"━━━━━━━━━━━━━━━━━━\n\n"
            f"{change_emoji} <b>Clicks วันนี้:</b> {today_clicks} ({change_str} vs เมื่อวาน {yesterday_clicks})\n"
            f"🏆 <b>Best Round:</b> {best_round_str}\n"
            f"🎯 <b>Best Group:</b> {best_group_str}\n"
            f"💰 <b>Conversion Rate:</b> {conv_rate}%\n"
            f"📸 <b>คลิปเหลือ:</b> {queue_remaining} ชิ้น\n"
            f"📝 <b>Best Caption Style:</b> {best_style_str}\n\n"
            f"💡 <b>แนะนำรอบเวลา (7d):</b> {recommended_str}\n"
            f"━━━━━━━━━━━━━━━━━━"
        )

        await admin_bot.send_message(
            chat_id=ADMIN_GROUP_ID,
            text=report,
            parse_mode="HTML",
        )
        logger.info("Daily content report sent to admin group")

    except Exception as exc:
        logger.error("send_daily_content_report failed: %s", exc)


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



async def _global_error_handler(update, context):
    """[Phase 4 D] Catch unhandled exceptions and notify via hub.

    Transient network errors (httpx.ReadError, TimedOut, NetworkError) come
    from long-polling and are auto-retried by PTB. Log but do NOT notify.
    """
    import logging as _logging
    _log = _logging.getLogger(__name__)
    err = context.error
    err_name = type(err).__name__
    _TRANSIENT = ("NetworkError", "TimedOut", "ReadError", "ConnectError",
                  "WriteError", "PoolTimeout", "ReadTimeout", "ConnectTimeout")
    if err_name in _TRANSIENT or "ReadError" in str(err):
        _log.warning("Transient network error (not alerting): %s: %s", err_name, err)
        return
    try:
        from shared.notify import notify as _notify
        await _notify("bot_crash",
                     title=f"Unhandled exception in {__name__}",
                     body=f"{err_name}: {err}")
    except Exception:
        pass




# ──────────────────────────────────────────────────────────────────────────
# Phase A.8 (2026-06-27): DB-driven schedule loader
# ──────────────────────────────────────────────────────────────────────────
async def _load_schedule_from_db() -> dict[str, dict]:
    """Return {job_name: {hour, minute, is_enabled}} from bot_schedules.

    Uses a fresh asyncpg connection (not shared pool) so it works inside a temp event loop.
    Returns empty dict on any failure → caller falls back to hardcoded.
    """
    import os as _os_sched
    try:
        import asyncpg as _ap
        url = _os_sched.environ.get("DATABASE_URL", "").replace("postgresql+asyncpg://", "postgresql://")
        if not url:
            return {}
        conn = await _ap.connect(url)
        try:
            rows = await conn.fetch(
                "SELECT job_name, schedule_hour, schedule_minute, is_enabled, handler_key "
                "FROM bot_schedules WHERE bot_key = 'content_bot'"
            )
            return {
                r['job_name']: {
                    'hour': int(r['schedule_hour']),
                    'handler_key': r.get('handler_key') or '',
                    'minute': int(r['schedule_minute']),
                    'is_enabled': bool(r['is_enabled']),
                }
                for r in rows
            }
        finally:
            await conn.close()
    except Exception as exc:
        logger.warning("load_schedule_from_db failed: %s", exc)
        return {}




# ──────────────────────────────────────────────────────────────────────────
# B.1.C (2026-06-27): Generic DB-driven template poster
# ──────────────────────────────────────────────────────────────────────────
# Lets boss create a new promo end-to-end from Dashboard:
#   1. Add row to content_templates (caption_html, image_path, buttons)
#   2. Add row to bot_schedules with handler_key='generic_template'
#      and job_name='template_<template_key>'
#   3. Restart content_bot — auto-picks up the schedule + posts
# ──────────────────────────────────────────────────────────────────────────

_TEMPLATE_CACHE: dict[str, dict] = {}
_TEMPLATE_CACHE_TS: float = 0.0
_TEMPLATE_CACHE_TTL = 60.0  # 60s cache so Dashboard edits show within 1 min


async def _load_template_from_db(template_key: str) -> dict | None:
    """Load one template from content_templates. Returns None if missing or disabled.
    
    Cached 60s to avoid hammering DB on every post.
    """
    import time as _t
    global _TEMPLATE_CACHE_TS, _TEMPLATE_CACHE
    if _t.time() - _TEMPLATE_CACHE_TS > _TEMPLATE_CACHE_TTL:
        _TEMPLATE_CACHE = {}  # force refresh
        _TEMPLATE_CACHE_TS = _t.time()
    if template_key in _TEMPLATE_CACHE:
        return _TEMPLATE_CACHE[template_key]
    try:
        import asyncpg, os as _os
        url = _os.environ.get("DATABASE_URL", "").replace("postgresql+asyncpg://", "postgresql://")
        if not url:
            return None
        conn = await asyncpg.connect(url)
        try:
            row = await conn.fetchrow(
                "SELECT template_key, display_name, caption_html, image_path, "
                "buttons, is_enabled FROM content_templates "
                "WHERE bot_key = $1 AND template_key = $2",
                "content_bot", template_key,
            )
            if not row or not row["is_enabled"]:
                _TEMPLATE_CACHE[template_key] = None
                return None
            # FIX 2026-06-29 (#468): asyncpg ไม่ register JSONB codec ใน content_bot
            # → row["buttons"] เป็น raw str ไม่ใช่ list → _build_inline_keyboard
            # loop char by char → ทุก template post ไม่มีปุ่ม
            # → parse json.loads ตรงนี้
            _btns_raw = row["buttons"]
            if isinstance(_btns_raw, str):
                try:
                    import json as _json_btn
                    _btns_raw = _json_btn.loads(_btns_raw)
                except Exception:
                    _btns_raw = []
            data = {
                "template_key": row["template_key"],
                "display_name": row["display_name"],
                "caption_html": row["caption_html"] or "",
                "image_path": row["image_path"] or "",
                "buttons": _btns_raw or [],
            }
            _TEMPLATE_CACHE[template_key] = data
            return data
        finally:
            await conn.close()
    except Exception as exc:
        logger.warning("template load failed for %s: %s", template_key, exc)
        return None


def _build_inline_keyboard(buttons: list) -> "InlineKeyboardMarkup | None":
    """Convert DB buttons JSONB into InlineKeyboardMarkup.
    
    Expected format: [[{"text": "...", "url": "..."}, ...], ...]
    or flat: [{"text": "...", "url": "..."}] (one row)
    """
    from telegram import InlineKeyboardMarkup, InlineKeyboardButton
    if not buttons:
        return None
    try:
        # Detect flat vs nested
        if isinstance(buttons, list) and buttons and isinstance(buttons[0], dict):
            rows_raw = [buttons]
        else:
            rows_raw = buttons
        rows = []
        for r in rows_raw:
            row = []
            for b in r:
                if not isinstance(b, dict) or not b.get("text"):
                    continue
                if b.get("url"):
                    row.append(InlineKeyboardButton(b["text"], url=b["url"]))
                elif b.get("callback_data"):
                    row.append(InlineKeyboardButton(b["text"], callback_data=b["callback_data"]))
            if row:
                rows.append(row)
        return InlineKeyboardMarkup(rows) if rows else None
    except Exception as exc:
        logger.warning("button parse failed: %s", exc)
        return None



# ─────────────────────────────────────────────────────────────────
# DAY 0 (2026-06-28): Promotions poster — reads from promotions table
# ─────────────────────────────────────────────────────────────────


async def post_template_to_free_groups(
    context: ContextTypes.DEFAULT_TYPE,
    template_key: str,
) -> None:
    """Generic poster: read template from DB, send to all free groups.
    
    Called via scheduler with template_key bound at registration time.
    """
    bot = context.bot
    tpl = await _load_template_from_db(template_key)
    if not tpl:
        logger.warning("Template %s not found or disabled — skipping", template_key)
        return
    
    free_groups = await _get_free_groups_async()
    if not free_groups:
        logger.warning("No free groups for template %s", template_key)
        return
    
    caption = tpl["caption_html"]
    image_path = tpl["image_path"]
    keyboard = _build_inline_keyboard(tpl["buttons"])
    success = 0
    failed = 0
    
    logger.info(
        "Posting template %s (%s) to %d groups",
        template_key, tpl["display_name"], len(free_groups),
    )
    
    # Resolve image path — accept relative paths from /app/assets/
    from pathlib import Path as _P
    img_full = None
    if image_path:
        img_full = _P("/app") / image_path.lstrip("/")
        if not img_full.exists():
            # Try campaigns/ prefix as fallback
            alt = _P("/app/assets/campaigns") / image_path
            if alt.exists():
                img_full = alt
            else:
                img_full = None
                logger.warning("Image not found: %s", image_path)
    
    for group_id in free_groups:
        try:
            if img_full and img_full.exists():
                with open(img_full, "rb") as _f:
                    await bot.send_photo(
                        chat_id=group_id, photo=_f, caption=caption,
                        parse_mode="HTML", reply_markup=keyboard,
                    )
            else:
                await bot.send_message(
                    chat_id=group_id, text=caption, parse_mode="HTML",
                    disable_web_page_preview=False, reply_markup=keyboard,
                )
            success += 1
            await asyncio.sleep(1.2)
        except Exception as exc:
            failed += 1
            logger.warning("template %s failed group %d: %s", template_key, group_id, exc)
    
    logger.info(
        "Template %s done: %d/%d success",
        template_key, success, len(free_groups),
    )
    try:
        await _send_discord_content_log(
            f"📤 **{tpl['display_name']}** posted\n"
            f"✅ {success} / ❌ {failed} / Total {len(free_groups)} groups"
        )
    except Exception:
        pass


def main() -> None:
    if not CONTENT_BOT_TOKEN:
        logger.error("CONTENT_BOT_TOKEN not set")
        return

    app = (
        Application.builder()
        .token(CONTENT_BOT_TOKEN)
        .read_timeout(30)
        .write_timeout(30)
        .connect_timeout(15)
        .build()
    )

    # Post-init: สร้างตาราง DB ถ้ายังไม่มี
    async def post_init(application: Application) -> None:
        await init_db()
        await _ensure_teaser_post_log_table()
        logger.info("DB initialized (content_queue + teaser_post_log tables ready)")

    app.post_init = post_init

    # Handler: รับรูปใน DM จาก authorized users
    app.add_handler(MessageHandler(
        (filters.PHOTO | filters.Document.IMAGE) & filters.ChatType.PRIVATE,
        handle_authorized_photo,
    ))

    # SCHEDULE_V2 — redesigned 2026-06-02; A.8 (2026-06-27): reads bot_schedules DB
    job_queue = app.job_queue

    # Load DB schedule overrides — use a temp loop, then restore for PTB
    import asyncio as _asyncio_sched
    _db_sched = {}
    try:
        _tmp_loop = _asyncio_sched.new_event_loop()
        try:
            _db_sched = _tmp_loop.run_until_complete(_load_schedule_from_db())
        finally:
            _tmp_loop.close()
        # Give PTB a fresh loop (it calls get_event_loop() internally)
        _asyncio_sched.set_event_loop(_asyncio_sched.new_event_loop())
    except Exception as _exc:
        logger.warning("DB schedule load crashed: %s — using hardcoded", _exc)
        _db_sched = {}
    logger.info("Loaded %d schedule overrides from bot_schedules", len(_db_sched))

    def _sched_time(job_name: str, default_h: int, default_m: int):
        """Look up DB schedule for this job. Returns None if disabled."""
        cfg = _db_sched.get(job_name)
        if cfg and not cfg.get("is_enabled", True):
            return None  # disabled
        if cfg:
            return dt_time(hour=cfg["hour"], minute=cfg["minute"], tzinfo=TH_TZ)
        return dt_time(hour=default_h, minute=default_m, tzinfo=TH_TZ)

    # TEASER posts — 7 default rounds, can be overridden/disabled per slot in DB
    teaser_defaults = [(1,0),(7,30),(11,0),(13,0),(17,0),(21,0),(23,0)]
    teaser_times = []
    for i, (dh, dm) in enumerate(teaser_defaults):
        t = _sched_time(f"teaser_{i}", dh, dm)
        if t is not None:
            teaser_times.append((i, t))
        else:
            logger.info("teaser_%d disabled via Dashboard", i)
    for i, t in teaser_times:
        job_queue.run_daily(scheduled_teaser, time=t, name=f"teaser_{i}")

    # VIP PROMO — 2 rounds default, DB-overridable per slot
    vip_defaults = [(9, 30), (19, 30)]
    for i, (dh, dm) in enumerate(vip_defaults):
        t = _sched_time(f"vip_promo_{i}", dh, dm)
        if t is not None:
            job_queue.run_daily(post_vip_promo_to_free_groups, time=t, name=f"vip_promo_{i}")
        else:
            logger.info("vip_promo_%d disabled via Dashboard", i)

    # GOD MODE end-month promo — REDUCED from 4 to 1 round/day (avoid spam)
    # Only runs when is_endmonth_vip_promo_active() returns True
    if os.environ.get("ENABLE_ENDMONTH_GOD_PROMO_SCHEDULE", "true").lower() == "true":
        job_queue.run_daily(
            post_endmonth_god_promo_to_free_groups,
            time=dt_time(hour=15, minute=0, tzinfo=TH_TZ),
            name="endmonth_god_promo_daily",
        )

    # SHAKER promo — daily at 13:00 (lunch hour — high engagement)
    job_queue.run_daily(
        post_shaker_promo_to_free_groups,
        time=dt_time(hour=13, minute=0, tzinfo=TH_TZ),
        name="shaker_promo_daily_1300",
    )

    # GACHA promo — daily at 20:00 (peak evening — entertainment time)
    job_queue.run_daily(
        post_gacha_promo_to_free_groups,
        time=dt_time(hour=20, minute=0, tzinfo=TH_TZ),
        name="gacha_promo_daily_2000",
    )

    # Schedule daily report ทุกวัน 23:30 ไทย
    job_queue.run_daily(
        send_daily_content_report,
        time=dt_time(hour=23, minute=30, tzinfo=TH_TZ),
        name="daily_content_report",
    )

    # Schedule daily best-round analysis ทุกวัน 23:00 ไทย (log ก่อนรายงาน)
    async def _scheduled_analyze(ctx: ContextTypes.DEFAULT_TYPE) -> None:
        await analyze_best_rounds(days=7)

    job_queue.run_daily(
        _scheduled_analyze,
        time=dt_time(hour=23, minute=0, tzinfo=TH_TZ),
        name="analyze_best_rounds",
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

    # ──────────────────────────────────────────────────────────────────────
    # B.1.C (2026-06-27): GENERIC TEMPLATES — auto-bind from DB
    # ──────────────────────────────────────────────────────────────────────
    # Any schedule with handler_key='generic_template' + job_name starting
    # with 'template_' gets bound to post_template_to_free_groups.
    # This lets boss create new promos end-to-end from Dashboard without code.
    import functools as _ft
    generic_count = 0
    for _job_name, _cfg in _db_sched.items():
        if _cfg.get("handler_key") != "generic_template":
            continue
        if not _cfg.get("is_enabled", True):
            logger.info("generic schedule %s disabled via Dashboard", _job_name)
            continue
        # Convention: job_name = 'template_<template_key>'
        if not _job_name.startswith("template_"):
            logger.warning("generic schedule %s ignored — must start with 'template_'", _job_name)
            continue
        _tpl_key = _job_name[len("template_"):]
        _t = dt_time(hour=_cfg["hour"], minute=_cfg["minute"], tzinfo=TH_TZ)
        # Bind template_key into the callback
        async def _generic_cb(_ctx, _k=_tpl_key):
            await post_template_to_free_groups(_ctx, _k)
        job_queue.run_daily(_generic_cb, time=_t, name=_job_name)
        generic_count += 1
        logger.info("Generic template scheduled: %s at %02d:%02d", _tpl_key, _cfg["hour"], _cfg["minute"])
    if generic_count:
        logger.info("Loaded %d generic template schedules from DB", generic_count)


    logger.info("Content Bot (มิน) starting — 5 rounds/day to %d groups", len(FREE_GROUPS))
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
