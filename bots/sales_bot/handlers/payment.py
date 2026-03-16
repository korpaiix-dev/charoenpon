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
)
from shared.utils import (
    check_duplicate_slip,
    compute_slip_hash,
    format_datetime_thai,
    format_thb,
    log_admin_action,
)

logger = logging.getLogger(__name__)

DISCORD_WEBHOOK_URL: str = os.environ.get("DISCORD_WEBHOOK_URL", "")

TIER_PRICES: dict[str, Decimal] = {
    "300": Decimal("300"),
    "500": Decimal("500"),
    "1299": Decimal("1299"),
    "2499": Decimal("2499"),
}

TRUEMONEY_PATTERN = re.compile(
    r"https?://gift\.truemoney\.com/campaign/\?v=([a-zA-Z0-9]+)", re.IGNORECASE
)

# OCR patterns to extract amount from slip
AMOUNT_PATTERNS = [
    re.compile(r"(?:จำนวน|amount|ยอด|total)[:\s]*([0-9,]+(?:\.\d{2})?)\s*(?:บาท|baht|thb)?", re.IGNORECASE),
    re.compile(r"([0-9,]+(?:\.\d{2})?)\s*(?:บาท|baht|thb)", re.IGNORECASE),
    re.compile(r"THB\s*([0-9,]+(?:\.\d{2})?)", re.IGNORECASE),
]

DATE_PATTERNS = [
    re.compile(r"(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{2,4})"),
    re.compile(r"(\d{1,2})\s+(?:ม\.ค\.|ก\.พ\.|มี\.ค\.|เม\.ย\.|พ\.ค\.|มิ\.ย\.|ก\.ค\.|ส\.ค\.|ก\.ย\.|ต\.ค\.|พ\.ย\.|ธ\.ค\.)\s+(\d{2,4})"),
]


async def _notify_discord(title: str, details: str) -> None:
    """Send payment notification to Discord."""
    if not DISCORD_WEBHOOK_URL:
        return
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(
                DISCORD_WEBHOOK_URL,
                json={"content": f"**{title}**\n{details}"},
            )
    except Exception as exc:
        logger.error("Discord notification failed: %s", exc)


def _extract_amount_from_ocr(text: str) -> Decimal | None:
    """Extract transfer amount from OCR text."""
    for pattern in AMOUNT_PATTERNS:
        match = pattern.search(text)
        if match:
            amount_str = match.group(1).replace(",", "")
            try:
                return Decimal(amount_str)
            except InvalidOperation:
                continue
    return None


def _check_date_within_24h(text: str) -> bool:
    """Check if any date found in OCR text is within 24 hours.

    Returns True if date is recent or no date found (benefit of doubt for OCR).
    """
    now = datetime.now(timezone.utc)
    for pattern in DATE_PATTERNS:
        match = pattern.search(text)
        if match:
            try:
                groups = match.groups()
                if len(groups) == 3:
                    day, month, year = int(groups[0]), int(groups[1]), int(groups[2])
                    if year < 100:
                        year += 2500 if year > 40 else 2000
                    if year > 2500:
                        year -= 543  # Buddhist era
                    slip_date = datetime(year, month, day, tzinfo=timezone.utc)
                    if (now - slip_date).total_seconds() <= 86400:
                        return True
                    return False
            except (ValueError, OverflowError):
                continue
    # No date found — give benefit of doubt
    return True


async def _ocr_slip_image(bot, file_id: str) -> str:
    """Download image from Telegram and run OCR."""
    file = await bot.get_file(file_id)
    buf = io.BytesIO()
    await file.download_to_memory(buf)
    buf.seek(0)
    image = Image.open(buf)
    text = pytesseract.image_to_string(image, lang="tha+eng")
    return text


