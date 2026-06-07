"""Broadcast worker — postgres-only (no Redis).

Polls `broadcasts` table for PENDING/IN_PROGRESS jobs, sends messages
in batches with checkpoint progress, and handles zombie recovery via
heartbeat staleness.

Concurrency model
-----------------
Single worker locks a job atomically with `SELECT ... FOR UPDATE SKIP LOCKED`
inside a short transaction that flips status PENDING -> IN_PROGRESS. While
the row is held, any other worker skips it. After picking, we release the
row lock and rely on `last_heartbeat_at` to detect zombies (container
killed mid-send) — a stale IN_PROGRESS job becomes pickable again.

Checkpoint
----------
Every CHECKPOINT_EVERY messages we UPDATE last_processed_idx + counters
+ heartbeat so a restart resumes from the last good index, not from 0.

Run
---
    python -m bots.broadcast_worker
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import sys
from datetime import datetime, timedelta, timezone

import asyncpg
from telegram import Bot
from telegram.error import (
    BadRequest,
    Forbidden,
    NetworkError,
    RetryAfter,
    TimedOut,
)

# --- Config ---
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
DB_HOST = os.environ.get("DB_HOST", "postgres").strip()
DB_PORT = int(os.environ.get("DB_PORT", "5432"))
DB_NAME = os.environ.get("DB_NAME", "charoenpon").strip()
DB_USER = os.environ.get("DB_USER", "postgres").strip()
DB_PASSWORD = os.environ.get("DB_PASSWORD", "").strip()

POLL_INTERVAL_SEC = int(os.environ.get("BROADCAST_POLL_SEC", "5"))
CHECKPOINT_EVERY = int(os.environ.get("BROADCAST_CHECKPOINT_EVERY", "50"))
SEND_DELAY_SEC = float(os.environ.get("BROADCAST_SEND_DELAY_SEC", "4.0"))  # FIX 2026-05-24: safe default)
HEARTBEAT_STALE_MIN = int(os.environ.get("BROADCAST_HEARTBEAT_STALE_MIN", "5"))

# --- Logging ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("broadcast_worker")

_shutdown = asyncio.Event()


# --- DB helpers ---
async def make_pool() -> asyncpg.Pool:
    return await asyncpg.create_pool(
        host=DB_HOST,
        port=DB_PORT,
        database=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD,
        min_size=1,
        max_size=4,
        command_timeout=30,
    )


async def pick_next_job(pool: asyncpg.Pool) -> dict | None:
    """Atomically pick the next runnable broadcast job.

    Picks either:
      - status='PENDING'
      - or status='IN_PROGRESS' with stale heartbeat (zombie recovery)

    Uses SELECT ... FOR UPDATE SKIP LOCKED inside a transaction so two
    workers can't pick the same row.
    """
    stale_cutoff = datetime.utcnow() - timedelta(minutes=HEARTBEAT_STALE_MIN)
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """
                SELECT id, message_text, message_photo_id, target_user_ids, last_processed_idx,
                       success_count, failed_count, status,
                       inline_buttons, photo_b64, parse_mode
                FROM broadcasts
                WHERE status = 'PENDING'
                   OR (status = 'IN_PROGRESS' AND
                       (last_heartbeat_at IS NULL OR last_heartbeat_at < $1))
                ORDER BY id ASC
                FOR UPDATE SKIP LOCKED
                LIMIT 1
                """,
                stale_cutoff,
            )
            if row is None:
                return None
            await conn.execute(
                """
                UPDATE broadcasts
                SET status='IN_PROGRESS',
                    last_heartbeat_at=NOW(),
                    started_at=COALESCE(started_at, NOW())
                WHERE id=$1
                """,
                row["id"],
            )
            return dict(row)


async def checkpoint(
    pool: asyncpg.Pool,
    bid: int,
    idx: int,
    ok: int,
    fail: int,
) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE broadcasts
            SET last_processed_idx=$2,
                success_count=$3,
                failed_count=$4,
                last_heartbeat_at=NOW()
            WHERE id=$1
            """,
            bid, idx, ok, fail,
        )


async def heartbeat(pool: asyncpg.Pool, bid: int) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE broadcasts SET last_heartbeat_at=NOW() WHERE id=$1",
            bid,
        )


