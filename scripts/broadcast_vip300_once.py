#!/usr/bin/env python3
"""One-shot VIP 300 morning broadcast — runs from cron at 06:00 Bangkok.

Sends to:
  • DM: customers who joined the bot in last 30 days + never paid
  • DM: customers who bought only TIER_100 (haven't upgraded)
  • Group posts: 14 FREE groups (FREE3..FREE18)

Self-cleanup: after queueing, comments out its own crontab entry so it
won't fire again next year.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import sys
from datetime import datetime

import asyncpg
import telegram as tg
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import RetryAfter, TimedOut, NetworkError, Forbidden, BadRequest

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("vip300")

# ─── Config ───
PROMO_IMAGE = "/root/charoenpon/assets/vip300_promo.png"

MESSAGE_TEXT = (
    "🔥 <b>VIP 300 — ห้องเดียวคุ้มสุด!</b>\n\n"
    "📦 30 วันเต็ม / 1 ห้อง\n"
    "🎬 ในห้องมี:\n"
    "  • งานทางบ้าน 🏠\n"
    "  • แอบถ่าย 🤫\n"
    "  • นักเรียน 🎒\n\n"
    "✅ อัปคลิปใหม่ทุกวัน ไม่ตัด ไม่เบลอ\n"
    "✅ ดูได้ทั้งมือถือ + คอม\n\n"
    "วันละ 10 บาท = 1 ขวดน้ำเปล่า\n"
    "แต่ดูได้เต็ม 30 วัน 🤤\n\n"
    "🚀 กดปุ่มสมัครได้เลย"
)

INLINE_BUTTONS = [[
    {"text": "🚀 สมัคร VIP 300 ตอนนี้",
     "url": "https://t.me/NamwarnJarern_bot?start=vip300"}
]]

FREE_GROUPS = [
    "FREE3", "FREE4", "FREE5", "FREE6", "FREE7", "FREE8",
    "FREE10", "FREE12", "FREE13", "FREE14", "FREE15",
    "FREE16", "FREE17", "FREE18",
]

# ─── Audience query ───

AUDIENCE_SQL = """
SELECT DISTINCT u.telegram_id
FROM users u
WHERE u.is_banned = FALSE
  AND u.is_blocked_bot = FALSE
  AND u.telegram_id IS NOT NULL
  AND (
    -- A: joined in last 30 days + never paid
    (
      u.created_at > NOW() - INTERVAL '30 days'
      AND NOT EXISTS (
        SELECT 1 FROM payments p WHERE p.user_id = u.id AND p.status = 'CONFIRMED'
      )
    )
    OR
    -- B: bought tier_100 only — has not upgraded yet
    (
      EXISTS (
        SELECT 1 FROM payments p JOIN packages pk ON pk.id = p.package_id
        WHERE p.user_id = u.id AND p.status = 'CONFIRMED' AND pk.tier = 'TIER_100'
      )
      AND NOT EXISTS (
        SELECT 1 FROM payments p JOIN packages pk ON pk.id = p.package_id
        WHERE p.user_id = u.id AND p.status = 'CONFIRMED'
        AND pk.tier IN ('TIER_300','TIER_500','TIER_1299','TIER_2499','TIER_ADD500')
      )
    )
  )
