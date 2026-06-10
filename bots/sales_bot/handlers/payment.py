# >>> MAY26_COMBO_PROMO <<<  # patched payment.py
"""Payment handler - Sales Bot แพร.

SOP ตรวจสลิป:
- รูป → OCR pytesseract
- gift.truemoney.com → TrueMoney link
- QR/อื่น → ปฏิเสธ

ตรวจ 3 ข้อ:
1. ยอดตรงกับแพ็กเกจ
2. ไม่เกิน 24 ชั่วโมง
3. ไม่ซ้ำ (slip_hash)

ผ่าน → อนุมัติ + เพิ่มกลุ่ม
สงสัย → Hold + แจ้ง Discord
ไม่ผ่าน → Reject + เหตุผล
log admin_log ทุกครั้ง
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

import httpx
import pytesseract
from PIL import Image
from sqlalchemy import select
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from shared.database import get_session
from shared.models import (
    GroupRegistry,
    Package,
    PackageTier,
    Payment,
    PaymentMethod,
    PaymentStatus,
    Subscription,
    SubscriptionStatus,
    User,
)
from shared.slip2go import verify_slip_image, Slip2GoError, receiver_is_boss, receiver_match_pool, amount_to_tier
from shared.endmonth_vip_promo import (
    PROMO_2499_PRICE,
    PROMO_DATE_TEXT,
    PROMO_PRICE,
    PROMO_500_PRICE,
    PROMO_1299_PRICE,
    PROMO_MAY_DATE_TEXT,
    get_effective_price_for_tier,
    is_endmonth_vip_promo_active,
    is_may_combo_promo_active,
    is_lucky_6_active,
)
# Strangler-fig Round 1
from bots.sales_bot.payment_util.utils import (
    _check_date_within_24h,
    _extract_amount_from_ocr,
    _looks_like_non_slip_ad,
    _notify_discord,
)
# Strangler-fig Round 2-3

from bots.sales_bot.payment_util.ai_helpers import (
    _ai_screen_image,
    _ai_read_slip,
    _ocr_slip_image,
)
# Strangler-fig Round 5
from bots.sales_bot.payment_util.truemoney_handler import handle_truemoney_link
from bots.sales_bot.payment_util.promo_helpers import (
    _get_active_promo_for_user,
    _verify_truemoney_link,
)
# Strangler-fig Round 4
from bots.sales_bot.payment_util.approve import _approve_payment

from shared.songkran_promo import get_group_display_title
from shared.utils import (
    check_duplicate_slip,
    compute_slip_hash,
    format_datetime_thai,
    format_thb,
    log_admin_action,
)

logger = logging.getLogger(__name__)

DISCORD_WEBHOOK_URL: str = os.environ.get("DISCORD_WEBHOOK_URL", "")

NON_SLIP_AD_KEYWORDS = (
    "เครดิตฟรี",
    "เครดิตฟรี",
    "เว็บพนัน",
    "คาสิโน",
    "บาคาร่า",
    "สล็อต",
    "ufa",
    "ufabet",
    "casino",
    "เครดิตฟรี50",
    "เครดิต ฟรี",
    "ปั่นหมุน",
    "ฝากรับ",
    "รับฟรี",
    "โปรโมชันแนะนำ",
    "โปรโมชั่นแนะนำ",
    # # >>> CASINO_BLOCK <<< — added 2026-06-02
    # Casino brand names + slot keywords commonly seen in ads sent as fake slips
    "nova777",
    "nova 777",
    ".online",
    "knockout",
    "dish delights",
    "คอมโบทำเงิน",
    "ทำเงิน",
    "แตกแจกถอน",
    "เบทละ",
    "ก้อนโต",
    "cashback",
    "วงล้อนำโชค",
    "วงล้อ",
    "แนะนำเพื่อน",
    "คลิกเลย",
    "joker",
    "pgslot",
    "pg slot",
    "สล็อต",
    "slotxo",
    "ufabet",
    "ufa",
    "lava",
    "ฝากเครดิต",
    "ฝาก-ถอน",
    "ฝาก ถอน",
    "ฟรีสปิน",
    "free spin",
    "bonus",
    "โบนัส",
    "หวย",
    "บาคาร่า",
    "baccarat",
)




async def _get_effective_price(tier: str, context_user_data: dict) -> Decimal:
    """Get effective price for a tier — delegates to shared.pricing.

    Order of precedence: comeback per-user promo > flash_sale_id (active record)
    > shared.pricing.effective_price (campaign/endmonth/base).
    """
    from shared.pricing import effective_price as _hub_effective_price, TIER_PRICES as _HUB_TIER_PRICES
    base_price = _HUB_TIER_PRICES.get(tier, Decimal("0"))

    # 1. Comeback promo — per-user (validates against comeback_dm_log)
    comeback_promo = context_user_data.get("comeback_promo")
    if comeback_promo:
        from bots.sales_bot.comeback_dm import validate_promo_code
        promo = await validate_promo_code(comeback_promo)
        if promo:
            discount_pct = promo["discount_pct"]
            return Decimal(str(int(base_price * (100 - discount_pct) / 100)))

    # 2. Active Flash Sale record (dynamic from flash_sales table) — TIER_300 only legacy
    flash_sale_id = context_user_data.get("flash_sale_id")
    if flash_sale_id and tier == "300":
        from bots.sales_bot.handlers.flash_sale import get_flash_sale_price
        from shared.database import get_session
        from shared.models import Package, PackageTier
        from sqlalchemy import select
        async with get_session() as session:
            pkg_result = await session.execute(
                select(Package).where(Package.tier == PackageTier.TIER_300)
            )
            package = pkg_result.scalar_one_or_none()
            if package:
                flash_price = await get_flash_sale_price(package.id)
                if flash_price is not None:
                    return flash_price

    # 3. Pricing Hub — covers Lucky 6.6, Birthday, Mid-Month Flash, End-month VIP, base
    return _hub_effective_price(tier, context_user_data)

    # Lucky 6.6 promo — VIP 166 / OF 266 / GOD3M 666 / Lifetime 2266
    try:
        from shared.endmonth_vip_promo import is_lucky_6_active
        if is_lucky_6_active():
            lucky_prices = {
                "300":  Decimal("166"),
                "500":  Decimal("266"),
                "1299": Decimal("666"),
                "2499": Decimal("2266"),
            }
            if tier in lucky_prices:
                return lucky_prices[tier]
    except Exception:
        pass

    return base_price



















async def _send_welcome_referral_dm(bot, telegram_id: int) -> None:
    """ส่ง DM ยินดีต้อนรับ + แนะนำชวนเพื่อน หลังสมัครสำเร็จ."""
    try:
        await bot.send_message(
            chat_id=telegram_id,
            text=WELCOME_REFERRAL_DM,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
        logger.info("Welcome referral DM sent to %s", telegram_id)
    except Exception as exc:
        logger.warning("Failed to send welcome referral DM to %s: %s", telegram_id, exc)




def _build_admin_approve_kb(user_id, *, include_reject: bool = True, include_chat: bool = True, username: str | None = None):
    """Phase 4 Round C: build admin approve keyboard from shared.pricing.approve_buttons()."""
    import telegram as tg
    from shared.pricing import approve_buttons
    rows = []
    for row in approve_buttons(user_id):
        btn_row = []
        for cell in row:
            btn_row.append(tg.InlineKeyboardButton(
                cell["text"],
                callback_data=cell["callback_data"],
                api_kwargs={"style": "success"},
            ))
        rows.append(btn_row)
    if include_reject:
        rows.append([tg.InlineKeyboardButton("❌ ปฏิเสธ" if not username else "❌ ซองเสีย",
                     callback_data=f"reject_{user_id}",
                     api_kwargs={"style": "danger"})])
    if include_chat:
        if username:
            rows.append([tg.InlineKeyboardButton(f"💬 @{username}", url=f"https://t.me/{username}",
                         api_kwargs={"style": "primary"})])
        else:
            rows.append([tg.InlineKeyboardButton(f"💬 ID: {user_id}", url=f"tg://user?id={user_id}",
                         api_kwargs={"style": "primary"})])
    return tg.InlineKeyboardMarkup(rows)

async def handle_photo_slip(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle photo message — OCR slip verification."""
    import telegram as tg

    if not update.message or not update.message.photo:
        return

    user = update.effective_user
    if not user:
        return

    photo = update.message.photo[-1]
    file_id = photo.file_id

    # Check if user has selected a package
    selected_tier = context.user_data.get("selected_tier")
    missing_context = not selected_tier
    expected_price = Decimal("0")

    if missing_context:
        logger.warning("Slip received without selected_tier: user=%s", user.id)
        await update.message.reply_text(
            "📩 ได้รับรูปแล้วค่ะ\n\n"
            "ระบบกำลังตรวจสอบว่าเป็นสลิปหรือไม่ กรุณารอสักครู่นะคะ 🙏",
        )
    else:
        expected_price = await _get_effective_price(selected_tier, context.user_data)
        if not expected_price:
            logger.warning("Slip received with invalid selected_tier: user=%s tier=%s", user.id, selected_tier)
            missing_context = True
            await update.message.reply_text(
                "📩 ได้รับรูปแล้วค่ะ\n\n"
                "ระบบกำลังตรวจสอบว่าเป็นสลิปหรือไม่ กรุณารอสักครู่นะคะ 🙏",
            )
        else:
            await update.message.reply_text("🔍 กำลังตรวจสอบสลิปค่ะ กรุณารอสักครู่...")

    logger.info(
        "Slip received from user %s, selected_tier=%s missing_context=%s",
        user.id,
        selected_tier,
        missing_context,
    )

    # AI screen: check if this is actually a payment slip
    try:
        import base64
        screen_file = await context.bot.get_file(file_id)
        screen_buf = io.BytesIO()
        await screen_file.download_to_memory(screen_buf)
        screen_buf.seek(0)
        b64_img = base64.b64encode(screen_buf.read()).decode("utf-8")

        screen_result = await _ai_screen_image(b64_img)
        if screen_result:
            screen_lower = screen_result.lower()
            admin_contact_button = [
                tg.InlineKeyboardButton(f"💬 @{user.username}", url=f"https://t.me/{user.username}", api_kwargs={"style": "primary"}) if user.username else tg.InlineKeyboardButton(f"💬 ID: {user.id}", url=f"tg://user?id={user.id}", api_kwargs={"style": "primary"})
            ]
            admin_reason = screen_result[:300]

            if "spam" in screen_lower and not any(x in screen_lower for x in ("gambling", "porn", "inappropriate")):
                # Generic spam/non-slip images should not clutter admin alerts.
                logger.info(
                    "Generic spam/non-slip image ignored for admin forwarding: user=%s ai=%s",
                    user.id,
                    admin_reason,
                )
                await update.message.reply_text(
                    "📩 ได้รับรูปแล้วค่า แต่ดูเหมือนไม่ใช่สลิปนะ\n\n"
                    "ถ้าต้องการสมัคร กรุณาส่งรูปสลิปโอนเงินเท่านั้นนะคะ 🙏\n"
                    "ถ้ามีคำถามหรือมีปัญหา พิมพ์บอกได้เลยค่ะ"
                )
                return

            if "gambling" in screen_lower or "porn" in screen_lower or "inappropriate" in screen_lower:
                # Do not clutter the admin room with obvious ad/gambling creatives.
                logger.warning("Blocked inappropriate/non-slip image before admin forwarding: user=%s ai=%s", user.id, admin_reason)
                await update.message.reply_text(
                    "📩 ได้รับรูปแล้วค่า แต่รูปนี้ไม่ใช่สลิปโอนเงินนะ\n\n"
                    "ถ้าต้องการสมัคร กรุณาส่งเฉพาะรูปสลิปโอนเงินจากธนาคาร/วอลเล็ทค่ะ 🙏"
                )
                return

            if "not_slip" in screen_lower or "question" in screen_lower or "support" in screen_lower:
                # Customer likely sent a normal/support image. Do NOT forward to admin group;
                # boss requested non-slip photos must not clutter the admin room (2026-04-26).
                logger.info(
                    "Non-slip image ignored for admin forwarding: user=%s ai=%s",
                    user.id,
                    admin_reason,
                )
                await update.message.reply_text(
                    "📩 ได้รับรูปแล้วค่า แต่ดูเหมือนไม่ใช่สลิปนะ\n\n"
                    "ถ้ามีคำถามหรือมีปัญหา พิมพ์บอกได้เลยค่ะ เดี๋ยวแอดมินช่วยดูให้ 🙏\n"
                    "ถ้าเข้ากลุ่มไม่ได้ กลุ่มหาย หรือลิงก์มีปัญหา พิมพ์บอกได้เลยนะคะ\n\n"
                    "ติดต่อแอดมินโดยตรง: @sperm6969"
                )
                return
    except Exception as exc:
        # FIX 2025-05-21 (Phase 2d caller): if circuit-open, defer slip and do NOT forward to admin
        from shared.api_cost_tracker import OpenRouterCircuitOpen as _CircuitOpen
        if isinstance(exc, _CircuitOpen):
            logger.warning("AI offline (circuit-open) for screen — defer user=%s: %s", user.id, exc)
            try:
                await _notify_discord(
                    "🛑 SLIP DEFERRED — AI offline (screen)",
                    f"User {user.id} ({user.first_name or '?'}) sent slip but AI circuit is open. "
                    f"Slip NOT forwarded to admin. User asked to retry in 30 min.",
                    color=0xFFA500,
                )
            except Exception:
                pass
            await update.message.reply_text(
                "⏳ ระบบประมวลผลสลิปอัตโนมัติหยุดทำงานชั่วคราวค่ะ\n\n"
                "กรุณาส่งสลิปอีกครั้งใน 30 นาที หรือทักแอดมินโดยตรง 🙏\n"
                "ติดต่อแอดมิน: @sperm6969"
            )
            return
        logger.warning("AI screen failed, proceeding with OCR: %s", exc)

    # >>> SLIP2GO_INTEGRATION <<<
    # >>> FIXALL_PAYMENT <<<
    # Bug #4: compute slip_hash from CONTENT (image bytes) — file_id is unstable
    # We compute it once below after downloading photo_bytes.
    slip_hash = None  # will be set after download

    # Bug #1: safe download with explicit exception handling
    photo_bytes = None
    try:
        _tg_file = await context.bot.get_file(file_id)
        import io as _io
        _buf = _io.BytesIO()
        await _tg_file.download_to_memory(_buf)
        photo_bytes = _buf.getvalue()
    except Exception as _exc_dl:
        logger.error("Failed to download slip image: %s", _exc_dl)

    # Bug #4: content-derived hash
    if photo_bytes:
        import hashlib as _hashlib
        slip_hash = _hashlib.sha256(photo_bytes).hexdigest()[:64]
    else:
        slip_hash = compute_slip_hash(file_id)  # fallback to file_id hash

    # ─── Slip2Go verification ───
    slip2go_data = None
    slip2go_err = None
    if photo_bytes is None:
        slip2go_err = Slip2GoError("DOWNLOAD_FAIL", "could not download slip image")
    else:
        try:
            slip2go_data = await verify_slip_image(photo_bytes)
        except Slip2GoError as _sg_err:
            slip2go_err = _sg_err
            logger.warning("Slip2Go verify failed for user %s: %s", user.id, _sg_err)
        except Exception as _sg_err:
            # Bug #1/10: catch generic exceptions too
            slip2go_err = Slip2GoError("UNKNOWN", str(_sg_err)[:200])
            logger.error("Slip2Go unexpected error: %s", _sg_err)

    # GACHA bundle hook — special case BEFORE normal approve flow.
    # Triggered when selected_tier is GACHA_1/3/10 AND Slip2Go confirmed payment.
    if selected_tier and selected_tier.startswith("GACHA_") and slip2go_data:
        from shared.pricing import TIER_PRICES
        _spins_map = {"GACHA_1": 1, "GACHA_3": 3, "GACHA_10": 10}
        _spins = _spins_map.get(selected_tier, 0)
        _gacha_amt = float(TIER_PRICES.get(selected_tier, 0))
        try:
            from shared.models import Payment as _P, PaymentMethod as _PM, PaymentStatus as _PS
            from sqlalchemy import text as _t
            async with get_session() as _s:
                _pay = _P(
                    user_id=db_user_id, package_id=1, amount=_gacha_amt,
                    method=_PM.SLIP, status=_PS.CONFIRMED,
                    slip_file_id=file_id, slip_hash=slip_hash,
                    verified_at=datetime.utcnow(),
                )
                _s.add(_pay)
                await _s.flush()
                _payid = _pay.id
                await _s.execute(_t(
                    "INSERT INTO gachapon_credits (user_id, telegram_id, credits, total_purchased) "
                    "VALUES (:uid, :tg, :sp, :sp) "
                    "ON CONFLICT (user_id) DO UPDATE SET "
                    "credits = gachapon_credits.credits + :sp, "
                    "total_purchased = gachapon_credits.total_purchased + :sp, "
                    "updated_at = NOW()"
                ), {"uid": db_user_id, "tg": user.id, "sp": _spins})
                await _s.commit()
            from telegram import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
            _kb = InlineKeyboardMarkup([
                [InlineKeyboardButton(
                    f"\U0001f3b0 หมุนเลย! (มี {_spins} สิทธิ์)",
                    web_app=WebAppInfo(url="https://telebord.net/gacha/"),
                )]
            ])
            _msg = (
                f"\U0001f389 <b>ได้รับสิทธิ์หมุนกาชาปอง {_spins} ครั้ง!</b>\n\n"
                "\U0001f381 ลุ้นรางวัล: VIP / OF / GOD / GOD ถาวร / คลิปพิเศษ / ส่วนลด\n"
                "\u26a1 กดปุ่มด้านล่างเปิดวงล้อเลยค่ะ!"
            )
            await update.message.reply_text(_msg, parse_mode="HTML", reply_markup=_kb)
            logger.info("GACHA: added %s credits to user %s payment %s", _spins, user.id, _payid)
            return
        except Exception as _exc_gacha:
            logger.error("GACHA hook failed: %s", _exc_gacha)

    # AUTO-RETRY: Slip2Go 200404 + valid tier -> queue retry instead of escalating
    if (not slip2go_data and slip2go_err and selected_tier
        and "200404" in str(slip2go_err)
        and selected_tier in ("300", "500", "1299", "2499", "100")):
        try:
            from shared.pricing import TIER_PRICES
            _exp_amt = float(TIER_PRICES.get(selected_tier, 0))
            from shared.models import Payment as _P, PaymentMethod as _PM, PaymentStatus as _PS, Package as _Pkg
            async with get_session() as _s:
                _pq = await _s.execute(select(_Pkg).where(_Pkg.tier == PackageTier(selected_tier)))
                _pkg = _pq.scalar_one_or_none()
                if _pkg:
                    _pending = _P(
                        user_id=db_user_id, package_id=_pkg.id, amount=_exp_amt,
                        method=_PM.SLIP, status=_PS.PENDING,
                        slip_file_id=file_id, slip_hash=slip_hash,
                    )
                    _s.add(_pending)
                    await _s.flush()
                    _pid = _pending.id
                    await _s.commit()
                    from shared.slip2go_retry_worker import enqueue_slip_for_retry as _enq
                    await _enq(
                        payment_id=_pid, user_id=db_user_id, telegram_id=user.id,
                        slip_file_id=file_id, slip_hash=slip_hash,
                        selected_tier=selected_tier, expected_amount=_exp_amt,
                    )
                    _retry_msg = (
                        "⏳ <b>กำลังตรวจสอบสลิปค่ะ...</b>\n\n"
                        "📡 ระบบกำลังรอข้อมูลจากธนาคาร (5-15 นาที)\n"
                        "✅ ถ้าผ่าน ระบบจะแจ้งและส่งลิงก์ให้อัตโนมัติค่ะ\n\n"
                        "🙏 ขอบคุณที่อดทนรอ"
                    )
                    await update.message.reply_text(_retry_msg, parse_mode="HTML")
                    logger.info("Slip2Go 200404 -> enqueued retry for user %s payment %s", user.id, _pid)
                    return
        except Exception as _exc_enq:
            logger.error("Auto-retry enqueue failed: %s -- falling back to admin", _exc_enq)

    if slip2go_data:
        # Successful Slip2Go response — try Smart Match auto-approve
        from decimal import Decimal as _D
        from datetime import datetime as _dt, timedelta as _td, timezone as _tz
        s2g_amount = _D(str(slip2go_data.get("amount") or "0"))
        # Bug #17: empty string → None to avoid UNIQUE violation on ""
        s2g_trans_ref = ((slip2go_data.get("transRef") or "").strip()[:64]) or None
        s2g_sender = slip2go_data.get("sender", {}) or {}
        s2g_sender_name = ((s2g_sender.get("account") or {}).get("name") or "")[:255]
        s2g_sender_bank = ((s2g_sender.get("bank") or {}).get("name") or "")[:64]
        s2g_sender_account = (((s2g_sender.get("account") or {}).get("bank") or {}).get("account") or "")[:64]

        # ── Receiver match ──
        rejection = None
        # # >>> POOL_INTEGRATION <<< use pool-aware matcher (supports multiple receiver accounts)
        from shared.slip2go import receiver_match_pool
        rcv_ok, rcv_reason, _matched_account = await receiver_match_pool(slip2go_data)
        if not rcv_ok:
            # # >>> OPTION_B_HARD_REJECT <<<
            # HARD REJECT — Slip2Go confirms real bank tx but receiver is NOT us.
            # Don't fall to AI, don't create payment, just inform customer + alert admin.
            _rcv_name = ((slip2go_data.get("receiver", {}) or {}).get("account", {}) or {}).get("name") or "ไม่ทราบ"
            await update.message.reply_text(
                "❌ <b>สลิปนี้ใช้ไม่ได้</b>\n\n"
                "ระบบตรวจสอบแล้วพบว่าคุณ <b>โอนเงินผิดบัญชี</b> ค่ะ\n"
                f"📌 ผู้รับในสลิป: <b>{_rcv_name}</b>\n"
                "📌 ผู้รับที่ถูก: <b>นาย ชาคริต กิ่งวงษา</b> (PromptPay 098-835-1578 / SCB 414-203-9642)\n\n"
                "กรุณาโอนเข้าบัญชีของร้านเจริญพรเท่านั้น แล้วส่งสลิปใหม่ค่ะ 🙏",
                parse_mode="HTML",
            )
            # Alert admin group (info only — no approve buttons)
            try:
                import telegram as tg, html as _h, os as _os
                _admin_bot = tg.Bot(token=_os.environ.get("ADMIN_BOT_TOKEN", ""))
                try:
                    await _admin_bot.initialize()
                    _admin_chat = int(_os.environ.get("ADMIN_GROUP_CHAT_ID", ""))
                    _safe_tg = _h.escape(str(user.first_name or user.username or "ลูกค้า"))
                    _safe_rcv = _h.escape(_rcv_name)
                    _msg = (
                        "⚠️ <b>WRONG-RECEIVER SLIP</b> (Slip2Go verified real, but not our account)\n"
                        "━━━━━━━━━━━━━━\n"
                        f"👤 Telegram: {_safe_tg} (<code>{user.id}</code>)\n"
                        f"💰 Slip amount: <b>฿{int(slip2go_data.get('amount') or 0):,}</b>\n"
                        f"🎯 <b>Receiver in slip:</b> {_safe_rcv}\n"
                        f"❌ <b>เข้าบัญชีเรา:</b> ไม่ใช่ (ผู้รับไม่ตรง)\n"
                        f"🔖 transRef: <code>{(slip2go_data.get('transRef') or '')[:32]}</code>\n"
                        f"📝 Reason: {_h.escape(rcv_reason or '-')}\n"
                        f"\nลูกค้าได้รับข้อความแจ้งให้โอนใหม่แล้ว — ไม่มี action ต้องทำ"
                    )
                    # Try send slip photo with caption
                    _wr_sent = False
                    try:
                        if update.message and update.message.photo:
                            import io as _io2
                            _slip_file = await context.bot.get_file(update.message.photo[-1].file_id)
                            _buf2 = _io2.BytesIO()
                            await _slip_file.download_to_memory(_buf2)
                            _buf2.seek(0)
                            await _admin_bot.send_photo(
                                chat_id=_admin_chat, photo=_buf2,
                                caption=_msg, parse_mode="HTML",
                            )
                            _wr_sent = True
                    except Exception as _exc_wrp:
                        logger.warning("wrong-receiver slip photo failed: %s", _exc_wrp)
                    if not _wr_sent:
                        await _admin_bot.send_message(chat_id=_admin_chat, text=_msg, parse_mode="HTML")
                finally:
                    try: await _admin_bot.shutdown()
                    except Exception: pass
            except Exception as _exc_an:
                logger.warning("admin alert (wrong-receiver) failed: %s", _exc_an)
            logger.warning("Hard reject (wrong receiver): user=%s amount=%s rcv=%s tref=%s",
                           user.id, slip2go_data.get("amount"), _rcv_name, slip2go_data.get("transRef"))
            return  # ← critical: do NOT fall through to AI path
        else:
            tier_match = amount_to_tier(s2g_amount)
            if not tier_match:
                # Bug #11: hand off to admin instead of hard reject
                rejection = None  # let admin review
                logger.info("Slip2Go: amount %s no tier match — routing to admin", s2g_amount)
                slip2go_data = None  # fall through to AI/admin path
                slip2go_err = Slip2GoError("NO_TIER", f"ยอด {int(s2g_amount)} ไม่ตรง tier ใดๆ")

        if slip2go_data and rejection is None:
            # Bug #2: do dup check + write in SAME session + catch IntegrityError
            tier_str, tier_label, is_promo = tier_match  # type: ignore

            # Phase 2: delegate to shared.pricing for tier mapping
            from shared.pricing import tier_str_to_enum as _tier_str_to_enum, admin_callback_tier_map as _admin_cb_map
            _cb_to_tier_str = _admin_cb_map()  # 'callback_amount' -> 'tier_str'
            tier_map_local = {
                cb: _tier_str_to_enum(tstr) for cb, tstr in _cb_to_tier_str.items()
            }
            # Keep TIER_99 entry (legacy callbacks may still reference it)
            tier_map_local['99'] = PackageTier.TIER_99
            # COMEBACK_PROMO_VALIDATE — safety check: only auto-approve if user has active promo in DB
            if tier_str in ("180", "210"):
                _active_promo = await _get_active_promo_for_user(user.id)
                if not _active_promo:
                    logger.warning(
                        "Slip2Go: comeback price %s but no active promo for user %s — fallback to admin",
                        tier_str, user.id,
                    )
                    slip2go_data = None
                    slip2go_err = Slip2GoError("NO_COMEBACK_PROMO",
                        f"ยอด {tier_str} (Comeback) — ผู้ใช้ไม่มี promo active ใน DB")
                    tier_match = None
                else:
                    # Inject promo into user_data so _approve_payment can mark it purchased
                    context.user_data["comeback_promo"] = _active_promo.get("promo_code")
                    context.user_data["comeback_discount"] = _active_promo.get("discount_pct")
                    logger.info("Slip2Go comeback auto-match: user=%s code=%s discount=%s",
                                user.id, _active_promo.get("promo_code"), _active_promo.get("discount_pct"))
            target_tier_enum = tier_map_local.get(tier_str)

            # Bug #13: TIER_ADD500 vs TIER_500 disambiguation
            # If amount=500 AND user has active TIER_2499 lifetime → use TIER_ADD500
            if int(s2g_amount) == 500 and tier_str == "500":
                from sqlalchemy import select as _sel_pre
                async with get_session() as _check_sess:
                    _u_pre = (await _check_sess.execute(_sel_pre(User).where(User.telegram_id == user.id))).scalar_one_or_none()
                    if _u_pre:
                        _has_lifetime = (await _check_sess.execute(_sel_pre(Subscription).join(
                            Package, Package.id == Subscription.package_id
                        ).where(
                            Subscription.user_id == _u_pre.id,
                            Subscription.status == SubscriptionStatus.ACTIVE,
                            Package.tier == PackageTier.TIER_2499,
                        ))).first()
                        if _has_lifetime:
                            target_tier_enum = PackageTier.TIER_ADD500  # type: ignore
                            tier_label = "Summer Fest Add-on"
                            logger.info("Auto-detected ADD500 (user has lifetime): tg=%s", user.id)

            # >>> FIX1_TIER_MISMATCH <<<
            # If customer explicitly selected a tier but slip amount matches DIFFERENT tier
            # → don't auto-approve; route to admin for verification
            if selected_tier and target_tier_enum is not None:
                try:
                    _expected_enum_map = {
                        "300": PackageTier.TIER_300, "500": PackageTier.TIER_500,
                        "1299": PackageTier.TIER_1299, "2499": PackageTier.TIER_2499,
                    }
                    _selected_enum = _expected_enum_map.get(str(selected_tier))
                    if _selected_enum is not None and _selected_enum != target_tier_enum:
                        logger.warning(
                            "Slip2Go tier mismatch: selected=%s tier_from_amount=%s amount=%s",
                            selected_tier, target_tier_enum.value, s2g_amount,
                        )
                        slip2go_data = None
                        slip2go_err = Slip2GoError(
                            "TIER_MISMATCH",
                            f"selected={selected_tier} but amount={int(s2g_amount)} → admin review",
                        )
                except Exception:
                    pass

            if not target_tier_enum:
                logger.error("Smart match returned unmappable tier=%s", tier_str)
                slip2go_data = None
                slip2go_err = Slip2GoError("UNMAPPABLE", f"tier_str={tier_str}")
            else:
                # Bug #7: use ONE _now for all date calcs
                _now = _dt.utcnow()
                from sqlalchemy import select as _sel, update as _upd
                from sqlalchemy.exc import IntegrityError as _IE

                _approve_ok = False
                _admin_alert_text = None
                _link_rows = []
                _pkg_name_safe = ""
                _expiry_text_safe = ""
                _new_pay_id = None

                try:
                    async with get_session() as _sess:
                        # Bug #2: dup check INSIDE write session
                        if s2g_trans_ref:
                            _dup = await _sess.execute(_sel(Payment).where(
                                Payment.slip_trans_ref == s2g_trans_ref
                            ))
                            if _dup.scalar_one_or_none():
                                rejection = f"สลิปนี้เคยถูกใช้แล้ว (transRef: {s2g_trans_ref[:16]}...)"
                                raise _IE("dup transRef", None, None)  # short-circuit

                        # Find / create user
                        _u = (await _sess.execute(_sel(User).where(User.telegram_id == user.id))).scalar_one_or_none()
                        if not _u:
                            _u = User(telegram_id=user.id, first_name=user.first_name, username=user.username)
                            _sess.add(_u)
                            await _sess.flush()
                        _pkg = (await _sess.execute(_sel(Package).where(Package.tier == target_tier_enum))).scalar_one()

                        # Bug #16: only set real_name on first purchase; log mismatch otherwise
                        if s2g_sender_name and not _u.real_name:
                            _u.real_name = s2g_sender_name
                            _u.last_sender_bank = s2g_sender_bank
                            _u.last_sender_account = s2g_sender_account
                        elif s2g_sender_name and _u.real_name and _u.real_name != s2g_sender_name:
                            logger.warning("Sender name mismatch: user=%s prev=%s new=%s",
                                           user.id, _u.real_name, s2g_sender_name)
                            # Update last_* anyway (latest sender)
                            _u.last_sender_bank = s2g_sender_bank
                            _u.last_sender_account = s2g_sender_account

                        # Bug #3: lifetime guard — only protect lifetime when buying NON-lifetime
                        _lifetime_pkgs = _sel(Package.id).where(Package.tier == PackageTier.TIER_2499)
                        if target_tier_enum == PackageTier.TIER_2499:
                            # Buying lifetime — expire EVERYTHING (including old lifetime — let new one take over)
                            await _sess.execute(_upd(Subscription).where(
                                Subscription.user_id == _u.id,
                                Subscription.status == SubscriptionStatus.ACTIVE,
                            ).values(status=SubscriptionStatus.EXPIRED))
                        else:
                            # Non-lifetime purchase — preserve any active lifetime sub
                            await _sess.execute(_upd(Subscription).where(
                                Subscription.user_id == _u.id,
                                Subscription.status == SubscriptionStatus.ACTIVE,
                                Subscription.package_id.notin_(_lifetime_pkgs),
                            ).values(status=SubscriptionStatus.EXPIRED))

                        # Bug #8: use sentinel date for lifetime
                        if target_tier_enum == PackageTier.TIER_2499:
                            _end = _dt(3000, 12, 31, 23, 59, 59)
                        elif _pkg.tier == PackageTier.TIER_99:
                            _end = _now + _td(hours=24)
                        else:
                            _end = _now + _td(days=_pkg.duration_days)

                        # Create Payment
                        _new_pay = Payment(
                            user_id=_u.id, package_id=_pkg.id, amount=s2g_amount,
                            method=PaymentMethod.SLIP, status=PaymentStatus.CONFIRMED,
                            slip_file_id=file_id, slip_hash=slip_hash,
                            slip_trans_ref=s2g_trans_ref,
                            sender_name=s2g_sender_name or None,
                            sender_bank_name=s2g_sender_bank or None,
                            sender_bank_account=s2g_sender_account or None,
                            auto_approved=True,
                            verified_at=_now,
                        )
                        _sess.add(_new_pay)
                        await _sess.flush()

                        # Create Subscription
                        _new_sub = Subscription(
                            user_id=_u.id, package_id=_pkg.id, status=SubscriptionStatus.ACTIVE,
                            start_date=_now, end_date=_end,
                            auto_renew=False, payment_id=_new_pay.id,
                        )
                        _sess.add(_new_sub)
                        _u.total_spent = (_u.total_spent or _D("0")) + s2g_amount
                        _new_pay_id = _new_pay.id
                        _pkg_name_safe = _pkg.name
                        _pkg_id_safe = _pkg.id
                        _pkg_dur_safe = _pkg.duration_days
                        _user_id_safe = _u.id

                    # Bug #2: only mark approved AFTER commit succeeded
                    _approve_ok = True

                except _IE as _ie:
                    # dup transRef or DB constraint — treat as already-processed
                    if not rejection:
                        rejection = "สลิปนี้เคยถูกใช้แล้ว (concurrent)"
                    logger.warning("Auto-approve IntegrityError: %s", _ie)
                except Exception as _ie:
                    logger.error("Auto-approve write failed: %s", _ie)
                    rejection = f"ระบบขัดข้องชั่วคราว: {str(_ie)[:80]}"

                if rejection:
                    await update.message.reply_text(
                        f"❌ <b>สลิปนี้ใช้ไม่ได้</b>\n\n{rejection}\n\n"
                        f"ติดต่อแอดมิน <a href='https://t.me/jarernpon'>@jarernpon</a> ได้เลยค่ะ",
                        parse_mode="HTML",
                    )
                    return

                if _approve_ok:
                    # ─── Generate invite links (Bug #14: try/finally for bot shutdown) ───
                    import telegram as tg
                    _invite_links = {}
                    if target_tier_enum != PackageTier.TIER_ADD500:
                        _guardian = tg.Bot(token=os.environ.get("GUARDIAN_BOT_TOKEN", ""))
                        try:
                            await _guardian.initialize()
                            from bots.guardian_bot.group_monitor import generate_invite_links_for_user
                            _invite_links = await generate_invite_links_for_user(_guardian, user.id, _pkg_id_safe)
                        except Exception as _exc_inv:
                            logger.error("Failed to generate invite links (auto): %s", _exc_inv)
                        finally:
                            try: await _guardian.shutdown()
                            except Exception: pass
                    else:
                        # ADD500 — add-on, use Summer Fest groups only via a different path
                        # For now, generate for the add-on package
                        _guardian = tg.Bot(token=os.environ.get("GUARDIAN_BOT_TOKEN", ""))
                        try:
                            await _guardian.initialize()
                            from bots.guardian_bot.group_monitor import generate_invite_links_for_user
                            _invite_links = await generate_invite_links_for_user(_guardian, user.id, _pkg_id_safe)
                        except Exception as _exc_inv:
                            logger.error("Failed to generate invite links (ADD500): %s", _exc_inv)
                        finally:
                            try: await _guardian.shutdown()
                            except Exception: pass

                    # Build keyboard
                    from shared.models import GroupRegistry as _GR
                    async with get_session() as _gsess:
                        for _slug, _link in _invite_links.items():
                            _g = (await _gsess.execute(_sel(_GR).where(_GR.slug == _slug))).scalar_one_or_none()
                            _title = _g.title if _g else _slug
                            _link_rows.append([tg.InlineKeyboardButton(f"🚀 {_title}", url=_link)])

                    # Bug #8/15: friendly expiry display (lifetime + TH timezone)
                    if target_tier_enum == PackageTier.TIER_2499:
                        _expiry_text_safe = "ตลอดชีพ (ไม่หมดอายุ)"
                    elif _pkg_dur_safe and _pkg_dur_safe <= 1:
                        # Trial — show hours
                        _expiry_th = (_now + _td(hours=24)).replace(tzinfo=_tz.utc).astimezone(_tz(_td(hours=7)))
                        _expiry_text_safe = _expiry_th.strftime("%d/%m %H:%M")
                    else:
                        _expiry_th = (_now + _td(days=_pkg_dur_safe)).replace(tzinfo=_tz.utc).astimezone(_tz(_td(hours=7)))
                        _expiry_text_safe = _expiry_th.strftime("%d/%m/%Y")

                    # Bug #20: warn if selected_tier mismatched (not error, just informational)
                    _selected_note = ""
                    if selected_tier and selected_tier != tier_str and selected_tier not in (None, ""):
                        _selected_note = f"\n\nℹ️ คุณเลือก {selected_tier} แต่โอนยอด {int(s2g_amount)} → ระบบจัด <b>{_pkg_name_safe}</b> ให้แทน"

                    await update.message.reply_text(
                        f"✅ <b>อนุมัติอัตโนมัติเรียบร้อยค่ะ!</b>\n\n"
                        f"📦 แพ็กเกจ: <b>{_pkg_name_safe}</b>\n"
                        f"💰 ยอดชำระ: <b>฿{int(s2g_amount):,}</b>\n"
                        f"⏰ หมดอายุ: <b>{_expiry_text_safe}</b>"
                        f"{_selected_note}\n\n"
                        f"กดลิงก์ด้านล่างเข้ากลุ่มได้เลย 👇" if _link_rows else
                        f"✅ <b>อนุมัติอัตโนมัติเรียบร้อยค่ะ!</b>\n\n"
                        f"📦 แพ็กเกจ: <b>{_pkg_name_safe}</b>\n"
                        f"💰 ยอดชำระ: <b>฿{int(s2g_amount):,}</b>\n"
                        f"⏰ หมดอายุ: <b>{_expiry_text_safe}</b>"
                        f"{_selected_note}\n\n"
                        f"ติดต่อแอดมินเพื่อขอลิงก์เข้ากลุ่ม: <a href='https://t.me/jarernpon'>@jarernpon</a>",
                        parse_mode="HTML",
                        reply_markup=tg.InlineKeyboardMarkup(_link_rows) if _link_rows else None,
                        disable_web_page_preview=True,
                    )

                    # ── Admin notification (Bug #14, #18: try/finally + Discord fallback) ──
                    try:
                        ADMIN_GROUP_ID = int(os.environ.get("ADMIN_GROUP_CHAT_ID", ""))
                        _admin_bot = tg.Bot(token=os.environ.get("ADMIN_BOT_TOKEN", ""))
                        try:
                            await _admin_bot.initialize()
                            import html as _h
                            _safe_tg_name = _h.escape(str(user.first_name or user.username or "ลูกค้า"))
                            _safe_real = _h.escape(s2g_sender_name or "-")
                            _safe_bank = _h.escape(f"{s2g_sender_bank} {s2g_sender_account}".strip() or "-")
                            # SLIP_TO_ADMIN — include our receiver bank info
                            _recv_label = "-"
                            if _matched_account:
                                _recv_label = _h.escape(
                                    f"{_matched_account.get('owner_name','?')} "
                                    f"({_matched_account.get('bank_name_th','')} {_matched_account.get('account_no','')})"
                                )
                            _admin_msg = (
                                f"🤖 <b>AUTO-APPROVED (Slip2Go)</b>\n"
                                f"━━━━━━━━━━━━━━\n"
                                f"📋 Pay #{_new_pay_id}\n"
                                f"👤 Telegram: {_safe_tg_name} (<code>{user.id}</code>)\n"
                                f"🆔 <b>ชื่อจริง:</b> {_safe_real}\n"
                                f"🏦 <b>จาก:</b> {_safe_bank}\n"
                                f"🎯 <b>เข้าบัญชีเรา:</b> {_recv_label}\n"
                                f"💰 ยอด: <b>฿{int(s2g_amount):,}</b>\n"
                                f"📦 แพ็ก: <b>{_h.escape(_pkg_name_safe)}</b> {'🔥 (โปร)' if is_promo else ''}\n"
                                f"🔖 transRef: <code>{s2g_trans_ref or '-'}</code>"
                            )
                            # Try send slip photo with caption; fallback to text if no file_id
                            _slip_sent = False
                            try:
                                if update.message and update.message.photo:
                                    import io as _io
                                    _slip_file = await context.bot.get_file(update.message.photo[-1].file_id)
                                    _buf = _io.BytesIO()
                                    await _slip_file.download_to_memory(_buf)
                                    _buf.seek(0)
                                    await _admin_bot.send_photo(
                                        chat_id=ADMIN_GROUP_ID, photo=_buf,
                                        caption=_admin_msg, parse_mode="HTML",
                                    )
                                    _slip_sent = True
                            except Exception as _exc_p:
                                logger.warning("slip photo to admin failed: %s", _exc_p)
                            if not _slip_sent:
                                await _admin_bot.send_message(chat_id=ADMIN_GROUP_ID, text=_admin_msg, parse_mode="HTML")
                        finally:
                            try: await _admin_bot.shutdown()
                            except Exception: pass
                    except Exception as _exc_an:
                        logger.warning("admin auto-approve notify failed: %s", _exc_an)
                        # Bug #18: Discord fallback
                        try:
                            await _notify_discord(
                                "🤖 AUTO-APPROVED (admin notify failed)",
                                f"Pay #{_new_pay_id} ฿{int(s2g_amount)} {_pkg_name_safe} — admin tg send failed: {_exc_an}",
                                color=0x00AA00,
                            )
                        except Exception: pass
                    # # >>> POOL_CUMULATIVE <<<
                    try:
                        if _matched_account is not None:
                            from shared.receiver_pool import record_payment_received
                            _rec = await record_payment_received(_matched_account['id'], s2g_amount)
                            if _rec.get('alert'):
                                _alert_bot = tg.Bot(token=os.environ.get('ADMIN_BOT_TOKEN', ''))
                                try:
                                    await _alert_bot.initialize()
                                    await _alert_bot.send_message(
                                        chat_id=int(os.environ.get('ADMIN_GROUP_CHAT_ID', '')),
                                        text=(
                                            f"💰 <b>ยอดสะสมถึง milestone</b>\n"
                                            f"━━━━━━━━━━━━━━\n"
                                            f"บัญชี: <b>{_rec['owner_name']}</b>\n"
                                            f"ยอดสะสมตอนนี้: <b>฿{int(_rec['cumulative']):,}</b>\n"
                                            f"ผ่าน threshold: <b>฿{int(_rec['milestone']):,}</b>\n\n"
                                            f"📌 พิจารณาถอนเงินออก แล้วใช้คำสั่ง /receivers reset เพื่อ reset counter"
                                        ),
                                        parse_mode='HTML',
                                    )
                                finally:
                                    try: await _alert_bot.shutdown()
                                    except Exception: pass
                    except Exception as _exc_rec:
                        logger.warning('record_payment_received fail: %s', _exc_rec)
                    return

    # ─── Slip2Go failed or unavailable — fall back to old AI path ───
    # OCR
    try:
        ocr_text = await _ocr_slip_image(context.bot, file_id)
    except Exception as exc:
        # FIX 2025-05-21 (Phase 2d caller): same circuit-open guard for OCR call (which uses AI too)
        from shared.api_cost_tracker import OpenRouterCircuitOpen as _CircuitOpen
        if isinstance(exc, _CircuitOpen):
            logger.warning("AI offline (circuit-open) for OCR — defer user=%s: %s", user.id, exc)
            try:
                await _notify_discord(
                    "🛑 SLIP DEFERRED — AI offline (OCR)",
                    f"User {user.id} sent slip but AI circuit is open. Not forwarded to admin.",
                    color=0xFFA500,
                )
            except Exception:
                pass
            await update.message.reply_text(
                "⏳ ระบบประมวลผลสลิปอัตโนมัติหยุดทำงานชั่วคราวค่ะ\n\n"
                "กรุณาส่งสลิปอีกครั้งใน 30 นาที 🙏"
            )
            return
        logger.error("OCR failed: %s", exc)
        await update.message.reply_text(
            "⚠️ ไม่สามารถอ่านสลิปได้ค่ะ กรุณาส่งรูปที่ชัดขึ้น หรือติดต่อแอดมิน @sperm6969ค่ะ"
        )
        return

    # Block gambling/ad creatives that OCR can mistake for payment slips.
    if _looks_like_non_slip_ad(ocr_text):
        logger.warning("Blocked non-slip ad image after OCR: user=%s ocr=%s", user.id, ocr_text[:300])
        await update.message.reply_text(
            "📩 ได้รับรูปแล้วค่า แต่รูปนี้ไม่ใช่สลิปโอนเงินนะ\n\n"
            "ถ้าต้องการสมัคร กรุณาส่งเฉพาะรูปสลิปโอนเงินจากธนาคาร/วอลเล็ทค่ะ 🙏"
        )
        return

    # Extract amount
    ocr_amount = _extract_amount_from_ocr(ocr_text)

    # Check 3 conditions
    reasons: list[str] = []

    # 0. AI fraud detection
    if "SUSPICIOUS" in ocr_text.upper():
        suspicious_reason = ocr_text.split("SUSPICIOUS")[-1].strip(": \n")[:200]
        reasons.append(f"AI ตรวจพบสัญญาณสลิปปลอม: {suspicious_reason}")

    # 1. Amount matches
    amount_ok = False
    if ocr_amount is not None:
        if missing_context:
            reasons.append(f"ไม่มีแพ็กเกจใน context, OCR อ่านได้ {format_thb(ocr_amount)}")
        else:
            # Allow small tolerance for OCR errors (±1 baht)
            if abs(ocr_amount - expected_price) <= Decimal("1"):
                amount_ok = True
            else:
                reasons.append(
                    f"ยอดไม่ตรง: อ่านได้ {format_thb(ocr_amount)} "
                    f"แต่ต้องการ {format_thb(expected_price)}"
                )
    else:
        reasons.append("ไม่สามารถอ่านยอดเงินจากสลิปได้")

    # 2. Within 24 hours
    date_ok = _check_date_within_24h(ocr_text)
    if not date_ok:
        reasons.append("สลิปเกิน 24 ชั่วโมง")

    # 3. Not duplicate (already checked above) — Bug #21: slip_hash computed in early section

    # Create user if needed and get user_id
    async with get_session() as session:
        user_result = await session.execute(
            select(User).where(User.telegram_id == user.id)
        )
        db_user = user_result.scalar_one_or_none()
        if not db_user:
            db_user = User(
                telegram_id=user.id,
                username=user.username,
                first_name=user.first_name,
                last_name=user.last_name,
            )
            session.add(db_user)
            await session.flush()
        else:
            updated = False
            if user.first_name and user.first_name != db_user.first_name:
                db_user.first_name = user.first_name
                updated = True
            if user.last_name and user.last_name != db_user.last_name:
                db_user.last_name = user.last_name
                updated = True
            if user.username != db_user.username:
                db_user.username = user.username
                updated = True
            if updated:
                await session.flush()

        package = None
        if not missing_context:
            pkg_result = await session.execute(
                select(Package).where(Package.tier == PackageTier(selected_tier))
            )
            package = pkg_result.scalar_one_or_none()
            if not package:
                logger.warning("Package not found for selected_tier: user=%s tier=%s", user.id, selected_tier)
                missing_context = True

        payment_id = None
        if not missing_context:
            # Duplicate payment guard: same user + same amount within 60 seconds
            dedup_cutoff = datetime.utcnow() - timedelta(seconds=60)
            dup_check = await session.execute(
                select(Payment).where(
                    Payment.user_id == db_user.id,
                    Payment.amount == expected_price,
                    Payment.method == PaymentMethod.SLIP,
                    Payment.created_at >= dedup_cutoff,
                )
            )
            if dup_check.scalar_one_or_none():
                logger.warning("Duplicate SLIP payment skipped: user_id=%s amount=%s", db_user.id, expected_price)
                await update.message.reply_text("⚠️ คุณเพิ่งส่งสลิปยอดนี้ไปแล้วค่ะ กรุณารอแอดมินตรวจสอบ 🙏")
                return

            # Create payment record
            payment = Payment(
                user_id=db_user.id,
                package_id=package.id,
                amount=expected_price,
                method=PaymentMethod.SLIP,
                status=PaymentStatus.PENDING,
                slip_file_id=file_id,
                slip_hash=slip_hash,
            )
            session.add(payment)
            await session.flush()
            payment_id = payment.id
            logger.info("Payment created: id=%s user=%s amount=%s", payment_id, user.id, expected_price)
        else:
            logger.warning(
                "Slip routed to admin fallback without payment record: user=%s ocr_amount=%s selected_tier=%s",
                user.id,
                ocr_amount,
                selected_tier,
            )

        user_db_id = db_user.id

    # Decision — ALL slips go to admin for manual review
    # AI info is stored for admin reference
    ai_info = ""
    if ocr_amount is not None:
        ai_info = f"AI อ่านได้: {format_thb(ocr_amount)}"
    if "SUSPICIOUS" in ocr_text.upper():
        ai_info += " ⚠️ AI สงสัยสลิปปลอม"
    if reasons:
        ai_info += f" | หมายเหตุ: {', '.join(reasons)}"

    if payment_id is not None:
        await update.message.reply_text(
            f"📩 <b>ได้รับสลิปแล้วค่ะ</b>\n\n"
            f"💰 แพ็กเกจ: {format_thb(expected_price)}\n"
            f"📋 หมายเลข: #PAY{payment_id}\n\n"
            f"แอดมินจะตรวจสอบและแจ้งผลให้เร็วที่สุดค่ะ\n"
            f"ขอบคุณที่รอนะคะ 🙏",
            parse_mode="HTML",
        )

    # Parse AI result for structured info
    ai_amount_str = str(ocr_amount) if ocr_amount else "อ่านไม่ได้"
    ai_suspicious = ""
    ai_details = []

    if ocr_text:
        for line in ocr_text.split("\n"):
            line_s = line.strip().lstrip("-*• ").strip()
            if not line_s:
                continue
            if "SUSPICIOUS" in line_s.upper():
                ai_suspicious = line_s.split("SUSPICIOUS")[-1].strip(": ")
            elif "VERIFIED" in line_s.upper():
                continue
            elif ":" in line_s and len(line_s) < 200:
                ai_details.append(line_s)

    ai_summary = "\n".join(f"• {d}" for d in ai_details[:8]) if ai_details else "AI อ่านไม่ได้"
    if ai_suspicious:
        ai_summary += f"\n⚠️ สงสัยปลอม: {ai_suspicious}"

    # Send slip to Telegram admin group with inline buttons
    ADMIN_GROUP_ID = int(os.environ.get("ADMIN_GROUP_CHAT_ID", ""))
    try:
        import telegram as tg
        admin_bot = tg.Bot(token=os.environ.get("ADMIN_BOT_TOKEN", ""))
        await admin_bot.initialize()

        import html as _html
        safe_name = _html.escape(str(user.first_name or user.username or "ลูกค้า"))
        now_th = datetime.now(timezone(timedelta(hours=7)))

        # Check for active promo for this user
        promo_info = None
        promo_caption = ""
        try:
            promo_info = await _get_active_promo_for_user(user.id)
            if promo_info:
                promo_caption = (
                    f"\n🎟 <b>โปรโมชั่น:</b> {promo_info['source']} "
                    f"ลด {promo_info['discount_pct']}% (฿{promo_info['discounted_price']})"
                )
        except Exception as promo_exc:
            logger.warning("Failed to check promo for user %d: %s", user.id, promo_exc)

        if is_endmonth_vip_promo_active() and selected_tier == "300":
            promo_caption += f"\n🔥 <b>โปรสิ้นเดือน VIP:</b> 300 เหลือ {int(PROMO_PRICE)} บาท ({PROMO_DATE_TEXT})"
        if is_endmonth_vip_promo_active() and selected_tier == "2499":
            promo_caption += f"\n💎 <b>โปรสิ้นเดือน GOD:</b> 2,499 เหลือ {int(PROMO_2499_PRICE):,} บาท ({PROMO_DATE_TEXT})"
        # >>> MAY26_COMBO_PROMO <<<
        if is_may_combo_promo_active() and selected_tier == "500":
            promo_caption += f"\n🔥 <b>โปรพ.ค.:</b> OF Combo 500 เหลือ {int(PROMO_500_PRICE)} บาท ({PROMO_MAY_DATE_TEXT})"
        if is_may_combo_promo_active() and selected_tier == "1299":
            promo_caption += f"\n🔥 <b>โปรพ.ค.:</b> GOD 3M 1,299 เหลือ {int(PROMO_1299_PRICE):,} บาท ({PROMO_MAY_DATE_TEXT})"
        # <<< MAY26_COMBO_PROMO >>>

        # ── ดึงประวัติลูกค้าสำหรับแจ้งแอดมิน ──
        customer_tag = ""
        try:
            async with get_session() as _hist_session:
                from sqlalchemy import func as sa_func
                _pay_count_result = await _hist_session.execute(
                    select(sa_func.count(Payment.id)).where(
                        Payment.user_id == user_db_id,
                        Payment.status == PaymentStatus.CONFIRMED,
                    )
                )
                _pay_count = _pay_count_result.scalar() or 0

                _prev_pkgs = []
                if _pay_count > 0:
                    _prev_result = await _hist_session.execute(
                        select(Package.name).join(Payment, Payment.package_id == Package.id).where(
                            Payment.user_id == user_db_id,
                            Payment.status == PaymentStatus.CONFIRMED,
                        ).distinct()
                    )
                    _prev_pkgs = [r[0] for r in _prev_result.all()]

                # เช็ค subscription ที่ active อยู่
                _active_sub_result = await _hist_session.execute(
                    select(Subscription).where(
                        Subscription.user_id == user_db_id,
                        Subscription.status == SubscriptionStatus.ACTIVE,
                    )
                )
                _has_active_sub = _active_sub_result.scalar_one_or_none() is not None

            if _pay_count == 0:
                customer_tag = "\n🆕 <b>ลูกค้าใหม่</b> (ยังไม่เคยซื้อ)"
            else:
                _pkgs_str = ", ".join(_prev_pkgs) if _prev_pkgs else "-"
                _status_str = "✅ สมาชิกอยู่" if _has_active_sub else "⏰ หมดอายุแล้ว"
                customer_tag = (
                    f"\n🔄 <b>ลูกค้าเก่า</b> (ซื้อมาแล้ว {_pay_count} ครั้ง)\n"
                    f"• สถานะ: {_status_str}\n"
                    f"• เคยซื้อ: {_pkgs_str}"
                )
        except Exception as _hist_exc:
            logger.warning("Failed to fetch customer history: %s", _hist_exc)

        # ชื่อแพ็กเกจที่ลูกค้าเลือก
        _selected_pkg_name = package.name if package else (selected_tier or "ไม่พบแพ็กเกจใน context")

        full_name = " ".join(part for part in [user.first_name, user.last_name] if part).strip() or safe_name
        selected_pkg_price_label = f" ({format_thb(expected_price)})" if not missing_context else ""
        fallback_caption = "\n⚠️ <b>Fallback:</b> ไม่พบ package context, ต้องตรวจมือ" if missing_context else ""

        caption = (
            f"📩 <b>สลิปใหม่ (รอตรวจ)</b>\n"
            f"🕒 {now_th.strftime('%d/%m/%Y %H:%M')}\n\n"
            f"👤 <b>ลูกค้า</b>\n"
            f"• ชื่อ: {full_name}\n"
            f"• User: @{user.username or '-'}\n"
            f"• ID: <code>{user.id}</code>"
            f"{customer_tag}\n\n"
            f"📦 <b>แพ็กเกจ</b>\n"
            f"• {_selected_pkg_name}{selected_pkg_price_label}\n\n"
            f"💳 <b>ผลอ่านสลิปจาก AI</b>\n"
            f"• ยอดเงิน: <b>{ai_amount_str} บาท</b>\n"
            f"{ai_summary}"
            f"{promo_caption}"
            f"{fallback_caption}"
        )

        # Build keyboard rows — add promo price button if active promo
        kb_rows = [
            [
                # TIER_99 button removed 2026-06-01
                tg.InlineKeyboardButton("⚡ 199 (Flash)", callback_data=f"approve_199_{user.id}", api_kwargs={"style": "success"}),
                tg.InlineKeyboardButton("🔥 200 (VIP โปร)", callback_data=f"approve_200_{user.id}", api_kwargs={"style": "success"}) if is_endmonth_vip_promo_active() else tg.InlineKeyboardButton("✅ 300 (VIP)", callback_data=f"approve_300_{user.id}", api_kwargs={"style": "success"}),
            ],
            [
                tg.InlineKeyboardButton("🔥 349 (OF โปร)", callback_data=f"approve_349_{user.id}", api_kwargs={"style": "success"}) if is_may_combo_promo_active() else tg.InlineKeyboardButton("✅ 500 (OF)", callback_data=f"approve_500_{user.id}", api_kwargs={"style": "success"}),
                tg.InlineKeyboardButton("🔥 999 (3M โปร)", callback_data=f"approve_999_{user.id}", api_kwargs={"style": "success"}) if is_may_combo_promo_active() else tg.InlineKeyboardButton("✅ 1299 (3M)", callback_data=f"approve_1299_{user.id}", api_kwargs={"style": "success"}),
            ],
            [
                tg.InlineKeyboardButton("💎 2000 (GOD โปร)", callback_data=f"approve_2000_{user.id}", api_kwargs={"style": "success"}) if is_endmonth_vip_promo_active() else tg.InlineKeyboardButton("✅ 2499 (GOD)", callback_data=f"approve_2499_{user.id}", api_kwargs={"style": "success"}),
                tg.InlineKeyboardButton("🌊 500 (Summer)", callback_data=f"approve_ADD500_{user.id}", api_kwargs={"style": "success"}),
            ],
            [
                tg.InlineKeyboardButton("❌ ปฏิเสธ", callback_data=f"reject_{user.id}", api_kwargs={"style": "danger"}),
            ],
        ]

        # Insert promo button row if active promo exists
        if promo_info:
            dp = promo_info["discounted_price"]
            pct = promo_info["discount_pct"]
            kb_rows.insert(0, [
                tg.InlineKeyboardButton(
                    f"🎟 {dp} (โปร{pct}%)",
                    callback_data=f"approve_promo_{user.id}",
                    api_kwargs={"style": "success"},
                ),
            ])

        kb_rows.append([
            tg.InlineKeyboardButton("🚫 แบน", callback_data=f"ban_{user.id}", api_kwargs={"style": "danger"}),
            tg.InlineKeyboardButton(f"💬 @{user.username}", url=f"https://t.me/{user.username}", api_kwargs={"style": "primary"}) if user.username else tg.InlineKeyboardButton("💬 เปิดข้อมูลลูกค้า", callback_data=f"chat_user_{user.id}", api_kwargs={"style": "primary"}),
        ])

        keyboard = tg.InlineKeyboardMarkup(kb_rows)

        # Download slip via Sales Bot, send as single post via Admin Bot
        slip_file = await context.bot.get_file(file_id)
        slip_buf = io.BytesIO()
        await slip_file.download_to_memory(slip_buf)
        slip_buf.seek(0)

        await admin_bot.send_photo(
            chat_id=ADMIN_GROUP_ID,
            photo=slip_buf,
            caption=caption,
            parse_mode="HTML",
            reply_markup=keyboard,
        )
        logger.info("Admin notification sent to group %s for payment %s", ADMIN_GROUP_ID, payment_id)
    except Exception as exc:
        logger.error("CRITICAL: Failed to notify admin for payment %s: %s", payment_id, exc)

    if payment_id is not None:
        await log_admin_action(
            admin_id=0,
            action="payment_pending_review",
            target_type="payment",
            target_id=payment_id,
            details=f"user_tg={user.id} amount={expected_price} ai_info={ai_info}",
        )

        # ── Log pending payment to Sheets ──
        try:
            from sheets.income_log import IncomeLogSheet
            await IncomeLogSheet.log_payment(payment_id, approved_by="-")
        except Exception as exc_s:
            logger.warning("Sheets log failed for pending payment #%d: %s", payment_id, exc_s)

    await _notify_discord(
        "⏸ PAYMENT HOLD — รอตรวจสอบ",
        f"**#{'PAY' + str(payment_id) if payment_id is not None else 'NO-PAYMENT-RECORD'}**",
        color=0xFFA500,
        fields=[
            {"name": "👤 ลูกค้า", "value": f"@{user.username or user.first_name} (ID: {user.id})", "inline": True},
            {"name": "📦 แพ็กเกจ", "value": f"{format_thb(expected_price)}" if not missing_context else (selected_tier or "ไม่พบแพ็กเกจใน context"), "inline": True},
            {"name": "💰 ยอด OCR", "value": f"{ai_amount_str} บาท", "inline": True},
        ] + ([{"name": "⚠️ เหตุผลที่ hold", "value": ai_suspicious or ("ไม่มี package context, ต้องตรวจมือ" if missing_context else "รอแอดมินตรวจสอบ"), "inline": False}]),
    )

    # Clear selection (including comeback promo data)
    context.user_data.pop("selected_tier", None)
    context.user_data.pop("selected_price", None)
    context.user_data.pop("comeback_promo", None)
    context.user_data.pop("comeback_discount", None)




