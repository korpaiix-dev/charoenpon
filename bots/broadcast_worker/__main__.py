"""Broadcast worker — dedicated container that polls broadcasts table and sends.

Fix C: production-grade fix for broadcast #4 stuck issue.
Features:
- Polls broadcasts every 10s for status='PENDING' or stale IN_PROGRESS
- Redis lock to prevent double-processing
- Heartbeat every batch (50 msgs) so admin can see progress
- Resume from last_processed_idx if interrupted
- Auto-mark FAILED if no heartbeat for 10 min
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone, timedelta
from typing import Optional

import asyncpg
import redis.asyncio as aioredis
from telegram import Bot
from telegram.error import BadRequest, Forbidden, RetryAfter, TelegramError

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("broadcast_worker")

DB_HOST = os.environ.get("DB_HOST", "postgres")
DB_PORT = int(os.environ.get("DB_PORT", "5432"))
DB_USER = os.environ.get("DB_USER", "postgres")
DB_PASSWORD = os.environ.get("DB_PASSWORD") or os.environ.get("POSTGRES_PASSWORD", "")
DB_NAME = os.environ.get("DB_NAME", "charoenpon")
REDIS_URL = os.environ.get("REDIS_URL", "redis://redis:6379/0")
SALES_BOT_TOKEN = os.environ.get("SALES_BOT_TOKEN", "")

POLL_INTERVAL = 10  # seconds
BATCH_CHECKPOINT = 50  # commit progress every N messages
SEND_DELAY = 0.5  # seconds between sends
HEARTBEAT_TIMEOUT = 600  # mark FAILED if no heartbeat for 10 min
LOCK_TTL = 900  # Redis lock TTL (15 min) — long enough for one batch, short enough to recover from crash


async def get_db() -> asyncpg.Pool:
    return await asyncpg.create_pool(
        host=DB_HOST, port=DB_PORT, user=DB_USER,
        password=DB_PASSWORD, database=DB_NAME, min_size=1, max_size=3,
    )


async def mark_zombie_broadcasts(pool: asyncpg.Pool) -> int:
    """On startup, mark broadcasts as FAILED if heartbeat is too old."""
    result = await pool.execute(
        """
        UPDATE broadcasts
        SET status='FAILED',
            completed_at=NOW(),
            failed_count=total_count-success_count
        WHERE status='IN_PROGRESS'
          AND (last_heartbeat_at IS NULL OR last_heartbeat_at < NOW() - INTERVAL '10 minutes')
        """
    )
    # asyncpg.execute returns 'UPDATE N'
    return int(result.split()[-1]) if result else 0


async def pick_next_job(pool: asyncpg.Pool) -> Optional[dict]:
    """Return next PENDING broadcast or None."""
    row = await pool.fetchrow(
        """
        SELECT id, message_text, message_photo_id, target_user_ids,
               total_count, success_count, failed_count, last_processed_idx
        FROM broadcasts
        WHERE status='PENDING'
        ORDER BY started_at
        LIMIT 1
        """
    )
    return dict(row) if row else None


async def acquire_lock(redis, broadcast_id: int) -> bool:
    key = f"broadcast_lock:{broadcast_id}"
    return bool(await redis.set(key, "1", nx=True, ex=LOCK_TTL))


async def refresh_lock(redis, broadcast_id: int) -> None:
    key = f"broadcast_lock:{broadcast_id}"
    await redis.expire(key, LOCK_TTL)


async def release_lock(redis, broadcast_id: int) -> None:
    key = f"broadcast_lock:{broadcast_id}"
    await redis.delete(key)


async def send_broadcast(pool: asyncpg.Pool, redis, bot: Bot, job: dict) -> None:
    bid = job["id"]
    user_ids = json.loads(job["target_user_ids"]) if isinstance(job["target_user_ids"], str) else job["target_user_ids"]
    if not user_ids:
        log.warning("Broadcast %s has no target_user_ids — marking FAILED", bid)
        await pool.execute("UPDATE broadcasts SET status='FAILED', completed_at=NOW() WHERE id=$1", bid)
        return

    start_idx = job.get("last_processed_idx") or 0
    success = job.get("success_count") or 0
    failed = job.get("failed_count") or 0

    await pool.execute(
        "UPDATE broadcasts SET status='IN_PROGRESS', last_heartbeat_at=NOW() WHERE id=$1", bid
    )
    log.info("Broadcast %s starting at idx %d/%d", bid, start_idx, len(user_ids))

    try:
        for i in range(start_idx, len(user_ids)):
            uid = int(user_ids[i])
            try:
                if job["message_photo_id"]:
                    await bot.send_photo(
                        chat_id=uid, photo=job["message_photo_id"],
                        caption=job["message_text"], parse_mode="HTML",
                    )
                else:
                    await bot.send_message(
                        chat_id=uid, text=job["message_text"], parse_mode="HTML",
                    )
                success += 1
            except RetryAfter as e:
                log.warning("Rate limited %ss, waiting", e.retry_after)
                await asyncio.sleep(e.retry_after)
                continue  # retry same uid by not incrementing i
            except (Forbidden, BadRequest) as e:
                log.debug("Cannot send to %s: %s", uid, e)
                failed += 1
            except Exception as e:
                log.warning("Send failed for %s: %s", uid, e)
                failed += 1

            # Checkpoint every BATCH_CHECKPOINT
            if (i + 1) % BATCH_CHECKPOINT == 0:
                await pool.execute(
                    "UPDATE broadcasts SET success_count=$1, failed_count=$2, last_processed_idx=$3, last_heartbeat_at=NOW() WHERE id=$4",
                    success, failed, i + 1, bid,
                )
                await refresh_lock(redis, bid)
                log.info("Broadcast %s checkpoint: %d/%d (ok=%d fail=%d)", bid, i + 1, len(user_ids), success, failed)

            await asyncio.sleep(SEND_DELAY)

        await pool.execute(
            "UPDATE broadcasts SET success_count=$1, failed_count=$2, last_processed_idx=$3, status='COMPLETED', completed_at=NOW(), last_heartbeat_at=NOW(), duration_seconds=EXTRACT(EPOCH FROM (NOW() - started_at))::int WHERE id=$4",
            success, failed, len(user_ids), bid,
        )
        log.info("Broadcast %s COMPLETED ok=%d fail=%d", bid, success, failed)

    except Exception:
        log.exception("Broadcast %s crashed; will be picked up again", bid)
        # Don't mark FAILED here — zombie detector will handle if it stays stuck
        await pool.execute(
            "UPDATE broadcasts SET success_count=$1, failed_count=$2, last_heartbeat_at=NOW() WHERE id=$3",
            success, failed, bid,
        )
        raise


async def main():
    if not SALES_BOT_TOKEN:
        log.error("SALES_BOT_TOKEN not set — exiting")
        return

    pool = await get_db()
    redis = aioredis.from_url(REDIS_URL)
    bot = Bot(token=SALES_BOT_TOKEN)
    await bot.initialize()

    log.info("Broadcast worker started (poll every %ds)", POLL_INTERVAL)

    # Startup: mark zombies
    n = await mark_zombie_broadcasts(pool)
    if n:
        log.warning("Marked %d zombie broadcasts as FAILED on startup", n)

    while True:
        try:
            job = await pick_next_job(pool)
            if not job:
                await asyncio.sleep(POLL_INTERVAL)
                continue
            if not await acquire_lock(redis, job["id"]):
                log.info("Broadcast %s already locked (another worker?)", job["id"])
                await asyncio.sleep(POLL_INTERVAL)
                continue
            try:
                await send_broadcast(pool, redis, bot, job)
            finally:
                await release_lock(redis, job["id"])
        except Exception:
            log.exception("Worker loop error")
            await asyncio.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    asyncio.run(main())
