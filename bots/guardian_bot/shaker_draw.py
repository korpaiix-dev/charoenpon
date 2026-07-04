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

        # Award TIER_1299 (GOD 90 days) — smart handling of existing subs
        # Rules:
        # - If user has TIER_2499 lifetime → DO NOT touch (already best). Log credit only.
        # - If user has TIER_1299 active → EXTEND end_date += 90 days (stack)
        # - If user has TIER_100/300/500 active → EXPIRE them; new TIER_1299 starts NOW
        # - Otherwise → INSERT new TIER_1299 starting NOW

        # Check current active subscriptions
        r_subs = await s.execute(sql_text("""
            SELECT sub.id, sub.package_id, sub.end_date, pk.tier
            FROM subscriptions sub
            JOIN packages pk ON pk.id = sub.package_id
            WHERE sub.user_id = :uid AND sub.status = 'ACTIVE'
              AND sub.end_date > NOW()
        """), {"uid": winner['user_id']})
        existing = [dict(row._mapping) for row in r_subs.all()]

        has_lifetime = any(s['tier'] == 'TIER_2499' for s in existing)
        existing_1299 = next((s for s in existing if s['tier'] == 'TIER_1299'), None)

        if has_lifetime:
            # Already lifetime — log only, no sub changes
            logger.info("Winner %s already has TIER_2499 lifetime — credit logged, no sub changes",
                        winner['user_id'])
            # Could log a credit/redeem record here in the future
        elif existing_1299:
            # Extend existing GOD 3M by +90 days
            from datetime import timedelta as _td
            new_end = existing_1299['end_date'] + _td(days=90)
            await s.execute(sql_text("""
                UPDATE subscriptions SET end_date = :end, updated_at = NOW()
                WHERE id = :sid
            """), {"end": new_end, "sid": existing_1299['id']})
            logger.info("Winner %s — extended TIER_1299 end_date to %s",
                        winner['user_id'], new_end)
        else:
            # Expire any active TIER_100/300/500 (lower tiers being replaced)
            await s.execute(sql_text("""
                UPDATE subscriptions SET status='EXPIRED', updated_at = NOW()
                WHERE user_id = :uid AND status='ACTIVE'
                  AND package_id IN (
                      SELECT id FROM packages WHERE tier IN ('TIER_100','TIER_300','TIER_500')
                  )
            """), {"uid": winner['user_id']})
            # New TIER_1299 starts today (วันที่ถูกรางวัล)
            await s.execute(sql_text("""
                INSERT INTO subscriptions (user_id, package_id, status, start_date, end_date)
                VALUES (:uid, (SELECT id FROM packages WHERE tier='TIER_1299' LIMIT 1), 'ACTIVE', NOW(), NOW() + INTERVAL '90 days')
            """), {"uid": winner['user_id']})
            logger.info("Winner %s — new TIER_1299 90 days starting NOW", winner['user_id'])

        # Update draw row
        await s.execute(sql_text("""
            UPDATE shaker_draws SET status='DRAWN', drawn_at=NOW(),
                winner_ticket_id=:tid, winner_user_id=:uid
            WHERE id=:did
        """), {"tid": winner['id'], "uid": winner['user_id'], "did": row.id})
        await s.commit()

    return {"winning_number": winning_number, "winner": winner, "reason": "success"}


