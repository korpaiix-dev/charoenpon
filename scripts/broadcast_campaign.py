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
ASSETS_DIR = Path(__file__).resolve().parents[1] / "assets" / "campaigns"
ENV_FILE = os.environ.get("ENV_FILE", "/root/charoenpon/.env")

CAMPAIGN_MAP = {
    "welcome":  ('01_welcome.png', '🎉 <b>ยินดีต้อนรับสู่ VIP เจริญพร</b>\n═══════════════\n\n💎 คลิป HD ครบทุกห้อง 10,000+ ชิ้น\n🔥 อัพเดทคลิปใหม่ทุกวัน — ไม่มีโฆษณา\n⚡ เริ่มต้นเพียง ฿300 / 30 วัน\n\n👉 <a href="https://t.me/NamwarnJarern_bot?start=packages">ดูแพ็คเกจทั้งหมด</a>'),
    "referral":  ('02_referral.png', '🎁 <b>ชวนเพื่อนมา VIP เจริญพร</b>\n═══════════════\n\n✨ ชวน 1 คน = +7 วัน VIP <b>ฟรี</b> (มูลค่า ฿100)\n🔥 ครบ 3 คน รับ VIP 30 วัน <b>ฟรี</b>!\n\n✅ ระบบให้ลิ้งชวนเฉพาะของคุณ\n✅ เพื่อนสมัครแพ็คเกจไหนก็ได้\n\n👉 <a href="https://t.me/NamwarnJarern_bot?start=packages">รับลิ้งชวนเพื่อน</a>'),
    "flash1":  ('03_flash1.png', '⚡ <b>FLASH SALE 48 ชั่วโมง</b> ⚡\n═══════════════\n\n🔥 ลดทุก tier — หมดเขตเร็วๆ นี้!\n\n💎 VIP 30 วัน    <s>฿300</s> <b>฿199</b>  (-33%)\n🔥 OF+VIP 30วัน  <s>฿500</s> <b>฿349</b>  (-30%)\n👑 GOD 90 วัน    <s>฿1,299</s> <b>฿999</b> (-23%)\n\n⏰ จำกัดเวลา 48 ชั่วโมง — รีบกดด่วน!\n👉 <a href="https://t.me/NamwarnJarern_bot?start=packages">กดสมัคร Flash Sale</a>'),
    "flash2":  ('04_flash2.png', '🎁 <b>BONUS DAYS — ซื้อตอนนี้รับวันฟรี!</b>\n═══════════════\n\n💎 VIP / OF+VIP รับ <b>+7 วัน ฟรี</b>\n👑 GOD รับ <b>+14 วัน ฟรี</b>\n\n✨ Bonus จำกัดเวลา — สมัครเลย\n👉 <a href="https://t.me/NamwarnJarern_bot?start=packages">รับโบนัส +วันฟรี</a>'),
    "winback":  ('05_winback.png', '💔 <b>ต้อนรับกลับมา!</b>\n═══════════════\n\n🎁 ส่วนลดเฉพาะคุณ <b>-30%</b>\n💎 VIP 30 วัน  <s>฿300</s> <b>฿210</b>\n\n⏰ ส่วนลดนี้หมดอายุใน 48 ชั่วโมง\n👉 <a href="https://t.me/NamwarnJarern_bot?start=packages">รับส่วนลด ฿210</a>'),

    "lucky66":  ("06_lucky66.png", '🍀 <b>LUCKY 6.6 SALE — วันนี้วันเดียว!</b> 🍀\n═══════════════\n\n🔥 ลดสุดทุก tier — 24 ชั่วโมงเท่านั้น!\n\n💎 VIP 30 วัน    <s>฿300</s> <b>฿166</b>  (-45%)\n🔥 OF+VIP 30วัน  <s>฿500</s> <b>฿266</b>  (-47%)\n👑 GOD 90 วัน    <s>฿1,299</s> <b>฿666</b> (-49%)\n🍀 GOD ถาวร      <s>฿2,499</s> <b>฿2,266</b>\n\n✨ Bonus: ทุก tier ได้ <b>+6 วัน ฟรี!</b>\n\n⏰ หมดเขต 23:59 คืนนี้\n👉 <a href="https://t.me/NamwarnJarern_bot?start=packages">กดสมัครเลย — Lucky 6.6</a>'),
    "birthday":  ('07_birthday.png', '🎂 <b>เดือนเกิดเฮียตั๋ง เจริญพร</b> 🎉\n═══════════════\n\n🎁 แจกใหญ่ <b>GOD MODE ถาวร</b>\n💎 มูลค่า ฿2,499 — สิทธิ์ตลอดชีพ\n\n📋 <b>กติกา:</b>\n✅ ซื้อ OF+VIP 30 วัน ฿500\n✅ ระบบเข้าจับฉลากให้อัตโนมัติ\n\n📅 ประกาศผล <b>10 มิ.ย. 18:00 น.</b>\n⏰ ปิดรับสมัคร 10 มิ.ย. 12:00\n\n👉 <a href="https://t.me/NamwarnJarern_bot?start=packages">สมัครเลย — ลุ้น GOD ถาวร</a>'),
}

POST_LOG_PATH = os.environ.get("BROADCAST_POST_LOG", "/tmp/broadcast_posts.jsonl")
def _log_post(campaign: str, chat_id: int, message_id: int) -> None:
    import json as _json, time as _time
    try:
        with open(POST_LOG_PATH, "a", encoding="utf-8") as _f:
            _f.write(_json.dumps({
                "ts": _time.time(), "campaign": campaign,
                "chat_id": chat_id, "message_id": message_id,
            }) + "\n")
    except Exception:
        pass

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
    """Get all กลุ่มฟรี chat IDs from group_registry table (min_tier=FREE, is_active=true)."""
    db_url = env.get("DATABASE_URL", "")
    if db_url.startswith("postgresql+asyncpg://"):
        db_url = db_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    if not db_url:
        # Fallback to .env
        return [int(v) for k, v in env.items()
                if k.startswith("TG_GROUP_") and v.lstrip("-").isdigit()]
    conn = await asyncpg.connect(db_url)
    try:
        rows = await conn.fetch(
            "SELECT chat_id FROM group_registry WHERE min_tier=\'FREE\' AND is_active=true ORDER BY slug"
        )
        return [r["chat_id"] for r in rows]
    finally:
        await conn.close()


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
                _msg = await bot.send_photo(chat_id=chat_id, photo=f, caption=caption, parse_mode="HTML")
                _log_post(os.environ.get("BROADCAST_CAMPAIGN", "unknown"), chat_id, _msg.message_id)
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

    # Set env so _log_post can read campaign name (scope-safe)
    os.environ["BROADCAST_CAMPAIGN"] = args.campaign
    # Phase 3: try shared.captions (DB) first, fall back to CAMPAIGN_MAP
    img_name, caption = None, None
    try:
        from shared.captions import load_caption
        spec = await load_caption(args.campaign)
        if spec and spec.image_path and spec.group_caption:
            # spec.image_path may be absolute (/app/assets/...) — extract basename
            from pathlib import Path as _P
            img_name = _P(spec.image_path).name
            caption = spec.group_caption
            print(f'[caption-hub] loaded from DB: {args.campaign}')
    except Exception as exc:
        print(f'[caption-hub] DB lookup failed ({exc}); using legacy CAMPAIGN_MAP')
    if img_name is None or caption is None:
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
