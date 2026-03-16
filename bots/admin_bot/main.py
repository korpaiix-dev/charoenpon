"""Admin Bot - Telegram bot สำหรับแอดมิน (นัท+บิ๊ก+แมน) บริษัทเจริญพร."""

from __future__ import annotations

import logging
import os
import sys

from telegram import BotCommand, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from shared.database import close_db, init_db

from bots.admin_bot.handlers.approval import (
    approve_payment_callback,
    cmd_pending_payments,
    cmd_pending_broadcasts,
    approve_broadcast_callback,
    reject_payment_callback,
    reject_broadcast_callback,
)
from bots.admin_bot.handlers.reports import (
    cmd_costs,
    cmd_members,
    cmd_revenue,
    cmd_summary,
)

logging.basicConfig(
    format="[%(asctime)s] [ADMIN_BOT] [%(levelname)s] %(message)s",
    level=logging.INFO,
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

ADMIN_BOT_TOKEN: str = os.environ.get("ADMIN_BOT_TOKEN", "")
ADMIN_IDS: list[int] = [
    int(x.strip())
    for x in os.environ.get("ADMIN_TELEGRAM_IDS", "").split(",")
    if x.strip()
]


def is_admin(user_id: int) -> bool:
    """Check if a Telegram user ID is in the admin list."""
    return user_id in ADMIN_IDS


async def admin_only(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Guard: reject non-admin users."""
    if update.effective_user and is_admin(update.effective_user.id):
        return True
    if update.effective_message:
        await update.effective_message.reply_text("⛔ คุณไม่มีสิทธิ์ใช้งาน Admin Bot")
    return False


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start command."""
    if not await admin_only(update, context):
        return
    user = update.effective_user
    text = (
        f"สวัสดีครับ {user.first_name} 🙏\n\n"
        "🏢 <b>Admin Bot - บริษัทเจริญพร</b>\n\n"
        "📋 คำสั่งที่ใช้ได้:\n"
        "/pending - ดู payment ที่รออนุมัติ\n"
        "/broadcasts - ดู broadcast ที่รออนุมัติ\n"
        "/revenue - รายงานรายได้\n"
        "/members - จำนวนสมาชิก active\n"
        "/costs - ค่า API วันนี้\n"
        "/summary - สรุปภาพรวม\n"
        "/help - วิธีใช้งาน"
    )
    await update.effective_message.reply_text(text, parse_mode="HTML")


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /help command."""
    if not await admin_only(update, context):
        return
    text = (
        "📖 <b>คู่มือ Admin Bot</b>\n\n"
        "<b>💳 Payment:</b>\n"
        "/pending — ดูรายการ payment ที่ hold อยู่\n"
        "  → กดปุ่ม ✅อนุมัติ หรือ ❌ไม่อนุมัติ\n\n"
        "<b>📢 Broadcast:</b>\n"
        "/broadcasts — ดู broadcast ที่รออนุมัติ\n"
        "  → กดปุ่ม ✅อนุมัติ หรือ ❌ไม่อนุมัติ\n\n"
        "<b>📊 รายงาน:</b>\n"
        "/revenue — รายได้วันนี้/เดือนนี้\n"
        "/members — จำนวนสมาชิก active\n"
        "/costs — ค่า API วันนี้\n"
        "/summary — สรุปภาพรวมทั้งหมด"
    )
    await update.effective_message.reply_text(text, parse_mode="HTML")


async def unknown_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle unknown commands."""
    if not await admin_only(update, context):
        return
    await update.effective_message.reply_text(
        "❓ ไม่รู้จักคำสั่งนี้ พิมพ์ /help เพื่อดูคำสั่งทั้งหมด"
    )


async def post_init(application: Application) -> None:
    """Run after bot is initialized — set up DB and bot commands."""
    await init_db()
    logger.info("Database initialized")

    commands = [
        BotCommand("start", "เริ่มต้นใช้งาน"),
        BotCommand("pending", "ดู payment ที่รออนุมัติ"),
        BotCommand("broadcasts", "ดู broadcast ที่รออนุมัติ"),
        BotCommand("revenue", "รายงานรายได้"),
        BotCommand("members", "จำนวนสมาชิก active"),
        BotCommand("costs", "ค่า API วันนี้"),
        BotCommand("summary", "สรุปภาพรวม"),
        BotCommand("help", "วิธีใช้งาน"),
    ]
    await application.bot.set_my_commands(commands)
    logger.info("Bot commands registered")


async def post_shutdown(application: Application) -> None:
    """Clean up on shutdown."""
    await close_db()
    logger.info("Database connection closed")


def main() -> None:
    """Entry point for Admin Bot."""
    if not ADMIN_BOT_TOKEN:
        logger.error("ADMIN_BOT_TOKEN environment variable is not set")
        sys.exit(1)

    if not ADMIN_IDS:
        logger.warning("ADMIN_TELEGRAM_IDS not set — no one can use the bot")

    application = (
        Application.builder()
        .token(ADMIN_BOT_TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    # Command handlers
    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("help", cmd_help))
    application.add_handler(CommandHandler("pending", cmd_pending_payments))
    application.add_handler(CommandHandler("broadcasts", cmd_pending_broadcasts))
    application.add_handler(CommandHandler("revenue", cmd_revenue))
    application.add_handler(CommandHandler("members", cmd_members))
    application.add_handler(CommandHandler("costs", cmd_costs))
    application.add_handler(CommandHandler("summary", cmd_summary))

    # Callback query handlers for inline buttons
    application.add_handler(CallbackQueryHandler(approve_payment_callback, pattern=r"^pay_approve:\d+$"))
    application.add_handler(CallbackQueryHandler(reject_payment_callback, pattern=r"^pay_reject:\d+$"))
    application.add_handler(CallbackQueryHandler(approve_broadcast_callback, pattern=r"^bc_approve:\d+$"))
    application.add_handler(CallbackQueryHandler(reject_broadcast_callback, pattern=r"^bc_reject:\d+$"))

    # Unknown command handler
    application.add_handler(MessageHandler(filters.COMMAND, unknown_command))

    logger.info("Admin Bot starting...")
    application.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
