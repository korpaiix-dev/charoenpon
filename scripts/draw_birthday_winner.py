"""Draw 1 winner for เดือนเกิดเฮียตั๋ง promo.

Eligibility: CONFIRMED payment of TIER_500 between 7 มิ.ย. 00:00 BKK and 10 มิ.ย. 12:00 BKK.
Result: random pick 1 → upgrade to TIER_2499 lifetime + DM winner + admin notify.

Schedule: 10 มิ.ย. 18:00 BKK (11:00 UTC) via cron.
"""
import asyncio
import os
import random
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, "/app")
from sqlalchemy import select, text
from shared.database import get_session
from shared.models import (
    User, Payment, PaymentStatus, Subscription, SubscriptionStatus,
    Package, PackageTier,
)
from telegram import Bot

# Window: 7 มิ.ย. 00:00 BKK to 10 มิ.ย. 12:00 BKK (UTC: 6 มิ.ย. 17:00 → 10 มิ.ย. 05:00)
START_UTC = datetime(2026, 6, 6, 17, 0, 0)
END_UTC = datetime(2026, 6, 10, 5, 0, 0)

ADMIN_GROUP_ID = int(os.environ.get("ADMIN_GROUP_CHAT_ID", ""))


async def main():
    # 1. Get all unique users who bought TIER_500 in window
    async with get_session() as s:
        r = await s.execute(text("""
            SELECT DISTINCT p.user_id, u.telegram_id, u.first_name, u.username
            FROM payments p
            JOIN packages pkg ON pkg.id = p.package_id
            JOIN users u ON u.id = p.user_id
            WHERE pkg.tier = 'TIER_500'
              AND p.status = 'CONFIRMED'
              AND p.verified_at >= :s
              AND p.verified_at < :e
              AND u.is_banned = false
            ORDER BY p.user_id
        """), {"s": START_UTC, "e": END_UTC})
        candidates = r.fetchall()

    print(f"Total candidates: {len(candidates)}")
    if not candidates:
        print("No candidates — no winner today.")
        return

    # 2. Random pick 1
    winner = random.choice(candidates)
    user_id, tg_id, first_name, username = winner
    print(f"Winner: user_id={user_id} tg={tg_id} name={first_name} username={username}")

    # 3. Upgrade to TIER_2499 (lifetime)
    async with get_session() as s:
        pkg = (await s.execute(select(Package).where(Package.tier == PackageTier.TIER_2499))).scalar_one_or_none()
        # End any current ACTIVE
        active = (await s.execute(select(Subscription).where(
            Subscription.user_id == user_id, Subscription.status == SubscriptionStatus.ACTIVE
        ))).scalars().all()
        for a in active:
            a.status = SubscriptionStatus.EXPIRED
            a.end_date = datetime.utcnow()
        # Lifetime sub
        from datetime import datetime as _dt
        new_sub = Subscription(
            user_id=user_id, package_id=pkg.id,
            status=SubscriptionStatus.ACTIVE,
            start_date=_dt.utcnow(), end_date=_dt(2099, 12, 31),
        )
        s.add(new_sub)
        # Log gift payment for tracking
        gift_pay = Payment(
            user_id=user_id, package_id=pkg.id, amount=0,
            status=PaymentStatus.CONFIRMED,
            slip_hash="BIRTHDAY_GIFT_DRAW_10JUN",
            created_at=_dt.utcnow(), verified_at=_dt.utcnow(),
        )
        # Need method
        from shared.models import PaymentMethod
        gift_pay.method = PaymentMethod.PROMPTPAY
        s.add(gift_pay)
        await s.commit()
        print(f"Upgraded user {user_id} to TIER_2499 lifetime")

    # 4. DM winner
    bot = Bot(os.environ["SALES_BOT_TOKEN"])
    name = first_name or username or "คุณ"
    win_msg = (
        f"🎉 <b>ยินดีด้วยค่ะ คุณ{name}!</b> 🎂\n\n"
        f"คุณคือผู้โชคดี <b>1 เดียว</b> ของกิจกรรม\n"
        f"<b>เดือนเกิดเฮียตั๋ง เจริญพร</b>!\n\n"
        f"🎁 รางวัล: <b>GOD MODE ถาวร</b>\n"
        f"💎 มูลค่า: <b>฿2,499</b> — สิทธิ์ตลอดชีพ ไม่หมดอายุ\n\n"
        f"✅ ระบบ upgrade ให้แล้ว — เข้าใช้งานได้ทันที\n"
        f"📌 กด /start เพื่อดูลิงก์เข้ากลุ่ม VIP ทั้งหมด\n\n"
        f"🙏 ขอบคุณที่อยู่กับเจริญพรค่ะ!"
    )
    try:
        await bot.send_message(chat_id=tg_id, text=win_msg, parse_mode="HTML")
        print(f"Winner DM sent to {tg_id}")
    except Exception as exc:
        print(f"Winner DM failed: {exc}")

    # 5. Admin notify
    try:
        from telegram import Bot as _Bot
        admin_bot = _Bot(os.environ["ADMIN_BOT_TOKEN"])
        admin_msg = (
            f"🎂 <b>BIRTHDAY DRAW RESULT — 10 มิ.ย. 18:00</b>\n"
            f"━━━━━━━━━━━━━━━\n"
            f"🎲 ผู้สมัครทั้งหมด: <b>{len(candidates)}</b> คน\n"
            f"🏆 <b>ผู้ชนะ:</b> {first_name or '-'} (@{username or '-'})\n"
            f"   tg_id=<code>{tg_id}</code>\n"
            f"   user_id={user_id}\n\n"
            f"✅ ระบบ upgrade เป็น GOD ถาวรแล้ว + DM ลูกค้าแล้ว"
        )
        await admin_bot.send_message(chat_id=ADMIN_GROUP_ID, text=admin_msg, parse_mode="HTML")
        print(f"Admin notify sent")
    except Exception as exc:
        print(f"Admin notify failed: {exc}")


asyncio.run(main())
