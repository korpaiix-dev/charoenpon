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
from datetime import datetime, time as dt_time, timedelta, timezone

import httpx
from PIL import Image, ImageFilter
from sqlalchemy import select, update
from telegram import Bot, Update
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

    prompts = [
        "เขียน caption สั้นๆ 1-2 บรรทัด ภาษาไทย สำหรับโพสต์ teaser คอนเทนต์ 18+ ในกลุ่ม Telegram ให้น่าสนใจ อยากดูต่อ ล่อให้สมัคร VIP ห้ามใส่ลิงก์ ห้ามใส่ราคา แค่ caption เสียวๆ ล่อใจ สร้างสรรค์ ไม่ซ้ำกัน",
        "เขียน caption teaser 18+ ภาษาไทย 1-2 บรรทัด ให้คนอยากดูเต็มๆ ใช้คำพูดเร้าใจแต่ไม่หยาบ สร้างความอยากรู้อยากเห็น",
        "สร้าง caption สั้นๆ ภาษาไทย สำหรับ teaser วิดีโอ 18+ ให้คนกดดูต่อ ใช้อีโมจิ 1-2 ตัว ห้ามใส่ลิงก์",
    ]

    try:
        data = await call_openrouter(
            model=AI_MODEL,
            messages=[{"role": "user", "content": random.choice(prompts)}],
            caller="content_bot/teaser_caption",
            temperature=0.9,
            max_tokens=100,
        )
        return data["choices"][0]["message"]["content"].strip().strip('"')
    except Exception as exc:
        logger.error("AI caption failed: %s", exc)

    # Fallback captions
    fallbacks = [
        "🔥 ของดีมาแล้ว ดูเต็มๆ ได้ใน VIP",
        "😈 แอบดูนิดนึง... อยากดูต่อ ต้อง VIP",
        "🔞 งานเด็ดวันนี้ ดูฟรีได้แค่นี้~",
        "💦 น้องคนนี้ ของดีจริงๆ ดูเต็มใน VIP",
        "🫣 แค่ตัวอย่าง... ของจริงอยู่ใน VIP",
    ]
    return random.choice(fallbacks)


async def blur_image(bot: Bot, file_id: str) -> io.BytesIO:
    """ดาวน์โหลดรูปจาก Telegram แล้วเบลอ."""
    file = await bot.get_file(file_id)
    buf = io.BytesIO()
    await file.download_to_memory(buf)
    buf.seek(0)

    img = Image.open(buf)
    # Blur แบบหวาบหวิว — เห็นรูปร่างแต่ไม่ชัด
    blurred = img.filter(ImageFilter.GaussianBlur(radius=12))

    out = io.BytesIO()
    blurred.save(out, format="JPEG", quality=65)
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
        f"🔥 <b>กลุ่ม VIP เจริญพร</b> 🔥\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"{base_caption}\n\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f'👉 <a href="tg://resolve?domain=NamwarnJarern_bot&start=t_{round_time}_g{group_index}">ดูเต็มๆ คลิกเลย</a>'
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
    """Scheduled job: โพสต์ teaser — ใช้รูปจาก DB ถ้ามี, ไม่งั้นโพสต์แค่ข้อความ."""
    logger.info("scheduled_teaser triggered")

    content = await fetch_latest_vip_content()

    if content:
        logger.info("Found content in queue: id=%d, file_id=%s", content["id"], content["file_id"][:20])
        await post_teaser_with_image(context, content["id"], content["file_id"])
    else:
        logger.info("No content in queue, posting text-only teaser")
        await post_teaser_to_free_groups(context)

    # เช็คและแจ้งเตือนหลังโพสต์ทุกรอบ
    await _check_content_queue_alert()


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

    logger.info("Content Bot (มิน) starting — 5 rounds/day to %d groups", len(FREE_GROUPS))
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
