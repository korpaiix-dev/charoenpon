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

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
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
    "🔥 <b>ตัวอย่างหลุด</b> — ดูเต็มในห้อง VIP เจริญพร\n💎 เริ่ม <b>฿{tier}</b>/30 วัน\n\n👉 <a href=\"{url}\">สมัครที่นี่ — ทักบอทเลย</a>",
    "💕 <b>แค่ตัวอย่าง</b> — ของเต็มจัดเต็มกว่านี้\n💎 สมัคร VIP เริ่ม <b>฿{tier}</b>\n\n👉 <a href=\"{url}\">กดเลย — เข้าห้อง VIP</a>",
    "🍃 <b>น้ำจิ้มก่อน</b> — เคลียร์ๆ ในห้องจริง\n📦 VIP <b>฿{tier}</b> ดูเต็มเลย\n\n👉 <a href=\"{url}\">สมัครที่นี่</a>",
    "✨ <b>ตัวอย่างเด็ดๆ</b> จากห้องเจริญพร\n🎬 ดูเต็มเริ่ม <b>฿{tier}</b>\n\n👉 <a href=\"{url}\">สมัครเลย — กดที่นี่</a>",
    "🎯 <b>ดูได้แค่ตัวอย่าง</b> — สมัครเข้า VIP ดูเต็ม\n💰 <b>฿{tier}</b>/30 วัน คุ้มสุดๆ\n\n👉 <a href=\"{url}\">สมัครที่นี่ — ทักบอท</a>",
    "🚀 <b>อยากดูเต็มไหม?</b> เข้า VIP เจริญพรเลย\n💸 เริ่ม <b>฿{tier}</b>\n\n👉 <a href=\"{url}\">กดเลย — สมัครที่นี่</a>",
    "🌟 <b>ตัวอย่างคลิป VIP เจริญพร</b>\n💎 สมัคร <b>฿{tier}</b> ทักบอท\n\n👉 <a href=\"{url}\">กดเลย — เข้าห้อง</a>",
    "💥 <b>ของจริงเต็มในห้อง</b> — นี่แค่ตัวอย่าง\n💎 VIP เริ่ม <b>฿{tier}</b>\n\n👉 <a href=\"{url}\">สมัครที่นี่ — ทักบอทเลย</a>",
    "🎁 <b>ดูฟรีแค่นี้</b> — เต็มอยู่ห้อง VIP\n📲 สมัคร <b>฿{tier}</b>/30 วัน\n\n👉 <a href=\"{url}\">กดที่นี่ — เข้าเลย</a>",
    "⭐ <b>ตัวอย่างเด็ดๆ</b> — สมัครดูเต็ม\n💎 เริ่ม <b>฿{tier}</b> เท่านั้น\n\n👉 <a href=\"{url}\">สมัครที่นี่ — กดเลย</a>",
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


async def pick_caption(tier: Optional[str]) -> str:
    """Build caption text (CTAs handled by inline buttons below)."""
    url = build_url(tier)
    if tier:
        tmpl = random.choice(CAPTION_TEMPLATES)
        return tmpl.format(tier=tier, url=url)
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
    """Return active SAMPLE groups — the dedicated "ตัวอย่าง" rooms.

    Renamed from 'free groups' historically — now strictly slug='SAMPLE'.
    To add more sample rooms: INSERT into group_registry with slug=SAMPLE.
    """
    import asyncpg
    db_url = os.environ.get("DATABASE_URL", "")
    if not db_url:
        logger.error("DATABASE_URL not set")
        return []
    db_url = db_url.replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(db_url)
    try:
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
        f"• กลุ่มตัวอย่าง active: <b>{len(groups)}</b> กลุ่ม\n"
        f"• Admin: <b>{len(ADMIN_IDS)}</b> คน\n\n"
        + "\n".join([f"  • {g['slug']} — {g['title']}" for g in groups[:20]])
    )
    await update.message.reply_text(msg, parse_mode="HTML")


