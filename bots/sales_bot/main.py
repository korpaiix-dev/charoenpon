"""Sales Bot (แพร) - Main entry point.

Telegram Bot ใช้ python-telegram-bot v21 async
AI Model: google/gemini-2.0-flash-lite-001 ผ่าน OpenRouter
"""

from __future__ import annotations

import logging
import os

# >>> FIXALL_TOKEN_ALERT <<< Bug #19
async def _slip2go_balance_check(context):
    """Every 6h: alert Discord if Slip2Go token balance < SLIP2GO_TOKEN_ALERT_THRESHOLD (default 50 slips)."""
    try:
        from shared.slip2go import get_account_info, Slip2GoError
        from bots.sales_bot.handlers.payment import _notify_discord
        import os as _os
        try:
            info = await get_account_info()
        except Slip2GoError as e:
            await _notify_discord("🛑 Slip2Go API DOWN",
                                   f"Cannot fetch account info: {e.code} {e.message}",
                                   color=0xFF0000)
            return
        remaining_slips = info.get("estimatedQuotaSlip", 0)
        threshold = int(_os.environ.get("SLIP2GO_TOKEN_ALERT_THRESHOLD", "50"))
        if remaining_slips < threshold:
            await _notify_discord(
                f"⚠️ Slip2Go quota low ({remaining_slips} slips)",
                f"เติม Slip2Go โทเคนด่วน — เหลือประมาณ {remaining_slips} สลิป (threshold={threshold})\nshop={info.get('shopName')} tokenRemaining={info.get('tokenRemaining')}",
                color=0xFFA500,
            )
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("slip2go balance check failed: %s", e)
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

from shared.database import close_db, get_session, init_db
from shared.models import User
from sqlalchemy import select

from bots.sales_bot.handlers.flash_sale import get_flash_sale_handlers
from bots.sales_bot.handlers.packages import get_package_handlers
from bots.sales_bot.handlers.payment import get_payment_handlers
from bots.sales_bot.handlers.referral import get_referral_handlers
from bots.sales_bot.handlers.start import get_start_handlers
# FIX 2025-05-21 (Phase 2a): /getlink — customer self-service for one-time invite links
from bots.sales_bot.handlers.getlink import get_getlink_handler
from bots.sales_bot.handlers.support import get_support_handlers
from bots.sales_bot.handlers.trial import get_trial_handlers
from bots.sales_bot.handlers.upsell import get_upsell_handlers, run_upsell_dm_job
from bots.sales_bot.comeback_dm import run_comeback_dm_job
# DEAD (Phase 1) from bots.sales_bot.trial_promo_dm import run_trial_promo_dm_job
from bots.sales_bot.flash_sale_scheduler import start_flash_sale, end_flash_sale, remind_flash_sale
from bots.sales_bot.promo_scheduler import (
    broadcast_referral_promo,
    broadcast_songkran_promo,
    broadcast_trial_promo,
)
# DEAD (Phase 1) from bots.sales_bot.trial_upsell import check_trial_upsell
# Lead follow-up DM jobs disabled by boss request (2026-04-26): too noisy / repeated admin alerts.
# from bots.sales_bot.lead_followup import run_lead_followup_job
# from bots.sales_bot.lead_followup_v2 import run_lead_followup_v2_job
from bots.sales_bot.spam_filter import spam_filter_middleware
from bots.sales_bot.handlers.referral import send_referral_reminder
from bots.sales_bot.daily_report import send_daily_report
from bots.sales_bot.handlers.birthday_upgrade import get_birthday_upgrade_handlers
from bots.sales_bot.handlers.shaker import get_shaker_handlers
from bots.sales_bot.preview_generator import run_preview_generator_job, ensure_tables as ensure_preview_tables
# DEAD (Phase 1) from bots.sales_bot.free_group_poster import post_to_free_groups
from bots.sales_bot.retention_alert import run_retention_alert_job
from bots.sales_bot.referral_v2 import send_referral_reminder_v2
# DEAD (Phase 1) from bots.sales_bot.marketing_brain import run_brain_weekly_job

logger = logging.getLogger(__name__)

SALES_BOT_TOKEN: str = os.environ.get("SALES_BOT_TOKEN", "")

from shared.tz import TH_TZ


