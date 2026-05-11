#!/usr/bin/env python3
"""Reconcile retention notification acknowledgements.

Logic: If a user RECEIVED a retention DM (expiry_notifications row) and
PAID a confirmed payment within 7 days, mark notif as acknowledged=true.

Runs hourly via cron. Idempotent (only updates rows where ack=false).
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys

import asyncpg

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("reconcile_retention_ack")

DB_HOST = os.environ.get("DB_HOST", "localhost")
DB_PORT = int(os.environ.get("DB_PORT", "5432"))
DB_USER = os.environ.get("DB_USER", "postgres")
DB_PASSWORD = os.environ.get("DB_PASSWORD") or os.environ.get("POSTGRES_PASSWORD", "")
DB_NAME = os.environ.get("DB_NAME", "charoenpon")

RECON_WINDOW_DAYS = int(os.environ.get("RECON_WINDOW_DAYS", "7"))


async def reconcile() -> dict:
    conn = await asyncpg.connect(
        host=DB_HOST, port=DB_PORT, user=DB_USER,
        password=DB_PASSWORD, database=DB_NAME,
    )
    try:
        result = await conn.fetch(
            f"""
            UPDATE expiry_notifications en
            SET acknowledged = true
            FROM payments p
            WHERE en.acknowledged = false
              AND p.user_id = en.user_id
              AND p.status = 'CONFIRMED'
              AND p.created_at >= en.sent_at
              AND p.created_at <= en.sent_at + INTERVAL '{RECON_WINDOW_DAYS} days'
            RETURNING en.id, en.notification_type::text AS t
            """
        )
        updated_by_type = {}
        for row in result:
            t = row["t"]
            updated_by_type[t] = updated_by_type.get(t, 0) + 1

        # Also compute current ack stats for visibility
        stats = await conn.fetch(
            """
            SELECT notification_type::text AS t,
                   COUNT(*) AS total,
                   COUNT(*) FILTER (WHERE acknowledged) AS acked
            FROM expiry_notifications
            WHERE sent_at >= NOW() - INTERVAL '30 days'
            GROUP BY 1
            """
        )
        return {
            "updated": len(result),
            "by_type": updated_by_type,
            "current_stats": [dict(r) for r in stats],
        }
    finally:
        await conn.close()


async def main() -> int:
    try:
        result = await reconcile()
    except Exception as e:
        log.exception("Reconcile failed: %s", e)
        return 2
    log.info("Updated %d rows: %s", result["updated"], result["by_type"])
    log.info("Current 30-day ack stats:")
    for s in result["current_stats"]:
        pct = (s["acked"] * 100.0 / s["total"]) if s["total"] else 0
        log.info("  %s: %d/%d (%.1f%%)", s["t"], s["acked"], s["total"], pct)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
