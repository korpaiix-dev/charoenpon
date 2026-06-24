"""Guardian Bot (ยาม) - Main entry point.

ไม่ใช้ AI — Python + SQL ล้วน
Bot entry + scheduler setup

Schedules:
- ทุก 6h: เตะหมดอายุ
- ทุกวัน 09:00: ส่งรายชื่อใกล้หมด
- ทุก 30min: ตรวจคนเข้ากลุ่มไม่มีสิทธิ์
- ทุกวัน 22:00: daily report
"""

from __future__ import annotations

import logging
import os
from datetime import time as dt_time
from datetime import timedelta, timezone

from telegram import Update
from telegram.ext import Application, CallbackQueryHandler, ChatMemberHandler, ContextTypes

from shared.database import close_db, get_session, init_db

from bots.guardian_bot.group_monitor import (
    check_and_kick_unauthorized,
    is_in_csv_whitelist,
    load_csv_whitelist,
    notify_admin_for_decision,
    pending_guardian_decisions,
    _log_kick_action,
    _log_member_join,
)
from bots.guardian_bot.content_distributor import (
    distribute_pending_content,
    get_distributor_handlers,
)
from bots.guardian_bot.scheduler import (
    check_unauthorized_members,
    generate_daily_report,
    kick_expired_members,
    send_expiring_list,
)

logger = logging.getLogger(__name__)

GUARDIAN_BOT_TOKEN: str = os.environ.get("GUARDIAN_BOT_TOKEN", "")

from shared.tz import TH_TZ


# --- Scheduler job wrappers ---

