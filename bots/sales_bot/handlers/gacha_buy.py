"""Gachapon buy flow v2 — UI for purchasing spin credits.

Customer presses "🎁 เติมสิทธิ์หมุนกาชาปอง" → shows 3 bundles → picks one →
shows receiver bank info + sends QR code photo (same UX as buy_package_callback).
"""
from __future__ import annotations

import logging

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes

from sqlalchemy import text as sql_text
from shared.database import get_session
from shared.bot_messages import render_or_fallback

logger = logging.getLogger(__name__)


BUNDLES = [
    {"key": "1",  "spins": 1,  "price": 99,  "tier": "GACHA_1",
     "label": "1 หมุน",         "discount": 0,
     "emoji": "🎫"},
    {"key": "3",  "spins": 3,  "price": 270, "tier": "GACHA_3",
     "label": "3 หมุน",         "discount": 27,
     "emoji": "🎫🎫🎫"},
    {"key": "10", "spins": 10, "price": 890, "tier": "GACHA_10",
     "label": "10 หมุน",        "discount": 100,
     "emoji": "🎫×10"},
]

DEFAULT_QR_URL = "https://img2.pic.in.th/-2026-03-15-143743.png"


async def _get_user_credit_balance(telegram_id: int) -> dict:
    """Return current spin credits + discount balance."""
    async with get_session() as s:
        c = await s.execute(sql_text(
            "SELECT credits, total_spun FROM gachapon_credits WHERE telegram_id = :tg"
        ), {"tg": telegram_id})
        crow = c.fetchone()
        d = await s.execute(sql_text(
            "SELECT balance FROM user_discount_credits WHERE telegram_id = :tg"
        ), {"tg": telegram_id})
        drow = d.fetchone()
    return {
        "credits": int(crow[0]) if crow else 0,
        "total_spun": int(crow[1]) if crow else 0,
        "discount": float(drow[0]) if drow else 0,
    }


def _build_buy_caption(state: dict) -> str:
    lines = [
        "🎰 <b>เติมสิทธิ์หมุนกาชาปอง</b>",
        "━━━━━━━━━━━━━━━",
        "",
        f"🎫 สิทธิ์หมุนปัจจุบัน: <b>{state['credits']}</b> ครั้ง",
    ]
    if state["discount"] > 0:
        lines.append(f"💰 ส่วนลดสะสม: <b>฿{state['discount']:,.0f}</b>")
    lines.extend([
        "",
        "━━━━━━━━━━━━━━━",
        "🎁 <b>เลือกแพ็คเกจ:</b>",
        "",
        "🎫 1 หมุน          <b>฿99</b>",
        "🎫🎫🎫 3 หมุน      <b>฿270</b>  <i>(ลด ฿27)</i>",
        "🎫×10 หมุน        <b>฿890</b>  <i>(ลด ฿100)</i>",
        "",
        "━━━━━━━━━━━━━━━",
        "🏆 <b>รางวัลที่ลุ้นได้</b> 9 ประเภท",
        "✓ ส่วนลด ฿50 (สะสมได้)",
        "✓ ชุดคลิป A/B/C",
        "✓ ห้องมีคนชัก / VIP / OF+VIP",
        "✓ GOD 90 วัน / GOD ถาวร 🌟",
    ])
    return "\n".join(lines)


def _build_buy_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎫 ซื้อ 1 หมุน ฿99",  callback_data="gacha_buy_1")],
        [InlineKeyboardButton("🎫🎫🎫 ซื้อ 3 หมุน ฿270",  callback_data="gacha_buy_3")],
        [InlineKeyboardButton("🎫×10 ซื้อ 10 หมุน ฿890", callback_data="gacha_buy_10")],
        [InlineKeyboardButton("🔙 กลับเมนูหลัก", callback_data="back_main")],
    ])


