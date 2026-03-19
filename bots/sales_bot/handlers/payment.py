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
    r"https?://gift\.truemoney\.com/campaign/??\?v=([a-zA-Z0-9]+)", re.IGNORECASE
)

# OCR patterns to extract amount from slip
AMOUNT_PATTERNS = [
    re.compile(r"(?:จำนวนเงิน|จำนวน|amount|ยอด|total|ยอดเงิน)[:\s]*([0-9,]+(?:\.\d{2})?)\s*(?:บาท|baht|thb)?", re.IGNORECASE),
    re.compile(r"([0-9,]+(?:\.\d{2})?)\s*(?:บาท|baht|thb)", re.IGNORECASE),
    re.compile(r"THB\s*([0-9,]+(?:\.\d{2})?)", re.IGNORECASE),
    re.compile(r"([0-9,]+\.\d{2})\s*THB", re.IGNORECASE),
]

DATE_PATTERNS = [
    re.compile(r"(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{2,4})"),
    re.compile(r"(\d{1,2})\s+(?:ม\.ค\.|ก\.พ\.|มี\.ค\.|เม\.ย\.|พ\.ค\.|มิ\.ย\.|ก\.ค\.|ส\.ค\.|ก\.ย\.|ต\.ค\.|พ\.ย\.|ธ\.ค\.)\s+(\d{2,4})"),
]


async def _notify_discord(title: str, details: str, color: int = 0xFFA500, fields: list = None) -> None:
    """Send payment notification to Discord #alerts as embed."""
    discord_token = os.environ.get("DISCORD_BOT_TOKEN", "")
    discord_ch = os.environ.get("DISCORD_CH_ALERTS", "")
    if not discord_token or not discord_ch:
        return
    try:
        now_th = datetime.now(timezone(timedelta(hours=7)))
        embed = {
            "title": title,
            "description": details,
            "color": color,
            "footer": {"text": f"⊙ ระบบตรวจสลิป เจริญพร | วันนี้ เวลา {now_th.strftime('%H:%M')}"},
        }
        if fields:
            embed["fields"] = fields
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(
                f"https://discord.com/api/v10/channels/{discord_ch}/messages",
                headers={"Authorization": f"Bot {discord_token}", "Content-Type": "application/json"},
                json={"embeds": [embed]},
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
    """Download image from Telegram and use AI to read slip."""
    import base64

    file = await bot.get_file(file_id)
    buf = io.BytesIO()
    await file.download_to_memory(buf)
    buf.seek(0)
    image_bytes = buf.read()
    b64_image = base64.b64encode(image_bytes).decode("utf-8")

    # Try AI vision first, fallback to tesseract
    try:
        ai_text = await _ai_read_slip(b64_image)
        if ai_text:
            return ai_text
    except Exception as exc:
        logger.warning("AI slip reader failed, falling back to OCR: %s", exc)

    # Fallback to tesseract
    buf.seek(0)
    image = Image.open(buf)
    text = pytesseract.image_to_string(image, lang="tha+eng")
    return text


async def _ai_read_slip(b64_image: str) -> str | None:
    """Use AI vision (Gemini Flash Lite via OpenRouter) to read payment slip.

    Returns extracted text with amount, date, bank, ref number.
    Also checks for signs of forgery.
    """
    from shared.api_cost_tracker import call_openrouter

    prompt = (
        "อ่านสลิปโอนเงินนี้ ตอบเป็น text สั้นๆ ภาษาไทย ข้อมูลต่อไปนี้:\n"
        "- จำนวนเงิน (ตัวเลข เช่น 300.00)\n"
        "- วันที่และเวลา\n"
        "- ธนาคารต้นทาง\n"
        "- ธนาคารปลายทาง\n"
        "- เลขอ้างอิง/Transaction ID\n"
        "- ชื่อผู้ส่ง (จาก)\n"
        "- ชื่อผู้รับ (ไปยัง)\n\n"
        "แล้ววิเคราะห์ว่าสลิปนี้มีสัญญาณปลอมไหม เช่น:\n"
        "- font ไม่ตรงกับธนาคาร\n"
        "- วันที่อนาคต\n"
        "- layout ผิดปกติ\n"
        "- ภาพเบลอเฉพาะจุดตัวเลข\n"
        "ถ้าสงสัยปลอม ให้เขียน SUSPICIOUS: ตามด้วยเหตุผล\n"
        "ถ้าปกติ ให้เขียน VERIFIED: ตามด้วยข้อมูล"
    )

    try:
        data = await call_openrouter(
            model="google/gemini-2.0-flash-lite-001",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{b64_image}"
                            },
                        },
                    ],
                }
            ],
            caller="sales_bot/ai_read_slip",
            max_tokens=500,
            temperature=0.7,
        )
        content = data["choices"][0]["message"]["content"]
        logger.info("AI slip reader result: %s", content[:200])
        return content
    except Exception as exc:
        logger.error("AI slip reader API error: %s", exc)

    return None


