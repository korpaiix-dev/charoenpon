"""Free Group Poster — โพสต์ preview ลงกลุ่มฟรี 11 กลุ่ม.

- 3 รอบ/วัน: 11:00, 15:00, 20:00 เวลาไทย
- เลือก preview ที่ยังไม่เคยโพสต์
- ส่งรูป + caption + ปุ่ม "สมัคร VIP"
- Rate limit: 3 วินาที/กลุ่ม
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest, Forbidden, RetryAfter
from telegram.ext import ContextTypes

from shared.database import get_session

logger = logging.getLogger(__name__)

TH_TZ = timezone(timedelta(hours=7))

ADMIN_GROUP_ID = int(os.environ.get("ADMIN_GROUP_CHAT_ID", "-1003830920430"))

# ─── DB Migration ────────────────────────────────────────────────────────────

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS free_group_posts (
    id SERIAL PRIMARY KEY,
    content_id INTEGER NOT NULL,
    preview_file_id TEXT NOT NULL,
    posted_at TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_free_group_posts_content_id ON free_group_posts(content_id);
"""


async def ensure_tables() -> None:
    """Create free_group_posts table if not exists."""
    async with get_session() as session:
        await session.execute(text(CREATE_TABLE_SQL))
        await session.commit()


# ─── Helpers ─────────────────────────────────────────────────────────────────

async def _get_free_group_chat_ids() -> list[int]:
    """Query all active FREE groups from group_registry."""
    async with get_session() as session:
        result = await session.execute(
            text("""
                SELECT chat_id FROM group_registry
                WHERE slug LIKE 'FREE%' AND is_active = true
                ORDER BY slug
            """)
        )
        return [row.chat_id for row in result.fetchall()]


async def _get_unposted_preview() -> dict | None:
    """Get 1 preview that hasn't been posted to free groups yet."""
    async with get_session() as session:
        result = await session.execute(
            text("""
                SELECT cp.content_id, cp.preview_file_id
                FROM content_previews cp
                LEFT JOIN free_group_posts fgp ON fgp.content_id = cp.content_id
                WHERE fgp.id IS NULL
                ORDER BY cp.created_at DESC
                LIMIT 1
            """)
        )
        row = result.fetchone()
        if row:
            return {"content_id": row.content_id, "preview_file_id": row.preview_file_id}
    return None


# ─── Post to Free Groups ────────────────────────────────────────────────────

CAPTION = (
    "🔥 <b>คลิปใหม่มาแล้ว!</b>\n"
    "\n"
    "ดูเต็มไม่เบลอ 10,000+ คลิป\n"
    "เริ่มต้นแค่ ฿300/เดือน\n"
    "\n"
    "👇 กดสมัคร VIP เลยค่ะ"
)


async def post_to_free_groups(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Post 1 preview photo to all free groups."""
    bot = context.bot

    await ensure_tables()

    preview = await _get_unposted_preview()
    if not preview:
        logger.info("No unposted previews available for free groups")
        return

    chat_ids = await _get_free_group_chat_ids()
    if not chat_ids:
        logger.warning("No active FREE groups found in group_registry")
        return

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(
            "🔓 สมัคร VIP",
            url="tg://resolve?domain=NamwarnJarern_bot&start=from_free_group",
        )]
    ])

    sent_count = 0
    failed_count = 0

    for chat_id in chat_ids:
        try:
            await bot.send_photo(
                chat_id=chat_id,
                photo=preview["preview_file_id"],
                caption=CAPTION,
                parse_mode="HTML",
                reply_markup=keyboard,
            )
            sent_count += 1
        except RetryAfter as e:
            logger.warning("Rate limited, waiting %d seconds", e.retry_after)
            await asyncio.sleep(e.retry_after)
            try:
                await bot.send_photo(
                    chat_id=chat_id,
                    photo=preview["preview_file_id"],
                    caption=CAPTION,
                    parse_mode="HTML",
                    reply_markup=keyboard,
                )
                sent_count += 1
            except Exception as exc:
                logger.error("Failed to post to group %d after retry: %s", chat_id, exc)
                failed_count += 1
        except (Forbidden, BadRequest) as exc:
            logger.error("Cannot post to group %d: %s", chat_id, exc)
            failed_count += 1
        except Exception as exc:
            logger.error("Failed to post to group %d: %s", chat_id, exc)
            failed_count += 1

        await asyncio.sleep(3)  # Rate limit between groups

    # Track in DB
    async with get_session() as session:
        await session.execute(
            text("INSERT INTO free_group_posts (content_id, preview_file_id) VALUES (:cid, :fid)"),
            {"cid": preview["content_id"], "fid": preview["preview_file_id"]},
        )
        await session.commit()

    logger.info(
        "Free group poster: content #%d posted to %d/%d groups (failed: %d)",
        preview["content_id"], sent_count, len(chat_ids), failed_count,
    )
