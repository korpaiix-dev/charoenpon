"""Gacha prize delivery worker — multi-source clip pack edition.

Polls gachapon_pulls table every 30 sec. For each unclaimed clip_pack pull,
look up the prize's source_chat_id and forward all media messages from that
group to the user's DM.

Each clip prize (CLIP_A, CLIP_B, CLIP_C) has its own source_chat_id stored
in gachapon_prizes table.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime

from sqlalchemy import text as sql_text
from telegram import Bot
from telegram.constants import ParseMode

from shared.database import get_session

logger = logging.getLogger(__name__)

GUARDIAN_BOT_TOKEN = os.environ.get("GUARDIAN_BOT_TOKEN", "")
SALES_BOT_TOKEN = os.environ.get("SALES_BOT_TOKEN", "") or os.environ.get("TELEGRAM_BOT_TOKEN", "")


async def _claim_pending_prizes() -> list[dict]:
    """Pick clip_pack pulls that are claimed but not yet delivered.

    Delivery tracked via admin_logs (action='gacha_clip_delivered', target_id=pull.id).
    """
    async with get_session() as s:
        r = await s.execute(sql_text("""
            SELECT p.id, p.user_id, p.telegram_id, p.prize_code, p.prize_label,
                   gp.source_chat_id
            FROM gachapon_pulls p
            JOIN gachapon_prizes gp ON gp.code = p.prize_code
            WHERE p.claimed = true
              AND gp.type = 'clip_pack'
              AND gp.source_chat_id IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1 FROM admin_logs a
                  WHERE a.action = 'gacha_clip_delivered' AND a.target_id = p.id
              )
            ORDER BY p.claimed_at
            LIMIT 10
        """))
        return [dict(row._mapping) for row in r.all()]


async def _ensure_msg_table():
    """Ensure gacha_prize_messages table exists (records per-group message IDs)."""
    async with get_session() as s:
        await s.execute(sql_text("""
            CREATE TABLE IF NOT EXISTS gacha_prize_messages (
                id SERIAL PRIMARY KEY,
                source_chat_id BIGINT NOT NULL,
                message_id BIGINT NOT NULL,
                kind VARCHAR(20),
                added_at TIMESTAMP NOT NULL DEFAULT NOW(),
                UNIQUE(source_chat_id, message_id)
            )
        """))
        await s.commit()


async def _get_messages_for_group(chat_id: int) -> list[int]:
    """Return list of message IDs to forward from a group."""
    async with get_session() as s:
        r = await s.execute(sql_text("""
            SELECT message_id FROM gacha_prize_messages
            WHERE source_chat_id = :chat ORDER BY message_id
        """), {"chat": chat_id})
        return [int(row[0]) for row in r.all()]


async def _mark_delivered(pull_id: int, telegram_id: int, count_sent: int):
    """Log delivery so worker won't re-send."""
    async with get_session() as s:
        await s.execute(sql_text("""
            INSERT INTO admin_logs (admin_id, action, target_type, target_id, details)
            VALUES (0, 'gacha_clip_delivered', 'user', :pid, :det)
        """), {"pid": pull_id, "det": f"tg={telegram_id} sent={count_sent}"})
        await s.commit()


async def _forward_clip_pack(pull: dict, guardian_bot: Bot, sales_bot: Bot) -> int:
    """Forward all messages from the prize's source_chat_id to user's DM."""
    target_tg = pull["telegram_id"]
    pull_id = pull["id"]
    src_chat = int(pull["source_chat_id"])
    prize_label = pull["prize_label"]

    # Intro DM via sales bot (customer-facing identity)
    intro = (
        f"🎉 <b>คุณได้รับรางวัล {prize_label}!</b>\n\n"
        "🎬 กำลังส่งคลิปทั้งหมดให้คุณ — รอสักครู่นะคะ"
    )
    try:
        await sales_bot.send_message(chat_id=target_tg, text=intro, parse_mode=ParseMode.HTML)
    except Exception as exc:
        logger.warning("intro DM failed for tg=%s: %s", target_tg, exc)

    msg_ids = await _get_messages_for_group(src_chat)
    if not msg_ids:
        await sales_bot.send_message(
            chat_id=target_tg,
            text=(
                "📥 <b>ระบบยังไม่มีคลิปในชุดนี้</b>\n\n"
                "แอดมินจะส่งให้คุณเร็วๆ นี้ — โปรดรอสักครู่ค่ะ"
            ),
            parse_mode=ParseMode.HTML,
        )
        return 0

    sent = 0
    for mid in msg_ids:
        try:
            await guardian_bot.forward_message(
                chat_id=target_tg,
                from_chat_id=src_chat,
                message_id=mid,
                disable_notification=True,
            )
            sent += 1
            await asyncio.sleep(0.5)
        except Exception as exc:
            logger.warning("forward msg_id=%s to tg=%s failed: %s", mid, target_tg, exc)

    if sent > 0:
        try:
            await sales_bot.send_message(
                chat_id=target_tg,
                text=f"✅ <b>ส่งครบ {sent} คลิป</b> — ขอให้สนุกค่ะ! 💜",
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            pass

    return sent


async def worker_loop():
    """Main worker tick — called by guardian-bot cron every 30 sec."""
    if not GUARDIAN_BOT_TOKEN or not SALES_BOT_TOKEN:
        return
    await _ensure_msg_table()
    pulls = await _claim_pending_prizes()
    if not pulls:
        return
    guardian_bot = Bot(GUARDIAN_BOT_TOKEN)
    sales_bot = Bot(SALES_BOT_TOKEN)
    for pull in pulls:
        try:
            sent = await _forward_clip_pack(pull, guardian_bot, sales_bot)
            await _mark_delivered(pull["id"], pull["telegram_id"], sent)
            logger.info("Gacha clip pack delivered: pull_id=%s prize=%s sent=%s",
                        pull["id"], pull["prize_code"], sent)
        except Exception as exc:
            logger.error("Gacha clip delivery failed pull_id=%s: %s",
                         pull.get("id"), exc, exc_info=True)


__all__ = ["worker_loop"]
