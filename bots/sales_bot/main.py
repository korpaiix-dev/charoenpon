"""Sales Bot (แพร) - Main entry point.

Telegram Bot ใช้ python-telegram-bot v21 async
AI Model: google/gemini-2.0-flash-lite-001 ผ่าน OpenRouter
"""

from __future__ import annotations

import logging
import os
from datetime import time as dt_time
from datetime import timezone, timedelta

from telegram import Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    TypeHandler,
    filters,
)

from shared.database import close_db, init_db

from bots.sales_bot.handlers.flash_sale import get_flash_sale_handlers
from bots.sales_bot.handlers.packages import get_package_handlers
from bots.sales_bot.handlers.payment import get_payment_handlers
from bots.sales_bot.handlers.start import get_start_handlers
from bots.sales_bot.handlers.support import get_support_handlers
from bots.sales_bot.flash_sale_scheduler import start_flash_sale, end_flash_sale, remind_flash_sale
from bots.sales_bot.spam_filter import spam_filter_middleware

logger = logging.getLogger(__name__)

SALES_BOT_TOKEN: str = os.environ.get("SALES_BOT_TOKEN", "")

TH_TZ = timezone(timedelta(hours=7))


async def _spam_filter_wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Middleware wrapper — runs spam filter, stops processing if blocked."""
    blocked = await spam_filter_middleware(update, context)
    if blocked:
        # Raise ApplicationHandlerStop to prevent further processing
        from telegram.ext import ApplicationHandlerStop
        raise ApplicationHandlerStop()


async def _request_expiring_list(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Scheduler job: request expiring user list from guardian bot at 09:00.

    This is called by the scheduler. The guardian bot sends the list
    via the shared database — we query it and notify users.
    """
    from shared.utils import get_expiring_users

    try:
        expiring_7d = await get_expiring_users(days=7)
        expiring_3d = await get_expiring_users(days=3)
        expiring_1d = await get_expiring_users(days=1)

        bot = context.bot

        # Send renewal reminders to users expiring within 1 day
        for user_info in expiring_1d:
            try:
                await bot.send_message(
                    chat_id=user_info["telegram_id"],
                    text=(
                        "⚠️ <b>แจ้งเตือนค่ะ!</b>\n\n"
                        f"แพ็กเกจของคุณจะหมดอายุภายใน <b>{user_info['days_left']:.0f} วัน</b>\n\n"
                        "หากต้องการต่ออายุ กรุณาพิมพ์ /packages\n"
                        "เพื่อเลือกแพ็กเกจและชำระเงินค่ะ 🙏"
                    ),
                    parse_mode="HTML",
                )
            except Exception as exc:
                logger.error(
                    "Failed to send expiry reminder to %s: %s",
                    user_info["telegram_id"],
                    exc,
                )

        # Send renewal reminders to users expiring within 3 days (but not 1 day)
        notified_1d_ids = {u["telegram_id"] for u in expiring_1d}
        for user_info in expiring_3d:
            if user_info["telegram_id"] in notified_1d_ids:
                continue
            try:
                await bot.send_message(
                    chat_id=user_info["telegram_id"],
                    text=(
                        "📢 <b>แจ้งเตือนค่ะ</b>\n\n"
                        f"แพ็กเกจของคุณจะหมดอายุภายใน <b>{user_info['days_left']:.0f} วัน</b>\n\n"
                        "ต่ออายุตอนนี้เพื่อไม่ให้พลาดสัญญาณนะคะ\n"
                        "พิมพ์ /packages ได้เลยค่ะ 😊"
                    ),
                    parse_mode="HTML",
                )
            except Exception as exc:
                logger.error(
                    "Failed to send 3d reminder to %s: %s",
                    user_info["telegram_id"],
                    exc,
                )

        logger.info(
            "Expiry reminders sent: 1d=%d, 3d=%d, 7d=%d",
            len(expiring_1d),
            len(expiring_3d),
            len(expiring_7d),
        )

    except Exception as exc:
        logger.error("Failed to process expiring list: %s", exc)


async def post_init(application: Application) -> None:
    """Post-init hook — initialize database."""
    await init_db()
    logger.info("Sales Bot (แพร) initialized — database ready")


async def post_shutdown(application: Application) -> None:
    """Post-shutdown hook — close database."""
    await close_db()
    logger.info("Sales Bot (แพร) shut down — database closed")


def create_application() -> Application:
    """Create and configure the Sales Bot application."""
    if not SALES_BOT_TOKEN:
        raise ValueError("SALES_BOT_TOKEN environment variable is required")

    builder = Application.builder().token(SALES_BOT_TOKEN)
    app = builder.post_init(post_init).post_shutdown(post_shutdown).build()

    # --- Group 0: Spam filter middleware (runs first) ---
    app.add_handler(
        TypeHandler(Update, _spam_filter_wrapper),
        group=-1,
    )

    # --- Group 0: Command & callback handlers ---
    for handler in get_start_handlers():
        app.add_handler(handler, group=0)

    for handler in get_flash_sale_handlers():
        app.add_handler(handler, group=0)

    for handler in get_package_handlers():
        app.add_handler(handler, group=0)

    for handler in get_payment_handlers():
        app.add_handler(handler, group=0)

    # --- Group 0: Support handlers (generic text handler LAST) ---
    for handler in get_support_handlers():
        app.add_handler(handler, group=0)

    # --- Scheduler: request expiring list from guardian at 09:00 TH time ---
    app.job_queue.run_daily(
        _request_expiring_list,
        time=dt_time(hour=9, minute=0, tzinfo=TH_TZ),
        name="request_expiring_list_0900",
    )

    # --- Scheduler: Flash Sale Friday ---
    # เปิด Flash Sale ทุกวันศุกร์ 21:00 ไทย (day_of_week=4 = Friday)
    app.job_queue.run_daily(
        start_flash_sale,
        time=dt_time(hour=21, minute=0, tzinfo=TH_TZ),
        days=(4,),  # Friday
        name="flash_sale_start_friday_2100",
    )
    # Remind Flash Sale ทุกวันศุกร์ 22:00 และ 23:00 ไทย
    app.job_queue.run_daily(
        remind_flash_sale,
        time=dt_time(hour=22, minute=0, tzinfo=TH_TZ),
        days=(4,),  # Friday
        name="flash_sale_remind_friday_2200",
    )
    app.job_queue.run_daily(
        remind_flash_sale,
        time=dt_time(hour=23, minute=0, tzinfo=TH_TZ),
        days=(4,),  # Friday
        name="flash_sale_remind_friday_2300",
    )
    # ปิด Flash Sale ทุกวันเสาร์ 00:00 ไทย (day_of_week=5 = Saturday)
    app.job_queue.run_daily(
        end_flash_sale,
        time=dt_time(hour=0, minute=0, tzinfo=TH_TZ),
        days=(5,),  # Saturday
        name="flash_sale_end_saturday_0000",
    )

    return app


def main() -> None:
    """Run the Sales Bot."""
    logging.basicConfig(
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        level=logging.INFO,
    )
    # Reduce noise from httpx
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("telegram").setLevel(logging.WARNING)

    app = create_application()
    logger.info("Starting Sales Bot (แพร)...")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
