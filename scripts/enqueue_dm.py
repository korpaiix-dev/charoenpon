# -*- coding: utf-8 -*-
"""7.7 DM — enqueue one wave into the dashboard `broadcasts` queue (worker sends, throttled + ban-safe).
Segments always EXCLUDE banned + blocked-bot users. trial = never-subscribed, joined last 30 days only.
  python enqueue_dm.py --wave day7_active            # enqueue (worker sends)
  python enqueue_dm.py --wave day7_active --dryrun    # count only, no enqueue
Waves: day6_teaser | day7_active | day7_expired | day7_trial | day7_reminder
"""
import asyncio, os, argparse, base64, json
import asyncpg

BTN = [{"text": "🛒 ดูโปร 7.7", "url": "https://t.me/NamwarnJarern_bot?start=packages"}]
BASE = "FROM users u WHERE NOT u.is_banned AND NOT COALESCE(u.is_blocked_bot, false)"
SEG = {
    "warm":    f"SELECT u.telegram_id {BASE} AND EXISTS(SELECT 1 FROM subscriptions s WHERE s.user_id=u.id) AND u.telegram_id IS NOT NULL",
    "active":  f"SELECT u.telegram_id {BASE} AND EXISTS(SELECT 1 FROM subscriptions s WHERE s.user_id=u.id AND s.status='ACTIVE' AND s.end_date>now()) AND u.telegram_id IS NOT NULL",
    "expired": f"SELECT u.telegram_id {BASE} AND EXISTS(SELECT 1 FROM subscriptions s WHERE s.user_id=u.id) AND NOT EXISTS(SELECT 1 FROM subscriptions s WHERE s.user_id=u.id AND s.status='ACTIVE' AND s.end_date>now()) AND u.telegram_id IS NOT NULL",
    "trial30": f"SELECT u.telegram_id {BASE} AND NOT EXISTS(SELECT 1 FROM subscriptions s WHERE s.user_id=u.id) AND u.created_at > now()-interval '30 days' AND u.telegram_id IS NOT NULL",
}

M_TEASER = ("🔔 <b>พรุ่งนี้! โปรใหญ่ 7.7</b> ราคาโหดสุดในรอบปี วันเดียว\n"
            "💚 VIP 177 · ❤️ GOD ถาวร 1,999 · 👑 Super VIP 3,777 + 🎁 หมุนกาชาฟรี\n"
            "เตรียมตัวไว้ พรุ่งนี้เที่ยงคืนเริ่ม! 🔥")
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
M_REMINDER = ("⏰ <b>เหลืออีกไม่กี่ชั่วโมง!</b> โปร 7.7 หมดเที่ยงคืนนี้\n"
              "ราคานี้ปีหน้าค่อยเจอกัน 🔥 รีบเลย!")

WAVES = {
    "day6_teaser":   ("warm",    M_TEASER,   True),
    "day7_active":   ("active",  M_ACTIVE,   True),
    "day7_expired":  ("expired", M_CHURNED,  True),
    "day7_trial":    ("trial30", M_NEVER,    True),
    "day7_reminder": ("warm",    M_REMINDER, False),
}


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--wave", required=True, choices=list(WAVES))
    ap.add_argument("--dryrun", action="store_true")
    ap.add_argument("--poster", default="/tmp/promo_77_2026.jpg")
    a = ap.parse_args()
    seg, msg, use_img = WAVES[a.wave]

    url = os.environ.get("DATABASE_URL", "").replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(url)
    try:
        rows = await conn.fetch(SEG[seg])
        ids = [int(r["telegram_id"]) for r in rows if r["telegram_id"]]
        print("wave=%s segment=%s recipients=%d (banned+blocked excluded)" % (a.wave, seg, len(ids)))
        if a.dryrun:
            print("DRY-RUN — nothing enqueued.")
            return
        if not ids:
            print("no recipients — skip")
            return
        media_type, media_b64 = None, None
        if use_img and os.path.exists(a.poster):
            with open(a.poster, "rb") as f:
                media_b64 = base64.b64encode(f.read()).decode("ascii")
            media_type = "photo"
        bid = await conn.fetchval(
            "INSERT INTO broadcasts (message_text, target_type, target_value, total_count, "
            "target_user_ids, status, started_at, parse_mode, media_type, media_b64, inline_buttons) "
            "VALUES ($1,$2,$2,$3,$4::jsonb,'PENDING',now(),'HTML',$5,$6,$7::jsonb) RETURNING id",
            msg, "77_" + seg, len(ids), json.dumps(ids), media_type, media_b64, json.dumps(BTN),
        )
        print("ENQUEUED broadcast id=%s recipients=%d (worker will send, ~20/s, auto-pause if fail>30%%)" % (bid, len(ids)))
    finally:
        await conn.close()

asyncio.run(main())
