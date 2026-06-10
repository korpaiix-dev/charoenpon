"""ห้องมีคนชัก draw — runs every Monday 21:00 BKK.

Uses Lao Lotto 2-digit bottom as winning number.
Pulled from apilotto.com after Lao draws at ~20:30 BKK.

Flow:
1. 20:35 BKK Monday → fetch Lao lotto + cache in shaker_draws (PENDING)
2. 21:00 BKK Monday → confirm fetched, pick winner, upgrade, announce
3. Auto-skip if winning_number has no active holder (no winner that week)
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, date, timedelta
from typing import Optional

from sqlalchemy import text as sql_text
from telegram import Bot
from telegram.constants import ParseMode

from shared.database import get_session
from shared.lao_lotto import fetch_lao_lotto_latest
from shared.admin_alert import _admin_group_id

logger = logging.getLogger(__name__)

ADMIN_GROUP = _admin_group_id()
SHAKER_GROUP_CHAT_ID = -1003910489544  # ห้องมีคนชัก
SALES_BOT_TOKEN = os.environ.get("SALES_BOT_TOKEN", "") or os.environ.get("TELEGRAM_BOT_TOKEN", "")


async def fetch_and_cache(today: Optional[date] = None) -> Optional[dict]:
    """Fetch Lao result and store in shaker_draws (PENDING).

    Idempotent — if today's row exists, just update.
    """
    today = today or date.today()
    data = await fetch_lao_lotto_latest()
    if not data:
        logger.warning("Lao lotto not available yet for %s", today)
        return None

    bottom = data.get("laolast2", {}).get("bottom")
    if not bottom or not bottom.isdigit() or len(bottom) != 2:
        logger.warning("Invalid laolast2.bottom: %r", bottom)
        return None

    async with get_session() as s:
        await s.execute(sql_text("""
            INSERT INTO shaker_draws (draw_date, winning_number, status, drawn_at)
            VALUES (:dt, :num, 'PENDING_DRAW', NOW())
            ON CONFLICT (draw_date) DO UPDATE SET
                winning_number = EXCLUDED.winning_number,
                status = CASE WHEN shaker_draws.status = 'DRAWN' THEN shaker_draws.status ELSE EXCLUDED.status END
        """), {"dt": today, "num": bottom})
        await s.commit()
    logger.info("Cached Lao bottom for %s: %s", today, bottom)
    return {"winning_number": bottom, "date": data.get("date")}


async def draw_winner(today: Optional[date] = None) -> dict:
    """Run the draw for today (Monday).

    Returns dict with keys: winning_number, winner (user record or None), date_str.
    """
    today = today or date.today()
    async with get_session() as s:
        r = await s.execute(sql_text("""
            SELECT id, winning_number, status FROM shaker_draws WHERE draw_date = :dt
        """), {"dt": today})
        row = r.fetchone()
        if not row:
            # Not cached — try fetch now
            await fetch_and_cache(today)
            r = await s.execute(sql_text("SELECT id, winning_number, status FROM shaker_draws WHERE draw_date = :dt"),
                                {"dt": today})
            row = r.fetchone()
        if not row:
            return {"winning_number": None, "winner": None, "reason": "lao_unavailable"}

        winning_number = row.winning_number
        if row.status == "DRAWN":
            return {"winning_number": winning_number, "winner": None, "reason": "already_drawn"}

        # Find holders of winning_number (ACTIVE, not expired)
        r2 = await s.execute(sql_text("""
            SELECT t.id, t.user_id, t.telegram_id, t.number, t.payment_id,
                   u.first_name, u.username
            FROM shaker_tickets t
            JOIN users u ON u.id = t.user_id
            WHERE t.number = :num
              AND t.status = 'ACTIVE'
              AND t.expires_at > NOW()
              AND NOT EXISTS (
                  SELECT 1 FROM shaker_winner_lock l
                  WHERE l.user_id = t.user_id AND l.lock_until > NOW()
              )
            ORDER BY t.purchased_at
        """), {"num": winning_number})
        holders = [dict(rr._mapping) for rr in r2.all()]

        if not holders:
            await s.execute(sql_text("UPDATE shaker_draws SET status='DRAWN', drawn_at=NOW() WHERE id=:id"),
                            {"id": row.id})
            await s.commit()
            return {"winning_number": winning_number, "winner": None, "reason": "no_active_holder"}

        # If multiple holders (shouldn't happen — numbers are unique pool), pick first
        winner = holders[0]

        # Update ticket → WON
        await s.execute(sql_text("""
            UPDATE shaker_tickets SET status='WON', won_at=NOW(), draw_id=:did WHERE id=:tid
        """), {"did": row.id, "tid": winner['id']})

        # Lock winner 90 days
        from datetime import timedelta as _td
        lock_until = datetime.utcnow() + _td(days=90)
        await s.execute(sql_text("""
            INSERT INTO shaker_winner_lock (user_id, won_at, lock_until)
            VALUES (:uid, NOW(), :until)
            ON CONFLICT (user_id) DO UPDATE SET won_at = NOW(), lock_until = EXCLUDED.lock_until
        """), {"uid": winner['user_id'], "until": lock_until})

        # Create TIER_1299 subscription (GOD 90 days)
        # Get package id 3 (TIER_1299)
        await s.execute(sql_text("""
            UPDATE subscriptions SET status='EXPIRED'
            WHERE user_id = :uid AND status='ACTIVE'
              AND package_id IN (SELECT id FROM packages WHERE tier IN ('TIER_300','TIER_500'))
        """), {"uid": winner['user_id']})

        await s.execute(sql_text("""
            INSERT INTO subscriptions (user_id, package_id, status, start_date, end_date)
            VALUES (:uid, 3, 'ACTIVE', NOW(), NOW() + INTERVAL '90 days')
        """), {"uid": winner['user_id']})

        # Update draw row
        await s.execute(sql_text("""
            UPDATE shaker_draws SET status='DRAWN', drawn_at=NOW(),
                winner_ticket_id=:tid, winner_user_id=:uid
            WHERE id=:did
        """), {"tid": winner['id'], "uid": winner['user_id'], "did": row.id})
        await s.commit()

    return {"winning_number": winning_number, "winner": winner, "reason": "success"}


async def announce(bot: Bot, result: dict, lao_date_str: str = ""):
    """Announce draw result in 3 places: ห้องมีคนชัก group + admin group + DM winner."""
    win_num = result.get("winning_number")
    winner = result.get("winner")
    reason = result.get("reason", "")

    if win_num is None:
        # No Lao data
        msg_admin = (
            "⚠️ <b>ห้องมีคนชัก — ดึงผลหวยลาวไม่สำเร็จ</b>\n"
            f"วันที่: {date.today().strftime('%d %b %Y')}\n"
            "กรุณา manual draw"
        )
        await bot.send_message(chat_id=ADMIN_GROUP, text=msg_admin, parse_mode=ParseMode.HTML)
        return

    if not winner:
        # No winner this week
        public_msg = (
            f"🎰 <b>ผลสุ่ม ห้องมีคนชัก</b>\n"
            f"━━━━━━━━━━━━━━━\n\n"
            f"📅 อิงหวยลาวงวด <b>{lao_date_str}</b>\n"
            f"🎯 เลขที่ออก: <b>{win_num}</b>\n\n"
            f"😢 รอบนี้<b>ไม่มีผู้ถือเลข {win_num}</b>\n"
            "รอบหน้าจันทร์หน้า ลุ้นใหม่!\n\n"
            "🎫 ซื้อเลขใหม่ได้เลย — กด <b>🎰 กิจกรรมห้องมีคนชัก</b> ในบอท"
        )
        await bot.send_message(chat_id=SHAKER_GROUP_CHAT_ID, text=public_msg, parse_mode=ParseMode.HTML)
        await bot.send_message(chat_id=ADMIN_GROUP, text=public_msg, parse_mode=ParseMode.HTML)
        return

    # WINNER!
    first_name = winner.get("first_name") or "คุณ"

    public_msg = (
        f"🎉🎉 <b>ประกาศผล ห้องมีคนชัก</b> 🎉🎉\n"
        f"━━━━━━━━━━━━━━━\n\n"
        f"📅 อิงหวยลาวงวด <b>{lao_date_str}</b>\n"
        f"🎯 เลขที่ออก: <b>{win_num}</b>\n\n"
        f"🏆 <b>ผู้โชคดี: {first_name}</b>\n"
        f"💎 รางวัล: GOD MODE 3 เดือน (มูลค่า ฿1,299)\n\n"
        f"✨ ระบบอัปเกรดให้แล้วอัตโนมัติ — ดูได้ทุกห้อง VIP!\n"
        "━━━━━━━━━━━━━━━\n"
        "ขอบคุณทุกท่านที่ร่วมสนุก 🙏\n"
        "จันทร์หน้า — ลุ้นใหม่!"
    )
    await bot.send_message(chat_id=SHAKER_GROUP_CHAT_ID, text=public_msg, parse_mode=ParseMode.HTML)
    await bot.send_message(chat_id=ADMIN_GROUP, text=public_msg, parse_mode=ParseMode.HTML)

    # DM winner via SALES bot (need separate bot instance)
    try:
        sales_bot = Bot(SALES_BOT_TOKEN)
        dm = (
            f"🎉 <b>ยินดีด้วย!</b> 🎁\n\n"
            f"คุณคือผู้โชคดีของ <b>ห้องมีคนชัก</b> สัปดาห์นี้!\n"
            f"🎯 เลข <b>{win_num}</b> ของคุณ ตรงกับ 2 ตัวล่างหวยลาวงวด {lao_date_str}\n\n"
            f"💎 รางวัล: <b>GOD MODE 3 เดือน</b> (มูลค่า ฿1,299)\n"
            "✨ ระบบอัปเกรดให้แล้ว — ใช้ได้เลย\n\n"
            "พิมพ์ /start ในบอทเพื่อรับลิงก์เข้ากลุ่ม VIP เพิ่ม"
        )
        await sales_bot.send_message(chat_id=winner['telegram_id'], text=dm, parse_mode=ParseMode.HTML)
    except Exception as exc:
        logger.warning("DM winner failed: %s", exc)


async def run_draw_now(bot: Bot, today: Optional[date] = None):
    """End-to-end: fetch + draw + announce. Idempotent."""
    today = today or date.today()
    cached = await fetch_and_cache(today)
    result = await draw_winner(today)
    lao_date = cached.get("date") if cached else ""
    await announce(bot, result, lao_date)
    return result


__all__ = ["fetch_and_cache", "draw_winner", "announce", "run_draw_now"]