async def _spam_filter_wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Middleware wrapper — runs spam filter, stops processing if blocked."""
    blocked = await spam_filter_middleware(update, context)
    if blocked:
        # Raise ApplicationHandlerStop to prevent further processing
        from telegram.ext import ApplicationHandlerStop
        raise ApplicationHandlerStop()


async def _banned_user_guard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Stop all Sales Bot interactions from users marked as banned."""
    user = update.effective_user
    if not user:
        return

    try:
        async with get_session() as session:
            result = await session.execute(
                select(User.is_banned).where(User.telegram_id == user.id)
            )
            is_banned = bool(result.scalar_one_or_none())
    except Exception as exc:
        logger.warning("Banned user guard failed open for user=%s: %s", user.id, exc)
        return

    if is_banned:
        logger.info("Blocked banned user interaction: telegram_id=%s username=%s", user.id, user.username)
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
    await ensure_preview_tables()
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
    app.add_handler(
        TypeHandler(Update, _banned_user_guard),
        group=-1,
    )

    # --- Group 0: Command & callback handlers ---
    from telegram.ext import CommandHandler as _CH
    app.add_handler(_CH("credits", cmd_credits), group=0)

    for handler in get_start_handlers():
        app.add_handler(handler, group=0)

    # FIX 2025-05-21 (Phase 2a): /getlink — VIP customers can request fresh one-time
    # invite links themselves without bothering admin.
    app.add_handler(get_getlink_handler(), group=0)

    # Trial handlers — ปิดแล้ว (ยกเลิกโปร 99)
    # for handler in get_trial_handlers():
    #     app.add_handler(handler, group=0)

    for handler in get_flash_sale_handlers():
        app.add_handler(handler, group=0)

    for handler in get_referral_handlers():
        app.add_handler(handler, group=0)

    for handler in get_upsell_handlers():
        app.add_handler(handler, group=0)

    for handler in get_package_handlers():
        app.add_handler(handler, group=0)

    # Birthday Promo /upgrade — เฉพาะลูกค้าที่มี birthday_upgrade_offers
    for handler in get_birthday_upgrade_handlers():
        app.add_handler(handler, group=0)

    # ห้องมีคนชัก lottery (/shaker, /myticket)
    for handler in get_shaker_handlers():
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

    # --- Scheduler: Flash Sale Friday — DISABLED 2026-06-04 ---
    # บอสตัดสินใจปิด weekly Flash Friday เพราะทับซ้อนกับ promo ใหม่
    # (Lucky 6.6, Birthday 7-10 มิ.ย., Mid-Month Flash 15-17 มิ.ย.)
    # หากต้องเปิดอีก ค่อย uncomment block นี้
    # app.job_queue.run_daily(start_flash_sale, time=dt_time(hour=21, minute=0, tzinfo=TH_TZ), days=(4,), name="flash_sale_start_friday_2100")
    # app.job_queue.run_daily(remind_flash_sale, time=dt_time(hour=22, minute=0, tzinfo=TH_TZ), days=(4,), name="flash_sale_remind_friday_2200")
    # app.job_queue.run_daily(remind_flash_sale, time=dt_time(hour=23, minute=0, tzinfo=TH_TZ), days=(4,), name="flash_sale_remind_friday_2300")
    # app.job_queue.run_daily(end_flash_sale, time=dt_time(hour=0, minute=0, tzinfo=TH_TZ), days=(5,), name="flash_sale_end_saturday_0000")

    # --- Scheduler: TRIAL PROMO DM ทุกวัน 00:30 ไทย (17:30 UTC) ---
    # หลัง Flash Sale ปิด 30 นาที — ส่ง DM Trial ฿99 — ปิดแล้ว (ยกเลิกโปร 99)
    # app.job_queue.run_daily(
    #     run_trial_promo_dm_job,
    #     time=dt_time(hour=0, minute=30, tzinfo=TH_TZ),
    #     name="trial_promo_dm_daily_0030",
    # )

    # --- Scheduler: COMEBACK DM ทุกวัน 10:00 ไทย ---
    app.job_queue.run_daily(
        run_comeback_dm_job,
        time=dt_time(hour=10, minute=0, tzinfo=TH_TZ),
        name="comeback_dm_daily_1000",
    )

    # --- Scheduler: Trial Upsell DM — ปิดแล้ว (ยกเลิกโปร 99) ---
    # app.job_queue.run_repeating(
    #     check_trial_upsell,
    #     interval=1800,
    #     first=60,
    #     name="trial_upsell_check",
    # )

    # --- Scheduler: GOD MODE Upsell DM ทุกวัน 15:00 ไทย ---
    app.job_queue.run_daily(
        run_upsell_dm_job,
        time=dt_time(hour=15, minute=0, tzinfo=TH_TZ),
        name="god_mode_upsell_dm_daily_1500",
    )

    # --- Scheduler: Trial Promo Broadcast — ปิดแล้ว (ยกเลิกโปร 99) ---
    # app.job_queue.run_daily(
    #     broadcast_trial_promo,
    #     time=dt_time(hour=14, minute=0, tzinfo=TH_TZ),
    #     days=(5,),  # Saturday
    #     name="trial_promo_broadcast_saturday_1400",
    # )

    # --- Scheduler: Referral Promo Broadcast อาทิตย์ 14:00 ไทย ---
    app.job_queue.run_daily(
        broadcast_referral_promo,
        time=dt_time(hour=14, minute=0, tzinfo=TH_TZ),
        days=(6,),  # Sunday
        name="referral_promo_broadcast_sunday_1400",
    )

    # --- Scheduler: Songkran 1299 promo via @jarern4_bot every day 12:00 และ 20:00 ไทย ---
    app.job_queue.run_daily(
        broadcast_songkran_promo,
        time=dt_time(hour=12, minute=0, tzinfo=TH_TZ),
        name="songkran_promo_broadcast_daily_1200",
    )
    app.job_queue.run_daily(
        broadcast_songkran_promo,
        time=dt_time(hour=20, minute=0, tzinfo=TH_TZ),
        name="songkran_promo_broadcast_daily_2000",
    )

    # >>> FIXALL_TOKEN_REGISTER <<<
    # Bug #19: Slip2Go balance check every 6 hours
    app.job_queue.run_repeating(
        _slip2go_balance_check,
        interval=6 * 3600,
        first=300,  # first check 5 min after startup
        name="slip2go_balance_check_6h",
    )

    # --- Scheduler: Lead Follow-up DM ทุก 1 ชม. (v1 — replaced by v2) ---
    # app.job_queue.run_repeating(
    #     run_lead_followup_job,
    #     interval=3600,
    #     first=120,
    #     name="lead_followup_hourly",
    # )

    # --- Scheduler: Lead Follow-up v2 DM ทุก 1 ชม. ---
    # Disabled by boss request (2026-04-26): ขึ้นแจ้งเตือนบ่อยและยิงหา lead ที่ DM ไม่ได้ซ้ำ ๆ
    # app.job_queue.run_repeating(
    #     run_lead_followup_v2_job,
    #     interval=3600,
    #     first=120,
    #     name="lead_followup_v2_hourly",
    # )

    # --- Scheduler: Referral Reminder DM ทุกวัน 15:00 ไทย ---
    app.job_queue.run_daily(
        send_referral_reminder,
        time=dt_time(hour=15, minute=0, tzinfo=TH_TZ),
        name="referral_reminder_daily_1500",
    )

    # --- Scheduler: Daily Report ทุกวัน 22:00 ไทย (15:00 UTC) ---
    app.job_queue.run_daily(
        send_daily_report,
        time=dt_time(hour=22, minute=0, tzinfo=TH_TZ),
        name="daily_report_2200",
    )

    # --- Scheduler: Preview Generator Batch ทุกวัน 06:00 ไทย ---
    app.job_queue.run_daily(
        run_preview_generator_job,
        time=dt_time(hour=6, minute=0, tzinfo=TH_TZ),
        name="preview_generator_daily_0600",
    )

    # --- Scheduler: Free Group Poster 3 รอบ/วัน ---
    # Disabled — content_bot (มิน) handles free group posting
    # app.job_queue.run_daily(
    #     post_to_free_groups,
    #     time=dt_time(hour=11, minute=0, tzinfo=TH_TZ),
    #     name="free_group_poster_1100",
    # )
    # app.job_queue.run_daily(
    #     post_to_free_groups,
    #     time=dt_time(hour=15, minute=0, tzinfo=TH_TZ),
    #     name="free_group_poster_1500",
    # )
    # app.job_queue.run_daily(
    #     post_to_free_groups,
    #     time=dt_time(hour=20, minute=0, tzinfo=TH_TZ),
    #     name="free_group_poster_2000",
    # )

    # --- Scheduler: Retention Alert v2 ทุกวัน 09:30 ไทย ---
    app.job_queue.run_daily(
        run_retention_alert_job,
        time=dt_time(hour=9, minute=30, tzinfo=TH_TZ),
        name="retention_alert_v2_0930",
    )

    # --- Scheduler: Referral Reminder v2 ทุกวันจันทร์ 15:00 ไทย ---
    app.job_queue.run_daily(
        send_referral_reminder_v2,
        time=dt_time(hour=15, minute=0, tzinfo=TH_TZ),
        days=(0,),  # Monday
        name="referral_reminder_v2_monday_1500",
    )

    # DISABLED 2026-05-22 (boss request): Marketing Brain weekly analysis
    # --- Scheduler: Marketing Brain ทุกวันอาทิตย์ 20:00 ไทย ---
    # app.job_queue.run_daily(
    #     run_brain_weekly_job,
    #     time=dt_time(hour=20, minute=0, tzinfo=TH_TZ),
    #     days=(6,),  # Sunday
    #     name="marketing_brain_weekly_sunday_2000",
    # )

    return app




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