async def announce(bot: Bot, result: dict, lao_date_str: str = ""):
    """Announce in 4 places:
    1) public msg in ห้องมีคนชัก group
    2) public msg in admin group
    3) DM winner (with congrats)
    4) DM all other active participants (with their own numbers + winning + CTA)
    """
    import asyncio as _aio
    from datetime import date as _date

    win_num = result.get("winning_number")
    winner = result.get("winner")

    if win_num is None:
        msg_admin = (
            "⚠️ <b>ห้องมีคนชัก — ดึงผลหวยลาวไม่สำเร็จ</b>\n"
            f"วันที่: {_date.today().strftime('%d %b %Y')}\n"
            "กรุณา manual draw"
        )
        await bot.send_message(chat_id=ADMIN_GROUP, text=msg_admin, parse_mode=ParseMode.HTML)
        return

    # 1+2) public msg
    if not winner:
        public_msg = (
            f"🎰 <b>ผลสุ่ม ห้องมีคนชัก</b>\n"
            f"━━━━━━━━━━━━━━━\n\n"
            f"📅 อิงหวยลาวงวด <b>{lao_date_str}</b>\n"
            f"🎯 เลขที่ออก: <b>{win_num}</b>\n\n"
            f"😢 รอบนี้<b>ไม่มีผู้ถือเลข {win_num}</b>\n"
            "รอบหน้าจันทร์หน้า ลุ้นใหม่!\n\n"
            "🎫 ซื้อเลขใหม่ได้เลย — กด <b>🎰 กิจกรรมห้องมีคนชัก</b> ในบอท"
        )
    else:
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

    try:
        await bot.send_message(chat_id=SHAKER_GROUP_CHAT_ID, text=public_msg, parse_mode=ParseMode.HTML)
    except Exception as exc:
        logger.warning("send to shaker group fail: %s", exc)
    try:
        await bot.send_message(chat_id=ADMIN_GROUP, text=public_msg, parse_mode=ParseMode.HTML)
    except Exception as exc:
        logger.warning("send to admin group fail: %s", exc)

    # 3+4) DM all active holders this round
    from datetime import datetime as _dt
    async with get_session() as s:
        rr = await s.execute(sql_text("""
            SELECT t.user_id, t.telegram_id, u.first_name,
                   array_agg(t.number ORDER BY t.purchased_at) AS numbers,
                   MAX(t.expires_at) AS last_expires
            FROM shaker_tickets t
            JOIN users u ON u.id = t.user_id
            WHERE t.status IN ('ACTIVE','WON')
              AND t.expires_at > NOW()
              AND t.purchased_at > NOW() - INTERVAL '40 days'
            GROUP BY t.user_id, t.telegram_id, u.first_name
        """))
        participants = [dict(rr2._mapping) for rr2 in rr.all()]

    sales_token = SALES_BOT_TOKEN
    sales_bot = Bot(sales_token) if sales_token else None
    sent_win = 0
    sent_lose = 0

    for p in participants:
        tg_id = p["telegram_id"]
        first_name = p["first_name"] or "คุณ"
        user_numbers = p["numbers"] or []
        is_winner = winner and p["user_id"] == winner["user_id"]
        days_left = max(0, (p["last_expires"] - _dt.utcnow()).days)
        numbers_str = " ".join(f"<b>{n}</b>" for n in user_numbers)

        if is_winner:
            dm = (
                "🎉🎉🎉 <b>ยินดีด้วย!</b> คุณคือผู้โชคดี! 🎉🎉🎉\n\n"
                f"🎯 เลข <b>{win_num}</b> ของคุณ\n"
                f"ตรงกับ 2 ตัวล่างหวยลาวงวด <b>{lao_date_str}</b>!\n\n"
                "🏆 <b>รางวัล: GOD MODE 3 เดือน</b>\n"
                "💎 มูลค่า ฿1,299\n"
                "⏰ ใช้ได้: 90 วัน\n\n"
                "✨ <b>ระบบอัปเกรดให้แล้วอัตโนมัติ!</b>\n"
                "✅ เข้าทุกห้อง VIP ของระบบเจริญพร\n"
                "✅ พิมพ์ /getlink รับลิงก์เข้ากลุ่มใหม่ทั้งหมด\n\n"
                "🙏 ขอบคุณที่ร่วมสนุกครับ!"
            )
            sent_win += 1
        else:
            dm = (
                f"🎰 <b>ผลสุ่ม ห้องมีคนชัก</b>\n\n"
                f"📅 อิงหวยลาวงวด <b>{lao_date_str}</b>\n"
                f"🎯 เลขที่ออก: <b>{win_num}</b>\n\n"
                "━━━━━━━━━━━━━━━\n"
                f"🎫 เลขของคุณรอบนี้: {numbers_str}\n"
                "━━━━━━━━━━━━━━━\n\n"
                "😢 รอบนี้ดวงไม่เข้า แต่ไม่เป็นไร!\n"
                f"✨ เลขของคุณยัง <b>active</b> อีก {days_left} วัน\n"
                "📅 ลุ้นใหม่ <b>จันทร์หน้า 21:00 น.</b>\n\n"
                "💡 อยากเพิ่มโอกาสไหม?\n"
                "🎫 ซื้อเลขเพิ่ม → กด <b>🎰 กิจกรรมห้องมีคนชัก</b> ในบอท\n\n"
                "ขอให้โชคดีรอบหน้าครับ 🍀"
            )
            sent_lose += 1

        try:
            if sales_bot:
                await sales_bot.send_message(chat_id=tg_id, text=dm, parse_mode=ParseMode.HTML)
            else:
                await bot.send_message(chat_id=tg_id, text=dm, parse_mode=ParseMode.HTML)
        except Exception as exc:
            logger.warning("DM to %s fail: %s", tg_id, exc)
        await _aio.sleep(0.05)  # ~20 DMs/sec safe

    logger.info("shaker DM sent — win=%s lose=%s", sent_win, sent_lose)


async def run_draw_now(bot: Bot, today: Optional[date] = None):
    """End-to-end: fetch + draw + announce. Idempotent."""
    today = today or date.today()
    cached = await fetch_and_cache(today)
    result = await draw_winner(today)
    lao_date = cached.get("date") if cached else ""
    await announce(bot, result, lao_date)
    return result


__all__ = ["fetch_and_cache", "draw_winner", "announce", "run_draw_now"]
