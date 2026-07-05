"""Orphan subscriptions watchdog.

Detects subscriptions that have NO linked payment row.
This indicates a bug in the create-sub path (typically admin_bot manual approve).
"""
from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


def _conn_str() -> str:
    return os.environ.get("DATABASE_URL", "").replace("postgresql+asyncpg://", "postgresql://")


async def _connect():
    import asyncpg
    return await asyncpg.connect(_conn_str())


async def find_orphan_subs(within_days: int = 7) -> list[dict]:
    """List ACTIVE subscriptions with payment_id IS NULL created within N days.

    GIFT subscriptions (via /gift-sub endpoint) are excluded by joining admin_logs.
    """
    try:
        conn = await _connect()
        try:
            rows = await conn.fetch(
                """
                SELECT s.id AS sub_id, s.user_id, s.start_date, p.tier::text AS tier, p.price,
                       u.telegram_id, u.first_name
                FROM subscriptions s
                JOIN packages p ON p.id = s.package_id
                JOIN users u ON u.id = s.user_id
                WHERE s.payment_id IS NULL
                  AND s.status = 'ACTIVE'
                  AND s.start_date > NOW() - INTERVAL '1 day' * $1
                  AND NOT EXISTS (
                      SELECT 1 FROM admin_logs al
                      WHERE al.action = 'subscription_gift'
                        AND al.target_id = s.id
                  )
                ORDER BY s.start_date DESC
                """,
                int(within_days),
            )
            return [dict(r) for r in rows]
        finally:
            await conn.close()
    except Exception as exc:
        logger.warning("orphan watchdog failed: %s", exc)
        return []


