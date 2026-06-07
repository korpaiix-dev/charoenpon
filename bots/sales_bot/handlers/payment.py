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




# ── Strangler-fig Round 6: photo-slip handler + helpers extracted ──
# Moved to bots/sales_bot/payment_util/slip_handler.py — logic unchanged.
from bots.sales_bot.payment_util.slip_handler import (
    _get_effective_price,
    _send_welcome_referral_dm,
    _build_admin_approve_kb,
    handle_photo_slip,
)

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
