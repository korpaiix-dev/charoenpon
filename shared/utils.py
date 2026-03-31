"""Utility helpers - บริษัทเจริญพร VIP Telegram System."""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from telegram import Bot, Message
from telegram.error import Forbidden, RetryAfter, TimedOut

from shared.database import get_session
from shared.models import (
    AdminLog,
    Payment,
    PaymentStatus,
    Subscription,
    SubscriptionStatus,
    User,
)

logger = logging.getLogger(__name__)

TH_TZ = timezone(timedelta(hours=7))


async def make_bot(token: str) -> Bot:
    """สร้าง Bot instance พร้อม initialize() — ป้องกัน Frozen_method_invalid."""
    bot = Bot(token=token)
    await bot.initialize()
    return bot

THAI_MONTHS = [
    "", "ม.ค.", "ก.พ.", "มี.ค.", "เม.ย.", "พ.ค.", "มิ.ย.",
    "ก.ค.", "ส.ค.", "ก.ย.", "ต.ค.", "พ.ย.", "ธ.ค.",
]


def format_thb(amount: Decimal | float | int) -> str:
    """Format a number as Thai Baht string.

    >>> format_thb(1299)
    '฿1,299.00'
    >>> format_thb(Decimal("300.5"))
    '฿300.50'
    """
    return f"฿{float(amount):,.2f}"


def format_datetime_thai(dt: datetime | None) -> str:
    """Format datetime to Thai readable string.

    >>> from datetime import datetime, timezone
    >>> format_datetime_thai(datetime(2025, 3, 15, 10, 30, tzinfo=timezone.utc))
    '15 มี.ค. 2568 17:30 น.'
    """
    if dt is None:
        return "-"
    # Convert to Thai timezone
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt_th = dt.astimezone(TH_TZ)
    # Buddhist era = Gregorian + 543
    be_year = dt_th.year + 543
    month = THAI_MONTHS[dt_th.month]
    return f"{dt_th.day} {month} {be_year} {dt_th.strftime('%H:%M')} น."


async def safe_send_message(
    bot: Bot,
    chat_id: int,
    text: str,
    max_retries: int = 3,
    parse_mode: str | None = "HTML",
    **kwargs: Any,
) -> Message | None:
    """Send a Telegram message with retry on transient errors.

    Handles RetryAfter (rate limit), TimedOut, and silently skips
    Forbidden (user blocked bot).
    """
    import asyncio

    for attempt in range(1, max_retries + 1):
        try:
            return await bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode=parse_mode,
                **kwargs,
            )
        except RetryAfter as e:
            wait = e.retry_after + 1
            logger.warning(
                "Rate limited sending to %s, waiting %ds (attempt %d/%d)",
                chat_id, wait, attempt, max_retries,
            )
            await asyncio.sleep(wait)
        except TimedOut:
            logger.warning(
                "Timeout sending to %s (attempt %d/%d)",
                chat_id, attempt, max_retries,
            )
            if attempt < max_retries:
                await asyncio.sleep(2 * attempt)
        except Forbidden:
            logger.info("User %s blocked the bot, skipping", chat_id)
            return None
        except Exception as exc:
            logger.error(
                "Unexpected error sending to %s: %s (attempt %d/%d)",
                chat_id, exc, attempt, max_retries,
            )
            if attempt < max_retries:
                await asyncio.sleep(2 * attempt)

    logger.error("Failed to send message to %s after %d attempts", chat_id, max_retries)
    return None


async def log_admin_action(
    admin_id: int,
    action: str,
    target_type: str | None = None,
    target_id: int | None = None,
    details: str | None = None,
    ip_address: str | None = None,
) -> AdminLog:
    """Log an admin action to the database."""
    entry = AdminLog(
        admin_id=admin_id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        details=details,
        ip_address=ip_address,
    )
    async with get_session() as session:
        session.add(entry)
        await session.flush()
        await session.refresh(entry)
    return entry


async def get_expiring_users(days: int = 3) -> list[dict[str, Any]]:
    """Get users whose subscriptions expire within N days.

    Returns list of dicts with: user_id, telegram_id, username, package_name,
    end_date, days_left.
    """
    now = datetime.utcnow()
    cutoff = now + timedelta(days=days)

    async with get_session() as session:
        result = await session.execute(
            select(Subscription, User)
            .join(User, Subscription.user_id == User.id)
            .where(
                Subscription.status == SubscriptionStatus.ACTIVE,
                Subscription.end_date >= now,
                Subscription.end_date <= cutoff,
            )
            .order_by(Subscription.end_date.asc())
        )
        rows = result.all()

    expiring = []
    for sub, user in rows:
        days_left = (sub.end_date - now).total_seconds() / 86400
        expiring.append({
            "user_id": user.id,
            "telegram_id": user.telegram_id,
            "username": user.username,
            "subscription_id": sub.id,
            "package_id": sub.package_id,
            "end_date": sub.end_date,
            "days_left": round(days_left, 1),
        })

    return expiring


def _compute_slip_hash(file_id_or_link: str) -> str:
    """Create a deterministic hash from a slip file ID or URL."""
    return hashlib.sha256(file_id_or_link.encode()).hexdigest()


async def check_duplicate_slip(file_id_or_link: str) -> Payment | None:
    """Check if a slip (by file_id or URL) has already been used for a payment.

    Returns the existing Payment if duplicate found, None otherwise.
    """
    slip_hash = _compute_slip_hash(file_id_or_link)

    async with get_session() as session:
        result = await session.execute(
            select(Payment).where(
                Payment.slip_hash == slip_hash,
                Payment.status.in_([PaymentStatus.CONFIRMED, PaymentStatus.PENDING]),
            )
        )
        return result.scalar_one_or_none()


def compute_slip_hash(file_id_or_link: str) -> str:
    """Public interface for computing slip hash — use when creating Payment records."""
    return _compute_slip_hash(file_id_or_link)
