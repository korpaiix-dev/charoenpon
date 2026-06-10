"""ห้องมีคนชัก — Lottery group ฿100/month.

Flow:
- /shaker → แสดงข้อมูล + ปุ่มซื้อ
- User กดซื้อ → ระบบหาเลขว่าง (00-99) → set selected_tier='100' + เก็บเลขใน user_data
- User โอน 100 → Slip2Go verify → _approve_payment เห็น tier='100' → branch SHAKER:
  - INSERT shaker_tickets (number assigned)
  - Subscription TIER_100 30 วัน (already in normal flow via Package)
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


async def cmd_shaker(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    pool = await _pool_status()
    msg = (
        "🎲 <b>ห้องมีคนชัก — ลอตเตอรี่กลุ่ม VIP</b>\n"
        "━━━━━━━━━━━━━━━\n\n"
        "💰 ราคา: <b>฿100</b> / ใบ\n"
        "⏰ สิทธิ์เข้ากลุ่ม: <b>30 วัน</b>\n"
        f"🎫 เลขที่เหลือ: <b>{pool['available']}/{pool['total']}</b>\n\n"
        "━━━━━━━━━━━━━━━\n"
        "🎁 <b>รางวัล: GOD MODE 3 เดือน</b> (มูลค่า ฿1,299)\n"
        "📅 สุ่มทุกวัน <b>จันทร์ 12:00 น.</b>\n"
        "🎯 โอกาสถูก: <b>1%</b> ต่อ 1 ใบ\n\n"
        "📋 <b>กฎ:</b>\n"
        "• ระบบสุ่มเลขให้อัตโนมัติ (ไม่ซ้ำ)\n"
        "• ซื้อกี่ใบก็ได้ — เพิ่มโอกาส\n"
        "• ผู้ถูกรางวัล lock 90 วัน (ขณะใช้ GOD 3 เดือน)\n\n"
    )
    if pool['available'] == 0:
        msg += "⚠️ <b>เลขเต็มแล้ว!</b> รอรอบใหม่สัปดาห์หน้า"
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("📋 ดูเลขของฉัน /myticket", callback_data="shaker_myticket")],
        ])
    else:
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton(f"🎫 ซื้อ 1 ใบ ฿100", callback_data="shaker_buy_1")],
            [InlineKeyboardButton(f"🎫🎫 ซื้อ 2 ใบ ฿200", callback_data="shaker_buy_2")],
            [InlineKeyboardButton(f"🎫🎫🎫 ซื้อ 5 ใบ ฿500", callback_data="shaker_buy_5")],
        ])
    await update.message.reply_text(msg, parse_mode="HTML", reply_markup=kb)


async def cmd_myticket(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_user:
        return
    tickets = await _user_tickets(update.effective_user.id)
    if not tickets:
        await update.message.reply_text(
            "❌ คุณยังไม่มีเลขลอตเตอรี่ค่ะ\n\n"
            "พิมพ์ /shaker เพื่อซื้อเลขลุ้น GOD MODE 3 เดือน 🎁",
            parse_mode="HTML",
        )
        return
    lines = ["🎫 <b>เลขของคุณ — ห้องมีคนชัก</b>", "━━━━━━━━━━━━━━━", ""]
    for t in tickets:
        exp = t['expires_at'].strftime("%d %b %Y")
        status_emoji = "🟢" if t['status'] == 'ACTIVE' else "🏆"
        status_th = "กำลังลุ้น" if t['status'] == 'ACTIVE' else "ถูกรางวัล!"
        lines.append(f"{status_emoji} เลข <b>{t['number']}</b> — {status_th}  (ถึง {exp})")
    lines.append("")
    lines.append("📅 สุ่มทุก<b>จันทร์ 12:00 น.</b>")
    lines.append("💎 รางวัล: GOD MODE 3 เดือน (฿1,299)")
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


async def cb_shaker_buy(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    if not q.from_user:
        return
    # data: shaker_buy_<n>
    n = int(q.data.rsplit("_", 1)[1])
    price = 100 * n
    pool = await _pool_status()
    if pool['available'] < n:
        await q.edit_message_text(
            f"⚠️ เลขในระบบเหลือ {pool['available']} ใบเท่านั้นค่ะ\n"
            "กรุณาลดจำนวนลง หรือลองอีกครั้งสัปดาห์หน้า",
        )
        return
    context.user_data['selected_tier'] = '100'
    context.user_data['shaker_count'] = n

    from shared.receiver_pool import pick_random
    acct = await pick_random()
    if not acct:
        await q.edit_message_text("⚠️ ระบบไม่พร้อม กรุณาทักแอดมินค่ะ")
        return

    msg = (
        f"💳 <b>คำสั่งซื้อ: ห้องมีคนชัก × {n} ใบ</b>\n"
        "━━━━━━━━━━━━━━━\n\n"
        f"💰 ยอด: <b>฿{price:,}</b>\n"
        f"🎫 จำนวนเลข: <b>{n} ใบ</b> (โอกาสถูก {n}%)\n\n"
        f"🏦 โอนเข้า: <b>{acct['bank_name_th']}</b>\n"
        f"👤 ชื่อบัญชี: <code>{acct['owner_name']}</code>\n"
        f"🔢 เลขบัญชี: <code>{acct['account_no']}</code>\n"
        f"📱 PromptPay: <code>{acct.get('promptpay_number','')}</code>\n\n"
        "━━━━━━━━━━━━━━━\n"
        f"📸 ส่งสลิปการโอนในแชทนี้\n"
        "⚡ ระบบจะสุ่มเลขให้อัตโนมัติทันที"
    )
    await q.edit_message_text(msg, parse_mode="HTML")


async def cb_view_shaker(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Callback for main menu 'ห้องมีคนชัก' button."""
    q = update.callback_query
    await q.answer()
    if not q.message or not q.from_user:
        return
    pool = await _pool_status()
    parts = [
        "🎲 <b>ห้องมีคนชัก — ลอตเตอรี่กลุ่ม VIP</b>",
        "━━━━━━━━━━━━━━━",
        "",
        "💰 ราคา: <b>฿100</b> / ใบ",
        "⏰ สิทธิ์เข้ากลุ่ม: <b>30 วัน</b>",
        f"🎫 เลขที่เหลือ: <b>{pool['available']}/{pool['total']}</b>",
        "",
        "━━━━━━━━━━━━━━━",
        "🎁 <b>รางวัล: GOD MODE 3 เดือน</b> (มูลค่า ฿1,299)",
        "📅 สุ่มทุกวัน <b>จันทร์ 12:00 น.</b>",
        "🎯 โอกาสถูก: <b>1%</b> ต่อ 1 ใบ",
        "",
        "📋 <b>กฎ:</b>",
        "• ระบบสุ่มเลขให้อัตโนมัติ (ไม่ซ้ำ)",
        "• ซื้อกี่ใบก็ได้ — เพิ่มโอกาส",
        "• ผู้ถูกรางวัล lock 90 วัน (ขณะใช้ GOD 3 เดือน)",
        "",
    ]
    msg = "\n".join(parts)
    if pool['available'] == 0:
        msg += "⚠️ <b>เลขเต็มแล้ว!</b> รอรอบใหม่สัปดาห์หน้า"
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 กลับเมนูหลัก", callback_data="back_main")],
        ])
    else:
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🎫 ซื้อ 1 ใบ ฿100", callback_data="shaker_buy_1")],
            [InlineKeyboardButton("🎫🎫 ซื้อ 2 ใบ ฿200", callback_data="shaker_buy_2")],
            [InlineKeyboardButton("🎫🎫🎫 ซื้อ 5 ใบ ฿500", callback_data="shaker_buy_5")],
            [InlineKeyboardButton("🔙 กลับเมนูหลัก", callback_data="back_main")],
        ])
    try:
        await q.edit_message_text(msg, parse_mode="HTML", reply_markup=kb)
    except Exception:
        await q.message.reply_text(msg, parse_mode="HTML", reply_markup=kb)


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


__all__ = ["cmd_shaker", "cmd_myticket", "cb_shaker_buy", "get_shaker_handlers", "assign_shaker_numbers", "cb_view_shaker"]