async def _verify_truemoney_link(link: str) -> dict:
    """Verify TrueMoney gift link and extract amount.

    Returns dict with: valid (bool), amount (Decimal|None), voucher_id (str).
    """
    match = TRUEMONEY_PATTERN.search(link)
    if not match:
        return {"valid": False, "amount": None, "voucher_id": ""}

    voucher_id = match.group(1)

    # Try to check the voucher via TrueMoney API
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"https://gift.truemoney.com/campaign/vouchers/{voucher_id}/verify",
                headers={"User-Agent": "Mozilla/5.0"},
            )
            if resp.status_code == 200:
                data = resp.json()
                status_data = data.get("status", {})
                if status_data.get("code") == "SUCCESS":
                    voucher = data.get("data", {}).get("voucher", {})
                    amount_str = voucher.get("amount_baht", "0")
                    try:
                        amount = Decimal(str(amount_str))
                    except InvalidOperation:
                        amount = None
                    return {
                        "valid": True,
                        "amount": amount,
                        "voucher_id": voucher_id,
                    }
            return {"valid": False, "amount": None, "voucher_id": voucher_id}
    except Exception as exc:
        logger.warning("TrueMoney verification failed: %s", exc)
        return {"valid": False, "amount": None, "voucher_id": voucher_id}


async def _approve_payment(
    payment: Payment,
    user_telegram_id: int,
    bot,
) -> list[str]:
    """Approve payment: create subscription and generate invite links."""
    invite_links: list[str] = []

    async with get_session() as session:
        # Update payment status
        result = await session.execute(
            select(Payment).where(Payment.id == payment.id)
        )
        db_payment = result.scalar_one()
        db_payment.status = PaymentStatus.CONFIRMED
        db_payment.verified_at = datetime.now(timezone.utc)

        # Get package
        pkg_result = await session.execute(
            select(Package).where(Package.id == db_payment.package_id)
        )
        package = pkg_result.scalar_one()

        # Create subscription
        now = datetime.now(timezone.utc)
        sub = Subscription(
            user_id=db_payment.user_id,
            package_id=package.id,
            status=SubscriptionStatus.ACTIVE,
            start_date=now,
            end_date=now + timedelta(days=package.duration_days),
            payment_id=db_payment.id,
        )
        session.add(sub)
        await session.flush()

        # Get group invite links
        group_slugs = package.group_list
        for slug in group_slugs:
            grp_result = await session.execute(
                select(GroupRegistry).where(GroupRegistry.slug == slug)
            )
            group = grp_result.scalar_one_or_none()
            if group and group.invite_link:
                invite_links.append(f"• {group.title}: {group.invite_link}")
            elif group:
                # Generate invite link
                try:
                    link = await bot.create_chat_invite_link(
                        chat_id=group.chat_id,
                        member_limit=1,
                        name=f"user_{user_telegram_id}_{package.tier.value}",
                    )
                    invite_links.append(f"• {group.title}: {link.invite_link}")
                except Exception as exc:
                    logger.error(
                        "Failed to create invite for group %s: %s", slug, exc
                    )
                    invite_links.append(f"• {group.title}: (ติดต่อแอดมินค่ะ)")

    return invite_links


