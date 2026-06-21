"""ห้องมีคนชัก — Lottery group ฿100/month.

Flow:
- /shaker หรือ ปุ่ม view_shaker → แสดงข้อมูล + ปุ่มซื้อ
- ลูกค้ากดซื้อ → ระบบหาเลขว่าง → set selected_tier='100' + shaker_count
- ลูกค้าโอน 100 × N → Slip2Go verify → _approve_payment เห็น tier='100' → SHAKER branch:
  - INSERT shaker_tickets (random unique numbers from pool)
  - Subscription TIER_100 30 วัน (normal flow via Package)
- /myticket → ดูเลขทั้งหมดของ user
"""
from __future__ import annotations

import logging
import random
from datetime import datetime, timedelta

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CommandHandler, CallbackQueryHandler, ContextTypes

from sqlalchemy import text as sql_text
from shared.database import get_session

logger = logging.getLogger(__name__)


async def _get_used_numbers() -> set:
    """Numbers currently active (not expired, not WON)."""
    async with get_session() as s:
        r = await s.execute(sql_text("""
            SELECT number FROM shaker_tickets
            WHERE status = 'ACTIVE' AND expires_at > NOW()
        """))
        return {row[0] for row in r.all()}


async def _pool_status() -> dict:
    used = await _get_used_numbers()
    total = 100
    available = total - len(used)
    return {"used": len(used), "available": available, "total": total}


async def _user_tickets(telegram_id: int) -> list[dict]:
    async with get_session() as s:
        r = await s.execute(sql_text("""
            SELECT number, purchased_at, expires_at, status
            FROM shaker_tickets
            WHERE telegram_id = :tg AND status IN ('ACTIVE','WON')
              AND expires_at > NOW()
            ORDER BY purchased_at DESC
        """), {"tg": telegram_id})
        return [dict(row._mapping) for row in r.all()]


def _build_main_caption(available: int) -> str:
    """Build the main 'ห้องมีคนชัก' caption with dynamic available count."""
    lines = [
        "🎰 <b>กิจกรรมห้องมีคนชัก</b>",
        "━━━━━━━━━━━━━━━",
        "",
        "💎 <b>จ่าย ฿100 — ได้ครบ 3 อย่าง:</b>",
        "",
        "🎫 <b>1) เลขลุ้น 1 ตัว (00-99)</b>",
        "   → ลุ้นทุกจันทร์ตลอด 30 วัน (รวม 4 ครั้ง!)",
        "",
        "🏠 <b>2) เข้ากลุ่ม \"ห้องมีคนชัก\" 30 วัน</b>",
        "   → ติดตามข่าวสาร ดูคอนเทนต์พิเศษ งานทางบ้าน นร นศ",
        "",
        "🏆 <b>3) สิทธิ์ลุ้น GOD MODE 3 เดือน</b>",
        "   → มูลค่า ฿1,299 — คุ้มเกินคุ้ม!",
        "",
        "━━━━━━━━━━━━━━━",
        "🎯 <b>ซื้อหลายใบ = เพิ่มโอกาส</b>",
        "• 1 ใบ → 1%",
        "• 2 ใบ → 2%",
        "• 5 ใบ → 5%",
        "",
        "📋 <b>วิธีเล่น:</b>",
        "1️⃣ กดปุ่มซื้อด้านล่าง",
        "2️⃣ โอนเงินตามที่ระบบแจ้ง",
        "3️⃣ ส่งสลิป → รับเลขทันที ⚡",
        "4️⃣ ลุ้นทุกจันทร์ 21:00 น. (อิงหวยลาว)",
        "",
        "💡 <b>ถ้าถูกรางวัล:</b>",
        "✅ ได้ GOD 3 เดือนทันที (ใช้ดูทุกห้อง VIP)",
        "✅ ระหว่างใช้ GOD จะพักการลุ้นไว้ก่อน",
        "✅ พอ GOD หมด — กลับมาลุ้นใหม่ได้",
        "",
        "🎫 <b>เลขที่เหลือ:</b> " + str(available) + "/100 ใบ",
        "⚠️ จำกัด 100 ใบ/รอบ — รีบเลือก!",
    ]
    return "\n".join(lines)


def _build_buy_keyboard(available: int, *, with_back: bool = False) -> InlineKeyboardMarkup:
    if available == 0:
        rows = []
    else:
        rows = [
            [InlineKeyboardButton("🎫 ซื้อ 1 ใบ ฿100", callback_data="shaker_buy_1")],
            [InlineKeyboardButton("🎫🎫 ซื้อ 2 ใบ ฿200", callback_data="shaker_buy_2")],
            [InlineKeyboardButton("🎫🎫🎫 ซื้อ 5 ใบ ฿500", callback_data="shaker_buy_5")],
        ]
    if with_back:
        rows.append([InlineKeyboardButton("🔙 กลับเมนูหลัก", callback_data="back_main")])
    return InlineKeyboardMarkup(rows)


