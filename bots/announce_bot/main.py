"""Announce Bot (ประกาศ) - Main entry point - บริษัทเจริญพร.

Features:
- Keyword moderation ในกลุ่มหลัก (TG_GROUP_MAIN) → แบน + ลบ + แจ้งเตือน
- /newgroup command → ประกาศย้ายกลุ่ม + บันทึก DB + Discord + Sheets
- แจ้งเตือนกลุ่ม Admin + Discord ทุกครั้งที่แบน
"""

from __future__ import annotations

import logging
import os
from datetime import timedelta, timezone

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from shared.database import close_db, init_db

from bots.announce_bot.moderation import (
    build_ban_alert_text,
    find_matched_keyword,
    get_ban_count,
    increment_ban_count,
    send_discord_alert,
)
from bots.announce_bot.migration import post_new_group_announcement

logger = logging.getLogger(__name__)

TH_TZ = timezone(timedelta(hours=7))

ANNOUNCE_BOT_TOKEN: str = os.environ.get("ANNOUNCE_BOT_TOKEN", "")
TG_GROUP_MAIN_IDS: list[int] = [
    int(os.environ[k])
    for k in ("TG_GROUP_MAIN_1", "TG_GROUP_MAIN_2", "TG_GROUP_MAIN_3")
    if os.environ.get(k, "").lstrip("-").isdigit()
]
TG_GROUP_ADMIN: int = int(os.environ.get("TG_GROUP_ADMIN", "0"))

ADMIN_TELEGRAM_IDS: set[int] = {
    int(x.strip())
    for x in os.environ.get("ADMIN_TELEGRAM_IDS", "").split(",")
    if x.strip().isdigit()
}


# ─── Keyword Moderation Handler ───────────────────────────────────────────────

async def handle_group_message(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """ดักจับข้อความในกลุ่มหลัก — ถ้าพบ keyword ต้องห้ามให้แบน + ลบ."""
    message = update.effective_message
    if not message:
        return

    chat_id = message.chat_id
    if chat_id not in TG_GROUP_MAIN_IDS:
        return

    user = message.from_user
    if not user or user.is_bot:
        return

    # Skip admins
    if user.id in ADMIN_TELEGRAM_IDS:
        return

    text = message.text or message.caption or ""
    if not text:
        return

    matched = find_matched_keyword(text)
    if not matched:
        return

    # === แบนถาวร + ลบข้อความ ===
    ban_success = False
    delete_success = False

    try:
        await context.bot.ban_chat_member(chat_id=chat_id, user_id=user.id)
        ban_success = True
        logger.info("Banned user %s (@%s) — keyword: %s", user.id, user.username, matched)
    except Exception as exc:
        logger.error("Failed to ban user %s: %s", user.id, exc)

    try:
        await message.delete()
        delete_success = True
    except Exception as exc:
        logger.warning("Failed to delete message from %s: %s", user.id, exc)

    if not ban_success:
        return

    ban_count = increment_ban_count()

    alert_text = build_ban_alert_text(
        username=user.username,
        user_id=user.id,
        message_text=text,
        matched_keyword=matched,
        ban_count=ban_count,
    )

    # ส่ง Discord เท่านั้น (ไม่แจ้งกลุ่ม Admin)
    await send_discord_alert(alert_text)


# ─── /newgroup Command Handler ─────────────────────────────────────────────────

async def cmd_newgroup(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """/newgroup https://t.me/xxx — ประกาศย้ายกลุ่ม."""
    message = update.effective_message
    user = update.effective_user
    if not message or not user:
        return

    # เช็คสิทธิ์
    if user.id not in ADMIN_TELEGRAM_IDS:
        await message.reply_text("❌ คุณไม่มีสิทธิ์ใช้คำสั่งนี้ครับ")
        return

    # เช็คว่ามาจากที่ที่อนุญาต: DM หรือกลุ่ม Admin
    chat_id = message.chat_id
    is_dm = message.chat.type == "private"
    is_admin_group = chat_id == TG_GROUP_ADMIN

    if not is_dm and not is_admin_group:
        return  # ไม่ตอบในกลุ่มอื่น

    # Parse link
    args = context.args
    if not args:
        await message.reply_text(
            "📋 วิธีใช้: /newgroup https://t.me/xxx\n\nกรุณาระบุลิ้งกลุ่มใหม่ครับ"
        )
        return

    new_link = args[0].strip()
    if not new_link.startswith("https://t.me/") and not new_link.startswith("http://t.me/"):
        await message.reply_text("❌ ลิ้งไม่ถูกต้องครับ ต้องเป็น https://t.me/... เท่านั้น")
        return

    # Processing
    processing_msg = await message.reply_text("⏳ กำลังโพสต์ประกาศ...")

    result = await post_new_group_announcement(
        bot=context.bot,
        new_link=new_link,
        created_by=user.id,
    )

    if result["success"]:
        success_text = (
            f"✅ โพสต์ประกาศเรียบร้อยแล้วครับ!\n\n"
            f"📌 ลิ้งใหม่: {new_link}\n"
            f"💾 บันทึก DB ID: {result.get('db_id', '-')}"
        )
        if result["errors"]:
            success_text += f"\n\n⚠️ มีบางอย่างผิดพลาด:\n" + "\n".join(f"• {e}" for e in result["errors"])
        await processing_msg.edit_text(success_text)
    else:
        error_text = "❌ โพสต์ไม่สำเร็จครับ\n\n" + "\n".join(f"• {e}" for e in result["errors"])
        await processing_msg.edit_text(error_text)


# ─── App setup ────────────────────────────────────────────────────────────────

async def post_init(application: Application) -> None:
    """Post-init hook — initialize database."""
    await init_db()
    logger.info("Announce Bot (ประกาศ) initialized — database ready")
    logger.info("Monitoring TG_GROUP_MAIN_IDS=%s for keyword violations", TG_GROUP_MAIN_IDS)


async def post_shutdown(application: Application) -> None:
    """Post-shutdown hook."""
    await close_db()
    logger.info("Announce Bot (ประกาศ) shut down")


def create_application() -> Application:
    """Create and configure the Announce Bot application."""
    if not ANNOUNCE_BOT_TOKEN:
        raise ValueError("ANNOUNCE_BOT_TOKEN environment variable is required")
    if not TG_GROUP_MAIN_IDS:
        raise ValueError("TG_GROUP_MAIN_1/2/3 environment variables are required")

    builder = Application.builder().token(ANNOUNCE_BOT_TOKEN)
    app = builder.post_init(post_init).post_shutdown(post_shutdown).build()

    # Keyword moderation — ดักทุกข้อความในกลุ่ม
    app.add_handler(
        MessageHandler(
            filters.ChatType.GROUPS & filters.TEXT,
            handle_group_message,
        )
    )

    # /newgroup command — รับจาก DM และกลุ่ม Admin
    app.add_handler(CommandHandler("newgroup", cmd_newgroup))

    return app


def main() -> None:
    """Run the Announce Bot."""
    logging.basicConfig(
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        level=logging.INFO,
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("telegram").setLevel(logging.WARNING)

    app = create_application()
    logger.info("Starting Announce Bot (ประกาศ)...")
    app.run_polling(
        allowed_updates=[Update.MESSAGE, Update.EDITED_MESSAGE],
        drop_pending_updates=True,
    )


if __name__ == "__main__":
    main()