ORDER BY u.telegram_id
"""


# ─── Helpers ───

def _parse_database_url():
    """Extract user/password/host/port/db from DATABASE_URL if individual vars not set."""
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        return {}
    import re
    # postgresql+asyncpg://user:pass@host:port/db
    m = re.match(r".*://([^:]+):([^@]+)@([^:/]+)(?::(\d+))?/(\w+)", url)
    if not m:
        return {}
    return {
        "user": m.group(1),
        "password": m.group(2),
        "host": m.group(3),
        "port": int(m.group(4) or 5432),
        "database": m.group(5),
    }


async def db_pool():
    parsed = _parse_database_url()
    return await asyncpg.create_pool(
        host=os.environ.get("DB_HOST") or parsed.get("host", "127.0.0.1"),
        port=int(os.environ.get("DB_PORT") or parsed.get("port", 5432)),
        database=os.environ.get("DB_NAME") or parsed.get("database", "charoenpon"),
        user=os.environ.get("DB_USER") or parsed.get("user", "postgres"),
        password=(os.environ.get("DB_PASSWORD")
                  or os.environ.get("POSTGRES_PASSWORD")
                  or parsed.get("password", "")),
        min_size=1, max_size=4, command_timeout=30,
    )


async def queue_dm_broadcast(pool, target_ids: list[int], photo_b64: str) -> int:
    """Insert one row in `broadcasts` — broadcast-worker picks it up."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO broadcasts (
              message_text, target_type, target_value, total_count, success_count,
              failed_count, sent_by, sent_by_username, status,
              target_user_ids, parse_mode, media_type, inline_buttons, photo_b64
            ) VALUES (
              $1, $2, $3, $4, 0, 0, $5, $6, 'PENDING',
              $7, 'HTML', 'photo', $8, $9
            )
            RETURNING id
            """,
            MESSAGE_TEXT,
            "custom_ids",
            f"vip300_morning_{len(target_ids)}",
            len(target_ids),
            8502597269,
            "boss_auto",
            json.dumps(target_ids),
            json.dumps(INLINE_BUTTONS),
            photo_b64,
        )
        return row["id"]


async def post_to_groups(pool, photo_b64: str):
    """Post to FREE groups via Sales Bot directly (concurrent, fast)."""
    token = os.environ.get("BOT_TOKEN") or os.environ.get("SALES_BOT_TOKEN", "")
    if not token:
        log.error("BOT_TOKEN missing — skipping group posts")
        return {"ok": 0, "fail": 0}

    # Map slugs → chat_id
    chat_ids = {}
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT slug::text AS slug, chat_id FROM group_registry "
            "WHERE slug::text = ANY($1) AND is_active = TRUE",
            FREE_GROUPS,
        )
        for r in rows:
            chat_ids[r["slug"]] = r["chat_id"]

    kb = InlineKeyboardMarkup(
        [[InlineKeyboardButton(b["text"], url=b["url"]) for b in row]
         for row in INLINE_BUTTONS]
    )

    bot = tg.Bot(token=token)
    await bot.initialize()
    photo_bytes = base64.b64decode(photo_b64)
    import io as _io

    ok = 0
    fail = 0
    try:
        for slug in FREE_GROUPS:
            cid = chat_ids.get(slug)
            if not cid:
                log.warning("slug %s not in registry — skip", slug)
                continue
            try:
                buf = _io.BytesIO(photo_bytes)
                buf.name = "vip300.png"
                await bot.send_photo(
                    chat_id=cid, photo=buf,
                    caption=MESSAGE_TEXT, parse_mode="HTML",
                    reply_markup=kb,
                )
                ok += 1
                log.info("✓ posted to %s (chat_id=%s)", slug, cid)
                await asyncio.sleep(1.0)  # gentle pacing for groups
            except RetryAfter as e:
                log.warning("Group %s rate-limited %ss, waiting...", slug, e.retry_after)
                await asyncio.sleep(float(e.retry_after) + 1)
                # retry once
                try:
                    buf = _io.BytesIO(photo_bytes)
                    buf.name = "vip300.png"
                    await bot.send_photo(chat_id=cid, photo=buf,
                                         caption=MESSAGE_TEXT, parse_mode="HTML",
                                         reply_markup=kb)
                    ok += 1
                except Exception as e2:
                    log.error("✗ %s retry failed: %s", slug, e2)
                    fail += 1
            except Exception as e:
                log.error("✗ %s failed: %s", slug, e)
                fail += 1
    finally:
        try: await bot.shutdown()
        except Exception: pass
    return {"ok": ok, "fail": fail}


def self_disable_crontab():
    """Comment out our crontab line so it won't fire next year."""
    try:
        import subprocess
        marker = "broadcast_vip300_once.py"
        cur = subprocess.run(["crontab", "-l"], capture_output=True, text=True).stdout
        new = "\n".join(
            (f"# disabled (one-shot done): {ln}" if (marker in ln and not ln.lstrip().startswith("#"))
             else ln)
            for ln in cur.splitlines()
        )
        if cur != new:
            p = subprocess.run(["crontab", "-"], input=new, text=True)
            log.info("crontab line self-disabled (rc=%s)", p.returncode)
    except Exception as e:
        log.warning("self-disable failed: %s", e)


# ─── Main ───

async def main():
    log.info("=== VIP 300 morning broadcast START ===")
    started = datetime.utcnow()

    # Encode promo image once
    with open(PROMO_IMAGE, "rb") as f:
        photo_b64 = base64.b64encode(f.read()).decode("utf-8")
    log.info("Promo image encoded: %d bytes (b64=%d chars)",
             os.path.getsize(PROMO_IMAGE), len(photo_b64))

    pool = await db_pool()
    try:
        # Get audience
        async with pool.acquire() as conn:
            rows = await conn.fetch(AUDIENCE_SQL)
        target_ids = [r["telegram_id"] for r in rows]
        log.info("DM target: %d unique users", len(target_ids))
        if not target_ids:
            log.warning("Target list empty — aborting DM broadcast")
            return

        # Queue DM broadcast
        bid = await queue_dm_broadcast(pool, target_ids, photo_b64)
        log.info("DM broadcast row id=%s (PENDING) — worker will start picking up", bid)

        # Post to groups
        log.info("Posting to %d FREE groups...", len(FREE_GROUPS))
        group_result = await post_to_groups(pool, photo_b64)
        log.info("Groups posted: ok=%s fail=%s", group_result["ok"], group_result["fail"])

        # Audit
        try:
            async with pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO admin_logs (admin_id, action, target_type, target_id, details, created_at)
                    VALUES ($1, 'broadcast_scheduled', 'broadcast', $2, $3, NOW())
                    """,
                    8502597269, bid,
                    f"VIP 300 morning: dm_target={len(target_ids)} "
                    f"groups_ok={group_result['ok']} groups_fail={group_result['fail']}",
                )
        except Exception as e:
            log.warning("audit log failed: %s", e)
    finally:
        await pool.close()

    elapsed = (datetime.utcnow() - started).total_seconds()
    log.info("=== DONE in %.1fs ===", elapsed)
    self_disable_crontab()


if __name__ == "__main__":
    asyncio.run(main())
