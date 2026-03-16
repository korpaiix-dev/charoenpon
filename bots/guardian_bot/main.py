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
from telegram.ext import Application, ChatMemberHandler, ContextTypes

from shared.database import close_db, init_db

from bots.guardian_bot.group_monitor import check_and_kick_unauthorized
from bots.guardian_bot.scheduler import (
    check_unauthorized_members,
    generate_daily_report,
    kick_expired_members,
    send_expiring_list,
)

logger = logging.getLogger(__name__)

GUARDIAN_BOT_TOKEN: str = os.environ.get("GUARDIAN_BOT_TOKEN", "")

TH_TZ = timezone(timedelta(hours=7))


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
        stats = await check_unauthorized_members(context.bot)
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

    slug = group.slug.value if hasattr(group.slug, "value") else str(group.slug)

    # Check authorization
    from bots.guardian_bot.group_monitor import _get_authorized_telegram_ids

    authorized_ids = await _get_authorized_telegram_ids(slug)

    if user.id in authorized_ids:
        logger.info("User %s joined group %s — authorized", user.id, slug)
        return

    # Check if admin
    from bots.guardian_bot.group_monitor import _get_admin_telegram_ids

    admin_ids = await _get_admin_telegram_ids()
    if user.id in admin_ids:
        return

    # Unauthorized — kick immediately
    try:
        await context.bot.ban_chat_member(chat_id=chat_id, user_id=user.id)
        await context.bot.unban_chat_member(
            chat_id=chat_id, user_id=user.id, only_if_banned=True
        )

        from shared.utils import log_admin_action

        await log_admin_action(
            admin_id=0,
            action="kick_unauthorized_realtime",
            target_type="user",
            target_id=user.id,
            details=f"tg={user.id} group={slug} username={user.username} realtime_join",
        )

        logger.info(
            "Kicked unauthorized user %s (@%s) from group %s (realtime)",
            user.id,
            user.username,
            slug,
        )

        try:
            await context.bot.send_message(
                chat_id=user.id,
                text=(
                    f"⚠️ คุณถูกนำออกจากกลุ่ม {group.title} "
                    "เนื่องจากไม่มี subscription ที่ active ครับ\n\n"
                    "หากต้องการเข้าใช้งาน สามารถสมัครแพ็กเกจได้ที่ @CharoenponBot ครับ"
                ),
            )
        except Exception:
            pass

    except Exception as exc:
        logger.error(
            "Failed to kick unauthorized join %s from %s: %s",
            user.id,
            slug,
            exc,
        )


# --- Application setup ---

async def post_init(application: Application) -> None:
    """Post-init hook — initialize database."""
    await init_db()
    logger.info("Guardian Bot (ยาม) initialized — database ready")


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

    # --- Scheduled jobs ---
    job_queue = app.job_queue

    # Every 6 hours: kick expired members
    job_queue.run_repeating(
        _job_kick_expired,
        interval=timedelta(hours=6),
        first=timedelta(minutes=1),  # Start 1 min after boot
        name="kick_expired_6h",
    )

    # Daily 09:00 TH: send expiring list
    job_queue.run_daily(
        _job_send_expiring_list,
        time=dt_time(hour=9, minute=0, tzinfo=TH_TZ),
        name="expiring_list_0900",
    )

    # Every 30 minutes: check unauthorized members
    job_queue.run_repeating(
        _job_check_unauthorized,
        interval=timedelta(minutes=30),
        first=timedelta(minutes=5),  # Start 5 min after boot
        name="check_unauthorized_30min",
    )

    # Daily 22:00 TH: daily report
    job_queue.run_daily(
        _job_daily_report,
        time=dt_time(hour=22, minute=0, tzinfo=TH_TZ),
        name="daily_report_2200",
    )

    return app


def main() -> None:
    """Run the Guardian Bot."""
    logging.basicConfig(
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        level=logging.INFO,
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    app = create_application()
    logger.info("Starting Guardian Bot (ยาม)...")
    app.run_polling(
        allowed_updates=[Update.CHAT_MEMBER],
        drop_pending_updates=True,
    )


if __name__ == "__main__":
    main()
