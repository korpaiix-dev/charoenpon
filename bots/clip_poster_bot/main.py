"""Clip Poster Bot — admin DMs video, bot watermarks + posts to FREE groups.

Workflow (zero-config for admin):
  1. Admin DM video (with optional caption text = tier hint like "300" or "VIP" or "2499")
  2. Bot:
     - Whitelist check (admin tg_id)
     - Download video to temp file
     - ffmpeg overlay logo PNG bottom-right (15% width)
     - Pick random caption from CAPTION_TEMPLATES (substitute {tier} if hint given)
     - Send to all active FREE groups in group_registry
     - Reply: "✅ ส่ง X/Y" with breakdown
  3. Logs to clip_poster_jobs table

Env vars (all required):
  CLIP_POSTER_BOT_TOKEN     - from @BotFather
  CLIP_POSTER_ADMIN_IDS     - comma-separated tg_ids (e.g. "8502597269,1234567")
  CLIP_POSTER_LOGO_PATH     - path to logo PNG (default: /app/assets/clip_logo.png)
  DATABASE_URL              - postgres URL (shared from .env)
"""
from __future__ import annotations

import asyncio
import logging
import os
import random
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

import time as _time
from io import BytesIO
from telegram import (
    InlineKeyboardButton, InlineKeyboardMarkup, Update,
    InputMediaPhoto, InputMediaVideo, InputMediaAnimation,
)
from telegram.ext import (
    Application, CallbackQueryHandler, CommandHandler, ContextTypes,
    MessageHandler, filters,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("clip_poster_bot")

# ─── Config ──────────────────────────────────────────────────────────────────
BOT_TOKEN = os.environ.get("CLIP_POSTER_BOT_TOKEN", "")
ADMIN_IDS = {
    int(x.strip()) for x in os.environ.get("CLIP_POSTER_ADMIN_IDS", "").split(",")
    if x.strip().isdigit()
}
LOGO_PATH = os.environ.get("CLIP_POSTER_LOGO_PATH", "/app/assets/clip_logo.png")
SALES_BOT_USERNAME = os.environ.get("SALES_BOT_USERNAME", "charoenpon_bot")

# In-memory pending uploads: admin_tg_id → {video, type, msg_id, ...}
# Cleared after tier picked or canceled. Survives across bot lifetime only.
pending_uploads: dict[int, dict] = {}

# Caption templates with clickable HTML links + tier-aware deep-link for tracking.
# {tier} = price hint, {url} = full deep link (e.g. https://t.me/bot?start=clip_300)
CAPTION_TEMPLATES = [
    "🔥 <b>ตัวอย่างหลุด</b> — ดูเต็มในห้อง VIP เจริญพร\n💎 <b>{offer}</b>\n\n👉 <a href=\"{url}\">สมัครที่นี่ — ทักบอทเลย</a>",
    "💕 <b>แค่ตัวอย่าง</b> — ของเต็มจัดเต็มกว่านี้\n💎 สมัคร <b>{offer}</b>\n\n👉 <a href=\"{url}\">กดเลย — เข้าห้อง VIP</a>",
    "🍃 <b>น้ำจิ้มก่อน</b> — เคลียร์ๆ ในห้องจริง\n📦 <b>{offer}</b> ดูเต็มเลย\n\n👉 <a href=\"{url}\">สมัครที่นี่</a>",
    "✨ <b>ตัวอย่างเด็ดๆ</b> จากห้องเจริญพร\n🎬 ดูเต็ม <b>{offer}</b>\n\n👉 <a href=\"{url}\">สมัครเลย — กดที่นี่</a>",
    "🎯 <b>ดูได้แค่ตัวอย่าง</b> — สมัครเข้า VIP ดูเต็ม\n💰 <b>{offer}</b> คุ้มสุดๆ\n\n👉 <a href=\"{url}\">สมัครที่นี่ — ทักบอท</a>",
    "🚀 <b>อยากดูเต็มไหม?</b> เข้า VIP เจริญพรเลย\n💸 <b>{offer}</b>\n\n👉 <a href=\"{url}\">กดเลย — สมัครที่นี่</a>",
    "🌟 <b>ตัวอย่างคลิป VIP เจริญพร</b>\n💎 <b>{offer}</b> — ทักบอท\n\n👉 <a href=\"{url}\">กดเลย — เข้าห้อง</a>",
    "💥 <b>ของจริงเต็มในห้อง</b> — นี่แค่ตัวอย่าง\n💎 <b>{offer}</b>\n\n👉 <a href=\"{url}\">สมัครที่นี่ — ทักบอทเลย</a>",
    "🎁 <b>ดูฟรีแค่นี้</b> — เต็มอยู่ห้อง VIP\n📲 สมัคร <b>{offer}</b>\n\n👉 <a href=\"{url}\">กดที่นี่ — เข้าเลย</a>",
    "⭐ <b>ตัวอย่างเด็ดๆ</b> — สมัครดูเต็ม\n💎 <b>{offer}</b> เท่านั้น\n\n👉 <a href=\"{url}\">สมัครที่นี่ — กดเลย</a>",
]
# When no tier hint → general fallback list (also clickable link)
CAPTION_GENERIC = [
    "🔥 <b>ตัวอย่างจากห้อง VIP เจริญพร</b>\n💎 ดูเต็มเริ่ม ฿300/30 วัน\n\n👉 <a href=\"{url}\">สมัครที่นี่ — ทักบอทเลย</a>",
    "💕 <b>แค่น้ำจิ้ม</b> — ของจริงในห้อง VIP\n💎 เริ่ม ฿300 มีหลายแพ็กให้เลือก\n\n👉 <a href=\"{url}\">กดเลย — สมัครที่นี่</a>",
    "🎬 <b>ตัวอย่างคลิป</b> — สมัครดูเต็ม\n📦 มี ฿300 / ฿1299 / ฿2499\n\n👉 <a href=\"{url}\">กดที่นี่ — เข้าห้อง VIP</a>",
    "✨ <b>ดูได้แค่นี้ฟรี</b> — เต็มในห้อง VIP\n💎 เริ่ม ฿300/30 วัน คุ้มสุดๆ\n\n👉 <a href=\"{url}\">สมัครเลย — กดที่นี่</a>",
    "🌟 <b>อยากดูเต็ม?</b> ห้อง VIP เจริญพร\n💸 เริ่ม ฿300 มีหลายราคาให้เลือก\n\n👉 <a href=\"{url}\">กดเลย — สมัครที่นี่</a>",
]

# ─── Tier detection from admin's caption ────────────────────────────────────
TIER_PATTERNS = {
    "300": r"\b300\b|tier.?300|TIER_300|VIP\s*300|G300",
    "500": r"\b500\b|tier.?500|TIER_500|G500",
    "1299": r"\b1[,.]?299\b|tier.?1299|TIER_1299|GOD|VGOD|SSS|INTER|RANDOM|SERIES",
    "2499": r"\b2[,.]?499\b|tier.?2499|TIER_2499|SUMMER|STORAGE",
    "4999": r"\b4[,.]?999\b|tier.?4999|TIER_4999|SUPER\s*VIP|SUPERVIP|WISDOM",
}




def build_url(tier: Optional[str]) -> str:
    """Deep-link to sales bot with tier-aware tracking param.

    Format: https://t.me/<sales_bot>?start=clip_<tier>  (e.g. clip_300)
    No tier → clip_promo (generic CTA)
    """
    code = f"clip_{tier}" if tier else "clip_promo"
    return f"https://t.me/{SALES_BOT_USERNAME}?start={code}"


def build_clip_keyboard(tier: Optional[str], credit_url: Optional[str]) -> InlineKeyboardMarkup:
    """Build inline keyboard for clip posts: VIP CTA + credit/review group."""
    sale_url = build_url(tier)
    sale_label = f"💎 สมัคร VIP ฿{tier}" if tier else "💎 สมัครเข้าห้อง VIP"
    rows = [[InlineKeyboardButton(sale_label, url=sale_url)]]
    if credit_url:
        rows.append([InlineKeyboardButton("📋 เช็คเครดิต / รีวิวลูกค้าจริง", url=credit_url)])
    return InlineKeyboardMarkup(rows)


# Real package facts from the packages table (single source of truth) — cached 300s.
_pkg_offer_cache: dict = {}


async def get_tier_offer(tier: str) -> dict:
    """Return {price_str, dur, name} for a tier from the ACTUAL packages table so captions never
    hardcode price/duration. dur = 'ตลอดชีพ' for lifetime, 'N เดือน'/'N วัน' otherwise. On any
    error falls back to the raw tier number with no duration (safe)."""
    import time as _t
    now = _t.time()
    cached = _pkg_offer_cache.get(tier)
    if cached and cached[1] > now:
        return cached[0]
    offer = {"price_str": str(tier), "dur": "", "name": ""}
    try:
        import asyncpg
        db_url = os.environ.get("DATABASE_URL", "").replace("postgresql+asyncpg://", "postgresql://")
        if db_url:
            conn = await asyncpg.connect(db_url)
            try:
                row = await conn.fetchrow(
                    "SELECT name, price, duration_days FROM packages WHERE tier = $1 AND is_active = TRUE",
                    f"TIER_{tier}",
                )
            finally:
                await conn.close()
            if row:
                price = int(float(row["price"]))
                dd = row["duration_days"]
                if dd is None or dd >= 3650:
                    dur = "ตลอดชีพ"
                elif dd >= 90 and dd % 30 == 0:
                    dur = f"{dd // 30} เดือน"
                else:
                    dur = f"{dd} วัน"
                offer = {"price_str": f"{price:,}", "dur": dur, "name": row["name"]}
                _pkg_offer_cache[tier] = (offer, now + 300)
    except Exception as exc:
        logger.warning("get_tier_offer(%s) failed: %s", tier, exc)
    return offer


async def pick_caption(tier: Optional[str]) -> str:
    """Build caption text (CTAs handled by inline buttons below).

    ROOT-FIX 2026-07-11: price + duration come from the ACTUAL packages table (get_tier_offer),
    never a hardcoded '30 วัน'. 2499 = ตลอดชีพ, 1299 = 3 เดือน, 300/500 = 30 วัน, etc.
    """
    url = build_url(tier)
    if tier:
        offer = await get_tier_offer(tier)
        offer_str = f"฿{offer['price_str']}"
        if offer.get("dur"):
            offer_str += f" · {offer['dur']}"
        tmpl = random.choice(CAPTION_TEMPLATES)
        return tmpl.format(offer=offer_str, url=url)
    tmpl = random.choice(CAPTION_GENERIC)
    return tmpl.format(url=url)




# Credit group URL — fetched from promo_config table (cached 60s)
_credit_cache = {"val": None, "expires": 0}


async def get_credit_group_url() -> Optional[str]:
    """Read credit_group_url from promo_config (60s cache). Return None on fail."""
    import time as _t
    now = _t.time()
    if _credit_cache["val"] and _credit_cache["expires"] > now:
        return _credit_cache["val"]
    try:
        import asyncpg
        db_url = os.environ.get("DATABASE_URL", "").replace("postgresql+asyncpg://", "postgresql://")
        if not db_url:
            return None
        conn = await asyncpg.connect(db_url)
        try:
            row = await conn.fetchrow(
                "SELECT value_json FROM promo_config WHERE config_key = 'credit_group_url'"
            )
            if row:
                data = row["value_json"]
                if isinstance(data, str):
                    import json as _json
                    data = _json.loads(data)
                url = data.get("url") if data else None
                _credit_cache["val"] = url
                _credit_cache["expires"] = now + 60
                return url
        finally:
            await conn.close()
    except Exception as exc:
        logger.warning("get_credit_group_url failed: %s", exc)
    return None


# ─── Watermark photo via PIL ─────────────────────────────────────────────────
_FONT_PATHS = [
    "/usr/share/fonts/truetype/tlwg/Sawasdee-Bold.ttf",
    "/usr/share/fonts/truetype/tlwg/Sawasdee.ttf",
    "/usr/share/fonts/truetype/tlwg/Waree-Bold.ttf",
    "/usr/share/fonts/truetype/tlwg/Waree.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]


def _resolve_font(font_size: int):
    """Find first available font on disk + size."""
    from PIL import ImageFont
    for fp in _FONT_PATHS:
        if Path(fp).exists():
            try:
                return ImageFont.truetype(fp, font_size)
            except Exception:
                continue
    return ImageFont.load_default()


def watermark_photo_bytes(input_bytes: bytes) -> bytes:
    """Overlay text watermark 'เจริญพร' bottom-right semi-transparent.
    Returns JPEG bytes ready to send_photo().
    """
    from PIL import Image, ImageDraw
    try:
        im = Image.open(BytesIO(input_bytes)).convert("RGBA")
        W, H = im.size
        # Text size scales with image width (≈4.5% of width, min 28)
        fs = max(28, int(W * 0.045))
        font = _resolve_font(fs)
        text = "เจริญพร"
        # Overlay transparent layer
        overlay = Image.new("RGBA", im.size, (255, 255, 255, 0))
        draw = ImageDraw.Draw(overlay)
        # Measure text
        try:
            bbox = draw.textbbox((0, 0), text, font=font)
            tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        except Exception:
            tw, th = fs * len(text) // 2, fs
        x = W - tw - int(W * 0.03)
        y = H - th - int(H * 0.03)
        # Shadow + text (semi-transparent white)
        draw.text((x + 2, y + 2), text, font=font, fill=(0, 0, 0, 160))
        draw.text((x, y), text, font=font, fill=(255, 255, 255, 220))
        out = Image.alpha_composite(im, overlay).convert("RGB")
        buf = BytesIO()
        out.save(buf, format="JPEG", quality=88, optimize=True)
        return buf.getvalue()
    except Exception as exc:
        logger.warning("watermark_photo_bytes failed: %s — using raw", exc)
        return input_bytes


# ─── HTML safety helpers ─────────────────────────────────────────────────────
def _strip_html(text: str) -> str:
    """Remove HTML tags + entities, keep plain text only.
    Use for preview text where formatting doesn't matter (summary, log)."""
    import re as _re_h
    import html as _html_h
    if not text:
        return ""
    # Decode entities first (&amp; → &), then strip tags
    no_ent = _html_h.unescape(str(text))
    no_tags = _re_h.sub(r"<[^>]*>", "", no_ent)
    return no_tags.strip()


def _safe_html(text, max_len: int = None) -> str:
    """Escape for HTML parse_mode + optional truncate.
    SAFE truncation: cuts at char boundary, then escapes (no broken entities)."""
    import html as _html_h
    s = str(text or "")
    if max_len is not None and len(s) > max_len:
        s = s[:max_len]
    return _html_h.escape(s, quote=False)


# ─── Watermark via ffmpeg ────────────────────────────────────────────────────
def watermark_video(input_path: str, output_path: str, logo_path: str) -> bool:
    """Overlay logo bottom-right at 15% video width.

    Returns True on success.
    """
    if not Path(logo_path).exists():
        logger.warning("Logo file not found: %s — copying raw", logo_path)
        # No logo → just copy
        import shutil
        shutil.copy(input_path, output_path)
        return True

    # ffmpeg filter: scale logo to 15% of video width, overlay bottom-right with 10px margin
    cmd = [
        "ffmpeg", "-y",
        "-i", input_path,
        "-i", logo_path,
        "-filter_complex",
        "[1:v]scale=iw*main_w/iw*0.15:-1[lg];[0:v][lg]overlay=W-w-20:H-h-20",
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-c:a", "copy",
        "-movflags", "+faststart",
        output_path,
    ]
    # Simpler filter — direct scale to fraction of width
    cmd_simple = [
        "ffmpeg", "-y",
        "-i", input_path,
        "-i", logo_path,
        "-filter_complex",
        "[1:v]scale=200:-1[lg];[0:v][lg]overlay=W-w-20:H-h-20",
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-c:a", "copy",
        "-movflags", "+faststart",
        output_path,
    ]
    try:
        r = subprocess.run(cmd_simple, capture_output=True, text=True, timeout=120)
        if r.returncode != 0:
            logger.error("ffmpeg failed: %s", r.stderr[-500:])
            return False
        return True
    except subprocess.TimeoutExpired:
        logger.error("ffmpeg timeout")
        return False


# ─── DB helpers ──────────────────────────────────────────────────────────────
async def get_free_groups() -> list[dict]:
    """Return target groups for clip distribution.

    FIX 2026-06-28: อ่านจาก bot_group_targets (เลือกผ่าน Dashboard)
    แทน hardcoded SAMPLE only — ทำให้ admin ติกเลือกกลุ่มเพิ่มได้

    Fallback strategy:
    1. ถ้ามี row ใน bot_group_targets WHERE bot_key='clip_poster_bot' AND is_active
       → ใช้กลุ่มเหล่านั้น (รวม cross-join group_registry เพื่อกรอง active เพิ่ม)
    2. ถ้าไม่มี (ระบบเพิ่งติด หรือ admin ลืมตั้ง) → fallback ใช้ SAMPLE
       เพื่อกัน "ส่งไปไม่มีที่" ตอนเริ่มต้น
    """
    import asyncpg
    db_url = os.environ.get("DATABASE_URL", "")
    if not db_url:
        logger.error("DATABASE_URL not set")
        return []
    db_url = db_url.replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(db_url)
    try:
        # PRIMARY: read from bot_group_targets (Dashboard checkbox)
        rows = await conn.fetch(
            "SELECT bgt.chat_id, gr.slug::text AS slug, gr.title "
            "FROM bot_group_targets bgt "
            "JOIN group_registry gr ON gr.chat_id = bgt.chat_id "
            "WHERE bgt.bot_key = 'clip_poster_bot' "
            "  AND bgt.is_active = TRUE "
            "  AND gr.is_active = TRUE "
            "ORDER BY gr.id"
        )
        if rows:
            logger.info("get_free_groups: %d targets from bot_group_targets", len(rows))
            return [dict(r) for r in rows]

        # FALLBACK: ไม่มี row → SAMPLE เดิม (กัน distribute fail ตอน initial)
        logger.warning("get_free_groups: no bot_group_targets — fallback to SAMPLE")
        rows = await conn.fetch(
            "SELECT chat_id, slug::text AS slug, title FROM group_registry "
            "WHERE is_active = TRUE AND slug::text = 'SAMPLE' "
            "ORDER BY id"
        )
        return [dict(r) for r in rows]
    finally:
        await conn.close()


async def log_job(
    admin_id: int, video_file_id: str, tier_hint: Optional[str],
    caption: str, sent: list[str], failed: list[dict],
):
    """Insert job audit row (best-effort)."""
    import asyncpg, json
    db_url = os.environ.get("DATABASE_URL", "")
    if not db_url:
        return
    db_url = db_url.replace("postgresql+asyncpg://", "postgresql://")
    try:
        conn = await asyncpg.connect(db_url)
        try:
            await conn.execute("""
                INSERT INTO clip_poster_jobs
                (admin_id, video_file_id, tier_hint, caption, sent_groups, failed_groups)
                VALUES ($1, $2, $3, $4, $5::jsonb, $6::jsonb)
            """, admin_id, video_file_id, tier_hint, caption,
                json.dumps(sent), json.dumps(failed))
        finally:
            await conn.close()
    except Exception as exc:
        logger.warning("log_job failed (table may not exist yet): %s", exc)


# ─── Handlers ────────────────────────────────────────────────────────────────
# DB-backed admin whitelist with 60s cache.
# Allows owner/super_admin/admin with can_post_clips=TRUE in dashboard_admins.
# Falls back to ENV CLIP_POSTER_ADMIN_IDS if DB unreachable.
_admin_cache = {"ids": None, "expires": 0}


async def get_allowed_admins() -> set[int]:
    """Return tg_ids allowed to use this bot.

    SOURCE OF TRUTH: admin_bot_permissions table (matrix admin × bot).
    Boss manages via Dashboard → Team page → 🤖 บอท modal (checkbox list).
    Cached 60s.

    Bot identifier: 'clip_poster_bot' (must match bot_registry.bot_key).
    Fail-closed: DB unreachable → empty set → reject all (safe).
    """
    import time as _t
    now = _t.time()
    if _admin_cache["ids"] is not None and _admin_cache["expires"] > now:
        return _admin_cache["ids"]
    try:
        import asyncpg
        db_url = os.environ.get("DATABASE_URL", "").replace("postgresql+asyncpg://", "postgresql://")
        if not db_url:
            logger.error("DATABASE_URL not set — fail-closed (no permissions)")
            return set()
        conn = await asyncpg.connect(db_url)
        try:
            rows = await conn.fetch(
                "SELECT da.telegram_id FROM admin_bot_permissions abp "
                "JOIN dashboard_admins da ON da.id = abp.admin_id "
                "WHERE abp.bot_key = $1 AND da.is_active = TRUE",
                "clip_poster_bot",
            )
            allowed = {int(r["telegram_id"]) for r in rows}
            _admin_cache["ids"] = allowed
            _admin_cache["expires"] = now + 60
            return allowed
        finally:
            await conn.close()
    except Exception as exc:
        logger.warning("get_allowed_admins failed: %s — fail-closed (no permissions)", exc)
        return set()


async def is_admin_async(tg_id: int) -> bool:
    allowed = await get_allowed_admins()
    return tg_id in allowed


def is_admin(tg_id: int) -> bool:
    """Deprecated sync helper — always returns False.

    DO NOT USE — call is_admin_async() instead (DB-driven, single source of truth).
    Kept only to preserve callsite signatures during refactor.
    """
    return False


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or not await is_admin_async(user.id):
        await update.message.reply_text("🚫 บอตนี้สำหรับแอดมินเท่านั้น")
        return
    await update.message.reply_text(
        "🎬 <b>Clip Poster Bot</b>\n\n"
        "ส่งวิดีโอมาได้เลย → บอตจะแปะ logo + ส่งทุกกลุ่มฟรีให้\n\n"
        "💡 ใส่ caption ในวิดีโอเป็นเลขแพ็กเกจที่ตัวอย่างนี้เป็นของ\n"
        "   เช่น <code>300</code> หรือ <code>2499</code> หรือ <code>VIP</code>\n"
        "   ถ้าไม่ใส่ → caption จะใช้ทั่วๆ ไป\n\n"
        f"✅ Logo: <code>{LOGO_PATH}</code>",
        parse_mode="HTML",
    )


async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or not await is_admin_async(user.id):
        return
    groups = await get_free_groups()
    logo_exists = Path(LOGO_PATH).exists()
    msg = (
        "📊 <b>สถานะ Clip Poster</b>\n\n"
        f"• Logo file: {'✅ พร้อม' if logo_exists else '⚠️ ยังไม่อัปโหลด'}\n"
        f"• Logo path: <code>{LOGO_PATH}</code>\n"
        f"• กลุ่มตัวอย่าง active: <b>{len(groups)}</b> กลุ่ม\n"
        f"• Admin: <b>{len(ADMIN_IDS)}</b> คน\n\n"
        + "\n".join([f"  • {g['slug']} — {g['title']}" for g in groups[:20]])
    )
    await update.message.reply_text(msg, parse_mode="HTML")


# Bot API getFile limit (Telegram). > limit → ส่ง file_id ตรง (relay, no WM)
BOT_API_DOWNLOAD_LIMIT = 20 * 1024 * 1024  # 20 MB
MEDIA_GROUP_DEBOUNCE_SEC = 2.0  # รอ Telegram ส่ง album items ครบ


def _classify(msg) -> Optional[dict]:
    """Extract media kind + file_id + size from a Telegram message.
    Returns dict or None if not media.
    """
    if msg.photo:
        # photo is a list of sizes — pick largest (last)
        ph = msg.photo[-1]
        return {"kind": "photo", "file_id": ph.file_id, "size": ph.file_size or 0}
    if msg.video:
        return {"kind": "video", "file_id": msg.video.file_id, "size": msg.video.file_size or 0}
    if msg.animation:
        return {"kind": "animation", "file_id": msg.animation.file_id, "size": msg.animation.file_size or 0}
    if msg.document and msg.document.mime_type and msg.document.mime_type.startswith("video/"):
        return {"kind": "video", "file_id": msg.document.file_id, "size": msg.document.file_size or 0}
    return None


async def _show_tier_picker(ctx, chat_id: int, items: list[dict]):
    """Reply to user with tier picker — called once per upload batch."""
    counts = {}
    for it in items:
        counts[it["kind"]] = counts.get(it["kind"], 0) + 1
    parts = []
    if counts.get("photo"): parts.append(f"🖼 {counts['photo']} รูป")
    if counts.get("video"): parts.append(f"🎬 {counts['video']} วิดีโอ")
    if counts.get("animation"): parts.append(f"✨ {counts['animation']} GIF")
    summary = " + ".join(parts) or "ไฟล์"
    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("💎 ฿300", callback_data="tier:300"),
            InlineKeyboardButton("💎 ฿500", callback_data="tier:500"),
            InlineKeyboardButton("💎 ฿2499", callback_data="tier:2499"),
        ],
        [InlineKeyboardButton("🌟 ทั่วไป (ไม่ระบุ tier)", callback_data="tier:generic")],
        [InlineKeyboardButton("❌ ยกเลิก", callback_data="tier:cancel")],
    ])
    return await ctx.bot.send_message(
        chat_id=chat_id,
        text=f"📥 <b>รับ {summary}</b>\n\nเลือก tier ที่ชุดนี้เป็นของ:",
        parse_mode="HTML",
        reply_markup=kb,
    )