async def handle_non_slip_payment(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle non-supported payment types (QR code, documents, etc.)."""
    if not update.message:
        return

    # Check if it's a document or sticker that isn't a slip/truemoney
    await update.message.reply_text(
        "⚠️ ขออภัยค่ะ ระบบรับเฉพาะ:\n"
        "1️⃣ <b>รูปสลิปโอนเงิน</b> (PromptPay/ธนาคาร)\n"
        "2️⃣ <b>ลิงก์ซอง TrueMoney</b> (gift.truemoney.com)\n\n"
        "QR Code หรือไฟล์อื่นๆ ไม่สามารถตรวจสอบได้ค่ะ\n"
        "กรุณาส่งรูปสลิป หรือลิงก์ซอง TrueMoney นะคะ 🙏",
        parse_mode="HTML",
    )


def _truemoney_link_filter(update: Update) -> bool:
    """Filter for messages containing TrueMoney gift links."""
    if update.message and update.message.text:
        return bool(TRUEMONEY_PATTERN.search(update.message.text))
    return False


def get_payment_handlers() -> list:
    """Return all handlers for the payment module."""
    return [
        # TrueMoney link handler (must be before generic text handler)
        MessageHandler(
            filters.TEXT & filters.Regex(r"gift\.truemoney\.com"),
            handle_truemoney_link,
        ),
        # Photo slip handler
        MessageHandler(filters.PHOTO, handle_photo_slip),
        # Non-supported payment types
        MessageHandler(
            filters.Document.ALL & ~filters.PHOTO,
            handle_non_slip_payment,
        ),
    ]
