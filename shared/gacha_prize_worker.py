"""Gacha prize delivery worker v2 — INVITE LINK edition.

Instead of forwarding messages (which doesn't work with new -100**3** supergroups
that don't deliver message updates to bots), this version creates a one-time
invite link to the source group and DMs the customer with a "Join" button.

Customer clicks → joins the group (locked to read-only) → views all clips
in the group.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta

from sqlalchemy import text as sql_text
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode

from shared.database import get_session

logger = logging.getLogger(__name__)

GUARDIAN_BOT_TOKEN = os.environ.get("GUARDIAN_BOT_TOKEN", "")
SALES_BOT_TOKEN = os.environ.get("SALES_BOT_TOKEN", "") or os.environ.get("TELEGRAM_BOT_TOKEN", "")


async def _claim_pending_prizes() -> list[dict]:
    """Pick clip_pack pulls that are claimed but not yet delivered."""
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


async def _mark_delivered(pull_id: int, telegram_id: int, success: bool):
    """Log delivery so worker won't re-send."""
    async with get_session() as s:
        await s.execute(sql_text("""
            INSERT INTO admin_logs (admin_id, action, target_type, target_id, details)
            VALUES (0, 'gacha_clip_delivered', 'user', :pid, :det)
        """), {"pid": pull_id, "det": f"tg={telegram_id} success={success}"})
        await s.commit()


async def _deliver_invite_link(pull: dict, guardian_bot: Bot, sales_bot: Bot) -> bool:
    """Create one-time invite link to source group and DM customer."""
    target_tg = pull["telegram_id"]
    pull_id = pull["id"]
    src_chat = int(pull["source_chat_id"])
    prize_label = pull["prize_label"]

    try:
        # Single-use invite, valid 30 days
        expire = datetime.utcnow() + timedelta(days=30)
        invite = await guardian_bot.create_chat_invite_link(
            chat_id=src_chat,
            name=f"gacha_pull_{pull_id}",
            expire_date=expire,
            member_limit=1,
        )
        invite_url = invite.invite_link
        logger.info("Created invite link for pull=%s chat=%s -> %s",
                    pull_id, src_chat, invite_url)
    except Exception as e:
        logger.exception("Failed to create invite link for pull=%s chat=%s: %s",
                         pull_id, src_chat, e)
        try:
            await sales_bot.send_message(
                chat_id=target_tg,
                text=(
                    f"🎉 <b>คุณได้รับรางวัล {prize_label}!</b>\n\n"
                    "📌 ระบบจะส่งคลิปให้คุณเร็วๆ นี้ — แอดมินกำลังตรวจสอบค่ะ"
                ),
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            pass
        return False

    text = (
        f"🎉 <b>คุณได้รับรางวัล {prize_label}!</b>\n\n"
        "🎬 กดปุ่มด้านล่างเพื่อเข้ากลุ่มดูคลิปทั้งหมด\n\n"
        "⚠️ ลิงก์ใช้ได้ <b>ครั้งเดียว</b> + หมดอายุใน 30 วัน\n"
        "✅ เข้ากลุ่มแล้วอยู่ได้ตลอด — ดูคลิปทั้งหมดที่อัพเดทเรื่อย ๆ"
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"🎬 เข้ากลุ่มดู {prize_label}", url=invite_url)],
    ])
    try:
        await sales_bot.send_message(
            chat_id=target_tg,
            text=text,
            parse_mode=ParseMode.HTML,
            reply_markup=kb,
            disable_web_page_preview=True,
        )
    except Exception as e:
        logger.warning("DM failed for tg=%s pull=%s: %s", target_tg, pull_id, e)
    return True


async def worker_loop():
    """Main worker tick — called by guardian-bot cron every 30 sec."""
    if not GUARDIAN_BOT_TOKEN or not SALES_BOT_TOKEN:
        return
    pulls = await _claim_pending_prizes()
    if not pulls:
        return

    guardian_bot = Bot(GUARDIAN_BOT_TOKEN)
    sales_bot = Bot(SALES_BOT_TOKEN)
    await guardian_bot.initialize()
    await sales_bot.initialize()
    try:
        for pull in pulls:
            try:
                ok = await _deliver_invite_link(pull, guardian_bot, sales_bot)
                await _mark_delivered(pull["id"], pull["telegram_id"], ok)
                logger.info("Gacha clip delivered (v2-invite): pull_id=%s prize=%s ok=%s",
                            pull["id"], pull["prize_code"], ok)
            except Exception as exc:
                logger.error("Gacha delivery failed pull_id=%s: %s",
                             pull.get("id"), exc, exc_info=True)
    finally:
        try: await guardian_bot.shutdown()
        except Exception: pass
        try: await sales_bot.shutdown()
        except Exception: pass


__all__ = ["worker_loop"]