async def _flush_pending(ctx, user_id: int):
    """หลัง debounce: ถ้าไม่มี media ใหม่มาแล้ว → reply tier picker."""
    pending = pending_uploads.get(user_id)
    if not pending:
        return
    # ถ้ามีการรับเข้ามาใหม่หลัง schedule → updated_at จะใหม่ → skip flush นี้
    if _time.time() - pending["updated_at"] < MEDIA_GROUP_DEBOUNCE_SEC - 0.1:
        return
    if pending.get("picker_sent"):
        return
    try:
        await _show_tier_picker(ctx, user_id, pending["items"])
        pending["picker_sent"] = True
    except Exception as exc:
        logger.warning("show_tier_picker failed: %s", exc)


async def on_media(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Receive photo/video/animation (single or media group) → buffer + tier picker."""
    msg = update.message
    if not msg or not msg.from_user:
        return
    user = msg.from_user
    if not await is_admin_async(user.id):
        await msg.reply_text("🚫 แอดมินเท่านั้นค่ะ")
        return

    info = _classify(msg)
    if not info:
        await msg.reply_text("⚠️ ส่งรูปหรือวิดีโอเท่านั้นค่ะ")
        return

    mgid = msg.media_group_id  # None for single message
    now = _time.time()
    existing = pending_uploads.get(user.id)

    # ถ้ามี pending เดิมและเป็น media_group เดียวกัน → append
    if existing and mgid and existing.get("media_group_id") == mgid:
        existing["items"].append(info)
        existing["updated_at"] = now
    else:
        # ใหม่ — ลบของเดิม (ลูกค้าเปลี่ยนใจส่งใหม่ก่อนกด tier)
        pending_uploads[user.id] = {
            "items": [info],
            "media_group_id": mgid,
            "updated_at": now,
            "first_msg_id": msg.message_id,
            "picker_sent": False,
        }

    # Debounce: schedule flush หลัง 2 วินาที
    # ถ้า media_group_id = None (ไฟล์เดี่ยว) → flush ทันที (no debounce)
    if mgid is None:
        await _show_tier_picker(ctx, user.id, pending_uploads[user.id]["items"])
        pending_uploads[user.id]["picker_sent"] = True
    else:
        async def _delayed_flush():
            await asyncio.sleep(MEDIA_GROUP_DEBOUNCE_SEC)
            await _flush_pending(ctx, user.id)
        asyncio.create_task(_delayed_flush())


async def _process_item_for_send(ctx, item: dict, td: str) -> Optional[dict]:
    """Process 1 item → return dict for send (file_id ตรง หรือ path watermark).
    Returns: {"kind": "photo|video|animation", "send_via": "file_id|bytes|path", "data": ...}
    """
    kind = item["kind"]
    fid = item["file_id"]
    size = item.get("size", 0) or 0

    if kind == "photo":
        # Photo: download via Bot API (Telegram photos always small) → PIL watermark
        try:
            tg_file = await ctx.bot.get_file(fid)
            in_path = os.path.join(td, f"in_{fid[:10]}.jpg")
            await tg_file.download_to_drive(in_path)
            with open(in_path, "rb") as f:
                raw = f.read()
            wm = await asyncio.to_thread(watermark_photo_bytes, raw)
            return {"kind": "photo", "send_via": "bytes", "data": wm}
        except Exception as exc:
            logger.warning("photo download/wm failed (%s) — relay file_id", exc)
            return {"kind": "photo", "send_via": "file_id", "data": fid}

    if kind in ("video", "animation"):
        # Video/anim: ถ้าใหญ่กว่า Bot API limit → relay file_id (no WM)
        if size and size > BOT_API_DOWNLOAD_LIMIT:
            logger.info("video %s too large (%s bytes) — relay file_id, no WM", fid[:10], size)
            return {"kind": kind, "send_via": "file_id", "data": fid}
        try:
            tg_file = await ctx.bot.get_file(fid)
            in_path = os.path.join(td, f"in_{fid[:10]}.mp4")
            out_path = os.path.join(td, f"out_{fid[:10]}.mp4")
            await tg_file.download_to_drive(in_path)
            ok = await asyncio.to_thread(watermark_video, in_path, out_path, LOGO_PATH)
            if not ok:
                out_path = in_path
            return {"kind": kind, "send_via": "path", "data": out_path}
        except Exception as exc:
            # download failed (likely too big despite missing size) → relay file_id
            logger.warning("video download failed (%s) — relay file_id", exc)
            return {"kind": kind, "send_via": "file_id", "data": fid}

    return None


async def _send_to_group(ctx, chat_id: int, prepared: list[dict], caption: str, kb) -> None:
    """Send prepared items to one group. Single = solo with caption+kb. Multi = album + follow-up text with kb."""
    if len(prepared) == 1:
        p = prepared[0]
        kw = {"chat_id": chat_id, "caption": caption, "parse_mode": "HTML", "reply_markup": kb}
        if p["kind"] == "photo":
            if p["send_via"] == "bytes":
                await ctx.bot.send_photo(photo=BytesIO(p["data"]), **kw)
            else:  # file_id
                await ctx.bot.send_photo(photo=p["data"], **kw)
        elif p["kind"] == "video":
            kw["supports_streaming"] = True
            if p["send_via"] == "path":
                with open(p["data"], "rb") as f:
                    await ctx.bot.send_video(video=f, **kw)
            else:
                await ctx.bot.send_video(video=p["data"], **kw)
        elif p["kind"] == "animation":
            if p["send_via"] == "path":
                with open(p["data"], "rb") as f:
                    await ctx.bot.send_animation(animation=f, **kw)
            else:
                await ctx.bot.send_animation(animation=p["data"], **kw)
        return

    # Multi-item: send_media_group (no buttons supported)
    # → caption ใส่ในไฟล์แรก, แล้วส่ง follow-up message พร้อมปุ่ม VIP
    media = []
    open_files = []
    try:
        for idx, p in enumerate(prepared[:10]):  # max 10
            cap = caption if idx == 0 else None
            if p["kind"] == "photo":
                if p["send_via"] == "bytes":
                    media.append(InputMediaPhoto(media=BytesIO(p["data"]), caption=cap, parse_mode="HTML"))
                else:
                    media.append(InputMediaPhoto(media=p["data"], caption=cap, parse_mode="HTML"))
            elif p["kind"] == "video":
                if p["send_via"] == "path":
                    fh = open(p["data"], "rb")
                    open_files.append(fh)
                    media.append(InputMediaVideo(media=fh, caption=cap, parse_mode="HTML", supports_streaming=True))
                else:
                    media.append(InputMediaVideo(media=p["data"], caption=cap, parse_mode="HTML", supports_streaming=True))
            elif p["kind"] == "animation":
                # animation ไม่อยู่ใน media_group spec ของ Telegram → cast เป็น video
                if p["send_via"] == "path":
                    fh = open(p["data"], "rb")
                    open_files.append(fh)
                    media.append(InputMediaVideo(media=fh, caption=cap, parse_mode="HTML"))
                else:
                    media.append(InputMediaVideo(media=p["data"], caption=cap, parse_mode="HTML"))
        sent_msgs = await ctx.bot.send_media_group(chat_id=chat_id, media=media)
        # Follow-up button message (reply to first of album)
        reply_to = sent_msgs[0].message_id if sent_msgs else None
        await ctx.bot.send_message(
            chat_id=chat_id,
            text="👇 <b>สมัคร VIP ดูเต็มเลย</b>",
            parse_mode="HTML",
            reply_markup=kb,
            reply_to_message_id=reply_to,
        )
    finally:
        for fh in open_files:
            try: fh.close()
            except Exception: pass


async def on_tier_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Handle tier button click → process all pending items + broadcast."""
    q = update.callback_query
    if not q or not q.from_user:
        return
    user = q.from_user
    if not await is_admin_async(user.id):
        await q.answer("🚫 แอดมินเท่านั้น", show_alert=True)
        return

    data = q.data or ""
    if not data.startswith("tier:"):
        return
    choice = data.split(":", 1)[1]

    pending = pending_uploads.pop(user.id, None)
    if not pending or not pending.get("items"):
        await q.answer("⚠️ ไม่มีไฟล์ค้าง — ส่งใหม่ก่อนค่ะ", show_alert=True)
        try:
            await q.edit_message_text("⚠️ Session หมดอายุ — ส่งใหม่อีกครั้ง")
        except Exception:
            pass
        return

    if choice == "cancel":
        await q.answer("ยกเลิกแล้ว")
        try:
            await q.edit_message_text("❌ ยกเลิก — ไม่ได้ส่ง")
        except Exception:
            pass
        return

    items = pending["items"]
    await q.answer(f"กำลังส่ง {len(items)} ไฟล์...")

    tier_hint = None if choice == "generic" else choice
    caption_text = await pick_caption(tier_hint)
    status_msg = q.message

    try:
        _tier_safe_st = _safe_html(tier_hint or "(ทั่วไป)")
        await status_msg.edit_text(
            f"⏳ <b>กำลังประมวลผล</b> ({len(items)} ไฟล์)\n"
            f"• Tier: <code>{_tier_safe_st}</code>",
            parse_mode="HTML",
        )
    except Exception:
        pass

    groups = await get_free_groups()
    if not groups:
        await status_msg.edit_text("❌ ไม่พบกลุ่มปลายทาง — เลือกใน Dashboard ก่อน")
        return

    credit_url = await get_credit_group_url()
    clip_kb = build_clip_keyboard(tier_hint, credit_url)

    with tempfile.TemporaryDirectory() as td:
        # Pre-process every item once (watermark / download)
        prepared = []
        for it in items:
            p = await _process_item_for_send(ctx, it, td)
            if p:
                prepared.append(p)
        if not prepared:
            await status_msg.edit_text("❌ ประมวลผลไฟล์ไม่สำเร็จ")
            return

        try:
            _tier_safe_sd = _safe_html(tier_hint or "(ทั่วไป)")
            await status_msg.edit_text(
                f"📤 <b>กำลังส่งไป {len(groups)} กลุ่ม</b> ({len(prepared)} ไฟล์/กลุ่ม)\n"
                f"• Tier: <code>{_tier_safe_sd}</code>",
                parse_mode="HTML",
            )
        except Exception:
            pass

        sent: list[str] = []
        failed: list[dict] = []
        for g in groups:
            try:
                await _send_to_group(ctx, g["chat_id"], prepared, caption_text, clip_kb)
                sent.append(g["slug"])
                await asyncio.sleep(1.5)
            except Exception as exc:
                logger.warning("send to %s failed: %s", g["slug"], exc)
                failed.append({"slug": g["slug"], "error": str(exc)[:100]})

    # FIX 2026-06-29 (#474): strip HTML from caption preview + escape all user vars
    # ก่อนหน้านี้ caption_text[:120] ตัดกลาง <b> tag → "unclosed start tag" error
    _tier_safe = _safe_html(tier_hint or "(ทั่วไป)")
    _caption_preview = _strip_html(caption_text)
    _caption_safe = _safe_html(_caption_preview, max_len=120)
    summary = (
        f"{'✅' if not failed else '⚠️'} <b>เสร็จแล้ว</b>\n\n"
        f"• ไฟล์: <b>{len(prepared)}</b>\n"
        f"• ส่งสำเร็จ: <b>{len(sent)}/{len(groups)}</b>\n"
        f"• Tier: <code>{_tier_safe}</code>\n"
        f"• Caption: <i>{_caption_safe}...</i>\n"
    )
    if failed:
        summary += "\n❌ <b>กลุ่มที่ส่งไม่ผ่าน:</b>\n"
        for f in failed[:10]:
            _slug_safe = _safe_html(f.get('slug', ''))
            _err_safe = _safe_html(f.get('error', ''), max_len=60)
            summary += f"  • {_slug_safe}: {_err_safe}\n"
    # FIX 2026-06-29 (#474): fallback to plain text if HTML still breaks
    try:
        await status_msg.edit_text(summary, parse_mode="HTML")
    except Exception as _e_edit:
        try:
            await ctx.bot.send_message(user.id, summary, parse_mode="HTML")
        except Exception as _e_html:
            # last resort: strip ALL HTML and send plain
            _plain = _strip_html(summary)
            try:
                await ctx.bot.send_message(user.id, _plain)
            except Exception as _e_plain:
                logger.warning("summary message failed (edit + send + plain): %s", _e_plain)

    await log_job(
        admin_id=user.id,
        video_file_id=items[0]["file_id"] if items else "",
        tier_hint=tier_hint,
        caption=caption_text,
        sent=sent,
        failed=failed,
    )


async def on_unknown(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.from_user:
        return
    if not await is_admin_async(update.message.from_user.id):
        return
    await update.message.reply_text(
        "💡 ส่งวิดีโอมาได้เลย\n"
        "• ใส่ caption เป็นเลขแพ็กเกจ (300/500/1299/2499) ถ้าอยากเจาะจง"
    )


def main():
    if not BOT_TOKEN:
        raise SystemExit("CLIP_POSTER_BOT_TOKEN not set in env")

    logger.info(
        "Clip Poster Bot starting — permissions=DB (dashboard_admins), logo=%s",
        LOGO_PATH,
    )

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CallbackQueryHandler(on_tier_callback, pattern=r"^tier:"))
    app.add_handler(MessageHandler(
        filters.ChatType.PRIVATE & (
            filters.PHOTO | filters.VIDEO | filters.ANIMATION |
            (filters.Document.ALL & filters.Document.VIDEO)
        ),
        on_media,
    ))
    app.add_handler(MessageHandler(
        filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND,
        on_unknown,
    ))
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
