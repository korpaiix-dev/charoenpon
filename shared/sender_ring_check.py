"""Sender Ring Detection — Detects scam rings reusing slips across Telegram accounts.

If the same sender_name has been used by >=2 different Telegram accounts in the last
7 days, flag the slip as suspicious and require admin review (block auto-approve).

Whitelisted senders (family transfers): can be configured via env or table.
"""
from __future__ import annotations
import logging
from sqlalchemy import text
from shared.database import get_session

logger = logging.getLogger(__name__)


async def is_sender_ring_suspicious(sender_name: str, current_user_id: int,
                                     window_days: int = 7) -> tuple[bool, list[int]]:
    """Returns (is_suspicious, other_user_ids).

    Suspicious = same sender_name has been used by OTHER user(s) within window.
    """
    if not sender_name or not sender_name.strip():
        return False, []

    async with get_session() as s:
        r = await s.execute(text(f"""
            SELECT DISTINCT user_id FROM payments
            WHERE sender_name = :sender
              AND user_id != :cuid
              AND created_at >= NOW() - interval '{window_days} days'
              AND status IN ('CONFIRMED', 'PENDING', 'REJECTED')
            LIMIT 5
        """), {"sender": sender_name.strip(), "cuid": current_user_id})
        rows = [row[0] for row in r.fetchall()]
        if rows:
            logger.warning(
                "SENDER_RING: sender=%r used by other user_ids=%s (current=%s)",
                sender_name, rows, current_user_id
            )
            return True, rows
    return False, []


__all__ = ["is_sender_ring_suspicious"]