async def on_video(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Receive video → store + show tier picker keyboard."""
    msg = update.message
    if not msg or not msg.from_user:
        return
    user = msg.from_user
    if not is_admin(user.id):
        await msg.reply_text("🚫 แอดมินเท่านั้นค่ะ")
        return

    video = msg.video or msg.animation
    doc = msg.document
    if doc and doc.mime_type and not doc.mime_type.startswith("video/"):
        await msg.reply_text("⚠️ ส่งไฟล์วิดีโอเท่านั้นค่ะ")
        return

    file_obj = video or doc
    if not file_obj:
        return

    # Store pending (overwrite if admin sends another video before picking)
    pending_uploads[user.id] = {
        "file_id": file_obj.file_id,
        "size": getattr(file_obj, "file_size", None),
    }

    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("💎 ฿300", callback_data="tier:300"),
            InlineKeyboardButton("💎 ฿500", callback_data="tier:500"),
            InlineKeyboardButton("💎 ฿2499", callback_data="tier:2499"),
        ],
        [
            InlineKeyboardButton("🌟 ทั่วไป (ไม่ระบุ tier)", callback_data="tier:generic"),
        ],
        [
            InlineKeyboardButton("❌ ยกเลิก", callback_data="tier:cancel"),
        ],
    ])

    size_kb = (file_obj.file_size // 1024) if getattr(file_obj, "file_size", None) else "?"
    await msg.reply_text(
        f"📹 <b>รับวิดีโอแล้ว</b> ({size_kb} KB)\n\n"
        f"เลือก tier ที่ตัวอย่างนี้เป็นของ:",
        parse_mode="HTML",
        reply_markup=kb,
    )


async def on_tier_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Handle tier button click → start watermark + broadcast."""
    q = update.callback_query
    if not q or not q.from_user:
        return
    user = q.from_user
    if not is_admin(user.id):
        await q.answer("🚫 แอดมินเท่านั้น", show_alert=True)
        return

    data = q.data or ""
    if not data.startswith("tier:"):
        return
    choice = data.split(":", 1)[1]

    pending = pending_uploads.pop(user.id, None)
    if not pending:
        await q.answer("⚠️ ไม่มีวิดีโอค้าง — ส่งใหม่ก่อนค่ะ", show_alert=True)
        try:
            await q.edit_message_text("⚠️ Session หมดอายุ — ส่งวิดีโอใหม่อีกครั้ง")
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

    await q.answer(f"กำลังส่ง tier ฿{choice if choice != 'generic' else 'ทั่วไป'}...")

    tier_hint = None if choice == "generic" else choice
    caption_text = await pick_caption(tier_hint)

    # Update status message
    status_msg = q.message
    try:
        await status_msg.edit_text(
            f"⏳ <b>กำลังประมวลผล</b>\n"
            f"• Tier: <code>{tier_hint or '(ทั่วไป)'}</code>\n"
            f"• กำลังดาวน์โหลดวิดีโอ...",
            parse_mode="HTML",
        )
    except Exception:
        pass

    # Get groups
    groups = await get_free_groups()
    if not groups:
        await status_msg.edit_text("❌ ไม่พบกลุ่มตัวอย่าง (slug=SAMPLE) ใน DB")
        return

    # Download via stored file_id
    try:
        tg_file = await ctx.bot.get_file(pending["file_id"])
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

        try:
            await status_msg.edit_text(
                f"⏳ <b>กำลังประมวลผล</b>\n"
                f"• Tier: <code>{tier_hint or '(ทั่วไป)'}</code>\n"
                f"• กำลังแปะ logo + encode...",
                parse_mode="HTML",
            )
        except Exception:
            pass

        ok = await asyncio.to_thread(watermark_video, in_path, out_path, LOGO_PATH)
        if not ok:
            try:
                await status_msg.edit_text("⚠️ ffmpeg ล้มเหลว — ส่งไฟล์ดิบแทน")
            except Exception:
                pass
            out_path = in_path

        try:
            await status_msg.edit_text(
                f"📤 <b>กำลังส่งไป {len(groups)} กลุ่ม...</b>\n"
                f"• Tier: <code>{tier_hint or '(ทั่วไป)'}</code>",
                parse_mode="HTML",
            )
        except Exception:
            pass

        # Build inline keyboard once (URL buttons — no callback needed)
        credit_url = await get_credit_group_url()
        clip_kb = build_clip_keyboard(tier_hint, credit_url)

        sent: list[str] = []
        failed: list[dict] = []
        for g in groups:
            try:
                with open(out_path, "rb") as f:
                    await ctx.bot.send_video(
                        chat_id=g["chat_id"],
                        video=f,
                        caption=caption_text,
                        parse_mode="HTML",
                        supports_streaming=True,
                        reply_markup=clip_kb,
                    )
                sent.append(g["slug"])
                await asyncio.sleep(1.5)
            except Exception as exc:
                logger.warning("send to %s failed: %s", g["slug"], exc)
                failed.append({"slug": g["slug"], "error": str(exc)[:100]})

    summary = (
        f"{'✅' if not failed else '⚠️'} <b>เสร็จแล้ว</b>\n\n"
        f"• ส่งสำเร็จ: <b>{len(sent)}/{len(groups)}</b>\n"
        f"• Tier: <code>{tier_hint or '(ทั่วไป)'}</code>\n"
        f"• Caption: <i>{caption_text[:120]}...</i>\n"
    )
    if failed:
        summary += "\n❌ <b>กลุ่มที่ส่งไม่ผ่าน:</b>\n"
        for f in failed[:10]:
            summary += f"  • {f['slug']}: {f['error'][:60]}\n"
    try:
        await status_msg.edit_text(summary, parse_mode="HTML")
    except Exception:
        await ctx.bot.send_message(user.id, summary, parse_mode="HTML")

    await log_job(
        admin_id=user.id,
        video_file_id=pending["file_id"],
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
    app.add_handler(CallbackQueryHandler(on_tier_callback, pattern=r"^tier:"))
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