async def _job_kick_expired(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Job: kick expired members every 6 hours."""
    logger.info("Running scheduled job: kick_expired_members")
    try:
        stats = await kick_expired_members(context.bot)
        logger.info("kick_expired result: %s", stats)
    except Exception as exc:
        logger.error("Job kick_expired_members failed: %s", exc)


async def _job_send_expiring_list(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Job: send expiring list daily at 09:00."""
    logger.info("Running scheduled job: send_expiring_list")
    try:
        summary = await send_expiring_list(context.bot)
        logger.info("send_expiring_list result: 1d=%d 3d=%d 7d=%d",
                     len(summary["1d"]), len(summary["3d"]), len(summary["7d"]))
    except Exception as exc:
        logger.error("Job send_expiring_list failed: %s", exc)


async def _job_check_unauthorized(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Job: check unauthorized members every 30 minutes."""
    logger.info("Running scheduled job: check_unauthorized_members")
    try:
        stats = await check_unauthorized_members(context.bot, job_queue=context.job_queue)
        logger.info("check_unauthorized result: %s", stats)
    except Exception as exc:
        logger.error("Job check_unauthorized_members failed: %s", exc)


async def _job_daily_report(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Job: generate daily report at 22:00."""
    logger.info("Running scheduled job: generate_daily_report")
    try:
        report = await generate_daily_report(context.bot)
        logger.info("Daily report generated successfully")
    except Exception as exc:
        logger.error("Job generate_daily_report failed: %s", exc)


# --- Chat member update handler ---

async def handle_chat_member_update(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle new members joining groups — kick if unauthorized.

    This provides real-time protection in addition to the scheduled checks.
    """
    if not update.chat_member:
        return

    member_update = update.chat_member
    new_member = member_update.new_chat_member

    # Only care about users joining (status becoming "member")
    if new_member.status not in ("member", "restricted"):
        return

    user = new_member.user
    if user.is_bot:
        return

    chat_id = member_update.chat.id

    # Find the group in registry
    from sqlalchemy import select

    async with get_session() as session:
        from shared.models import GroupRegistry

        group_result = await session.execute(
            select(GroupRegistry).where(GroupRegistry.chat_id == chat_id)
        )
        group = group_result.scalar_one_or_none()

    if not group:
        return  # Not a monitored group

    # Marketing attribution (run BEFORE FREE skip so PROMO_HUB/PROMO_NEWS get tracked)
    try:
        _slug_for_marketing = group.slug.value if hasattr(group.slug, 'value') else str(group.slug)
        _invite = getattr(member_update, 'invite_link', None)
        _invite_name = getattr(_invite, 'name', None) if _invite else None
        if _invite_name:
            from bots.guardian_bot.marketing_tracker import track_marketing_join
            await track_marketing_join(
                group_slug=_slug_for_marketing,
                invite_link_name=_invite_name,
                telegram_id=user.id,
                tg_username=user.username,
                tg_first_name=user.first_name,
                tg_last_name=user.last_name,
            )
    except Exception as _mt_exc:
        logger.exception('marketing tracking failed (non-fatal): %s', _mt_exc)

    # 2026-06-18: skip FREE groups — anyone can join, no sub check, no log spam
    tier_str = group.min_tier.value if hasattr(group.min_tier, "value") else str(group.min_tier)
    if tier_str in ("FREE", "TIER_FREE"):
        return

    slug = group.slug.value if hasattr(group.slug, "value") else str(group.slug)

    # Check authorization
    from bots.guardian_bot.group_monitor import _get_authorized_telegram_ids

    authorized_ids = await _get_authorized_telegram_ids(slug)

    if user.id in authorized_ids:
        logger.info("User %s joined group %s — authorized (DB)", user.id, slug)
        await _log_member_join(
            context.bot, user.id, user.username,
            user.full_name, group.title, "✅ DB authorized"
        )
        return

    # Check if admin
    from bots.guardian_bot.group_monitor import _get_admin_telegram_ids

    admin_ids = await _get_admin_telegram_ids()
    if user.id in admin_ids:
        return

    # ชั้น 2: Check CSV whitelist
    if is_in_csv_whitelist(user.id):
        logger.info("User %s joined group %s — authorized (CSV)", user.id, slug)
        await _log_member_join(
            context.bot, user.id, user.username,
            user.full_name, group.title, "✅ CSV whitelist"
        )
        return

    # ── CSV Expired → เตะอัตโนมัติ ──
    from bots.guardian_bot.group_monitor import is_csv_expired
    if is_csv_expired(user.id):
        try:
            await context.bot.ban_chat_member(chat_id=chat_id, user_id=user.id)
            await context.bot.unban_chat_member(chat_id=chat_id, user_id=user.id, only_if_banned=True)
            log_msg = (
                f"👤 {user.full_name} (@{user.username or '-'})\n"
                f"🆔 TG ID: <code>{user.id}</code>\n"
                f"📍 กลุ่ม: {group.title}\n"
                f"📋 CSV status = Expired\n"
                f"✅ เตะ + unban อัตโนมัติ"
            )
            await _log_kick_action(context.bot, log_msg)
            logger.info("Auto-kicked CSV expired user %s from %s on join", user.id, slug)
        except Exception as exc:
            logger.error("Failed to kick CSV expired %s on join: %s", user.id, exc)
        return

    # ชั้น 3: ไม่เจอเลย → แจ้ง Admin พร้อมปุ่ม (ไม่เตะทันที!)
    await _log_member_join(
        context.bot, user.id, user.username,
        user.full_name, group.title, "⚠️ ไม่มีสิทธิ์ — รอ Admin ตัดสินใจ"
    )
    logger.info(
        "User %s (@%s) joined group %s — NOT authorized, notifying admin",
        user.id, user.username, slug,
    )

    await notify_admin_for_decision(
        bot=context.bot,
        user_id=user.id,
        username=user.username,
        chat_id=chat_id,
        group_title=group.title,
        job_queue=context.application.job_queue,
    )


# --- Callback query handlers for admin decisions ---

async def handle_guardian_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle ✅ ปล่อย / ❌ เตะ callback from admin group."""
    query = update.callback_query
    if not query or not query.data:
        return

    await query.answer()

    data = query.data  # guardian_keep_{user_id}_{chat_id} or guardian_kick_{user_id}_{chat_id}
    parts = data.split("_")
    # Format: guardian_keep_12345_-100xxx or guardian_kick_12345_-100xxx
    if len(parts) < 4:
        return

    action = parts[1]  # keep or kick
    try:
        user_id = int(parts[2])
        chat_id = int("_".join(parts[3:]))  # chat_id can be negative with underscore
    except (ValueError, IndexError):
        return

    admin_user = query.from_user
    admin_name = f"@{admin_user.username}" if admin_user.username else admin_user.full_name

    decision_key = f"{user_id}_{chat_id}"

    # Cancel the timeout job
    job_name = pending_guardian_decisions.pop(decision_key, None)
    if job_name:
        jobs = context.job_queue.get_jobs_by_name(job_name)
        for job in jobs:
            job.schedule_removal()

    if action == "keep":
        # ปล่อย — don't kick
        await query.edit_message_text(
            text=f"✅ ปล่อยแล้ว โดย {admin_name}\n\n👤 User: {user_id}"
        )
        logger.info("Admin %s decided to KEEP user %s in chat %s", admin_name, user_id, chat_id)

    elif action == "kick":
        # เตะ
        try:
            await context.bot.ban_chat_member(chat_id=chat_id, user_id=user_id)
            await context.bot.unban_chat_member(chat_id=chat_id, user_id=user_id, only_if_banned=True)

            from shared.utils import log_admin_action
            await log_admin_action(
                admin_id=admin_user.id,
                action="kick_by_admin_decision",
                target_type="user",
                target_id=user_id,
                details=f"tg={user_id} chat={chat_id} by={admin_name}",
            )

            # Notify user
            try:
                from telegram import Bot as _Bot
                _sales = _Bot(token=os.environ.get("SALES_BOT_TOKEN", ""))
                await _sales.initialize()
                await _sales.send_message(
                    chat_id=user_id,
                    text=(
                        "⚠️ คุณถูกนำออกจากกลุ่มเนื่องจากไม่มี subscription ที่ active ครับ\n\n"
                        "หากต้องการเข้าใหม่ สามารถสมัครแพ็กเกจได้ที่ @NamwarnJarern_bot ครับ"
                    ),
                )
            except Exception:
                pass

        except Exception as exc:
            logger.error("Failed to kick user %s from chat %s: %s", user_id, chat_id, exc)

        await query.edit_message_text(
            text=f"❌ เตะแล้ว โดย {admin_name}\n\n👤 User: {user_id}"
        )
        logger.info("Admin %s decided to KICK user %s from chat %s", admin_name, user_id, chat_id)


# --- Application setup ---

async def post_init(application: Application) -> None:
    """Post-init hook — initialize database + load CSV whitelist."""
    await init_db()
    load_csv_whitelist()
    logger.info("Guardian Bot (ยาม) initialized — database ready, CSV whitelist loaded")


async def post_shutdown(application: Application) -> None:
    """Post-shutdown hook — close database."""
    await close_db()
    logger.info("Guardian Bot (ยาม) shut down — database closed")


def create_application() -> Application:
    """Create and configure the Guardian Bot application."""
    if not GUARDIAN_BOT_TOKEN:
        raise ValueError("GUARDIAN_BOT_TOKEN environment variable is required")

    builder = Application.builder().token(GUARDIAN_BOT_TOKEN)
    app = builder.post_init(post_init).post_shutdown(post_shutdown).build()

    # --- Real-time chat member handler ---
    app.add_handler(
        ChatMemberHandler(handle_chat_member_update, ChatMemberHandler.CHAT_MEMBER)
    )

    # --- Callback query handler for admin decisions ---
    app.add_handler(
        CallbackQueryHandler(handle_guardian_callback, pattern=r"^guardian_(keep|kick)_")
    )

    # --- Content distributor: capture media from source group ---
    for h in get_distributor_handlers():
        app.add_handler(h)

    # --- Gacha clip listener: capture media in CLIP source groups ---
    from bots.guardian_bot.gacha_clip_listener import get_gacha_clip_listener_handler
    app.add_handler(get_gacha_clip_listener_handler())

    # --- Scheduled jobs ---
    job_queue = app.job_queue

    # Kick expired members (เตะเฉพาะคนที่มี subscription หมดอายุใน DB)
    job_queue.run_repeating(
        _job_kick_expired,
        interval=timedelta(hours=6),
        first=timedelta(minutes=5),
        name="kick_expired_6h",
    )

    # Daily 09:00 TH: send expiring list
    job_queue.run_daily(
        _job_send_expiring_list,
        time=dt_time(hour=9, minute=0, tzinfo=TH_TZ),
        name="expiring_list_0900",
    )

    # Check unauthorized members (3-tier: DB → CSV → ถาม Admin)
    # FIX 2026-05-22 (boss request): 30 min → 2 hours (was too frequent)
    job_queue.run_repeating(
        _job_check_unauthorized,
        interval=timedelta(hours=2),
        first=timedelta(minutes=10),
        name="check_unauthorized_2h",
    )

    # Daily 22:00 TH: daily report
    job_queue.run_daily(
        _job_daily_report,
        time=dt_time(hour=22, minute=0, tzinfo=TH_TZ),
        name="daily_report_2200",
    )

    # ห้องมีคนชัก draw — every Monday 21:00 TH (after Lao lotto 20:30)
    async def _job_shaker_draw(context):
        from bots.guardian_bot.shaker_draw import run_draw_now
        logger.info("Running scheduled job: shaker_draw (Monday 21:00)")
        try:
            result = await run_draw_now(context.bot)
            logger.info("shaker_draw result: %s", result)
        except Exception as exc:
            logger.error("shaker_draw failed: %s", exc, exc_info=True)

    job_queue.run_daily(
        _job_shaker_draw,
        time=dt_time(hour=21, minute=0, tzinfo=TH_TZ),
        days=(0,),  # 0 = Monday in PTB
        name="shaker_draw_monday_2100",
    )

    # Content distributor — runs every N minutes (interval from distribution_config DB)
    # First fetch interval; default 60 min if config missing
    import asyncio as _asyncio
    async def _get_interval() -> int:
        from shared.database import get_session as _gs
        from sqlalchemy import text as _t
        try:
            async with _gs() as _s:
                r = await _s.execute(_t("SELECT value FROM distribution_config WHERE key='schedule_interval_minutes'"))
                row = r.fetchone()
                return int(row[0]) if row else 60
        except Exception:
            return 60
    try:
        loop = _asyncio.get_event_loop()
        interval_min = loop.run_until_complete(_get_interval()) if not loop.is_running() else 60
    except Exception:
        interval_min = 60
    job_queue.run_repeating(
        distribute_pending_content,
        interval=timedelta(minutes=interval_min),
        first=timedelta(minutes=2),
        name=f"content_distribute_{interval_min}m",
    )

    # Slip2Go retry worker — every 2 min, retries slips that hit ITMX cache lag
    async def _job_slip2go_retry(context):
        from shared.slip2go_retry_worker import worker_loop
        try:
            await worker_loop()
        except Exception as exc:
            logger.error("slip2go retry worker failed: %s", exc, exc_info=True)
    job_queue.run_repeating(
        _job_slip2go_retry,
        interval=timedelta(minutes=2),
        first=timedelta(minutes=1),
        name="slip2go_retry_2m",
    )

    # Marketing daily digest — 09:00 BKK every day → post to #marketing-รวม
    async def _job_marketing_digest(context):
        from bots.guardian_bot.scheduler import marketing_daily_digest
        try:
            await marketing_daily_digest()
        except Exception as exc:
            logger.error("marketing_daily_digest failed: %s", exc, exc_info=True)
    job_queue.run_daily(
        _job_marketing_digest,
        time=dt_time(hour=9, minute=0, tzinfo=TH_TZ),
        name="marketing_digest_0900",
    )

    # Marketing monthly leaderboard — 1st of each month at 09:30 BKK
    # Uses CronTrigger via apscheduler (PTB job_queue doesn't support cron natively)
    async def _job_monthly_leaderboard(context):
        from bots.guardian_bot.scheduler import marketing_monthly_leaderboard
        # Only run on the 1st of the month
        from datetime import datetime, timezone, timedelta
        bkk = timezone(timedelta(hours=7))
        if datetime.now(bkk).day != 1:
            return
        try:
            await marketing_monthly_leaderboard()
        except Exception as exc:
            logger.error("monthly_leaderboard failed: %s", exc, exc_info=True)
    job_queue.run_daily(
        _job_monthly_leaderboard,
        time=dt_time(hour=9, minute=30, tzinfo=TH_TZ),
        name="marketing_leaderboard_monthly",
    )

    # Marketing stale link check — daily 10:00 BKK
    async def _job_marketing_stale(context):
        from bots.guardian_bot.scheduler import marketing_stale_link_check
        try:
            await marketing_stale_link_check()
        except Exception as exc:
            logger.error("marketing_stale_link failed: %s", exc, exc_info=True)
    job_queue.run_daily(
        _job_marketing_stale,
        time=dt_time(hour=10, minute=0, tzinfo=TH_TZ),
        name="marketing_stale_link_1000",
    )

    # DISABLED 2026-06-22 — superseded by event-driven delivery in gacha_api
    # Old worker scanned every 30s for clip_pack delivery.
    # Event-driven path (gacha_api/gacha_deliver.py) now handles all prize types
    # immediately on claim. If event delivery fails, customer can request via
    # Prae bot or admin /resend.
    #
    # async def _job_gacha_prize(context):
    #     from shared.gacha_prize_worker import worker_loop as _gpw
    #     try:
    #         await _gpw()
    #     except Exception as exc:
    #         logger.error("gacha prize worker failed: %s", exc, exc_info=True)
    # job_queue.run_repeating(
    #     _job_gacha_prize,
    #     interval=timedelta(seconds=30),
    #     first=timedelta(seconds=20),
    #     name="gacha_prize_30s",
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
    """Run the Guardian Bot."""
    logging.basicConfig(
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        level=logging.INFO,
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("telegram").setLevel(logging.WARNING)

    app = create_application()
    logger.info("Starting Guardian Bot (ยาม)...")
    app.run_polling(
        allowed_updates=[Update.CHAT_MEMBER, Update.CALLBACK_QUERY, Update.MESSAGE, Update.CHANNEL_POST],
        drop_pending_updates=True,
    )


if __name__ == "__main__":
    main()