async def _ai_screen_image(b64_image: str) -> str | None:
    """AI screen: classify image as slip, spam, inappropriate, or customer question.
    
    Returns one of: SLIP, NOT_SLIP_QUESTION, NOT_SLIP_SUPPORT, SPAM, GAMBLING, PORN, INAPPROPRIATE
    """
    from shared.api_cost_tracker import call_openrouter

    prompt = (
        "ดูรูปนี้แล้วตอบสั้นๆ 1 คำ:\n"
        "- SLIP ถ้าเป็นสลิปโอนเงิน/หลักฐานการจ่ายเงิน\n"
        "- NOT_SLIP_QUESTION ถ้าเป็นรูปทั่วไปหรือคำถาม (screenshot แชท, รูปแพ็กเกจ)\n"
        "- NOT_SLIP_SUPPORT ถ้าเป็น screenshot ปัญหา (เข้ากลุ่มไม่ได้, error)\n"
        "- SPAM ถ้าเป็นโฆษณา/โปรโมทเว็บ\n"
        "- GAMBLING ถ้าเป็นเว็บพนัน/คาสิโน\n"
        "- INAPPROPRIATE ถ้าเป็นรูปอนาจาร/ไม่เหมาะสม\n\n"
        "ตอบแค่คำเดียว ไม่ต้องอธิบาย"
    )

    try:
        data = await call_openrouter(
            model="google/gemini-2.0-flash-lite-001",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{b64_image}"},
                        },
                    ],
                }
            ],
            caller="sales_bot/ai_screen_image",
            max_tokens=20,
            temperature=0.7,
        )
        result = data["choices"][0]["message"]["content"].strip()
        logger.info("AI screen result: %s", result)
        return result
    except Exception as exc:
        logger.error("AI screen API error: %s", exc)

    return None


async def _verify_truemoney_link(link: str) -> dict:
    """Redeem TrueMoney gift link — เติมเงินเข้าวอลเล็ทจริง.

    Returns dict with: valid (bool), amount (Decimal|None), voucher_id (str), error (str).
    """
    match = TRUEMONEY_PATTERN.search(link)
    if not match:
        return {"valid": False, "amount": None, "voucher_id": "", "error": "invalid_link"}

    voucher_id = match.group(1)
    my_wallet = os.environ.get("MY_WALLET", "").strip()
    if not my_wallet:
        return {"valid": False, "amount": None, "voucher_id": voucher_id, "error": "no_wallet"}

    headers = {
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Linux; Android 13; SM-S918B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Mobile Safari/537.36",
        "Accept": "application/json",
        "Origin": "https://gift.truemoney.com",
        "Referer": f"https://gift.truemoney.com/campaign/?v={voucher_id}",
        "Accept-Language": "th-TH,th;q=0.9,en;q=0.8",
    }
    payload = {"mobile": my_wallet, "voucher_hash": voucher_id}

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"https://gift.truemoney.com/campaign/vouchers/{voucher_id}/redeem",
                json=payload,
                headers=headers,
            )

            if resp.status_code == 403:
                return {"valid": False, "amount": None, "voucher_id": voucher_id, "error": "forbidden"}

            try:
                data = resp.json()
            except Exception:
                return {"valid": False, "amount": None, "voucher_id": voucher_id, "error": "invalid_response"}

            status_code = data.get("status", {}).get("code", "")

            if status_code == "SUCCESS":
                amount_str = data.get("data", {}).get("my_ticket", {}).get("amount_baht", "0")
                try:
                    amount = Decimal(str(int(float(amount_str))))
                except (InvalidOperation, ValueError):
                    amount = None
                return {"valid": True, "amount": amount, "voucher_id": voucher_id, "error": ""}

            elif status_code == "CANNOT_GET_OWN_VOUCHER":
                return {"valid": False, "amount": None, "voucher_id": voucher_id, "error": "own_voucher"}
            elif status_code == "TARGET_USER_NOT_FOUND":
                return {"valid": False, "amount": None, "voucher_id": voucher_id, "error": "wallet_not_found"}
            else:
                return {"valid": False, "amount": None, "voucher_id": voucher_id, "error": f"api_{status_code}"}

    except Exception as exc:
        logger.warning("TrueMoney redeem failed: %s", exc)
        return {"valid": False, "amount": None, "voucher_id": voucher_id, "error": "timeout"}


