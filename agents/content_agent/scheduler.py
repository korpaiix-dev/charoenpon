"""Content Scheduler (มิน) - auto-post content ตามเวลา + retention messages.

Schedule: 10:00/14:00/18:00/21:00 ทุกกลุ่ม VIP
Retention: 08:50 เตรียมข้อความ personalized ให้แพร(Sales Bot)
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, time, timedelta, timezone
from typing import Any

from sqlalchemy import select, update

from shared.api_cost_tracker import call_openrouter
from shared.database import get_session
from shared.models import (
    ContentSchedule,
    ExpiryNotification,
    GroupRegistry,
    GroupSlug,
    NotificationType,
    Subscription,
    SubscriptionStatus,
    User,
)
from shared.utils import TH_TZ, format_datetime_thai, format_thb, get_expiring_users

logger = logging.getLogger(__name__)

MODEL = "anthropic/claude-haiku-3-5"
CALLER = "content_agent/scheduler"

POST_TIMES_TH = [
    time(10, 0),
    time(14, 0),
    time(18, 0),
    time(21, 0),
]

RETENTION_PREP_TIME_TH = time(8, 50)

POST_INTERVAL_SECONDS = 2


def _now_th() -> datetime:
    """Get current time in Thai timezone."""
    return datetime.now(TH_TZ)


def _today_at_th(t: time) -> datetime:
    """Get a datetime for today at a specific Thai time."""
    now = _now_th()
    return now.replace(hour=t.hour, minute=t.minute, second=0, microsecond=0)


async def get_pending_content(
    group_slug: str | None = None,
    limit: int = 50,
) -> list[ContentSchedule]:
    """ดึง content ที่ยังไม่ได้ส่ง และถึงเวลาแล้ว."""
    now_utc = datetime.now(timezone.utc)

    async with get_session() as session:
        stmt = (
            select(ContentSchedule)
            .where(
                ContentSchedule.is_sent.is_(False),
                ContentSchedule.scheduled_at <= now_utc,
            )
            .order_by(ContentSchedule.scheduled_at.asc())
            .limit(limit)
        )
        if group_slug:
            stmt = stmt.where(ContentSchedule.group_slug == GroupSlug(group_slug))

        result = await session.execute(stmt)
        return list(result.scalars().all())


async def get_group_chat_ids() -> dict[str, int]:
    """ดึง mapping ระหว่าง group slug กับ chat_id."""
    async with get_session() as session:
        result = await session.execute(
            select(GroupRegistry).where(GroupRegistry.is_active.is_(True))
        )
        groups = result.scalars().all()

    return {g.slug.value: g.chat_id for g in groups}


async def send_content_to_group(
    bot: Any,
    content: ContentSchedule,
    chat_id: int,
) -> bool:
    """ส่ง content ไปยังกลุ่ม Telegram."""
    from shared.utils import safe_send_message

    try:
        if content.content_type == "photo" and content.media_file_id:
            await bot.send_photo(
                chat_id=chat_id,
                photo=content.media_file_id,
                caption=content.caption,
                parse_mode="HTML",
            )
        elif content.content_type == "video" and content.media_file_id:
            await bot.send_video(
                chat_id=chat_id,
                video=content.media_file_id,
                caption=content.caption,
                parse_mode="HTML",
            )
        elif content.content_type == "document" and content.media_file_id:
            await bot.send_document(
                chat_id=chat_id,
                document=content.media_file_id,
                caption=content.caption,
                parse_mode="HTML",
            )
        else:
            await safe_send_message(bot, chat_id, content.caption or "")

        async with get_session() as session:
            await session.execute(
                update(ContentSchedule)
                .where(ContentSchedule.id == content.id)
                .values(is_sent=True, sent_at=datetime.now(timezone.utc))
            )

        logger.info("Sent content #%d to group %s", content.id, content.group_slug.value)
        return True

    except Exception as exc:
        logger.error("Failed to send content #%d: %s", content.id, exc)
        async with get_session() as session:
            await session.execute(
                update(ContentSchedule)
                .where(ContentSchedule.id == content.id)
                .values(error=str(exc))
            )
        return False


async def process_scheduled_posts(bot: Any) -> dict[str, int]:
    """ประมวลผลโพสต์ที่ถึงเวลาแล้ว ส่งไปทุกกลุ่ม."""
    group_chat_ids = await get_group_chat_ids()
    pending = await get_pending_content()

    stats = {"sent": 0, "failed": 0, "skipped": 0}

    for content in pending:
        slug = content.group_slug.value
        chat_id = group_chat_ids.get(slug)

        if not chat_id:
            logger.warning("No chat_id for group %s, skipping content #%d", slug, content.id)
            stats["skipped"] += 1
            continue

        success = await send_content_to_group(bot, content, chat_id)
        if success:
            stats["sent"] += 1
        else:
            stats["failed"] += 1

        await asyncio.sleep(POST_INTERVAL_SECONDS)

    logger.info("Scheduled posts processed: %s", stats)
    return stats


def _build_retention_prompt(
    username: str,
    days_left: float,
    package_name: str | None = None,
) -> list[dict[str, str]]:
    """สร้าง prompt สำหรับ retention message ที่ personalized."""
    system_msg = (
        "คุณคือ 'มิน' Content Creator ของบริษัทเจริญพร\n"
        "เขียนข้อความ retention สำหรับให้แพร(Sales Bot) ส่งให้สมาชิกที่ใกล้หมดอายุ\n\n"
        "กฎ:\n"
        "- ภาษาไทย น้ำเสียงเป็นกันเอง อบอุ่น\n"
        "- เรียกชื่อสมาชิก\n"
        "- บอกว่าเหลือกี่วัน\n"
        "- บอก highlight ของ content ที่จะมาในสัปดาห์นี้\n"
        "- ชวนต่ออายุ\n"
        "- ห้ามกดดัน ให้รู้สึกว่าเป็นการดูแล\n"
        "- ความยาว 3-5 บรรทัด\n"
        "- ห้ามใส่ URL\n"
    )

    user_msg = (
        f"เขียนข้อความ retention สำหรับ:\n"
        f"ชื่อ: {username}\n"
        f"เหลืออีก: {days_left:.0f} วัน\n"
    )
    if package_name:
        user_msg += f"แพ็กเกจ: {package_name}\n"

    return [
        {"role": "system", "content": system_msg},
        {"role": "user", "content": user_msg},
    ]


async def generate_retention_message(
    username: str,
    days_left: float,
    package_name: str | None = None,
) -> str:
    """สร้างข้อความ retention personalized ด้วย AI."""
    messages = _build_retention_prompt(username, days_left, package_name)

    response = await call_openrouter(
        model=MODEL,
        messages=messages,
        caller=CALLER,
        temperature=0.8,
        max_tokens=300,
        metadata={"username": username, "days_left": days_left},
    )

    msg = response["choices"][0]["message"]["content"].strip()
    logger.info("Generated retention message for %s (%d days left)", username, days_left)
    return msg


async def prepare_retention_messages() -> list[dict[str, Any]]:
    """เตรียมข้อความ retention สำหรับ 08:50 ให้แพร(Sales Bot) ส่ง.

    ดึงสมาชิกที่จะหมดอายุใน 3 วัน และสร้างข้อความ personalized ให้แต่ละคน
    """
    expiring_users = await get_expiring_users(days=3)

    if not expiring_users:
        logger.info("No expiring users found for retention")
        return []

    retention_messages = []
    for user_data in expiring_users:
        username = user_data.get("username") or f"สมาชิก #{user_data['telegram_id']}"
        days_left = user_data["days_left"]

        message = await generate_retention_message(
            username=username,
            days_left=days_left,
        )

        retention_messages.append({
            "telegram_id": user_data["telegram_id"],
            "user_id": user_data["user_id"],
            "username": username,
            "days_left": days_left,
            "message": message,
            "subscription_id": user_data["subscription_id"],
        })

    logger.info("Prepared %d retention messages", len(retention_messages))
    return retention_messages


async def should_run_now(task: str) -> bool:
    """ตรวจสอบว่าถึงเวลา run task หรือยัง."""
    now = _now_th()
    current_time = now.time()

    if task == "retention":
        target = RETENTION_PREP_TIME_TH
        return (
            target.hour == current_time.hour
            and target.minute <= current_time.minute < target.minute + 5
        )

    if task == "post":
        for t in POST_TIMES_TH:
            if (
                t.hour == current_time.hour
                and t.minute <= current_time.minute < t.minute + 5
            ):
                return True

    return False


async def run_scheduler_tick(bot: Any) -> dict[str, Any]:
    """รัน scheduler 1 รอบ เรียกจาก main loop ทุกนาที."""
    results: dict[str, Any] = {"timestamp": _now_th().isoformat(), "actions": []}

    if await should_run_now("retention"):
        logger.info("Running retention message preparation (08:50)")
        messages = await prepare_retention_messages()
        results["actions"].append({
            "type": "retention_prep",
            "count": len(messages),
            "messages": messages,
        })

    if await should_run_now("post"):
        logger.info("Running scheduled post processing")
        stats = await process_scheduled_posts(bot)
        results["actions"].append({
            "type": "scheduled_posts",
            "stats": stats,
        })

    return results


async def run_scheduler_loop(bot: Any, interval_seconds: int = 60) -> None:
    """Main scheduler loop - รันต่อเนื่อง ตรวจทุก 1 นาที."""
    logger.info("Content Scheduler started (interval=%ds)", interval_seconds)

    while True:
        try:
            result = await run_scheduler_tick(bot)
            if result["actions"]:
                logger.info("Scheduler tick result: %s", result)
        except Exception as exc:
            logger.error("Scheduler tick error: %s", exc, exc_info=True)

        await asyncio.sleep(interval_seconds)
