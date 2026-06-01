"""End-month VIP promo broadcast tool.

Default is dry-run only. Use explicit flags to send:
  python -m bots.sales_bot.endmonth_promo_broadcast --dry-run
  python -m bots.sales_bot.endmonth_promo_broadcast --send-groups
  python -m bots.sales_bot.endmonth_promo_broadcast --send-users

This campaign:
- Group posts: promo image + caption with embedded bot link text "สมัครสมาชิกกดที่นี่"
- User broadcast: promo image + inline URL button to package picker
- Target users: non-banned users with no active subscription
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import text
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest, Forbidden, RetryAfter

from shared.database import get_session, init_db, close_db
from shared.endmonth_vip_promo import (
    PROMO_IMAGE_PATH,
    SALES_BOT_DEEPLINK,
    get_group_promo_caption,
    get_user_broadcast_caption,
    is_endmonth_vip_promo_active,
)

logger = logging.getLogger(__name__)

SALES_BOT_TOKEN = os.environ.get("SALES_BOT_TOKEN", "")
CAMPAIGN_LOG_DIR = Path(os.environ.get("PROMO_CAMPAIGN_LOG_DIR", "/app/logs"))
CAMPAIGN_LOG_PATH = CAMPAIGN_LOG_DIR / "endmonth_vip_promo_user_attempted.txt"

FALLBACK_FREE_GROUPS = [
    -1003733093219,
    -1003772512123,
    -1003706880995,
    -1003740382332,
    -1003861673687,
    -1003841389411,
    -1003723154612,
    -1003981084328,
    -1003805660760,
]


async def get_target_groups() -> list[int]:
    """Prefer DB active FREE groups; fallback to content-bot hardcoded list."""
    try:
        async with get_session() as session:
            result = await session.execute(text("""
                SELECT chat_id
                FROM group_registry
                WHERE slug::text LIKE 'FREE%' AND is_active = true
                ORDER BY slug
            """))
            groups = [int(row.chat_id) for row in result.fetchall()]
            if groups:
                return groups
    except Exception as exc:
        logger.warning("DB group lookup failed, using fallback list: %s", exc)
    return FALLBACK_FREE_GROUPS


async def get_non_member_users() -> list[int]:
    """Users who started the Sales Bot and do not currently have active membership."""
    async with get_session() as session:
        result = await session.execute(text("""
            SELECT u.telegram_id
            FROM users u
            WHERE COALESCE(u.is_banned, false) = false
              AND NOT EXISTS (
                  SELECT 1
                  FROM subscriptions s
                  WHERE s.user_id = u.id
                    AND s.status = 'ACTIVE'
                    AND (s.end_date IS NULL OR s.end_date > NOW())
              )
            ORDER BY u.created_at DESC
        """))
        return [int(row.telegram_id) for row in result.fetchall()]


async def send_groups(bot: Bot, chat_ids: list[int], dry_run: bool) -> tuple[int, int]:
    caption_base = get_group_promo_caption()
    if dry_run:
        logger.info("DRY RUN group promo: %d groups", len(chat_ids))
        logger.info("Sample caption:\n%s", caption_base)
        return 0, 0

    sent = failed = 0
    for idx, chat_id in enumerate(chat_ids):
        caption = get_group_promo_caption(idx)
        try:
            with open(PROMO_IMAGE_PATH, "rb") as image:
                await bot.send_photo(
                    chat_id=chat_id,
                    photo=image,
                    caption=caption,
                    parse_mode="HTML",
                )
            sent += 1
        except RetryAfter as exc:
            await asyncio.sleep(exc.retry_after + 1)
            try:
                with open(PROMO_IMAGE_PATH, "rb") as image:
                    await bot.send_photo(chat_id=chat_id, photo=image, caption=caption, parse_mode="HTML")
                sent += 1
            except Exception as retry_exc:
                failed += 1
                logger.error("Group %s retry failed: %s", chat_id, retry_exc)
        except (Forbidden, BadRequest) as exc:
            failed += 1
            logger.error("Cannot send group promo to %s: %s", chat_id, exc)
        except Exception as exc:
            failed += 1
            logger.error("Group promo failed for %s: %s", chat_id, exc)
        await asyncio.sleep(3)
    return sent, failed


def load_attempted_users() -> set[int]:
    if not CAMPAIGN_LOG_PATH.exists():
        return set()
    attempted: set[int] = set()
    for line in CAMPAIGN_LOG_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            attempted.add(int(line.split("\t", 1)[0]))
        except ValueError:
            continue
    return attempted


def append_attempted_user(chat_id: int, status: str) -> None:
    CAMPAIGN_LOG_DIR.mkdir(parents=True, exist_ok=True)
    with CAMPAIGN_LOG_PATH.open("a", encoding="utf-8") as fh:
        fh.write(f"{chat_id}\t{status}\t{datetime.utcnow().isoformat()}Z\n")
        fh.flush()


async def send_users(bot: Bot, chat_ids: list[int], dry_run: bool, resume: bool = False, skip_first: int = 0) -> tuple[int, int]:
    caption = get_user_broadcast_caption()
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔥 สมัครโปร 200 บาท", url=SALES_BOT_DEEPLINK)]
    ])
    attempted = load_attempted_users() if resume else set()
    if skip_first:
        skipped_by_offset = set(chat_ids[:skip_first])
        attempted.update(skipped_by_offset)
        logger.info("Skipping first %d users by requested offset", skip_first)
    if attempted:
        chat_ids = [chat_id for chat_id in chat_ids if chat_id not in attempted]
        logger.info("Resume mode: skipping %d already-attempted/offset users, remaining=%d", len(attempted), len(chat_ids))
    if dry_run:
        logger.info("DRY RUN user promo: %d users", len(chat_ids))
        logger.info("Sample caption:\n%s", caption)
        return 0, 0

    sent = failed = 0
    for i, chat_id in enumerate(chat_ids):
        if i > 0 and i % 25 == 0:
            await asyncio.sleep(1.5)
        try:
            with open(PROMO_IMAGE_PATH, "rb") as image:
                await bot.send_photo(
                    chat_id=chat_id,
                    photo=image,
                    caption=caption,
                    parse_mode="HTML",
                    reply_markup=keyboard,
                )
            sent += 1
            append_attempted_user(chat_id, "sent")
        except RetryAfter as exc:
            await asyncio.sleep(exc.retry_after + 1)
            try:
                with open(PROMO_IMAGE_PATH, "rb") as image:
                    await bot.send_photo(chat_id=chat_id, photo=image, caption=caption, parse_mode="HTML", reply_markup=keyboard)
                sent += 1
                append_attempted_user(chat_id, "sent_after_retry")
            except Exception as retry_exc:
                failed += 1
                append_attempted_user(chat_id, "failed_after_retry")
                logger.error("User %s retry failed: %s", chat_id, retry_exc)
        except (Forbidden, BadRequest) as exc:
            failed += 1
            append_attempted_user(chat_id, "failed")
            logger.info("Cannot send user promo to %s: %s", chat_id, exc)
        except Exception as exc:
            failed += 1
            append_attempted_user(chat_id, "failed")
            logger.error("User promo failed for %s: %s", chat_id, exc)
    return sent, failed


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Preview targets/counts only")
    parser.add_argument("--send-groups", action="store_true", help="Send promo to free/public groups")
    parser.add_argument("--send-users", action="store_true", help="Broadcast promo to non-member users")
    parser.add_argument("--resume", action="store_true", help="Skip users recorded in campaign attempt log")
    parser.add_argument("--skip-first", type=int, default=0, help="Skip first N users from deterministic target order")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if not SALES_BOT_TOKEN:
        raise SystemExit("SALES_BOT_TOKEN is required")
    if not is_endmonth_vip_promo_active():
        raise SystemExit("Promo is inactive; refusing to send")

    dry_run = args.dry_run or not (args.send_groups or args.send_users)

    await init_db()
    try:
        groups = await get_target_groups()
        users = await get_non_member_users()
        logger.info("Targets: groups=%d non_member_users=%d dry_run=%s", len(groups), len(users), dry_run)

        bot = Bot(token=SALES_BOT_TOKEN)
        await bot.initialize()
        if args.send_groups or dry_run:
            sent, failed = await send_groups(bot, groups, dry_run=dry_run)
            logger.info("Group promo result: sent=%d failed=%d", sent, failed)
        if args.send_users or dry_run:
            sent, failed = await send_users(bot, users, dry_run=dry_run, resume=args.resume, skip_first=args.skip_first)
            logger.info("User promo result: sent=%d failed=%d", sent, failed)
    finally:
        await close_db()


if __name__ == "__main__":
    asyncio.run(main())
