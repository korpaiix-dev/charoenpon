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

from telegram import Update
from telegram.ext import (
    Application, CommandHandler, ContextTypes,
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

# Caption templates — {tier} substituted when hint provided
CAPTION_TEMPLATES = [
    "🔥 ตัวอย่างหลุด — ดูเต็มในห้อง VIP เจริญพร\n\n💎 เริ่ม ฿{tier} • @{bot}",
    "💕 แค่ตัวอย่าง — ของเต็มจัดเต็มกว่านี้\n\n👉 สมัคร VIP ฿{tier}: @{bot}",
    "🍃 น้ำจิ้มก่อน — เคลียร์ๆ ในห้องจริง\n\n📦 VIP ฿{tier}: @{bot}",
    "✨ ตัวอย่างเด็ดๆ จากห้องเจริญพร\n\n🎬 ดูเต็มเริ่ม ฿{tier} → @{bot}",
    "🎯 ดูได้แค่ตัวอย่าง — สมัครเข้า VIP ดูเต็ม\n\n💰 ฿{tier}/30 วัน • @{bot}",
    "🚀 อยากดูเต็มไหม? เข้า VIP เจริญพรเลย\n\n💸 ฿{tier} • @{bot}",
    "🌟 ตัวอย่างคลิป VIP เจริญพร\n\n💎 สมัคร ฿{tier} ทักบอท: @{bot}",
    "💥 ของจริงเต็มในห้อง — นี่แค่ตัวอย่าง\n\n👉 ฿{tier}: @{bot}",
    "🎁 ดูฟรีแค่นี้ — เต็มอยู่ห้อง VIP\n\n📲 สมัคร ฿{tier} @{bot}",
    "⭐ ตัวอย่างเด็ดๆ — สมัครดูเต็ม\n\n💎 เริ่ม ฿{tier} • @{bot}",
]
# When no tier hint → use general fallback list
CAPTION_GENERIC = [
    "🔥 ตัวอย่างจากห้อง VIP เจริญพร\n\n💎 ดูเต็มเริ่ม ฿300 • @{bot}",
    "💕 แค่น้ำจิ้ม — ของจริงในห้อง VIP\n\n👉 เริ่ม ฿300 ทักบอท: @{bot}",
    "🎬 ตัวอย่างคลิป — สมัครดูเต็ม\n\n📦 มี ฿300 / ฿1299 / ฿2499 • @{bot}",
    "✨ ดูได้แค่นี้ฟรี — เต็มในห้อง VIP\n\n💎 @{bot}",
    "🌟 อยากดูเต็ม? ห้อง VIP เจริญพร\n\n💸 เริ่ม ฿300 → @{bot}",
]

# ─── Tier detection from admin's caption ────────────────────────────────────
TIER_PATTERNS = {
    "100": r"\b100\b|tier.?100|TIER_100|SHAKER|ห้องชัก",
    "300": r"\b300\b|tier.?300|TIER_300|VIP\s*300|G300",
    "500": r"\b500\b|tier.?500|TIER_500|G500",
    "1299": r"\b1[,.]?299\b|tier.?1299|TIER_1299|GOD|VGOD|SSS|INTER|RANDOM|SERIES",
    "2499": r"\b2[,.]?499\b|tier.?2499|TIER_2499|SUMMER|STORAGE",
}


def detect_tier(text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    text_lower = text.lower()
    for tier, pat in TIER_PATTERNS.items():
        if re.search(pat, text_lower, re.IGNORECASE):
            return tier
    return None


def pick_caption(tier: Optional[str]) -> str:
    if tier:
        tmpl = random.choice(CAPTION_TEMPLATES)
        return tmpl.format(tier=tier, bot=SALES_BOT_USERNAME)
    tmpl = random.choice(CAPTION_GENERIC)
    return tmpl.format(bot=SALES_BOT_USERNAME)


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
    """Return active FREE-tier groups (the 'sample' rooms)."""
    import asyncpg
    db_url = os.environ.get("DATABASE_URL", "")
    if not db_url:
        logger.error("DATABASE_URL not set")
        return []
    # Strip sqlalchemy prefix if present
    db_url = db_url.replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(db_url)
    try:
        rows = await conn.fetch(
            "SELECT chat_id, slug::text AS slug, title FROM group_registry "
            "WHERE is_active = TRUE AND min_tier::text = 'FREE' "
            "ORDER BY slug"
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
def is_admin(tg_id: int) -> bool:
    return tg_id in ADMIN_IDS


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or not is_admin(user.id):
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
    if not user or not is_admin(user.id):
        return
    groups = await get_free_groups()
    logo_exists = Path(LOGO_PATH).exists()
    msg = (
        "📊 <b>สถานะ Clip Poster</b>\n\n"
        f"• Logo file: {'✅ พร้อม' if logo_exists else '⚠️ ยังไม่อัปโหลด'}\n"
        f"• Logo path: <code>{LOGO_PATH}</code>\n"
        f"• กลุ่ม FREE active: <b>{len(groups)}</b> กลุ่ม\n"
        f"• Admin: <b>{len(ADMIN_IDS)}</b> คน\n\n"
        + "\n".join([f"  • {g['slug']} — {g['title']}" for g in groups[:20]])
    )
    await update.message.reply_text(msg, parse_mode="HTML")


async def on_video(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or not msg.from_user:
        return
    user = msg.from_user
    if not is_admin(user.id):
        await msg.reply_text("🚫 แอดมินเท่านั้นค่ะ")
        return

    # Extract video or document (mime video/*)
    video = msg.video or msg.animation
    doc = msg.document
    if doc and doc.mime_type and not doc.mime_type.startswith("video/"):
        await msg.reply_text("⚠️ ส่งไฟล์วิดีโอเท่านั้นค่ะ")
        return

    file_obj = video or doc
    if not file_obj:
        return

    # Tier hint from caption
    tier_hint = detect_tier(msg.caption or "")
    caption_text = pick_caption(tier_hint)

    # Status reply
    status_msg = await msg.reply_text(
        f"⏳ รับวิดีโอแล้ว\n"
        f"• Tier hint: <code>{tier_hint or '(ทั่วไป)'}</code>\n"
        f"• กำลังแปะ logo...",
        parse_mode="HTML",
    )

    # Get groups
    groups = await get_free_groups()
    if not groups:
        await status_msg.edit_text("❌ ไม่พบกลุ่ม FREE active ใน DB")
        return

    # Download
    try:
        tg_file = await file_obj.get_file()
    except Exception as exc:
        await status_msg.edit_text(f"❌ download fail: {exc}")
        return

    with tempfile.TemporaryDirectory() as td:
        in_path = os.path.join(td, "in.mp4")
        out_path = os.path.join(td, "out.mp4")
        try:
            await tg_file.download_to_drive(in_path)
        except Exception as exc:
            await status_msg.edit_text(f"❌ download fail: {exc}")
            return

        await status_msg.edit_text(
            f"⏳ ดาวน์โหลดสำเร็จ\n"
            f"• ขนาด: {Path(in_path).stat().st_size // 1024} KB\n"
            f"• กำลังแปะ logo + encode...",
            parse_mode="HTML",
        )

        # Watermark
        ok = await asyncio.to_thread(watermark_video, in_path, out_path, LOGO_PATH)
        if not ok:
            await status_msg.edit_text("❌ ffmpeg ล้มเหลว — ส่งไฟล์ดิบแทน")
            out_path = in_path  # fall through with original

        # Send to all groups
        await status_msg.edit_text(
            f"📤 กำลังส่งไป {len(groups)} กลุ่ม...\n"
            f"💬 Caption: <i>{caption_text[:80]}...</i>",
            parse_mode="HTML",
        )

        sent: list[str] = []
        failed: list[dict] = []
        for g in groups:
            try:
                with open(out_path, "rb") as f:
                    await ctx.bot.send_video(
                        chat_id=g["chat_id"],
                        video=f,
                        caption=caption_text,
                        supports_streaming=True,
                    )
                sent.append(g["slug"])
                await asyncio.sleep(1.5)  # polite delay
            except Exception as exc:
                logger.warning("send to %s failed: %s", g["slug"], exc)
                failed.append({"slug": g["slug"], "error": str(exc)[:100]})

    # Final report
    summary = (
        f"{'✅' if not failed else '⚠️'} <b>เสร็จแล้ว</b>\n\n"
        f"• ส่งสำเร็จ: <b>{len(sent)}/{len(groups)}</b>\n"
        f"• Tier: <code>{tier_hint or '(ทั่วไป)'}</code>\n"
        f"• Caption: <i>{caption_text[:100]}</i>\n"
    )
    if failed:
        summary += "\n❌ <b>กลุ่มที่ส่งไม่ผ่าน:</b>\n"
        for f in failed[:10]:
            summary += f"  • {f['slug']}: {f['error'][:60]}\n"
    await status_msg.edit_text(summary, parse_mode="HTML")

    # Audit log
    await log_job(
        admin_id=user.id,
        video_file_id=file_obj.file_id,
        tier_hint=tier_hint,
        caption=caption_text,
        sent=sent,
        failed=failed,
    )


async def on_unknown(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.from_user:
        return
    if not is_admin(update.message.from_user.id):
        return
    await update.message.reply_text(
        "💡 ส่งวิดีโอมาได้เลย\n"
        "• ใส่ caption เป็นเลขแพ็กเกจ (300/500/1299/2499) ถ้าอยากเจาะจง"
    )


def main():
    if not BOT_TOKEN:
        raise SystemExit("CLIP_POSTER_BOT_TOKEN not set in env")
    if not ADMIN_IDS:
        raise SystemExit("CLIP_POSTER_ADMIN_IDS not set in env")

    logger.info("Clip Poster Bot starting — admins=%s, logo=%s",
                ADMIN_IDS, LOGO_PATH)

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(MessageHandler(
        filters.ChatType.PRIVATE & (
            filters.VIDEO | filters.ANIMATION |
            (filters.Document.ALL & filters.Document.VIDEO)
        ),
        on_video,
    ))
    app.add_handler(MessageHandler(
        filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND,
        on_unknown,
    ))
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
