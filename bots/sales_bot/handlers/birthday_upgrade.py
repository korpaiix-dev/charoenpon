"""Birthday Promo Upgrade — สิทธิ์เฉพาะลูกค้า TIER_500 ที่ร่วมโปรวันเกิดเฮียตั๋ง.

Flow:
1. ลูกค้ากด /upgrade ในบอท
2. ระบบเช็ค birthday_upgrade_offers — มีสิทธิ์ไหม + ยังไม่หมดอายุ
3. แสดงตัวเลือก 2 plan: GOD 3ด. ฿899 (รวมวันเหลือ TIER_500) | GOD ถาวร ฿1,999
4. ลูกค้าเลือก → ระบบ set context.user_data['selected_tier'] = 'BIRTHDAY_1299' หรือ 'BIRTHDAY_2499'
5. ลูกค้าส่งสลิป — Slip2Go verify → ระบบจับยอด 899/1999 → upgrade
"""
from __future__ import annotations

import logging
from datetime import datetime

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CommandHandler, CallbackQueryHandler, ContextTypes

from sqlalchemy import select, text as sql_text
from shared.database import get_session
from shared.models import User, Subscription, SubscriptionStatus, PackageTier

logger = logging.getLogger(__name__)


async def _get_offer(telegram_id: int) -> dict | None:
    async with get_session() as s:
        r = await s.execute(sql_text("""
            SELECT id, user_id, discount_god_3m_amount, discount_god_lifetime_amount,
                   expires_at, upgraded_to_tier
            FROM birthday_upgrade_offers
            WHERE telegram_id = :tg AND expires_at > NOW() AND upgraded_to_tier IS NULL
            LIMIT 1
        """), {"tg": telegram_id})
        row = r.fetchone()
        return dict(row._mapping) if row else None


async def _get_active_500_sub_days(user_id: int) -> int:
    """Return days remaining in active TIER_500 subscription (0 if none)."""
    async with get_session() as s:
        r = await s.execute(sql_text("""
            SELECT GREATEST(0, EXTRACT(DAY FROM end_date - NOW()))::int AS days
            FROM subscriptions sub
            JOIN packages pk ON pk.id = sub.package_id
            WHERE sub.user_id = :uid AND sub.status = 'ACTIVE'
              AND pk.tier = 'TIER_500'
              AND sub.end_date > NOW()
            ORDER BY sub.end_date DESC LIMIT 1
        """), {"uid": user_id})
        row = r.fetchone()
        return int(row.days) if row else 0


async def cmd_upgrade(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_user:
        return
    user_tg = update.effective_user.id
    offer = await _get_offer(user_tg)
    if not offer:
        await update.message.reply_text(
            "❌ <b>ไม่พบสิทธิ์อัปเกรด</b>\n\n"
            "สิทธิ์นี้สำหรับลูกค้าที่ร่วมโปร Birthday Sale เฮียตั๋ง เท่านั้นค่ะ\n"
            "📦 ดูแพ็คเกจปกติ พิมพ์ /packages",
            parse_mode="HTML",
        )
        return

    bonus_days = await _get_active_500_sub_days(offer["user_id"])
    expires = offer["expires_at"].strftime("%d %b %Y %H:%M")

    msg = (
        "🎂 <b>สิทธิพิเศษ — Birthday Sale เฮียตั๋ง</b>\n"
        "━━━━━━━━━━━━━━━\n\n"
        f"⏰ สิทธิ์นี้หมดอายุ: <b>{expires} น.</b>\n\n"
        "━━━━━━━━━━━━━━━\n"
        "💎 <b>GOD MODE 3 เดือน</b>\n"
        f"   <s>฿1,299</s> → <b>฿899</b> (ลด ฿400)\n"
        f"   ✨ Bonus: รวมวันเหลือของ OF+VIP <b>+{bonus_days} วัน</b>\n\n"
        "👑 <b>GOD MODE ถาวร</b>\n"
        f"   <s>฿2,499</s> → <b>฿1,999</b> (ลด ฿500)\n"
        f"   ✨ สิทธิ์ตลอดชีพ ไม่มีหมดอายุ\n\n"
        "━━━━━━━━━━━━━━━\n"
        "เลือกแพ็คเกจที่ต้องการ:"
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"💎 GOD 3ด. ฿899 (+{bonus_days} วัน)", callback_data="bd_upgrade_1299")],
        [InlineKeyboardButton("👑 GOD ถาวร ฿1,999", callback_data="bd_upgrade_2499")],
    ])
    await update.message.reply_text(msg, parse_mode="HTML", reply_markup=kb)


