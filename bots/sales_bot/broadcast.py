"""Safe Broadcast - Telegram rate-limit aware message sender.

Rate limits:
- 30 messages/second to different chats
- 20 messages/minute to the same group
- Handle RetryAfter, Forbidden, ChatNotFound
- Mark blocked/not_started users
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select, update
from telegram import Bot
from telegram.error import BadRequest, Forbidden, RetryAfter

from shared.database import get_session
from shared.models import User

logger = logging.getLogger(__name__)


@dataclass
class BroadcastResult:
    """Result of a broadcast operation."""

    total: int = 0
    sent: int = 0
    failed: int = 0
    blocked: int = 0
    not_found: int = 0
    errors: list[dict[str, Any]] = field(default_factory=list)


# --- Per-group rate tracking ---
_group_send_times: dict[int, list[float]] = {}

GROUP_RATE_LIMIT = 20  # messages per minute
GROUP_RATE_WINDOW = 60.0  # seconds
GLOBAL_RATE_LIMIT = 30  # messages per second


async def _wait_for_group_rate(chat_id: int) -> None:
    """Wait if necessary to respect the 20 msg/min per group limit."""
    if chat_id not in _group_send_times:
        _group_send_times[chat_id] = []

    now = time.monotonic()
    # Remove timestamps older than the window
    _group_send_times[chat_id] = [
        t for t in _group_send_times[chat_id] if now - t < GROUP_RATE_WINDOW
    ]

    if len(_group_send_times[chat_id]) >= GROUP_RATE_LIMIT:
        oldest = _group_send_times[chat_id][0]
        wait_time = GROUP_RATE_WINDOW - (now - oldest) + 0.5
        if wait_time > 0:
            logger.info("Group %s rate limit hit, waiting %.1fs", chat_id, wait_time)
            await asyncio.sleep(wait_time)

    _group_send_times[chat_id].append(time.monotonic())


async def _mark_user_blocked(telegram_id: int) -> None:
    """Mark a user as banned (blocked bot) in the database."""
    async with get_session() as session:
        await session.execute(
            update(User)
            .where(User.telegram_id == telegram_id)
            .values(is_banned=True)
        )


async def _send_single(
    bot: Bot,
    chat_id: int,
    text: str,
    parse_mode: str | None = "HTML",
    **kwargs: Any,
) -> dict[str, Any]:
    """Send a single message with error handling.

    Returns a dict with status: "sent", "blocked", "not_found", "error".
    """
    try:
        await bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode=parse_mode,
            **kwargs,
        )
        return {"chat_id": chat_id, "status": "sent"}

    except RetryAfter as e:
        wait = e.retry_after + 1
        logger.warning("RetryAfter for %s: waiting %ds", chat_id, wait)
        await asyncio.sleep(wait)
        # Retry once after waiting
        try:
            await bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode=parse_mode,
                **kwargs,
            )
            return {"chat_id": chat_id, "status": "sent"}
        except Exception as exc2:
            logger.error("Retry failed for %s: %s", chat_id, exc2)
            return {"chat_id": chat_id, "status": "error", "error": str(exc2)}

    except Forbidden:
        logger.info("User %s blocked the bot", chat_id)
        await _mark_user_blocked(chat_id)
        return {"chat_id": chat_id, "status": "blocked"}

    except BadRequest as e:
        err_msg = str(e).lower()
        if "chat not found" in err_msg or "not started" in err_msg:
            logger.info("Chat %s not found / bot not started", chat_id)
            await _mark_user_blocked(chat_id)
            return {"chat_id": chat_id, "status": "not_found"}
        logger.error("BadRequest sending to %s: %s", chat_id, e)
        return {"chat_id": chat_id, "status": "error", "error": str(e)}

    except Exception as exc:
        logger.error("Unexpected error sending to %s: %s", chat_id, exc)
        return {"chat_id": chat_id, "status": "error", "error": str(exc)}


async def safe_broadcast(
    bot: Bot,
    chat_ids: list[int],
    text: str,
    parse_mode: str | None = "HTML",
    is_group: bool = False,
    **kwargs: Any,
) -> BroadcastResult:
    """Broadcast a message to multiple chats respecting Telegram rate limits.

    Args:
        bot: Telegram Bot instance.
        chat_ids: List of chat IDs to send to.
        text: Message text.
        parse_mode: Parse mode (default HTML).
        is_group: If True, apply group rate limiting (20/min).
        **kwargs: Additional kwargs for send_message.

    Returns:
        BroadcastResult with counts of sent/failed/blocked.
    """
    result = BroadcastResult(total=len(chat_ids))

    # Process in batches of GLOBAL_RATE_LIMIT
    for i, chat_id in enumerate(chat_ids):
        # Global rate: 30 msg/sec — sleep every 30 messages
        if i > 0 and i % GLOBAL_RATE_LIMIT == 0:
            await asyncio.sleep(1.0)

        # Per-group rate limit
        if is_group:
            await _wait_for_group_rate(chat_id)

        send_result = await _send_single(bot, chat_id, text, parse_mode, **kwargs)

        status = send_result["status"]
        if status == "sent":
            result.sent += 1
        elif status == "blocked":
            result.blocked += 1
            result.failed += 1
        elif status == "not_found":
            result.not_found += 1
            result.failed += 1
        else:
            result.failed += 1
            result.errors.append(send_result)

    logger.info(
        "Broadcast complete: %d/%d sent, %d blocked, %d not_found, %d errors",
        result.sent,
        result.total,
        result.blocked,
        result.not_found,
        len(result.errors),
    )

    return result


async def broadcast_to_active_users(
    bot: Bot,
    text: str,
    parse_mode: str | None = "HTML",
    **kwargs: Any,
) -> BroadcastResult:
    """Broadcast to all active (non-banned) users."""
    async with get_session() as session:
        stmt = select(User.telegram_id).where(
            User.is_banned == False,  # noqa: E712
        )
        rows = await session.execute(stmt)
        chat_ids = [row[0] for row in rows.all()]

    return await safe_broadcast(bot, chat_ids, text, parse_mode, **kwargs)