async def handle_photo_slip(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle photo message — OCR slip verification."""
    if not update.message or not update.message.photo:
        return

    user = update.effective_user
    if not user:
        return

    # Check if user has selected a package
    selected_tier = context.user_data.get("selected_tier")
    if not selected_tier:
        await update.message.reply_text(
            "กรุณาเลือกแพ็กเกจก่อนส่งสลิปนะคะ 📦\n"
            "พิมพ์ /packages เพื่อดูแพ็กเกจค่ะ",
        )
        return

    expected_price = TIER_PRICES.get(selected_tier)
    if not expected_price:
        await update.message.reply_text("แพ็กเกจไม่ถูกต้องค่ะ กรุณาเลือกใหม่นะคะ")
        return

    await update.message.reply_text("🔍 กำลังตรวจสอบสลิปค่ะ กรุณารอสักครู่...")

    # Get the largest photo
    photo = update.message.photo[-1]
    file_id = photo.file_id

    # Check duplicate
    dup = await check_duplicate_slip(file_id)
    if dup:
        await update.message.reply_text(
            "❌ สลิปนี้เคยใช้แล้วค่ะ กรุณาส่งสลิปใหม่นะคะ"
        )
        await log_admin_action(
            admin_id=0,
            action="payment_reject_duplicate",
            target_type="user",
            target_id=user.id,
            details=f"Duplicate slip: file_id={file_id}",
        )
        return

    # OCR
    try:
        ocr_text = await _ocr_slip_image(context.bot, file_id)
    except Exception as exc:
        logger.error("OCR failed: %s", exc)
        await update.message.reply_text(
            "⚠️ ไม่สามารถอ่านสลิปได้ค่ะ กรุณาส่งรูปที่ชัดขึ้น หรือติดต่อแอดมินค่ะ"
        )
        return

    # Extract amount
    ocr_amount = _extract_amount_from_ocr(ocr_text)

    # Check 3 conditions
    reasons: list[str] = []

    # 1. Amount matches
    amount_ok = False
    if ocr_amount is not None:
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

    # 3. Not duplicate (already checked above)
    slip_hash = compute_slip_hash(file_id)

    # Create user if needed and get user_id
    async with get_session() as session:
        from shared.models import User

        user_result = await session.execute(
            select(User).where(User.telegram_id == user.id)
        )
        db_user = user_result.scalar_one_or_none()
        if not db_user:
            db_user = User(
                telegram_id=user.id,
                username=user.username,
                first_name=user.first_name,
            )
            session.add(db_user)
            await session.flush()

        # Find package
        pkg_result = await session.execute(
            select(Package).where(Package.tier == PackageTier(selected_tier))
        )
        package = pkg_result.scalar_one_or_none()
        if not package:
            await update.message.reply_text("ไม่พบแพ็กเกจในระบบค่ะ ติดต่อแอดมินนะคะ")
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
        user_db_id = db_user.id

    # Decision
    if amount_ok and date_ok and not reasons:
        # APPROVED
        invite_links = await _approve_payment(payment, user.id, context.bot)
        links_text = "\n".join(invite_links) if invite_links else "ติดต่อแอดมินเพื่อรับลิงก์ค่ะ"

        await update.message.reply_text(
            f"✅ <b>ชำระเงินสำเร็จค่ะ!</b>\n\n"
            f"💰 ยอด: {format_thb(expected_price)}\n"
            f"📦 แพ็กเกจ: {selected_tier} บาท\n\n"
            f"🔗 <b>ลิงก์เข้ากลุ่ม:</b>\n{links_text}\n\n"
            f"ขอบคุณที่ใช้บริการค่ะ 🙏",
            parse_mode="HTML",
        )

        await log_admin_action(
            admin_id=0,
            action="payment_approved_auto",
            target_type="payment",
            target_id=payment_id,
            details=f"user_tg={user.id} tier={selected_tier} amount={expected_price}",
        )

        await _notify_discord(
            "✅ Payment Approved (Auto)",
            f"User: @{user.username or user.id}\n"
            f"Package: {selected_tier} THB\n"
            f"Payment ID: {payment_id}",
        )

        # Clear selection
        context.user_data.pop("selected_tier", None)
        context.user_data.pop("selected_price", None)

    elif ocr_amount is None and date_ok:
        # HOLD — can't read amount, but date OK — suspicious
        async with get_session() as session:
            result = await session.execute(
                select(Payment).where(Payment.id == payment_id)
            )
            p = result.scalar_one()
            p.status = PaymentStatus.PENDING

        await update.message.reply_text(
            "⏳ <b>สลิปอยู่ระหว่างตรวจสอบค่ะ</b>\n\n"
            "ระบบไม่สามารถอ่านยอดเงินได้ชัดเจน\n"
            "แอดมินจะตรวจสอบและแจ้งผลให้เร็วที่สุดค่ะ\n\n"
            "หมายเลขอ้างอิง: #PAY{payment_id}",
            parse_mode="HTML",
        )

        await log_admin_action(
            admin_id=0,
            action="payment_hold",
            target_type="payment",
            target_id=payment_id,
            details=f"user_tg={user.id} reason=OCR unreadable amount",
        )

        await _notify_discord(
            "⏳ Payment On Hold",
            f"User: @{user.username or user.id}\n"
            f"Package: {selected_tier} THB\n"
            f"Payment ID: {payment_id}\n"
            f"Reason: Cannot read amount from slip",
        )

    else:
        # REJECTED
        async with get_session() as session:
            result = await session.execute(
                select(Payment).where(Payment.id == payment_id)
            )
            p = result.scalar_one()
            p.status = PaymentStatus.REJECTED
            p.reject_reason = "; ".join(reasons)

        reasons_text = "\n".join(f"• {r}" for r in reasons)
        await update.message.reply_text(
            f"❌ <b>สลิปไม่ผ่านการตรวจสอบค่ะ</b>\n\n"
            f"<b>เหตุผล:</b>\n{reasons_text}\n\n"
            f"กรุณาส่งสลิปใหม่ที่ถูกต้อง หรือติดต่อแอดมินค่ะ\n"
            f"หมายเลขอ้างอิง: #PAY{payment_id}",
            parse_mode="HTML",
        )

        await log_admin_action(
            admin_id=0,
            action="payment_rejected_auto",
            target_type="payment",
            target_id=payment_id,
            details=f"user_tg={user.id} reasons={'; '.join(reasons)}",
        )

        await _notify_discord(
            "❌ Payment Rejected (Auto)",
            f"User: @{user.username or user.id}\n"
            f"Package: {selected_tier} THB\n"
            f"Payment ID: {payment_id}\n"
            f"Reasons: {'; '.join(reasons)}",
        )


async def handle_truemoney_link(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle TrueMoney gift link."""
    if not update.message or not update.message.text:
        return

    user = update.effective_user
    if not user:
        return

    text = update.message.text.strip()
    match = TRUEMONEY_PATTERN.search(text)
    if not match:
        return

    link = match.group(0)

    # Check selected package
    selected_tier = context.user_data.get("selected_tier")
    if not selected_tier:
        await update.message.reply_text(
            "กรุณาเลือกแพ็กเกจก่อนส่งลิงก์ซองนะคะ 📦\n"
            "พิมพ์ /packages เพื่อดูแพ็กเกจค่ะ",
        )
        return

    expected_price = TIER_PRICES.get(selected_tier)
    if not expected_price:
        await update.message.reply_text("แพ็กเกจไม่ถูกต้องค่ะ กรุณาเลือกใหม่นะคะ")
        return

    await update.message.reply_text("🔍 กำลังตรวจสอบซอง TrueMoney ค่ะ กรุณารอสักครู่...")

    # Check duplicate
    dup = await check_duplicate_slip(link)
    if dup:
        await update.message.reply_text(
            "❌ ลิงก์ซองนี้เคยใช้แล้วค่ะ กรุณาส่งลิงก์ใหม่นะคะ"
        )
        await log_admin_action(
            admin_id=0,
            action="payment_reject_duplicate_truemoney",
            target_type="user",
            target_id=user.id,
            details=f"Duplicate TrueMoney link: {link}",
        )
        return

    # Verify TrueMoney
    tm_result = await _verify_truemoney_link(link)
    slip_hash = compute_slip_hash(link)

    # Get user and package from DB
    async with get_session() as session:
        from shared.models import User as UserModel

        user_result = await session.execute(
            select(UserModel).where(UserModel.telegram_id == user.id)
        )
        db_user = user_result.scalar_one_or_none()
        if not db_user:
            db_user = UserModel(
                telegram_id=user.id,
                username=user.username,
                first_name=user.first_name,
            )
            session.add(db_user)
            await session.flush()

        pkg_result = await session.execute(
            select(Package).where(Package.tier == PackageTier(selected_tier))
        )
        package = pkg_result.scalar_one_or_none()
        if not package:
            await update.message.reply_text("ไม่พบแพ็กเกจในระบบค่ะ ติดต่อแอดมินนะคะ")
            return

        payment = Payment(
            user_id=db_user.id,
            package_id=package.id,
            amount=expected_price,
            method=PaymentMethod.TRUEWALLET,
            status=PaymentStatus.PENDING,
            slip_url=link,
            slip_hash=slip_hash,
            transaction_ref=tm_result.get("voucher_id", ""),
        )
        session.add(payment)
        await session.flush()
        payment_id = payment.id

    # Decision
    reasons: list[str] = []

    if not tm_result["valid"]:
        reasons.append("ไม่สามารถยืนยันซอง TrueMoney ได้")
    elif tm_result["amount"] is not None:
        if abs(tm_result["amount"] - expected_price) > Decimal("1"):
            reasons.append(
                f"ยอดไม่ตรง: ซอง {format_thb(tm_result['amount'])} "
                f"แต่ต้องการ {format_thb(expected_price)}"
            )

    if not reasons and tm_result["valid"]:
        # APPROVED
        invite_links = await _approve_payment(payment, user.id, context.bot)
        links_text = "\n".join(invite_links) if invite_links else "ติดต่อแอดมินเพื่อรับลิงก์ค่ะ"

        await update.message.reply_text(
            f"✅ <b>ชำระเงินสำเร็จค่ะ!</b>\n\n"
            f"💰 ยอด: {format_thb(expected_price)}\n"
            f"📦 แพ็กเกจ: {selected_tier} บาท\n"
            f"💳 ช่องทาง: TrueMoney\n\n"
            f"🔗 <b>ลิงก์เข้ากลุ่ม:</b>\n{links_text}\n\n"
            f"ขอบคุณที่ใช้บริการค่ะ 🙏",
            parse_mode="HTML",
        )

        await log_admin_action(
            admin_id=0,
            action="payment_approved_truemoney",
            target_type="payment",
            target_id=payment_id,
            details=f"user_tg={user.id} tier={selected_tier} voucher={tm_result.get('voucher_id', '')}",
        )

        await _notify_discord(
            "✅ Payment Approved (TrueMoney)",
            f"User: @{user.username or user.id}\n"
            f"Package: {selected_tier} THB\n"
            f"Voucher: {tm_result.get('voucher_id', 'N/A')}",
        )

        context.user_data.pop("selected_tier", None)
        context.user_data.pop("selected_price", None)

    elif tm_result["valid"] and tm_result["amount"] is None:
        # HOLD — valid link but can't read amount
        await update.message.reply_text(
            "⏳ <b>ซองอยู่ระหว่างตรวจสอบค่ะ</b>\n\n"
            "แอดมินจะตรวจสอบและแจ้งผลให้เร็วที่สุดค่ะ\n"
            f"หมายเลขอ้างอิง: #PAY{payment_id}",
            parse_mode="HTML",
        )

        await log_admin_action(
            admin_id=0,
            action="payment_hold_truemoney",
            target_type="payment",
            target_id=payment_id,
            details=f"user_tg={user.id} reason=Cannot verify amount",
        )

        await _notify_discord(
            "⏳ Payment On Hold (TrueMoney)",
            f"User: @{user.username or user.id}\n"
            f"Package: {selected_tier} THB\n"
            f"Payment ID: {payment_id}\n"
            f"Reason: Cannot verify amount",
        )

    else:
        # REJECTED
        async with get_session() as session:
            result = await session.execute(
                select(Payment).where(Payment.id == payment_id)
            )
            p = result.scalar_one()
            p.status = PaymentStatus.REJECTED
            p.reject_reason = "; ".join(reasons)

        reasons_text = "\n".join(f"• {r}" for r in reasons)
        await update.message.reply_text(
            f"❌ <b>ซอง TrueMoney ไม่ผ่านการตรวจสอบค่ะ</b>\n\n"
            f"<b>เหตุผล:</b>\n{reasons_text}\n\n"
            f"กรุณาส่งลิงก์ใหม่ที่ถูกต้อง หรือติดต่อแอดมินค่ะ\n"
            f"หมายเลขอ้างอิง: #PAY{payment_id}",
            parse_mode="HTML",
        )

        await log_admin_action(
            admin_id=0,
            action="payment_rejected_truemoney",
            target_type="payment",
            target_id=payment_id,
            details=f"user_tg={user.id} reasons={'; '.join(reasons)}",
        )

        await _notify_discord(
            "❌ Payment Rejected (TrueMoney)",
            f"User: @{user.username or user.id}\n"
            f"Package: {selected_tier} THB\n"
            f"Reasons: {'; '.join(reasons)}",
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