async def cb_upgrade_pick(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    try:
        await q.answer()
    except Exception:
        pass  # callback may be too old / already answered
    if not q.from_user:
        return
    user_tg = q.from_user.id
    offer = await _get_offer(user_tg)
    if not offer:
        await q.edit_message_text("❌ สิทธิ์อัปเกรดหมดอายุหรือไม่พบค่ะ")
        return

    data = q.data  # bd_upgrade_1299 or bd_upgrade_2499
    if data == "bd_upgrade_1299":
        tier = "BIRTHDAY_1299"
        price = 899
        label = "GOD MODE 3 เดือน (Birthday)"
    elif data == "bd_upgrade_2499":
        tier = "BIRTHDAY_2499"
        price = 1999
        label = "GOD MODE ถาวร (Birthday)"
    else:
        return

    # Set selected tier so payment handler knows what to do
    context.user_data["selected_tier"] = tier
    context.user_data["birthday_offer_id"] = offer["id"]

    from shared.receiver_pool import pick_random
    acct = await pick_random()
    if not acct:
        from shared.contact_admin import contact_admin_kb as _cak
        await q.edit_message_text("⚠️ ระบบไม่สามารถสร้างคำสั่งซื้อได้ในขณะนี้ค่ะ กดปุ่มด้านล่างทักแอดมิน", reply_markup=_cak())
        return

    msg = (
        f"💳 <b>คำสั่งซื้อ: {label}</b>\n"
        "━━━━━━━━━━━━━━━\n\n"
        f"💰 ยอด: <b>฿{price:,}</b>\n\n"
        f"🏦 โอนเข้า: <b>{acct['bank_name_th']}</b>\n"
        f"👤 ชื่อบัญชี: <code>{acct['owner_name']}</code>\n"
        f"🔢 เลขบัญชี: <code>{acct['account_no']}</code>\n\n"
        f"📱 PromptPay: <code>{acct.get('promptpay_number','')}</code>\n\n"
        "━━━━━━━━━━━━━━━\n"
        f"📸 ส่ง <b>สลิปการโอน</b> ในแชทนี้\n"
        f"💸 หรือซอง <b>TrueMoney</b> ยอด ฿{price:,}\n"
        "⚡ ระบบจะอัปเกรดอัตโนมัติทันที"
    )
    await q.edit_message_text(msg, parse_mode="HTML")

    # Send QR PromptPay (consistent with packages.py / shaker.py)
    qr_url = acct.get("qr_url") or "https://img2.pic.in.th/-2026-03-15-143743.png"
    try:
        await context.bot.send_photo(
            chat_id=q.message.chat_id,
            photo=qr_url,
            caption=f"📱 สแกน QR PromptPay เพื่อโอน <b>฿{price:,}</b>\nแล้วส่งสลิปมาที่แชทนี้เลยค่ะ 🙏",
            parse_mode="HTML",
        )
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning("Birthday upgrade QR send failed: %s", exc)


def get_birthday_upgrade_handlers() -> list:
    return [
        CommandHandler("upgrade", cmd_upgrade),
        CallbackQueryHandler(cb_upgrade_pick, pattern=r"^bd_upgrade_(1299|2499)\$"),
    ]


__all__ = ["cmd_upgrade", "cb_upgrade_pick", "get_birthday_upgrade_handlers"]