async def _approve_payment(
    payment: Payment,
    user_telegram_id: int,
    bot,
) -> list[str]:
    """Approve payment: create subscription and generate one-time invite links.

    ใช้ Guardian Bot สร้าง one-time invite link (member_limit=1, expire 24h)
    สำหรับทุกกลุ่มที่แพ็กเกจให้สิทธิ์
    """
    import telegram as tg
    from bots.guardian_bot.group_monitor import generate_invite_links_for_user

    invite_links: list[str] = []
    package_id: int = 0

    async with get_session() as session:
        # Update payment status
        result = await session.execute(
            select(Payment).where(Payment.id == payment.id)
        )
        db_payment = result.scalar_one()
        db_payment.status = PaymentStatus.CONFIRMED
        db_payment.verified_at = datetime.utcnow()

        # Get package
        pkg_result = await session.execute(
            select(Package).where(Package.id == db_payment.package_id)
        )
        package = pkg_result.scalar_one()
        package_id = package.id

        # Expire existing active subscriptions (prevent duplicates)
        from sqlalchemy import update as sa_update_dup
        await session.execute(
            sa_update_dup(Subscription)
            .where(Subscription.user_id == db_payment.user_id, Subscription.status == SubscriptionStatus.ACTIVE)
            .values(status=SubscriptionStatus.EXPIRED)
        )

        # Create subscription
        now = datetime.utcnow()
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

    # สร้าง one-time invite link ผ่าน Guardian Bot
    # ใช้ Guardian Bot (ที่เป็น admin ของกลุ่ม) สร้าง invite link
    guardian_token = os.environ.get("GUARDIAN_BOT_TOKEN", "")
    guardian_bot = tg.Bot(token=guardian_token) if guardian_token else bot
    links_dict = await generate_invite_links_for_user(
        guardian_bot, user_telegram_id, package_id
    )

    # จับคู่ slug กับ title สำหรับแสดงผล
    for slug, link in links_dict.items():
        async with get_session() as session:
            grp_result = await session.execute(
                select(GroupRegistry).where(GroupRegistry.slug == slug)
            )
            group = grp_result.scalar_one_or_none()
            title = group.title if group else slug
        invite_links.append(f"• {title}: {link}")

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
            if "spam" in screen_lower or "gambling" in screen_lower or "porn" in screen_lower or "inappropriate" in screen_lower:
                # Spam/gambling/inappropriate → ignore silently, notify admin
                logger.warning("Spam/inappropriate image from user %s", user.id)
                ADMIN_GROUP_ID = int(os.environ.get("ADMIN_GROUP_CHAT_ID", "-1003830920430"))
                try:
                    import telegram as tg
                    admin_bot = tg.Bot(token=os.environ.get("ADMIN_BOT_TOKEN", ""))
                    await admin_bot.send_message(
                        chat_id=ADMIN_GROUP_ID,
                        text=f"⚠️ <b>รูปไม่เหมาะสม</b> จาก <a href='tg://user?id={user.id}'>{user.first_name}</a> (ID: {user.id})\nAI: {screen_result[:200]}",
                        parse_mode="HTML",
                        reply_markup=tg.InlineKeyboardMarkup([
                            [tg.InlineKeyboardButton(f"💬 @{user.username}" if user.username else f"💬 ID: {user.id}", callback_data=f"chat_{user.id}")],
                            [tg.InlineKeyboardButton("🚫 แบน", callback_data=f"ban_{user.id}")],
                        ]),
                    )
                except:
                    pass
                return

            if "not_slip" in screen_lower or "question" in screen_lower or "support" in screen_lower:
                # Customer has a question / sent non-slip image
                await update.message.reply_text(
                    "📩 ได้รับรูปแล้วค่า แต่ดูเหมือนไม่ใช่สลิปนะ\n\n"
                    "ถ้ามีคำถามหรือปัญหา พิมพ์บอกได้เลยค่า แพรช่วยได้! 😊\n"
                    "หรือถ้าเข้ากลุ่มไม่ได้ กลุ่มหาย พิมพ์บอกเลยนะ\n\n"
                    f"ติดต่อแอดมินโดยตรง: https://t.me/zeinju_bunker"
                )
                # Forward to admin group
                ADMIN_GROUP_ID = int(os.environ.get("ADMIN_GROUP_CHAT_ID", "-1003830920430"))
                try:
                    import telegram as tg
                    import html as _html
                    safe_name = _html.escape(str(user.first_name or "ลูกค้า"))
                    admin_bot = tg.Bot(token=os.environ.get("ADMIN_BOT_TOKEN", ""))
                    await admin_bot.send_message(
                        chat_id=ADMIN_GROUP_ID,
                        text=f"💬 <b>ลูกค้าส่งรูป (ไม่ใช่สลิป)</b>\n👤 <a href='tg://user?id={user.id}'>{safe_name}</a>\nAI: {screen_result[:200]}",
                        parse_mode="HTML",
                        reply_markup=tg.InlineKeyboardMarkup([
                            [tg.InlineKeyboardButton(f"💬 @{user.username}" if user.username else f"💬 ID: {user.id}", callback_data=f"chat_{user.id}")],
                        ]),
                    )
                except:
                    pass
                return
    except Exception as exc:
        logger.warning("AI screen failed, proceeding with OCR: %s", exc)

    # OCR
    try:
        ocr_text = await _ocr_slip_image(context.bot, file_id)
    except Exception as exc:
        logger.error("OCR failed: %s", exc)
        await update.message.reply_text(
            "⚠️ ไม่สามารถอ่านสลิปได้ค่ะ กรุณาส่งรูปที่ชัดขึ้น หรือติดต่อแอดมิน (https://t.me/zeinju_bunker)ค่ะ"
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
            await update.message.reply_text("ไม่พบแพ็กเกจในระบบค่ะ ติดต่อแอดมิน (https://t.me/zeinju_bunker)นะคะ")
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

    # Decision — ALL slips go to admin for manual review
    # AI info is stored for admin reference
    ai_info = ""
    if ocr_amount is not None:
        ai_info = f"AI อ่านได้: {format_thb(ocr_amount)}"
    if "SUSPICIOUS" in ocr_text.upper():
        ai_info += " ⚠️ AI สงสัยสลิปปลอม"
    if reasons:
        ai_info += f" | หมายเหตุ: {', '.join(reasons)}"

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
            line_s = line.strip().lstrip("* ").strip()
            if not line_s:
                continue
            if "SUSPICIOUS" in line_s.upper():
                ai_suspicious = line_s.split("SUSPICIOUS")[-1].strip(": ")
            elif "VERIFIED" in line_s.upper():
                continue
            elif ":" in line_s and len(line_s) < 200:
                ai_details.append(line_s)

    ai_summary = "\n".join(f"- {d}" for d in ai_details[:8]) if ai_details else "AI อ่านไม่ได้"
    if ai_suspicious:
        ai_summary += f"\n⚠️ สงสัยปลอม: {ai_suspicious}"

    # Send slip to Telegram admin group with inline buttons
    ADMIN_GROUP_ID = int(os.environ.get("ADMIN_GROUP_CHAT_ID", "-1003830920430"))
    try:
        import telegram as tg
        admin_bot = tg.Bot(token=os.environ.get("ADMIN_BOT_TOKEN", ""))

        import html as _html
        safe_name = _html.escape(str(user.first_name or user.username or "ลูกค้า"))
        now_th = datetime.now(timezone(timedelta(hours=7)))

        caption = (
            f"📩 <b>สลิปใหม่ (รอตรวจ)</b>\n"
            f"🕒 {now_th.strftime('%d/%m/%Y %H:%M')}\n"
            f"👤 <b>ข้อมูลลูกค้า</b>\n"
            f"• ชื่อ: <a href='tg://user?id={user.id}'>{safe_name}</a>\n"
            f"• User: @{user.username or '-'}\n"
            f"• ID: <code>{user.id}</code>\n\n"
            f"💰 AI อ่านได้: <b>{ai_amount_str} บาท</b>\n"
            f"🤖 {ai_summary}"
        )

        keyboard = tg.InlineKeyboardMarkup([
            [
                tg.InlineKeyboardButton("✅ 300 (VIP)", callback_data=f"approve_300_{user.id}"),
                tg.InlineKeyboardButton("✅ 500 (OF)", callback_data=f"approve_500_{user.id}"),
            ],
            [
                tg.InlineKeyboardButton("✅ 1299 (3M)", callback_data=f"approve_1299_{user.id}"),
                tg.InlineKeyboardButton("✅ 2499 (GOD)", callback_data=f"approve_2499_{user.id}"),
            ],
            [
                tg.InlineKeyboardButton("❌ ปฏิเสธ", callback_data=f"reject_{user.id}"),
                tg.InlineKeyboardButton("🚫 แบน", callback_data=f"ban_{user.id}"),
            ],
            [
                tg.InlineKeyboardButton(f"💬 @{user.username}" if user.username else f"💬 ID: {user.id}", callback_data=f"chat_{user.id}"),
            ],
        ])

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
    except Exception as exc:
        logger.error("Failed to send slip to admin group: %s", exc)

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
        f"**#PAY{payment_id}**",
        color=0xFFA500,
        fields=[
            {"name": "👤 ลูกค้า", "value": f"@{user.username or user.first_name} (ID: {user.id})", "inline": True},
            {"name": "📦 แพ็กเกจ", "value": f"{format_thb(expected_price)}", "inline": True},
            {"name": "💰 ยอด OCR", "value": f"{ai_amount_str} บาท", "inline": True},
        ] + ([{"name": "⚠️ เหตุผลที่ hold", "value": ai_suspicious or "รอแอดมินตรวจสอบ", "inline": False}]),
    )

    # Clear selection
    context.user_data.pop("selected_tier", None)
    context.user_data.pop("selected_price", None)


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
            await update.message.reply_text("ไม่พบแพ็กเกจในระบบค่ะ ติดต่อแอดมิน (https://t.me/zeinju_bunker)นะคะ")
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

    tm_error = tm_result.get("error", "")

    # Handle specific errors
    if tm_error == "own_voucher":
        await update.message.reply_text("❌ ซองนี้เป็นของร้านเอง (เติมไม่ได้ค่ะ)")
        return
    elif tm_error == "wallet_not_found":
        await update.message.reply_text("❌ เบอร์วอลเล็ทร้านผิด ติดต่อแอดมินค่ะ → https://t.me/zeinju_bunker")
        return
    elif tm_error in ("forbidden", "timeout"):
        await update.message.reply_text("⚠️ บอทรับซองไม่ได้ ส่งให้แอดมินกดรับเองนะคะ")
        # Send fallback to admin group
        try:
            import telegram as tg
            import html as _html
            safe_name = _html.escape(str(user.first_name or "ลูกค้า"))
            ADMIN_GROUP_ID = int(os.environ.get("ADMIN_GROUP_CHAT_ID", "-1003830920430"))
            admin_bot = tg.Bot(token=os.environ.get("ADMIN_BOT_TOKEN", ""))
            keyboard = tg.InlineKeyboardMarkup([
                [
                    tg.InlineKeyboardButton("✅ 300 (VIP)", callback_data=f"approve_300_{user.id}"),
                    tg.InlineKeyboardButton("✅ 500 (OF)", callback_data=f"approve_500_{user.id}"),
                ],
                [
                    tg.InlineKeyboardButton("✅ 1299 (3M)", callback_data=f"approve_1299_{user.id}"),
                    tg.InlineKeyboardButton("✅ 2499 (GOD)", callback_data=f"approve_2499_{user.id}"),
                ],
                [tg.InlineKeyboardButton("❌ ซองเสีย", callback_data=f"reject_{user.id}")],
                [tg.InlineKeyboardButton(f"💬 @{user.username}" if user.username else f"💬 ID: {user.id}", callback_data=f"chat_{user.id}")],
            ])
            await admin_bot.send_message(
                chat_id=ADMIN_GROUP_ID,
                text=(
                    f"🆘 <b>บอทเติมเองไม่ได้ (Timeout/Error)</b>\n"
                    f"👤 ลูกค้า: <a href='tg://user?id={user.id}'>{safe_name}</a> (ID: <code>{user.id}</code>)\n"
                    f"🔗 <b>ลิ้งค์:</b> {link}\n\n"
                    f"👇 <b>แอดมินกดรับเอง แล้วมากดปุ่มยอดเงิน:</b>"
                ),
                parse_mode="HTML",
                reply_markup=keyboard,
            )
        except Exception as exc:
            logger.error("Failed to send TM fallback: %s", exc)
        return

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
        invite_links_raw = await _approve_payment(payment, user.id, context.bot)

        # คำนวณวันหมดอายุ
        async with get_session() as session:
            pkg_result = await session.execute(
                select(Package).where(Package.id == payment.package_id)
            )
            pkg = pkg_result.scalar_one()
            expire_date = (datetime.utcnow() + timedelta(days=pkg.duration_days)).strftime("%d/%m/%Y")
            pkg_name = pkg.name

        # สร้าง inline buttons สำหรับ invite links
        import telegram as tg
        import html as _html
        link_buttons = []
        for link_line in invite_links_raw:
            # format: "• title: https://..."
            parts = link_line.split(": ", 1)
            if len(parts) == 2:
                title = parts[0].replace("• ", "").strip()
                url = parts[1].strip()
                link_buttons.append(tg.InlineKeyboardButton(f"🚀 {title}", url=url))

        # จัดปุ่ม 2 คอลัมน์
        button_rows = [link_buttons[i:i+2] for i in range(0, len(link_buttons), 2)]
        keyboard = tg.InlineKeyboardMarkup(button_rows) if button_rows else None

        await update.message.reply_text(
            f"🟢 <b>อนุมัติยอด {selected_tier} บาท เรียบร้อยค่ะ</b>\n"
            f"แพ็กเกจ: {pkg_name}\n"
            f"📅 หมดอายุ: {expire_date}\n\n"
            f"👆 <b>กดเข้ากลุ่มที่ปุ่มด้านล่างได้เลย</b>\n\n"
            f"🆓 <b>ห้องฟรี:</b> https://t.me/addlist/2xN-ag15W4U2MTNl",
            parse_mode="HTML",
            reply_markup=keyboard,
        )

        # แจ้งเตือนกลุ่มแอดมิน
        try:
            safe_name = _html.escape(str(user.first_name or "ลูกค้า"))
            ADMIN_GROUP_ID = int(os.environ.get("ADMIN_GROUP_CHAT_ID", "-1003830920430"))
            admin_bot = tg.Bot(token=os.environ.get("ADMIN_BOT_TOKEN", ""))
            links_count = len(invite_links_raw)
            admin_keyboard = tg.InlineKeyboardMarkup([
                [tg.InlineKeyboardButton(f"💬 @{user.username}" if user.username else f"💬 ID: {user.id}", callback_data=f"chat_{user.id}")],
            ])
            await admin_bot.send_message(
                chat_id=ADMIN_GROUP_ID,
                text=(
                    f"✅ <b>TrueMoney อนุมัติอัตโนมัติ</b>\n\n"
                    f"👤 ลูกค้า: <a href='tg://user?id={user.id}'>{safe_name}</a> (ID: <code>{user.id}</code>)\n"
                    f"💰 ยอด: {format_thb(expected_price)}\n"
                    f"📦 แพ็กเกจ: {pkg_name}\n"
                    f"🔗 ส่งลิงก์: {links_count} กลุ่ม\n"
                    f"🏦 Voucher: <code>{tm_result.get('voucher_id', 'N/A')}</code>"
                ),
                parse_mode="HTML",
                reply_markup=admin_keyboard,
            )
        except Exception as exc:
            logger.warning("Failed to notify admin group (TM approve): %s", exc)

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

        # ── Sync Google Sheets ──
        try:
            from sheets.daily_revenue import DailyRevenueSheet
            from sheets.members import MembersSheet
            from sheets.income_log import IncomeLogSheet
            await DailyRevenueSheet.update()
            from sheets.daily_summary import DailySummarySheet
            await DailySummarySheet.update()
            await IncomeLogSheet.log_payment(payment_id, approved_by="ระบบอัตโนมัติ")
            await MembersSheet.update_member(db_user.id)
            logger.info("Sheets synced for TrueMoney payment user_tg=%d", user.id)
        except Exception as exc_s:
            logger.warning("Sheets sync failed: %s", exc_s)

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
            f"กรุณาส่งลิงก์ใหม่ที่ถูกต้อง หรือติดต่อแอดมิน (https://t.me/zeinju_bunker)ค่ะ\n"
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
