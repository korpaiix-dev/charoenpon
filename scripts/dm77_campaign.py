# -*- coding: utf-8 -*-
"""7.7 DM campaign — segmented, rate-limit-safe, dry-run first.
Run inside sales-bot container:
  python dm77_campaign.py --wave day7_active --dryrun     # count only, no send
  python dm77_campaign.py --wave day7_active --send        # actually send
Waves: day6_teaser | day7_active | day7_churned | day7_never | day7_reminder
"""
import asyncio, os, argparse
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from shared.database import get_session
from sqlalchemy import text as T
from bots.sales_bot.broadcast import safe_broadcast

BTN = ("🛒 ดูโปร 7.7", "https://t.me/NamwarnJarern_bot?start=packages")

Q_ACTIVE = ("SELECT DISTINCT u.telegram_id FROM users u JOIN subscriptions s ON s.user_id=u.id "
            "WHERE u.is_banned=false AND s.status='ACTIVE' AND s.end_date>now()")
Q_CHURNED = ("SELECT DISTINCT u.telegram_id FROM users u WHERE u.is_banned=false "
             "AND u.id IN (SELECT user_id FROM payments WHERE status='CONFIRMED') "
             "AND u.id NOT IN (SELECT user_id FROM subscriptions WHERE status='ACTIVE' AND end_date>now())")
Q_NEVER = ("SELECT u.telegram_id FROM users u WHERE u.is_banned=false AND u.telegram_id IS NOT NULL "
           "AND u.id NOT IN (SELECT DISTINCT user_id FROM payments WHERE status='CONFIRMED')")
Q_WARM = "(%s) UNION (%s)" % (Q_ACTIVE, Q_CHURNED)

M_ACTIVE = ("🎊 <b>7.7 FLASH SALE เริ่มแล้ว!</b> วันเดียว\n"
            "คุณเป็นสมาชิกอยู่แล้ว 🙏 อัปเป็น \"ถาวร\" คุ้มสุดรอบปี:\n"
            "👑 Super VIP <s>4,999</s> <b>3,777</b>\n❤️ GOD ถาวร <s>2,499</s> <b>1,999</b>\n"
            "จ่ายครั้งเดียว ดูตลอดชีพ 🎁 + หมุนกาชาฟรี\n⏰ หมดเที่ยงคืนนี้!")
M_CHURNED = ("🥺 <b>คิดถึงนะ!</b> กลับมา 7.7 ราคาพิเศษสุด\n"
             "💚 VIP <s>300</s> <b>177</b> · ❤️ GOD ถาวร <s>2,499</s> <b>1,999</b> · 👑 Super VIP <b>3,777</b>\n"
             "🎁 + หมุนกาชาฟรีทุกคน\n⏰ วันเดียว หมดเที่ยงคืนนี้!")
M_NEVER = ("🎊 <b>7.7 FLASH SALE VIP เจริญพร</b> วันเดียว!\n"
           "เริ่มแค่ 💚 <b>VIP 177</b> (จาก 300) คลิป HD 10,000+ ชิ้น\n"
           "🎁 ซื้อวันนี้ หมุนกาชาฟรี!\n⏰ หมดเที่ยงคืนนี้!")
M_TEASER = ("🔔 <b>พรุ่งนี้! โปรใหญ่ 7.7</b> ราคาโหดสุดในรอบปี วันเดียว\n"
            "💚 VIP 177 · ❤️ GOD ถาวร 1,999 · 👑 Super VIP 3,777 + 🎁 หมุนกาชาฟรี\n"
            "เตรียมตัวไว้ พรุ่งนี้เที่ยงคืนเริ่ม! 🔥")
M_REMINDER = ("⏰ <b>เหลืออีกไม่กี่ชั่วโมง!</b> โปร 7.7 หมดเที่ยงคืนนี้\n"
              "ราคานี้ปีหน้าค่อยเจอกัน 🔥 รีบเลย!")

WAVES = {
    "day6_teaser":   (Q_WARM,   M_TEASER),
    "day7_active":   (Q_ACTIVE, M_ACTIVE),
    "day7_churned":  (Q_CHURNED, M_CHURNED),
    "day7_never":    (Q_NEVER,  M_NEVER),
    "day7_reminder": (Q_WARM,   M_REMINDER),
}

async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--wave", required=True, choices=list(WAVES))
    ap.add_argument("--send", action="store_true")
    ap.add_argument("--dryrun", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    q, msg = WAVES[a.wave]
    async with get_session() as s:
        rows = (await s.execute(T(q))).fetchall()
    ids = [r[0] for r in rows if r[0]]
    if a.limit: ids = ids[:a.limit]
    print("WAVE=%s recipients=%d" % (a.wave, len(ids)))
    print("MESSAGE:\n%s\n" % msg)
    if not a.send or a.dryrun:
        print("DRY-RUN — no messages sent. sample ids:", ids[:5])
        return
    bot = Bot(os.environ["SALES_BOT_TOKEN"])
    kb = InlineKeyboardMarkup([[InlineKeyboardButton(BTN[0], url=BTN[1])]])
    res = await safe_broadcast(bot, ids, msg, parse_mode="HTML", reply_markup=kb)
    print("RESULT:", res)

asyncio.run(main())