async def finish_job(
    pool: asyncpg.Pool,
    bid: int,
    ok: int,
    fail: int,
    status: str = "COMPLETED",
) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE broadcasts
            SET status=$2,
                success_count=$3,
                failed_count=$4,
                completed_at=NOW(),
                last_heartbeat_at=NOW()
            WHERE id=$1
            """,
            bid, status, ok, fail,
        )


# --- Send ---
async def send_one(bot: Bot, user_id: int, text: str, parse_mode: str | None,
                   photo_id: str | None = None, photo_bytes: bytes | None = None,
                   keyboard=None) -> tuple[bool, str]:
    """Returns (ok, reason). reason is empty on success or 'blocked'/'badreq'/'flood'/'net'."""
    try:
        # FIX 2026-05-24: support inline keyboard + raw photo bytes
        if photo_bytes:
            import io
            await bot.send_photo(
                chat_id=user_id,
                photo=io.BytesIO(photo_bytes),
                caption=text,
                parse_mode=parse_mode or "HTML",
                reply_markup=keyboard,
            )
        elif photo_id:
            await bot.send_photo(
                chat_id=user_id,
                photo=photo_id,
                caption=text,
                parse_mode=parse_mode or "HTML",
                reply_markup=keyboard,
            )
        else:
            await bot.send_message(
                chat_id=user_id,
                text=text,
                parse_mode=parse_mode or None,
                disable_web_page_preview=True,
                reply_markup=keyboard,
            )
        return True, ""
    except Forbidden:
        return False, "blocked"
    except BadRequest as e:
        log.warning("BadRequest for %s: %s", user_id, e)
        return False, "badreq"
    except RetryAfter as e:
        log.warning("RetryAfter %ss for %s", e.retry_after, user_id)
        await asyncio.sleep(float(e.retry_after) + 1)
        # caller decides whether to retry — return as transient failure
        return False, "flood"
    except (TimedOut, NetworkError) as e:
        log.warning("Network error for %s: %s", user_id, e)
        return False, "net"
    except Exception as e:  # noqa: BLE001
        log.exception("Unexpected send error for %s: %s", user_id, e)
        return False, "err"


async def run_job(pool: asyncpg.Pool, bot: Bot, job: dict) -> None:
    bid = job["id"]
    text = job["message_text"]
    parse_mode = job.get("parse_mode") or "HTML"
    photo_id = job.get("message_photo_id")
    raw_targets = job["target_user_ids"]
    # FIX 2026-05-24: load photo_b64 + inline_buttons from job
    import base64 as _b64lib
    photo_b64_raw = job.get("photo_b64")
    photo_bytes = _b64lib.b64decode(photo_b64_raw) if photo_b64_raw else None
    keyboard = None
    inline_buttons_raw = job.get("inline_buttons")
    if inline_buttons_raw:
        try:
            from telegram import InlineKeyboardButton, InlineKeyboardMarkup
            btns_data = inline_buttons_raw if isinstance(inline_buttons_raw, (list, dict)) else json.loads(inline_buttons_raw)
            rows = []
            for row in btns_data:
                row_btns = []
                for b in row:
                    if b.get("url"):
                        row_btns.append(InlineKeyboardButton(b["text"], url=b["url"]))
                    elif b.get("callback_data"):
                        row_btns.append(InlineKeyboardButton(b["text"], callback_data=b["callback_data"]))
                rows.append(row_btns)
            keyboard = InlineKeyboardMarkup(rows)
        except Exception as e:
            log.warning("Failed to parse inline_buttons: %s", e)
    if isinstance(raw_targets, str):
        targets = json.loads(raw_targets)
    else:
        targets = raw_targets or []

    start_idx = job.get("last_processed_idx") or 0
    ok = job.get("success_count") or 0
    fail = job.get("failed_count") or 0
    total = len(targets)

    log.info(
        "Job %s: resume from idx=%s/%s (ok=%s fail=%s)",
        bid, start_idx, total, ok, fail,
    )

    # FIX 2026-05-24 (safe): per-job delay override + 429 backoff + failure threshold
    current_delay = SEND_DELAY_SEC
    consecutive_429 = 0
    consecutive_failures = 0
    FAILURE_RATE_THRESHOLD = 0.30  # 30% fail → pause job
    MIN_SAMPLES_BEFORE_CHECK = 50  # don't pause too early

    for idx in range(start_idx, total):
        if _shutdown.is_set():
            log.info("Shutdown signal — checkpoint and exit job %s", bid)
            await checkpoint(pool, bid, idx, ok, fail)
            return

        uid = int(targets[idx])
        # Personalize text if it contains {first_name}
        send_text = text
        if "{first_name}" in send_text:
            # Try fetch first_name from DB
            try:
                async with pool.acquire() as conn:
                    fn_row = await conn.fetchrow("SELECT first_name FROM users WHERE telegram_id=$1", uid)
                fn = (fn_row["first_name"] if fn_row else None) or "คุณ"
                send_text = send_text.replace("{first_name}", fn[:30])
            except Exception:
                send_text = send_text.replace("{first_name}", "คุณ")

        success, reason = await send_one(bot, uid, send_text, parse_mode, photo_id, photo_bytes=photo_bytes, keyboard=keyboard)
        if success:
            ok += 1
            consecutive_failures = 0
            if consecutive_429 > 0:
                consecutive_429 = max(0, consecutive_429 - 1)
        else:
            fail += 1
            consecutive_failures += 1
            if reason == "flood":  # 429
                consecutive_429 += 1
                # Exponential backoff: 4s → 8s → 16s → 32s → 60s cap
                current_delay = min(60.0, current_delay * 2)
                log.warning("Job %s — 429 detected (%d consecutive), delay → %.1fs",
                            bid, consecutive_429, current_delay)
                if consecutive_429 >= 5:
                    log.error("Job %s — 5+ consecutive 429s, PAUSING job for safety", bid)
                    await checkpoint(pool, bid, idx + 1, ok, fail)
                    # Pause: mark status=PAUSED, can resume manually
                    async with pool.acquire() as conn:
                        await conn.execute("UPDATE broadcasts SET status='PAUSED' WHERE id=$1", bid)
                    return

        # Failure rate check (after warmup)
        if (idx - start_idx + 1) >= MIN_SAMPLES_BEFORE_CHECK:
            sent_so_far = (idx - start_idx + 1)
            recent_failures = fail - (job.get("failed_count") or 0)
            rate = recent_failures / sent_so_far if sent_so_far else 0
            if rate > FAILURE_RATE_THRESHOLD:
                log.error("Job %s — failure rate %.1f%% > threshold, PAUSING", bid, rate * 100)
                try:
                    from shared.notify import notify as _notify
                    await _notify("broadcast_paused", title=f"⚠️ Broadcast {bid} paused", body=f"Failure rate {rate*100:.1f}% > 30%")
                except Exception:
                    pass
                await checkpoint(pool, bid, idx + 1, ok, fail)
                async with pool.acquire() as conn:
                    await conn.execute("UPDATE broadcasts SET status='PAUSED' WHERE id=$1", bid)
                return

        # checkpoint every N
        if (idx + 1) % CHECKPOINT_EVERY == 0:
            await checkpoint(pool, bid, idx + 1, ok, fail)
            log.info("Job %s checkpoint @ %s/%s ok=%s fail=%s delay=%.1fs", bid, idx + 1, total, ok, fail, current_delay)

        await asyncio.sleep(current_delay)

    await finish_job(pool, bid, ok, fail, status="COMPLETED")
    log.info("Job %s COMPLETED ok=%s fail=%s total=%s", bid, ok, fail, total)


# --- Signal handling ---
def _install_signal_handlers(loop: asyncio.AbstractEventLoop) -> None:
    def _stop(signum):
        log.info("Received signal %s — shutting down gracefully", signum)
        _shutdown.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _stop, sig)
        except NotImplementedError:
            # Windows / non-unix
            signal.signal(sig, lambda s, _f: _stop(s))


# --- Main loop ---
async def main() -> int:
    if not BOT_TOKEN:
        log.error("BOT_TOKEN not set")
        return 2
    if not DB_PASSWORD:
        log.error("DB_PASSWORD not set")
        return 2

    _install_signal_handlers(asyncio.get_running_loop())

    pool = await make_pool()
    bot = Bot(token=BOT_TOKEN)

    log.info(
        "Broadcast worker started — poll=%ss checkpoint=%s delay=%.2fs stale=%smin",
        POLL_INTERVAL_SEC, CHECKPOINT_EVERY, SEND_DELAY_SEC, HEARTBEAT_STALE_MIN,
    )

    try:
        while not _shutdown.is_set():
            try:
                job = await pick_next_job(pool)
            except Exception as e:  # noqa: BLE001
                log.exception("pick_next_job failed: %s", e)
                await asyncio.sleep(POLL_INTERVAL_SEC)
                continue

            if job is None:
                await asyncio.sleep(POLL_INTERVAL_SEC)
                continue

            try:
                await run_job(pool, bot, job)
            except Exception as e:  # noqa: BLE001
                log.exception("Job %s crashed: %s", job["id"], e)
                # leave status=IN_PROGRESS; heartbeat will go stale → another worker picks it up
                await asyncio.sleep(POLL_INTERVAL_SEC)
    finally:
        await pool.close()
        log.info("Worker stopped")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
