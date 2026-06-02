#!/usr/bin/env python3
"""broadcast_campaign.py — ส่ง campaign image+caption ไปทุก channel.

Usage:
    python3 broadcast_campaign.py <campaign_name> [--groups] [--dm-warm] [--pin]

Examples:
    # ส่ง Flash Sale ไปทุก กลุ่มฟรี + DM warm users
    python3 broadcast_campaign.py flash1 --groups --dm-warm

    # ส่ง Referral ไปเฉพาะกลุ่มฟรี
    python3 broadcast_campaign.py referral --groups

    # ส่ง Welcome update + pin ใน sale bot
    python3 broadcast_campaign.py welcome --pin

Audiences:
    --groups   : กลุ่มฟรี 19 กลุ่ม (TG_GROUP_* in .env)
    --dm-warm  : DM ลูกค้า ever_paid (404 คน) — safe ban risk = 0
    --pin      : Update sale bot welcome image
"""
import argparse
import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import asyncpg
from telegram import Bot
from telegram.error import Forbidden, RetryAfter, TimedOut

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger("broadcast_campaign")

# ─── Config ────────────────────────────────────────────────────────────────
ASSETS_DIR = Path("/root/charoenpon/assets/campaigns")
ENV_FILE = "/root/charoenpon/.env"

CAMPAIGN_MAP = {
    "welcome":  ("01_welcome.png",  "🎉 ยินดีต้อนรับสู่ VIP เจริญพร — คลิป HD 10,000+ ชิ้น อัพเดททุกวัน!"),
    "referral": ("02_referral.png", "🎁 ชวนเพื่อนมา VIP เจริญพร — ได้ +7 วัน VIP ฟรี (= ฿100)\nครบ 3 คน รับ VIP 30 วันฟรี!"),
    "flash1":   ("03_flash1.png",   "⚡ FLASH SALE 48 ชม.! ลดทุก tier 30%\n🔥 VIP ฿199 | OF+VIP ฿349 | GOD ฿999"),
    "flash2":   ("04_flash2.png",   "🎁 BONUS DAYS! ซื้อตอนนี้ +7 วัน VIP ฟรี\n💎 VIP/OF +7 | GOD +14"),
    "winback":  ("05_winback.png",  "💔 ต้อนรับกลับมา! ส่วนลดเฉพาะคุณ -30%\n💎 VIP 30 วัน ฿210 เท่านั้น (จาก ฿300)"),
}

RATE_DM_PER_MIN = 100   # safe under telegram limit
RATE_GROUP_PER_MIN = 30 # don't spam groups


def load_env() -> dict:
    """Parse .env file."""
    env = {}
    with open(ENV_FILE) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            v = v.strip().strip('"').strip("'")
            env[k.strip()] = v
    return env


async def get_group_ids(env: dict) -> list[int]:
    """Get all กลุ่มฟรี chat IDs from .env (TG_GROUP_*)."""
    ids = []
    for k, v in env.items():
        if k.startswith("TG_GROUP_") and v.lstrip("-").isdigit():
            ids.append(int(v))
    return ids


async def get_warm_user_ids(db_url: str) -> list[int]:
    """Get ever_paid telegram_ids — safe DM audience."""
    conn = await asyncpg.connect(db_url)
    try:
        rows = await conn.fetch(
            """SELECT DISTINCT u.telegram_id FROM users u
               WHERE EXISTS (SELECT 1 FROM payments p WHERE p.user_id = u.telegram_id AND p.status='CONFIRMED')
               ORDER BY u.telegram_id"""
        )
        return [r["telegram_id"] for r in rows]
    finally:
        await conn.close()


async def send_to_chats(bot: Bot, chat_ids: list[int], img_path: Path, caption: str,
                        rate_per_min: int, label: str) -> tuple[int, int]:
    """Send photo+caption to a list of chats; rate-limited; return (sent, failed)."""
    delay = 60.0 / rate_per_min
    sent = failed = 0
    for i, chat_id in enumerate(chat_ids):
        try:
            with open(img_path, "rb") as f:
                await bot.send_photo(chat_id=chat_id, photo=f, caption=caption, parse_mode="HTML")
            sent += 1
            logger.info("%s %d/%d → %s OK", label, i+1, len(chat_ids), chat_id)
        except Forbidden:
            failed += 1
            logger.info("%s %d/%d → %s blocked", label, i+1, len(chat_ids), chat_id)
        except RetryAfter as exc:
            wait = exc.retry_after + 1
            logger.warning("Rate-limited; sleeping %ds", wait)
            await asyncio.sleep(wait)
            try:
                with open(img_path, "rb") as f:
                    await bot.send_photo(chat_id=chat_id, photo=f, caption=caption, parse_mode="HTML")
                sent += 1
            except Exception as exc2:
                failed += 1
                logger.warning("Retry failed: %s", exc2)
        except (TimedOut, Exception) as exc:
            failed += 1
            logger.warning("%s %d/%d → %s ERROR: %s", label, i+1, len(chat_ids), chat_id, exc)
        await asyncio.sleep(delay)
    return sent, failed


async def main():
    p = argparse.ArgumentParser()
    p.add_argument("campaign", choices=list(CAMPAIGN_MAP.keys()))
    p.add_argument("--groups", action="store_true", help="Post to free groups")
    p.add_argument("--dm-warm", action="store_true", help="DM ever-paid users (~400)")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    img_name, caption = CAMPAIGN_MAP[args.campaign]
    img_path = ASSETS_DIR / img_name
    if not img_path.exists():
        logger.error("Image not found: %s", img_path)
        sys.exit(1)

    env = load_env()
    db_url = env.get("DATABASE_URL")
    if db_url and db_url.startswith("postgresql+asyncpg://"):
        db_url = db_url.replace("postgresql+asyncpg://", "postgresql://", 1)

    # Use content_bot token for groups, sales_bot for DM
    content_token = env.get("CONTENT_BOT_TOKEN") or env.get("JARERN4_BOT_TOKEN")
    sales_token = env.get("SALES_BOT_TOKEN") or env.get("NAMWAN_TOKEN")

    if not content_token or not sales_token:
        logger.error("Missing bot tokens in .env (CONTENT_BOT_TOKEN/SALES_BOT_TOKEN)")
        sys.exit(2)

    total_sent = total_failed = 0

    if args.groups:
        group_ids = await get_group_ids(env)
        logger.info("Posting to %d groups", len(group_ids))
        if args.dry_run:
            logger.info("DRY RUN — skipped %d groups", len(group_ids))
        else:
            bot = Bot(content_token)
            s, f = await send_to_chats(bot, group_ids, img_path, caption, RATE_GROUP_PER_MIN, "GROUP")
            total_sent += s
            total_failed += f

    if args.dm_warm:
        warm_ids = await get_warm_user_ids(db_url)
        logger.info("DM'ing %d warm users", len(warm_ids))
        if args.dry_run:
            logger.info("DRY RUN — skipped %d DMs", len(warm_ids))
        else:
            bot = Bot(sales_token)
            s, f = await send_to_chats(bot, warm_ids, img_path, caption, RATE_DM_PER_MIN, "DM")
            total_sent += s
            total_failed += f

    logger.info("DONE — sent=%d failed=%d", total_sent, total_failed)


if __name__ == "__main__":
    asyncio.run(main())