async def cmd_shaker(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/shaker command — direct entry."""
    if not update.message:
        return
    pool = await _pool_status()
    msg = _build_main_caption(pool["available"])
    if pool["available"] == 0:
        msg += "\n\n⚠️ <b>เลขเต็มแล้ว!</b> รอรอบใหม่สัปดาห์หน้า"
    kb = _build_buy_keyboard(pool["available"], with_back=False)
    await update.message.reply_text(msg, parse_mode="HTML", reply_markup=kb)


async def cb_view_shaker(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Callback for main menu 'กิจกรรมห้องมีคนชัก' button."""
    q = update.callback_query
    try:
        await q.answer()
    except Exception:
        pass  # callback may be too old / already answered
    if not q.message or not q.from_user:
        return
    pool = await _pool_status()
    msg = _build_main_caption(pool["available"])
    if pool["available"] == 0:
        msg += "\n\n⚠️ <b>เลขเต็มแล้ว!</b> รอรอบใหม่สัปดาห์หน้า"
    kb = _build_buy_keyboard(pool["available"], with_back=True)
    try:
        await q.edit_message_text(msg, parse_mode="HTML", reply_markup=kb)
    except Exception:
        # original may be a photo — send new
        await q.message.reply_text(msg, parse_mode="HTML", reply_markup=kb)


async def cmd_myticket(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_user:
        return
    tickets = await _user_tickets(update.effective_user.id)
    if not tickets:
        await update.message.reply_text(
            "❌ คุณยังไม่มีเลขลอตเตอรี่ค่ะ\n\n"
            "กด <b>🎰 กิจกรรมห้องมีคนชัก</b> ในเมนู หรือพิมพ์ /shaker",
            parse_mode="HTML",
        )
        return
    lines = ["🎫 <b>เลขของคุณ — ห้องมีคนชัก</b>", "━━━━━━━━━━━━━━━", ""]
    for t in tickets:
        exp = t["expires_at"].strftime("%d %b %Y")
        status_emoji = "🟢" if t["status"] == "ACTIVE" else "🏆"
        status_th = "กำลังลุ้น" if t["status"] == "ACTIVE" else "ถูกรางวัล!"
        lines.append(f"{status_emoji} เลข <b>{t['number']}</b> — {status_th}  (ถึง {exp})")
    lines.append("")
    lines.append("📅 สุ่มทุก<b>จันทร์ 21:00 น.</b> (อิงหวยลาว)")
    lines.append("💎 รางวัล: GOD MODE 3 เดือน (฿1,299)")
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


async def cb_shaker_buy(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    try:
        await q.answer()
    except Exception:
        pass  # callback may be too old / already answered
    if not q.from_user:
        return
    n = int(q.data.rsplit("_", 1)[1])
    price = 100 * n
    pool = await _pool_status()
    if pool["available"] < n:
        await q.edit_message_text(
            f"⚠️ เลขในระบบเหลือ {pool['available']} ใบเท่านั้นค่ะ\n"
            "กรุณาลดจำนวนลง หรือลองอีกครั้งสัปดาห์หน้า",
        )
        return
    context.user_data["selected_tier"] = "100"
    context.user_data["shaker_count"] = n

    from shared.receiver_pool import pick_random
    acct = await pick_random()
    if not acct:
        from shared.contact_admin import contact_admin_kb as _cak
        await q.edit_message_text("⚠️ ระบบไม่พร้อม กดปุ่มด้านล่างทักแอดมินได้เลยค่ะ", reply_markup=_cak())
        return

    msg = (
        f"💳 <b>คำสั่งซื้อ: ห้องมีคนชัก × {n} ใบ</b>\n"
        "━━━━━━━━━━━━━━━\n\n"
        f"💰 ยอด: <b>฿{price:,}</b>\n"
        f"🎫 จำนวนเลข: <b>{n} ใบ</b> (โอกาสถูก {n}%)\n\n"
        f"📌 <b>วิธีชำระเงิน:</b>\n"
        f"1️⃣ สแกน QR PromptPay ด้านล่าง หรือโอนตามยอด\n"
        f"2️⃣ ส่งสลิปโอนในแชทนี้\n"
        f"3️⃣ ระบบสุ่มเลขให้อัตโนมัติทันที\n\n"
        f"💳 <b>ช่องทางชำระ:</b>\n"
        f"🏦 {acct['bank_name_th']}\n"
        f"👤 <code>{acct['owner_name']}</code>\n"
        f"🔢 <code>{acct['account_no']}</code>\n"
        f"📱 PromptPay: <code>{acct.get('promptpay_number','')}</code>\n\n"
        "━━━━━━━━━━━━━━━\n"
        "⚠️ กรุณาโอนตามยอด <b>฿{}</b> เท่านั้นค่ะ".format(price)
    )
    await q.edit_message_text(msg, parse_mode="HTML")

    # Send QR PromptPay (matches packages.py pattern — every tier shows QR)
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
        logging.getLogger(__name__).warning("Shaker QR send failed: %s", exc)


def get_shaker_handlers() -> list:
    return [
        CommandHandler("shaker", cmd_shaker),
        CommandHandler("myticket", cmd_myticket),
        CallbackQueryHandler(cb_shaker_buy, pattern=r"^shaker_buy_\d+$"),
        CallbackQueryHandler(cb_view_shaker, pattern=r"^view_shaker$"),
    ]


# Helper functions for _approve_payment to call
async def assign_shaker_numbers(user_id: int, telegram_id: int, count: int, payment_id: int) -> list[str]:
    """Assign 'count' unique numbers (00-99) to user. Returns assigned numbers as strings."""
    used = await _get_used_numbers()
    available = [f"{i:02d}" for i in range(100) if f"{i:02d}" not in used]
    if len(available) < count:
        raise ValueError(f"Pool exhausted: need {count}, available {len(available)}")
    chosen = random.sample(available, count)
    expires_at = datetime.utcnow() + timedelta(days=30)
    async with get_session() as s:
        for num in chosen:
            await s.execute(sql_text("""
                INSERT INTO shaker_tickets (user_id, telegram_id, number, payment_id, purchased_at, expires_at, status)
                VALUES (:uid, :tg, :num, :pid, NOW(), :exp, 'ACTIVE')
            """), {"uid": user_id, "tg": telegram_id, "num": num, "pid": payment_id, "exp": expires_at})
        await s.commit()
    return chosen


__all__ = [
    "cmd_shaker", "cmd_myticket", "cb_shaker_buy", "cb_view_shaker",
    "get_shaker_handlers", "assign_shaker_numbers",
]
