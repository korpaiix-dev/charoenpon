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
from shared.contact_admin import contact_admin_kb
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
from bots.sales_bot.payment_util.utils import _resolve_tier
from shared import discount_helper as _disc_helper

from shared.songkran_promo import get_group_display_title
from shared.utils import (
    check_duplicate_slip,
    compute_slip_hash,
    format_datetime_thai,
    format_thb,
    log_admin_action,
)

logger = logging.getLogger(__name__)

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

    # Bug #1: safe download with retry + extended timeout
    # 2026-06-16 FIX: Telegram CDN sometimes slow → 5s default timeout was too aggressive
    # Now: 3 retries, exponential backoff (2/4/8s), 30s per attempt
    photo_bytes = None
    import asyncio as _asyncio
    import io as _io
    _retry_count = 0
    _last_err = None
    for _attempt in range(3):
        try:
            _tg_file = await context.bot.get_file(file_id, read_timeout=30, connect_timeout=15)
            _buf = _io.BytesIO()
            await _tg_file.download_to_memory(_buf)
            photo_bytes = _buf.getvalue()
            if _attempt > 0:
                logger.info("Slip download succeeded after %d retries", _attempt)
            break
        except Exception as _exc_dl:
            _retry_count += 1
            _last_err = _exc_dl
            if _attempt < 2:
                _delay = 2 ** (_attempt + 1)  # 2s, 4s
                logger.warning("Slip download attempt %d failed: %s — retry in %ds",
                                _attempt + 1, _exc_dl, _delay)
                await _asyncio.sleep(_delay)
            else:
                logger.error("Failed to download slip image after 3 attempts: %s", _exc_dl)

    # Bug #4: content-derived hash
    if photo_bytes:
        import hashlib as _hashlib
        slip_hash = _hashlib.sha256(photo_bytes).hexdigest()[:64]
    else:
        slip_hash = compute_slip_hash(file_id)  # fallback to file_id hash

    # ─── EARLY BLACKLIST CHECK 2026-06-18 (POST-SKY-WALK) ───
    # ตรวจก่อน Slip2Go เพื่อ: (1) ประหยัด API quota
    # (2) ตอบลูกค้าตรงประเด็น ไม่ต้องให้พิมพ์ /support
    # (3) แจ้งแอดมินอัตโนมัติพร้อมข้อมูล
    try:
        from shared.ban_service import is_slip_blacklisted
        _is_bl_slip, _bl_reason = await is_slip_blacklisted(None, slip_hash)
        if _is_bl_slip:
            logger.warning(
                "EARLY BLACKLIST block: tg=%s slip_hash=%s reason=%s",
                user.id, slip_hash[:16], _bl_reason,
            )
            await update.message.reply_text(
                "🔍 <b>กำลังตรวจสอบสลิปให้นะคะ</b>\n\n"
                "ระบบกำลังให้แอดมินช่วยตรวจ — ขอเวลา 5-10 นาทีค่ะ 🙏\n"
                "ถ้าด่วนกดปุ่มทักแอดมินด้านล่างได้เลยนะคะ",
                parse_mode="HTML",
                reply_markup=contact_admin_kb(),
            )
            # แจ้งห้องแอดมินอัตโนมัติ
            try:
                from shared.admin_alert import notify_admin_group
                import html as _h_bl
                await notify_admin_group(
                    f"🚨 <b>สลิปอยู่ในบัญชีดำ — บล็อกอัตโนมัติ</b>\n"
                    f"━━━━━━━━━━━━━━\n"
                    f"👤 ลูกค้า: {_h_bl.escape(str(user.first_name or '-'))} "
                    f"(<code>{user.id}</code>)\n"
                    f"📋 เหตุผล: <i>{_h_bl.escape(_bl_reason or 'in blacklist')}</i>\n"
                    f"💬 ระบบตอบลูกค้าให้แล้ว: '\''กำลังตรวจสอบ — แอดมินจะติดต่อกลับ'\''\n"
                    f"🔍 หากต้องการช่วย กดปุ่มเปิดแชท",
                    parse_mode="HTML",
                )
            except Exception as _exc_bl:
                logger.warning("blacklist admin notify failed: %s", _exc_bl)
            return
    except Exception as _exc_bl_check:
        logger.warning("blacklist check failed (allow): %s", _exc_bl_check)

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
    # FALLBACK: if amount matches a GACHA tier even without selected_tier (e.g. customer sent slip without picking package), override selected_tier.
    if slip2go_data:
        try:
            from shared.pricing import amount_to_tier as _amt_to_tier_g
            _s2g_amt = float(slip2go_data.get("amount") or 0)
            _tinfo = _amt_to_tier_g(int(_s2g_amt))
            if _tinfo and _tinfo[0].startswith("GACHA_") and (not selected_tier or not selected_tier.startswith("GACHA_")):
                logger.info("GACHA fallback: amount=%s detected as %s (selected_tier was %s)", _s2g_amt, _tinfo[0], selected_tier)
                selected_tier = _tinfo[0]
                context.user_data["selected_tier"] = _tinfo[0]
        except Exception as _ge:
            logger.warning("GACHA detect fallback failed: %s", _ge)

    # FIX 2026-06-26 (PRAE-CHAT FALLBACK): if customer talked via Prae chat instead of pressing buttons,
    # selected_tier may be empty. Use Slip2Go amount → tier mapping as fallback.
    # Only triggers when missing_context is True (button flow unaffected).
    if missing_context and slip2go_data and not selected_tier:
        try:
            from shared.pricing import amount_to_tier as _amt_to_tier_v
            _s2g_amt_v = float(slip2go_data.get("amount") or 0)
            _tinfo_v = _amt_to_tier_v(int(_s2g_amt_v))
            if _tinfo_v:
                _tier_str, _tier_label, _is_promo = _tinfo_v
                logger.info("PRAE-CHAT FALLBACK: amount=%s -> tier=%s label=%s — recovering missing_context", _s2g_amt_v, _tier_str, _tier_label)
                selected_tier = _tier_str
                context.user_data["selected_tier"] = _tier_str
                expected_price = await _get_effective_price(selected_tier, context.user_data)
                if expected_price:
                    missing_context = False
        except Exception as _pe:
            logger.warning("Prae-chat amount fallback failed: %s", _pe)

    if selected_tier and selected_tier.startswith("GACHA_") and slip2go_data:
        from shared.pricing import TIER_PRICES
        _spins_map = {"GACHA_1": 1, "GACHA_3": 3, "GACHA_10": 10}
        _spins = _spins_map.get(selected_tier, 0)
        _gacha_amt = float(TIER_PRICES.get(selected_tier, 0))
        try:
            from shared.models import Payment as _P, PaymentMethod as _PM, PaymentStatus as _PS, User as _U
            from sqlalchemy import text as _t, select as _sel
            # Extract slip metadata for dup check
            _g_trans_ref = (slip2go_data or {}).get("transRef") or None
            _g_sender_raw = (slip2go_data or {}).get("sender") or {}
            _g_sender_name = ((_g_sender_raw.get("account") or {}).get("name") or "") if isinstance(_g_sender_raw, dict) else ""
            async with get_session() as _s:
                # FIX 2026-06-16: upsert user FIRST so db_user_id is available
                _u = (await _s.execute(_sel(_U).where(_U.telegram_id == user.id))).scalar_one_or_none()
                if not _u:
                    _u = _U(telegram_id=user.id, first_name=user.first_name, username=user.username)
                    _s.add(_u)
                    await _s.flush()
                db_user_id = _u.id

                # FIX 2026-06-16 (POST-BIG-FRAUD): dup check on slip_trans_ref OR slip_hash
                # Big Fewry sent same stolen slip 2x in 9 min and bypassed because GACHA branch
                # didn\u0027t check for duplicates. Block ANY re-use of the same slip.
                _g_dup = None
                if _g_trans_ref:
                    _g_dup = (await _s.execute(_sel(_P).where(_P.slip_trans_ref == _g_trans_ref))).scalar_one_or_none()
                if not _g_dup and slip_hash:
                    _g_dup = (await _s.execute(_sel(_P).where(_P.slip_hash == slip_hash))).scalar_one_or_none()
                if _g_dup:
                    logger.warning("GACHA dup-slip block: user=%s existing_payment=%s trans_ref=%s",
                                    user.id, _g_dup.id, _g_trans_ref)
                    try:
                        await update.message.reply_text(
                            "\u26a0\ufe0f \u0e2a\u0e25\u0e34\u0e1b\u0e19\u0e35\u0e49\u0e16\u0e39\u0e01\u0e43\u0e0a\u0e49\u0e44\u0e1b\u0e41\u0e25\u0e49\u0e27 \u0e44\u0e21\u0e48\u0e2a\u0e32\u0e21\u0e32\u0e23\u0e16\u0e23\u0e31\u0e1a\u0e0b\u0e49\u0e33\u0e44\u0e14\u0e49\u0e04\u0e48\u0e30"
                        )
                    except Exception:
                        pass
                    return

                # FIX 2026-06-18 (POST-DAM-SCAM): Sender Ring Detection
                # Block auto-approve if this sender_name has been used by other Telegram accounts
                # in the last 7 days (scam ring pattern). Send to admin review instead.
                if _g_sender_name:
                    try:
                        from shared.sender_ring_check import is_sender_ring_suspicious
                        is_ring, other_uids = await is_sender_ring_suspicious(_g_sender_name, db_user_id)
                        if is_ring:
                            logger.warning(
                                "GACHA SCAM RING block: tg=%s sender=%r used_by=%s",
                                user.id, _g_sender_name, other_uids
                            )
                            try:
                                await update.message.reply_text(
                                    "\u26a0\ufe0f \u0e2a\u0e25\u0e34\u0e1b\u0e19\u0e35\u0e49\u0e15\u0e49\u0e2d\u0e07\u0e15\u0e23\u0e27\u0e08\u0e2a\u0e2d\u0e1a\u0e42\u0e14\u0e22\u0e41\u0e2d\u0e14\u0e21\u0e34\u0e19 \u0e01\u0e23\u0e38\u0e13\u0e32\u0e23\u0e2d\u0e2a\u0e31\u0e01\u0e04\u0e23\u0e39\u0e48"
                                )
                            except Exception: pass
                            # Notify admin
                            try:
                                from shared.admin_alert import notify_admin_group
                                await notify_admin_group(
                                    f"\ud83d\udea8 <b>SCAM RING DETECTED</b>\n"
                                    f"\ud83d\udc64 tg=<code>{user.id}</code> ({user.first_name})\n"
                                    f"\ud83d\udcc4 sender=<i>{_g_sender_name}</i>\n"
                                    f"\ud83d\udca1 used by {len(other_uids)} other Telegram acc(s) in 7d\n"
                                    f"\ud83d\udcb0 GACHA \u0e3f{_gacha_amt} (blocked auto-approve)",
                                    parse_mode="HTML",
                                )
                            except Exception as _e:
                                logger.warning("admin notify failed: %s", _e)
                            return
                    except Exception as _e:
                        logger.warning("sender ring check failed (allowing): %s", _e)

                # REFACTOR 2026-06-18 (POST-DAM-SCAM): route through canonical
                # service so payment + gacha_credits write ATOMICALLY.
                # Old code committed credits BEFORE later checks could revoke,
                # letting Dam spin 20x from scam slip before admin caught it.
                pass
            # ─── Atomic write via canonical service ───
            from shared.payment_approval import (
                apply_payment_approval as _gp_fn,
                ApprovalInput as _GpInp,
                ApprovalSource as _GpSrc,
            )
            from decimal import Decimal as _GpDec
            _g_sender_bank = ((_g_sender_raw.get("bank") or {}).get("short") if isinstance(_g_sender_raw, dict) else "") or ""
            _g_sender_account = ((_g_sender_raw.get("account") or {}).get("value") or "") if isinstance(_g_sender_raw, dict) else ""
            _gp = await _gp_fn(_GpInp(
                user_id=db_user_id,
                telegram_id=user.id,
                source=_GpSrc.GACHA,
                amount_paid=_GpDec(str(_gacha_amt)),
                explicit_tier=selected_tier,
                slip2go_amount=_GpDec(str(_gacha_amt)),
                slip_trans_ref=_g_trans_ref,
                slip_hash=slip_hash,
                sender_name=_g_sender_name or None,
                sender_bank_name=_g_sender_bank or None,
                sender_bank_account=_g_sender_account or None,
                slip_file_id=file_id,
                method="SLIP",
                skip_dup_check=True,
                skip_sender_ring=True,
                skip_dm=True,
            ))
            if not _gp.success:
                logger.error("GACHA apply_payment_approval failed: %s (%s)", _gp.error, _gp.error_details)
                try:
                    await update.message.reply_text(
                            "⚠️ ระบบขัดข้องชั่วคราว กรุณาลองอีกครั้ง หรือกดปุ่มทักแอดมินด้านล่าง",
                            reply_markup=contact_admin_kb(),
                        )
                except Exception:
                    pass
                return
            _payid = _gp.payment_id
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

            try:
                import telegram as _tg_g, html as _h_g, os as _os_g, io as _io_g
                _admin_bot_g = _tg_g.Bot(token=_os_g.environ.get("ADMIN_BOT_TOKEN", ""))
                try:
                    await _admin_bot_g.initialize()
                    _admin_chat_g = int(_os_g.environ.get("ADMIN_GROUP_CHAT_ID", ""))
                    _safe_tg_g = _h_g.escape(str(user.first_name or user.username or "ลูกค้า"))
                    _trans_ref = (slip2go_data.get("transRef") or "")[:32]
                    _alert = (
                        "🎰 <b>ซื้อกาชา — อนุมัติอัตโนมัติ</b>\n"
                        "━━━━━━━━━━━━━━\n"
                        f"👤 ลูกค้า: {_safe_tg_g} (<code>{user.id}</code>)\n"
                        f"💰 ยอด: <b>฿{int(_gacha_amt):,}</b>\n"
                        f"🎲 สิทธิ์หมุน: <b>{_spins}</b> ครั้ง\n"
                        f"🆔 เลขที่: <code>{_payid}</code>\n"
                        f"🔖 เลขสลิป: <code>{_trans_ref}</code>"
                    )
                    try:
                        if update.message and update.message.photo:
                            _slip_file_g = await context.bot.get_file(update.message.photo[-1].file_id)
                            _buf_g = _io_g.BytesIO()
                            await _slip_file_g.download_to_memory(_buf_g)
                            _buf_g.seek(0)
                            await _admin_bot_g.send_photo(chat_id=_admin_chat_g, photo=_buf_g, caption=_alert, parse_mode="HTML")
                        else:
                            await _admin_bot_g.send_message(chat_id=_admin_chat_g, text=_alert, parse_mode="HTML")
                    except Exception:
                        await _admin_bot_g.send_message(chat_id=_admin_chat_g, text=_alert, parse_mode="HTML")
                finally:
                    try: await _admin_bot_g.shutdown()
                    except Exception: pass
            except Exception as _exc_alert_g:
                logger.warning("gacha admin alert failed: %s", _exc_alert_g)

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
            from shared.models import Payment as _P, PaymentMethod as _PM, PaymentStatus as _PS, Package as _Pkg, User as _U
            async with get_session() as _s:
                # FIX 2026-06-16: upsert user FIRST (was NameError before)
                _u = (await _s.execute(select(_U).where(_U.telegram_id == user.id))).scalar_one_or_none()
                if not _u:
                    _u = _U(telegram_id=user.id, first_name=user.first_name, username=user.username)
                    _s.add(_u)
                    await _s.flush()
                db_user_id = _u.id
                _pq = await _s.execute(select(_Pkg).where(_Pkg.tier == _resolve_tier(selected_tier)))
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

        # ─── DUP / BLACKLIST GUARD 2026-06-18 (POST-SKY-WALK) ───
        # ระบบเป็นคนตรวจ — ไม่ส่งสลิปซ้ำ/ปลอมไปแอดมิน
        try:
            from shared.contact_admin import contact_admin_kb
            from sqlalchemy import select as _sel_g, text as _t_g
            # 1) transRef ซ้ำ = สลิปเคยถูกใช้แล้ว
            if s2g_trans_ref:
                async with get_session() as _s_dup:
                    _dup_res = await _s_dup.execute(_t_g(
                        "SELECT id, status FROM payments WHERE slip_trans_ref = :tref LIMIT 1"
                    ), {"tref": s2g_trans_ref})
                    _dup_row = _dup_res.fetchone()
                if _dup_row:
                    logger.warning(
                        "SLIP DUP block: tg=%s transRef=%s existing_payment_id=%s",
                        user.id, s2g_trans_ref, _dup_row[0],
                    )
                    await update.message.reply_text(
                        "❌ <b>สลิปนี้เคยถูกใช้แล้วค่ะ</b>\n\n"
                        "ระบบตรวจพบว่ามีคนใช้สลิปใบนี้สมัครไปแล้ว\n"
                        "ใช้สลิปเดิมซ้ำไม่ได้นะคะ 🙏\n\n"
                        "ถ้าคิดว่าเป็นข้อผิดพลาด กดปุ่มด้านล่างทักแอดมินได้เลยค่ะ",
                        parse_mode="HTML",
                        reply_markup=contact_admin_kb(),
                    )
                    return
            # 2) เลขสลิปในบัญชีดำ
            from shared.ban_service import is_slip_blacklisted, is_sender_blacklisted
            _bl_ok, _bl_reason = await is_slip_blacklisted(s2g_trans_ref, slip_hash)
            if _bl_ok:
                logger.warning(
                    "SLIP BLACKLIST block: tg=%s transRef=%s reason=%s",
                    user.id, s2g_trans_ref, _bl_reason,
                )
                await update.message.reply_text(
                    "❌ <b>สลิปนี้ใช้ไม่ได้ค่ะ</b>\n\n"
                    "ระบบตรวจพบว่าเป็นสลิปที่มีปัญหา\n\n"
                    "ถ้าคิดว่าเป็นข้อผิดพลาด กดปุ่มด้านล่างทักแอดมินได้เลยค่ะ",
                    parse_mode="HTML",
                    reply_markup=contact_admin_kb(),
                )
                return
            # 3) ชื่อผู้ส่งในบัญชีดำ → silent block (ไม่บอกว่าทำไม)
            _bl_sn, _ = await is_sender_blacklisted(s2g_sender_name)
            if _bl_sn:
                logger.warning(
                    "SENDER BLACKLIST block: tg=%s sender=%r",
                    user.id, s2g_sender_name,
                )
                await update.message.reply_text(
                    "❌ <b>ระบบไม่สามารถยืนยันสลิปนี้ได้ค่ะ</b>\n\n"
                    "ถ้าคิดว่าเป็นข้อผิดพลาด กดปุ่มด้านล่างทักแอดมินได้เลยค่ะ",
                    parse_mode="HTML",
                    reply_markup=contact_admin_kb(),
                )
                # Notify admin silently (เพื่อ track ขบวนการสแกม)
                try:
                    from shared.admin_alert import notify_admin_group
                    import html as _h_bl
                    await notify_admin_group(
                        f"🔇 <b>silent block (sender ในบัญชีดำ)</b>\n"
                        f"👤 ลูกค้า: {_h_bl.escape(str(user.first_name or '-'))} "
                        f"(<code>{user.id}</code>)\n"
                        f"📋 sender_name: <i>{_h_bl.escape(s2g_sender_name)}</i>\n"
                        f"💬 ระบบตอบลูกค้าแล้วว่ายืนยันไม่ได้",
                        parse_mode="HTML",
                    )
                except Exception:
                    pass
                return
        except Exception as _exc_dup:
            logger.warning("dup/blacklist check failed (allow through): %s", _exc_dup)
        # ─── END GUARD ───

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

            # ── Layer 2: Gemini Vision fallback (silent — no customer message yet) ──
            _layer2_passed = False
            try:
                from shared.slip_vision_layer2 import layer2_verify_and_decide
                _l2_img = None
                try:
                    import base64 as _b64x
                    _l2_img = _b64x.b64encode(photo_bytes).decode("utf-8") if photo_bytes else None
                except NameError:
                    pass
                if _l2_img:
                    _expected_amt = float(slip2go_data.get("amount") or 0) or None
                    _l2 = await layer2_verify_and_decide(
                        image_b64=_l2_img,
                        expected_amount=_expected_amt,
                        expected_tier=None,
                    )
                    logger.info("Layer 2 decision: approve=%s confidence=%.2f reason=%s",
                                _l2.get("approve"), _l2.get("confidence", 0), _l2.get("reason"))
                    if _l2.get("approve") and _l2.get("confidence", 0) >= 0.85:
                        _vd = _l2.get("vision_data") or {}
                        slip2go_data["receiver"] = slip2go_data.get("receiver") or {}
                        slip2go_data["receiver"]["account"] = {
                            "name": _vd.get("receiver_name") or _rcv_name,
                        }
                        try:
                            rcv_ok, rcv_reason, _matched_account = await receiver_match_pool(slip2go_data)
                        except Exception:
                            pass
                        if rcv_ok:
                            _layer2_passed = True
                            logger.info("Layer 2 PASS: re-matched receiver via vision")
            except Exception as _l2e:
                logger.warning("Layer 2 fallback failed: %s", _l2e)

            if not _layer2_passed:
                # ── Layer 3: Admin Review (customer NOT rejected — just waiting) ──
                logger.warning("Layer 2 fail → escalating to admin review: user=%s amount=%s rcv=%s tref=%s",
                               user.id, slip2go_data.get("amount"), _rcv_name, slip2go_data.get("transRef"))
                try:
                    from shared.slip_review import insert_pending_review_payment, build_admin_review_buttons
                    from shared.pricing import amount_to_tier as _amt_to_tier
                    from sqlalchemy import text as _sql_t

                    _slip_amt = float(slip2go_data.get("amount") or 0)
                    _tier_info = _amt_to_tier(int(_slip_amt))
                    _tier_str = (_tier_info[0] if _tier_info else (selected_tier or "300"))
                    _tier_enum_name = f"TIER_{_tier_str}"

                    async with get_session() as _sess0:
                        _r = await _sess0.execute(_sql_t(
                            "SELECT id FROM packages WHERE tier = :tier ORDER BY id LIMIT 1"
                        ), {"tier": _tier_enum_name})
                        _pkg_row = _r.fetchone()
                        _pkg_id_lookup = int(_pkg_row[0]) if _pkg_row else 7

                        _u_r = await _sess0.execute(_sql_t(
                            "SELECT id FROM users WHERE telegram_id = :tg"
                        ), {"tg": user.id})
                        _u_row = _u_r.fetchone()
                        if not _u_row:
                            _u_r2 = await _sess0.execute(_sql_t(
                                "INSERT INTO users (telegram_id, first_name, username, created_at, last_seen_at) "
                                "VALUES (:tg, :fn, :un, NOW(), NOW()) RETURNING id"
                            ), {"tg": user.id, "fn": (user.first_name or "")[:80], "un": (user.username or "")[:80]})
                            _user_db_id = _u_r2.scalar()
                        else:
                            _user_db_id = int(_u_row[0])
                        await _sess0.commit()

                    # Extract sender info
                    _snd = slip2go_data.get("sender") or {}
                    _snd_account = _snd.get("account") or {}
                    _snd_bank = _snd.get("bank") or {}

                    _pay_id = await insert_pending_review_payment(
                        user_db_id=_user_db_id,
                        package_id=_pkg_id_lookup,
                        amount=_slip_amt,
                        slip_trans_ref=slip2go_data.get("transRef"),
                        sender_name=_snd_account.get("name"),
                        sender_bank_name=_snd_bank.get("name"),
                        sender_bank_account=_snd_account.get("value"),
                    )

                    # DM customer: gentle "we are checking"
                    try:
                        await update.message.reply_text(
                            "🔍 <b>กำลังตรวจสอบสลิปเพิ่มเติม</b>\n\n"
                            "ทีมแอดมินจะตอบกลับภายใน 5-10 นาทีค่ะ กรุณารอสักครู่นะคะ 🙏\n"
                            "(ระบบจะแจ้งทันทีเมื่อยืนยันเสร็จ)",
                            parse_mode="HTML",
                        )
                    except Exception as _dm_e:
                        logger.warning("review DM failed: %s", _dm_e)

                    # Admin alert WITH Approve/Reject buttons
                    try:
                        import telegram as _tg, html as _h, os as _os
                        _admin_bot = _tg.Bot(token=_os.environ.get("ADMIN_BOT_TOKEN", ""))
                        try:
                            await _admin_bot.initialize()
                            _admin_chat = int(_os.environ.get("ADMIN_GROUP_CHAT_ID", ""))
                            _safe_tg = _h.escape(str(user.first_name or user.username or "ลูกค้า"))
                            _safe_rcv = _h.escape(_rcv_name)
                            _msg = (
                                "🔍 <b>สลิปต้องตรวจสอบ</b> (สลิปจริง แต่บัญชีไม่ตรง)\n"
                                "━━━━━━━━━━━━━━\n"
                                f"👤 ลูกค้า: {_safe_tg} (<code>{user.id}</code>)\n"
                                f"💰 ยอดในสลิป: <b>฿{int(_slip_amt):,}</b>\n"
                                f"📦 ระบบเดาเป็นแพ็ก: <b>{_tier_str}</b>\n"
                                f"🎯 บัญชีปลายทางในสลิป: {_safe_rcv}\n"
                                f"❌ เข้าบัญชีเรา: ไม่ใช่ ({_h.escape(rcv_reason or '-')})\n"
                                f"🔖 เลขสลิป: <code>{(slip2go_data.get('transRef') or '')[:32]}</code>\n"
                                f"🆔 เลขชำระเงิน: <code>{_pay_id}</code>\n"
                                f"\n💬 ลูกค้าได้รับแจ้งว่ากำลังตรวจสอบ — กรุณากดปุ่มด้านล่าง"
                            )
                            _kb = build_admin_review_buttons(_pay_id, user.id)
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
                                        reply_markup=_kb,
                                    )
                                    _wr_sent = True
                            except Exception as _exc_wrp:
                                logger.warning("review alert photo failed: %s", _exc_wrp)
                            if not _wr_sent:
                                await _admin_bot.send_message(
                                    chat_id=_admin_chat, text=_msg, parse_mode="HTML",
                                    reply_markup=_kb,
                                )
                        finally:
                            try: await _admin_bot.shutdown()
                            except Exception: pass
                    except Exception as _exc_alert:
                        logger.warning("review admin alert failed: %s", _exc_alert)

                except Exception as _exc_review:
                    logger.exception("review flow failed: %s", _exc_review)
                    # FIX 2026-06-18 (POST-SKY-WALK): ลูกค้าไม่ต้องพิมพ์ /support เอง
                    # ระบบจะแจ้งแอดมินอัตโนมัติ
                    try:
                        await update.message.reply_text(
                            "🔍 <b>กำลังตรวจสอบสลิปให้นะคะ</b>\n\n"
                            "ระบบกำลังให้แอดมินช่วยตรวจ — ขอเวลา 5-10 นาทีค่ะ 🙏\n"
                            "ถ้าด่วนกดปุ่มทักแอดมินด้านล่างได้เลยนะคะ",
                            parse_mode="HTML",
                            reply_markup=contact_admin_kb(),
                        )
                    except Exception:
                        pass
                    # 2026-06-18: ลบ alert "ระบบ escalate ไม่สำเร็จ" ออก เพราะส่วนใหญ่
                    # เป็นสลิปซ้ำ/ปลอม ที่ระบบจัดการได้แล้วใน guard ก่อนหน้านี้

                return  # ← do NOT fall through to AI path
            # If Layer 2 passed, fall through to normal auto-approve flow below
        else:
            # ── Discount auto-apply: bump matching amount by pending discount ─
            _pending_disc = float(context.user_data.get('use_discount_pending') or 0)
            _exp_slip     = float(context.user_data.get('use_discount_expected_slip') or 0)
            _s2g_for_match = s2g_amount
            if _pending_disc > 0 and _exp_slip > 0:
                from decimal import Decimal as _Dd
                # Only honor discount if slip amount ≈ expected (within ±1 THB rounding)
                if abs(float(s2g_amount) - _exp_slip) <= 1.0:
                    _s2g_for_match = s2g_amount + _Dd(str(_pending_disc))
                    logger.info("Discount applied: slip=%s + disc=%s -> match=%s",
                                s2g_amount, _pending_disc, _s2g_for_match)
                else:
                    logger.info("Discount pending=%s but slip=%s mismatch expected=%s -> ignore disc",
                                _pending_disc, s2g_amount, _exp_slip)
                    _pending_disc = 0  # don't apply
            tier_match = amount_to_tier(_s2g_for_match)
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
            # Tier 100 (ห้องมีคนชัก/SHAKER) — admin_callback_tier_map() doesn't include it
            tier_map_local['100'] = PackageTier.TIER_100
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

                # REFACTOR 2026-06-18: ทุก subscription/payment/dup-check/lifetime-guard/
                # birthday-bonus/shaker/audit-log/receiver-pool ทำใน apply_payment_approval
                # Handler ยังเก็บ custom updates (real_name, total_spent), retention bonus,
                # discount apply_usage, customer reply formatting
                try:
                    from shared.payment_approval import (
                        apply_payment_approval as _ap_fn,
                        ApprovalInput as _ApInp,
                        ApprovalSource as _ApSrc,
                    )
                    from decimal import Decimal as _ApDec
                    _ap_result = await _ap_fn(_ApInp(
                        user_id=0,
                        telegram_id=user.id,
                        source=_ApSrc.SLIP2GO_AUTO,
                        amount_paid=_ApDec(str(s2g_amount)),
                        explicit_tier=target_tier_enum,
                        slip_trans_ref=s2g_trans_ref or None,
                        slip_hash=slip_hash or None,
                        sender_name=s2g_sender_name or None,
                        sender_bank_name=s2g_sender_bank or None,
                        sender_bank_account=s2g_sender_account or None,
                        slip_file_id=file_id,
                        method="SLIP",
                        matched_receiver_account_id=(
                            _matched_account.get("id") if _matched_account else None
                        ),
                        skip_sender_ring=True,  # already checked upstream
                        skip_dm=True,           # handler does its own reply
                    ))
                    if not _ap_result.success:
                        if _ap_result.error and _ap_result.error.startswith("dup_"):
                            rejection = "สลิปนี้เคยถูกใช้แล้ว"
                        else:
                            rejection = f"ระบบขัดข้องชั่วคราว: {_ap_result.error or 'unknown'}"
                            logger.error("apply_payment_approval returned: %s (%s)",
                                         _ap_result.error, _ap_result.error_details)
                        raise _IE("approval failed", None, None)

                    _new_pay_id = _ap_result.payment_id
                    _pkg_name_safe = _ap_result.package_name

                    # Look up package + user for handler-needed fields + apply custom updates
                    async with get_session() as _sess:
                        _u = (await _sess.execute(_sel(User).where(User.telegram_id == user.id))).scalar_one_or_none()
                        if _u:
                            _user_id_safe = _u.id
                            # Bug #16: only set real_name on first purchase
                            if s2g_sender_name and not _u.real_name:
                                _u.real_name = s2g_sender_name
                                _u.last_sender_bank = s2g_sender_bank
                                _u.last_sender_account = s2g_sender_account
                            elif s2g_sender_name and _u.real_name and _u.real_name != s2g_sender_name:
                                logger.warning("Sender name mismatch: user=%s prev=%s new=%s",
                                               user.id, _u.real_name, s2g_sender_name)
                                _u.last_sender_bank = s2g_sender_bank
                                _u.last_sender_account = s2g_sender_account
                            # REMOVED 2026-06-21: trigger trg_payments_sync_total_spent handles this (was causing double-count)
                        _pkg = (await _sess.execute(
                            _sel(Package).where(Package.tier == target_tier_enum)
                        )).scalar_one_or_none()
                        if _pkg:
                            _pkg_id_safe = _pkg.id
                            _pkg_dur_safe = _pkg.duration_days

                    # Pre-build invite_links dict for downstream code (matches old shape)
                    _invite_links_from_service = {
                        f"il_{i}_{il.title}": il.url
                        for i, il in enumerate(_ap_result.invite_links or [])
                    }
                    _approve_ok = True

                    # ── Retention bonus grant (gacha/shaker) ──
                    try:
                        _retention_code = context.user_data.get("comeback_promo")
                        if _retention_code and _new_pay_id:
                            from shared.retention_bonus import grant_retention_bonus, build_bonus_message
                            _bonus = await grant_retention_bonus(
                                user_id=_user_id_safe,
                                telegram_id=user.id,
                                promo_code=_retention_code,
                                payment_id=_new_pay_id,
                            )
                            if _bonus.get("granted") and (_bonus.get("gacha", 0) > 0 or _bonus.get("shaker")):
                                # Queue bonus DM to send AFTER subscription DM
                                context.user_data["_pending_bonus_dm"] = build_bonus_message(_bonus)
                                logger.info("Retention bonus granted: tg=%s round=%s gacha=%s shaker=%s",
                                            user.id, _bonus.get("round"),
                                            _bonus.get("gacha"), _bonus.get("shaker"))
                    except Exception as _be:
                        logger.exception("Retention bonus hook failed: %s", _be)

                    # ── Discount auto-deduct ──────────────────────────────
                    try:
                        _pd = float(context.user_data.get('use_discount_pending') or 0)
                        if _pd > 0 and _new_pay_id:
                            from decimal import Decimal as _Dx
                            _full_p = _Dx(str(context.user_data.get('use_discount_base_price') or 0))
                            _paid_p = _Dx(str(context.user_data.get('use_discount_expected_slip') or 0))
                            await _disc_helper.apply_usage(
                                telegram_id=user.id,
                                payment_id=_new_pay_id,
                                tier_purchased=str(selected_tier or ''),
                                full_price=_full_p,
                                discount_used=_Dx(str(_pd)),
                                actual_paid=_paid_p,
                            )
                            logger.info("Discount auto-deducted: tg=%s pay=%s disc=%s",
                                        user.id, _new_pay_id, _pd)
                            _disc_helper.clear_context(context)
                    except Exception as _de:
                        logger.exception("Discount apply_usage failed: %s", _de)
                    # ──────────────────────────────────────────────────────

                except _IE as _ie:
                    # FIX 2026-06-16: Only treat as "dup" when the constraint truly is unique-slip
                    # Other IntegrityError (FK, NOT NULL) should surface as system error
                    _ie_str = str(_ie).lower()
                    _is_dup = ("slip_trans_ref" in _ie_str or "slip_hash" in _ie_str
                               or "unique" in _ie_str or "duplicate key" in _ie_str)
                    if _is_dup:
                        if not rejection:
                            rejection = "สลิปนี้เคยถูกใช้แล้ว (concurrent)"
                        logger.warning("Auto-approve IntegrityError (dup): %s", _ie)
                    else:
                        if not rejection:
                            rejection = f"ระบบขัดข้องชั่วคราว: {str(_ie)[:80]}"
                        logger.error("Auto-approve IntegrityError (non-dup): %s", _ie)
                except Exception as _ie:
                    logger.error("Auto-approve write failed: %s", _ie)
                    rejection = f"ระบบขัดข้องชั่วคราว: {str(_ie)[:80]}"

                if rejection:
                    await update.message.reply_text(
                        f"❌ <b>สลิปนี้ใช้ไม่ได้</b>\n\n{rejection}\n\n"
                        f"กดปุ่มด้านล่างเพื่อทักแอดมินได้เลยค่ะ",
                        parse_mode="HTML",
                        reply_markup=contact_admin_kb(),
                    )
                    return

                if _approve_ok:
                    # REFACTOR 2026-06-18: invite links already generated by service
                    # — use _ap_result.invite_links directly (no second Guardian call)
                    import telegram as tg
                    for il in (_ap_result.invite_links or []):
                        _link_rows.append([tg.InlineKeyboardButton(
                            f"🚀 {il.title}", url=il.url
                        )])

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

                    # NEW 2026-06-20: customer-facing onboarding gift line
                    _onb_cust = ""
                    try:
                        _og = int(getattr(_ap_result, "onboarding_gacha_added", 0) or 0)
                        _od = int(getattr(_ap_result, "onboarding_discount_added", 0) or 0)
                        _odays = int(getattr(_ap_result, "onboarding_extra_days", 0) or 0)
                        if _og or _od or _odays:
                            _parts = []
                            if _og:    _parts.append(f"🎰 กาชา {_og} หมุน")
                            if _od:    _parts.append(f"💵 บัตรลด ฿{_od}")
                            if _odays: _parts.append(f"📅 +{_odays} วันฟรี")
                            _onb_cust = "\n\n🎁 <b>ของขวัญต้อนรับสมาชิกใหม่:</b>\n   " + " · ".join(_parts)
                    except Exception:
                        pass
                    await update.message.reply_text(
                        f"✅ <b>อนุมัติอัตโนมัติเรียบร้อยค่ะ!</b>\n\n"
                        f"📦 แพ็กเกจ: <b>{_pkg_name_safe}</b>\n"
                        f"💰 ยอดชำระ: <b>฿{int(s2g_amount):,}</b>\n"
                        f"⏰ หมดอายุ: <b>{_expiry_text_safe}</b>"
                        f"{_selected_note}{_onb_cust}\n\n"
                        f"กดลิงก์ด้านล่างเข้ากลุ่มได้เลย 👇" if _link_rows else
                        f"✅ <b>อนุมัติอัตโนมัติเรียบร้อยค่ะ!</b>\n\n"
                        f"📦 แพ็กเกจ: <b>{_pkg_name_safe}</b>\n"
                        f"💰 ยอดชำระ: <b>฿{int(s2g_amount):,}</b>\n"
                        f"⏰ หมดอายุ: <b>{_expiry_text_safe}</b>"
                        f"{_selected_note}{_onb_cust}\n\n"
                        f"กดปุ่มด้านล่างทักแอดมินเพื่อขอลิงก์เข้ากลุ่มได้เลยค่ะ",
                        parse_mode="HTML",
                        reply_markup=tg.InlineKeyboardMarkup(_link_rows) if _link_rows else None,
                        disable_web_page_preview=True,
                    )

                    # ── Pending retention bonus DM (if any) ──
                    try:
                        _bonus_msg = context.user_data.pop("_pending_bonus_dm", None)
                        if _bonus_msg:
                            await update.message.reply_text(_bonus_msg, parse_mode="HTML")
                    except Exception as _bonus_dm_err:
                        logger.warning("Bonus DM send failed: %s", _bonus_dm_err)

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
                            # NEW 2026-06-20: append onboarding gift section (first-payment only)
                            _onb_lines = ""
                            try:
                                _og = int(getattr(_ap_result, "onboarding_gacha_added", 0) or 0)
                                _od = int(getattr(_ap_result, "onboarding_discount_added", 0) or 0)
                                _odays = int(getattr(_ap_result, "onboarding_extra_days", 0) or 0)
                                if _og or _od or _odays:
                                    _onb_lines = "\n\n🎁 <b>ของขวัญต้อนรับ (ลูกค้าใหม่):</b>"
                                    if _og:    _onb_lines += f"\n   🎰 กาชา {_og} หมุน"
                                    if _od:    _onb_lines += f"\n   💵 บัตรลด ฿{_od}"
                                    if _odays: _onb_lines += f"\n   📅 +{_odays} วัน"
                            except Exception:
                                pass
                            _admin_msg = (
                                f"🤖 <b>อนุมัติอัตโนมัติ (Slip2Go)</b>\n"
                                f"━━━━━━━━━━━━━━\n"
                                f"📋 เลขที่: #{_new_pay_id}\n"
                                f"👤 ลูกค้า: {_safe_tg_name} (<code>{user.id}</code>)\n"
                                f"🆔 <b>ชื่อจริง:</b> {_safe_real}\n"
                                f"🏦 <b>จาก:</b> {_safe_bank}\n"
                                f"🎯 <b>เข้าบัญชีเรา:</b> {_recv_label}\n"
                                f"💰 ยอด: <b>฿{int(s2g_amount):,}</b>\n"
                                f"📦 แพ็ก: <b>{_h.escape(_pkg_name_safe)}</b> {'🔥 (โปร)' if is_promo else ''}\n"
                                f"🔖 เลขสลิป: <code>{s2g_trans_ref or '-'}</code>"
                                f"{_onb_lines}"
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
                                "🤖 อนุมัติอัตโนมัติ (แจ้งห้องแอดมินไม่ได้)",
                                f"เลขที่ #{_new_pay_id} ฿{int(s2g_amount)} {_pkg_name_safe} — ส่งไป Telegram แอดมินไม่ได้: {_exc_an}",
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
                select(Package).where(Package.tier == _resolve_tier(selected_tier))
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
                tg.InlineKeyboardButton("🎰 100 (ห้องมีคนชัก)", callback_data=f"approve_100_{user.id}", api_kwargs={"style": "success"}),
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
        "⏸ ระงับชำระเงินไว้ — รอตรวจสอบ",
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