async def cmd_gacha_buy(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/gacha_buy command."""
    if not update.message or not update.effective_user:
        return
    state = await _get_user_credit_balance(update.effective_user.id)
    await update.message.reply_text(
        _build_buy_caption(state),
        parse_mode="HTML",
        reply_markup=_build_buy_keyboard(),
    )


async def cb_view_gacha_buy(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Callback: 'view_gacha_buy' from main menu."""
    q = update.callback_query
    try:
        await q.answer()
    except Exception:
        pass  # callback may be too old / already answered
    if not q.message or not q.from_user:
        return
    state = await _get_user_credit_balance(q.from_user.id)
    msg = _build_buy_caption(state)
    kb = _build_buy_keyboard()
    try:
        await q.edit_message_text(msg, parse_mode="HTML", reply_markup=kb)
    except Exception:
        await q.message.reply_text(msg, parse_mode="HTML", reply_markup=kb)


async def cb_gacha_pick(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Customer picked a bundle → show full payment instructions + QR code photo."""
    q = update.callback_query
    try:
        await q.answer()
    except Exception:
        pass  # callback may be too old / already answered
    if not q.from_user:
        return
    key = q.data.rsplit("_", 1)[1]  # gacha_buy_1 → "1"
    bundle = next((b for b in BUNDLES if b["key"] == key), None)
    if not bundle:
        await q.edit_message_text("⚠️ ตัวเลือกไม่ถูกต้อง")
        return

    # Set selected_tier so existing payment hook routes to gacha credit add
    context.user_data["selected_tier"] = bundle["tier"]
    context.user_data["gacha_spins"] = bundle["spins"]
    context.user_data["selected_price"] = str(bundle["price"])

    # Pick a receiver account
    from shared.receiver_pool import pick_random
    acct = await pick_random()
    if not acct:
        from shared.contact_admin import contact_admin_kb as _cak
        await q.edit_message_text(await render_or_fallback("system_not_ready", "⚠️ ระบบไม่พร้อม กดปุ่มด้านล่างทักแอดมินได้เลยค่ะ"), reply_markup=_cak())
        return

    price_str = f"{bundle['price']:,}"

    # ============ Step 1: Order confirmation + payment instructions ============
    parts = [
        f"💳 <b>คำสั่งซื้อ: กาชาปอง {bundle['spins']} หมุน</b>",
        "━━━━━━━━━━━━━━━",
        "",
        f"💰 ยอดที่ต้องชำระ: <b>฿{price_str}</b>",
        f"🎫 จำนวนสิทธิ์: <b>{bundle['spins']} หมุน</b>",
    ]
    if bundle["discount"] > 0:
        parts.append(f"💚 ประหยัด ฿{bundle['discount']} (เทียบราคาเต็ม ฿{bundle['spins']*99:,})")
    parts.extend([
        "",
        "📌 <b>วิธีชำระเงิน:</b>",
        "1️⃣ สแกน QR PromptPay ด้านล่าง หรือโอนเงินตามยอด",
        "2️⃣ ส่งสลิปโอนเงิน <b>หรือ</b> ลิงก์ซอง TrueMoney",
        "3️⃣ ระบบยืนยันอัตโนมัติ → ใส่สิทธิ์หมุนให้ทันที",
        "",
        "💳 <b>ช่องทางชำระ:</b>",
        "🏦 PromptPay / โอนธนาคาร → ส่งรูปสลิปในแชทนี้",
        "💸 TrueMoney Wallet → ส่งลิงก์ <code>gift.truemoney.com</code>",
        "",
        "━━━━━━━━━━━━━━━",
        "🏦 <b>ข้อมูลบัญชีรับเงิน:</b>",
        f"• ธนาคาร: <b>{acct['bank_name_th']}</b>",
        f"• ชื่อบัญชี: <code>{acct['owner_name']}</code>",
        f"• เลขบัญชี: <code>{acct['account_no']}</code>",
        f"• PromptPay: <code>{acct.get('promptpay_number','')}</code>",
        "",
        f"⚠️ <b>กรุณาโอน ฿{price_str} เท่านั้น</b> (ห้ามขาด/เกิน)",
    ])

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🎰 เปลี่ยนแพ็คเกจ", callback_data="view_gacha_buy")],
        [InlineKeyboardButton("🔙 กลับเมนูหลัก", callback_data="back_main")],
    ])

    await q.edit_message_text("\n".join(parts), parse_mode="HTML", reply_markup=keyboard)

    # ============ Step 2: Send QR code photo ============
    qr_url = acct.get("qr_url") or DEFAULT_QR_URL
    try:
        await context.bot.send_photo(
            chat_id=q.message.chat_id,
            photo=qr_url,
            caption=(
                f"📱 <b>สแกน QR PromptPay เพื่อโอน ฿{price_str}</b>\n\n"
                "หลังโอนเสร็จ → ส่งรูปสลิปมาที่แชทนี้เลยค่ะ 🙏\n\n"
                "💸 ถ้าใช้ TrueMoney → ส่งลิงก์ซอง gift.truemoney.com มาแทนได้เลย"
            ),
            parse_mode="HTML",
        )
    except Exception as exc:
        logger.warning("Failed to send Gacha QR: %s", exc)
        # Fallback: tell customer in chat where to look
        try:
            await q.message.reply_text(
                "⚠️ ส่ง QR ไม่สำเร็จ — ใช้เลขบัญชี/PromptPay ด้านบนแทนได้เลยค่ะ"
            )
        except Exception:
            pass


def get_gacha_buy_handlers() -> list:
    return [
        CommandHandler("gacha_buy", cmd_gacha_buy),
        CallbackQueryHandler(cb_view_gacha_buy, pattern=r"^view_gacha_buy$"),
        CallbackQueryHandler(cb_gacha_pick, pattern=r"^gacha_buy_(1|3|10)$"),
    ]


__all__ = [
    "cmd_gacha_buy", "cb_view_gacha_buy", "cb_gacha_pick",
    "get_gacha_buy_handlers",
]
