"""Enqueue Lucky 6.6 teaser broadcast — never-paid + paid-expired users.

Safe rate: BROADCAST_SEND_DELAY_SEC=1.0 → 60 msg/min = 1/30 of Telegram bot-wide limit
For 12,500 users: ETA ~3.5 hours
"""
import asyncio
import base64
import json
import sys
from pathlib import Path

sys.path.insert(0, "/app")
from sqlalchemy import text
from shared.database import get_session


CAPTION = (
    "🍀 <b>LUCKY 6.6 พรุ่งนี้!</b> 🔥\n"
    "═══════════════════════\n"
    "ลดสุดทุก tier — 24 ชั่วโมงเท่านั้น!\n\n"
    "💎 VIP 30 วัน    <s>฿300</s> <b>฿166</b>  (-45%)\n"
    "🔥 OF+VIP 30วัน  <s>฿500</s> <b>฿266</b>  (-47%)\n"
    "👑 GOD 90 วัน    <s>฿1,299</s> <b>฿666</b> (-49%)\n"
    "🍀 GOD ถาวร      <s>฿2,499</s> <b>฿2,266</b>\n\n"
    "✨ Bonus: ทุก tier ได้ <b>+6 วัน ฟรี!</b>\n\n"
    "⏰ เริ่ม 6 มิ.ย. 00:00 — หมดเขต 23:59 น.\n"
    "👉 กดสมัครพรุ่งนี้ตอน 00:00 ได้เลย"
)

IMAGE_PATH = "/app/assets/campaigns/06_lucky66.png"
INLINE_BUTTONS = [
    [{"text": "🍀 ดูแพ็กเกจ Lucky 6.6", "url": "https://t.me/NamwarnJarern_bot?start=packages"}],
]


async def main():
    # 1. Build target audience: never-paid + paid-expired users
    async with get_session() as s:
        # Never-paid: users who never have any CONFIRMED payment
        # + Paid-expired: users with expired subscriptions (no active)
        r = await s.execute(text("""
            SELECT DISTINCT u.telegram_id
            FROM users u
            WHERE u.is_banned = false
              AND NOT EXISTS (
                SELECT 1 FROM subscriptions sub
                WHERE sub.user_id = u.id
                  AND sub.status = 'ACTIVE'
              )
              AND u.telegram_id IS NOT NULL
            ORDER BY u.telegram_id
        """))
        ids = [row[0] for row in r.fetchall()]
        print(f"Total audience (no-active-sub): {len(ids)}")

    if not ids:
        print("FAIL: empty audience")
        return

    # 2. Read image + encode base64
    img = Path(IMAGE_PATH).read_bytes()
    img_b64 = base64.b64encode(img).decode("ascii")
    print(f"Image: {len(img)} bytes, base64 {len(img_b64)} chars")

    # 3. Insert broadcast row (PENDING — worker picks up immediately)
    async with get_session() as s:
        r = await s.execute(text("""
            INSERT INTO broadcasts
                (message_text, target_type, target_user_ids, photo_b64, media_type,
                 inline_buttons, parse_mode, status, total_count, success_count,
                 failed_count, sent_by, started_at, last_processed_idx)
            VALUES (:txt, 'custom', :ids, :p64, 'photo',
                    :btns, 'HTML', 'PENDING', :total, 0,
                    0, NULL, NOW(), 0)
            RETURNING id
        """), {
            "txt": CAPTION,
            "ids": json.dumps(ids),
            "p64": img_b64,
            "btns": json.dumps(INLINE_BUTTONS),
            "total": len(ids),
        })
        new_id = r.fetchone()[0]
        await s.commit()

    print(f"OK enqueued broadcast id={new_id} targets={len(ids)}")
    print(f"ETA at 1.0s/msg: ~{len(ids)/60:.1f} minutes (~{len(ids)/3600:.1f} hours)")


asyncio.run(main())
