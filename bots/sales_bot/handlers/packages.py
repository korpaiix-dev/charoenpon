"""Package display handler - Sales Bot แพร.

แสดง 4 แพ็กเกจ: 300 / 500 / 1299 / 2499 พร้อมรายละเอียดกลุ่ม.
"""

from __future__ import annotations

import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes

logger = logging.getLogger(__name__)

# ---- Package definitions ----

PACKAGES = [
    {
        "tier": "2499",
        "name": "💎 2,499.- | GOD MODE (ถาวร)",
        "price": "2,499",
        "duration": "ถาวร",
        "groups": ["VIP", "SSS", "OnlyFans", "นานาชาติ", "V GOD", "หนังซีรีส์", "สายสุ่ม"],
        "details": (
            "💎 <b>2,499.- | GOD MODE (ถาวร)</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "ตัวจบของจริง จ่ายครั้งเดียว ดูได้ตลอดชีพ!\n\n"
            "✅ เข้าครบทุกกลุ่ม (6 ห้อง + หนัง):\n"
            "• VIP (งานทางบ้าน/แอบถ่าย/นักเรียน)\n"
            "• SSS (งานแรร์กว่า หายากกว่า VIP ทีเด็ด)\n"
            "• OnlyFans (รวมงานแรร์ 50 คน++)\n"
            "• นานาชาติ VIP (คลิปต่างชาติ ยุโรป เอเชีย)\n"
            "• V GOD (งานหลุดทางบ้าน เซฟได้) ✨\n"
            "• สายสุ่ม (llอU ถ่าe) 🎲\n"
            "• หนังซีรีส์ ไทย ฝรั่ง จีน เกาหลี\n\n"
            "✅ สถานะ Lifetime ไม่ต้องต่ออายุ\n"
            "✅ คุ้มที่สุดในระยะยาว"
        ),
    },
    {
        "tier": "1299",
        "name": "🥈 1,299.- | GOD MODE (3 เดือน)",
        "price": "1,299",
        "duration": "90 วัน",
        "groups": ["VIP", "SSS", "OnlyFans", "นานาชาติ", "V GOD", "หนังซีรีส์", "สายสุ่ม"],
        "details": (
            "🥈 <b>1,299.- | GOD MODE (3 เดือน)</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "สายเหมา งบประหยัด จัดเต็มครบทุกห้อง!\n\n"
            "✅ เข้าครบทุกกลุ่ม (6 ห้อง + หนัง):\n"
            "• VIP (งานทางบ้าน/แอบถ่าย/นักเรียน)\n"
            "• SSS (งานแรร์กว่า หายากกว่า VIP ทีเด็ด)\n"
            "• V GOD (งานหลุดทางบ้าน เซฟได้) ✨\n"
            "• OnlyFans (รวมงานแรร์ 50 คน++)\n"
            "• นานาชาติ VIP (คลิปต่างชาติ ยุโรป เอเชีย)\n"
            "• สายสุ่ม (llอU ถ่าe) 🎲\n"
            "• หนังซีรีส์ ไทย ฝรั่ง จีน เกาหลี\n\n"
            "✅ 90 วัน (เฉลี่ยวันละ 14 บาท)"
        ),
    },
    {
        "tier": "500",
        "name": "👙 500.- | OnlyFans + VIP (30 วัน)",
        "price": "500",
        "duration": "30 วัน",
        "groups": ["VIP", "OnlyFans"],
        "details": (
            "👙 <b>500.- | OnlyFans + VIP (30 วัน)</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "คอมโบยอดฮิต! ได้ทั้งงานแรร์และงานทางบ้าน\n\n"
            "✅ เข้าได้ 2 ห้อง:\n"
            "• OnlyFans (รวมงานแรร์ 50 คน++)\n"
            "• VIP (งานทางบ้าน/นักเรียน)\n\n"
            "✅ 30 วัน\n"
            "✅ เพิ่มนิดเดียวจากตัวเริ่มต้น ได้ OF ตัวเด็ดเพิ่ม"
        ),
    },
    {
        "tier": "300",
        "name": "🥉 300.- | VIP (30 วัน)",
        "price": "300",
        "duration": "30 วัน",
        "groups": ["VIP"],
        "details": (
            "🥉 <b>300.- | VIP (30 วัน)</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "แพ็กเกจเริ่มต้น สำหรับสายทดลอง\n\n"
            "✅ เข้าได้ 1 ห้อง:\n"
            "• VIP (งานทางบ้าน/แอบถ่าย/นักเรียน)\n\n"
            "✅ 30 วัน\n"
            "✅ อัปเดตงานใหม่ทุกวัน"
        ),
    },
]


def _build_package_list_text() -> str:
    """Build the text for the package overview."""
    lines = [
        "<b>📦 แพ็กเกจ VIP ทั้งหมด</b>\n",
        "เลือกแพ็กเกจที่สนใจได้เลยค่ะ 👇\n",
    ]
    for pkg in PACKAGES:
        groups_str = ", ".join(pkg["groups"])
        lines.append(
            f"{'─' * 20}\n"
            f"{pkg['name']}\n"
            f"💰 ราคา: <b>{pkg['price']} บาท</b> / {pkg['duration']}\n"
            f"🏠 ห้อง: {groups_str}\n"
        )
    lines.append(
        f"{'─' * 20}\n"
        "กดเลือกแพ็กเกจเพื่อดูรายละเอียดเพิ่มเติมค่ะ"
    )
    return "\n".join(lines)


def _build_package_keyboard() -> InlineKeyboardMarkup:
    """Build inline keyboard for package selection."""
    buttons = [
        [InlineKeyboardButton(f"🥉 300 บาท", callback_data="pkg_300")],
        [InlineKeyboardButton(f"🥈 500 บาท", callback_data="pkg_500")],
        [InlineKeyboardButton(f"🥇 1,299 บาท", callback_data="pkg_1299")],
        [InlineKeyboardButton(f"💎 2,499 บาท", callback_data="pkg_2499")],
        [InlineKeyboardButton("🔙 กลับเมนูหลัก", callback_data="back_main")],
    ]
    return InlineKeyboardMarkup(buttons)


def _build_package_detail_text(tier: str) -> str | None:
    """Build detail text for a specific package tier."""
    for pkg in PACKAGES:
        if pkg["tier"] == tier:
            groups_str = "\n  ".join(f"• {g}" for g in pkg["groups"])
            return (
                f"{pkg['name']}\n\n"
                f"💰 <b>ราคา: {pkg['price']} บาท / {pkg['duration']}</b>\n\n"
                f"📋 <b>รายละเอียด:</b>\n{pkg['details']}\n\n"
                f"🏠 <b>ห้องที่เข้าได้:</b>\n  {groups_str}\n\n"
                f"สนใจสมัครกดปุ่มด้านล่างได้เลยค่ะ 😊"
            )
    return None


def _build_detail_keyboard(tier: str) -> InlineKeyboardMarkup:
    """Build keyboard for package detail view."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "✅ สมัครแพ็กเกจนี้",
                    callback_data=f"buy_{tier}",
                )
            ],
            [InlineKeyboardButton("📦 ดูแพ็กเกจอื่น", callback_data="view_packages")],
            [InlineKeyboardButton("🔙 กลับเมนูหลัก", callback_data="back_main")],
        ]
    )


async def view_packages_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """/packages command — show all packages."""
    if not update.message:
        return
    await update.message.reply_text(
        _build_package_list_text(),
        parse_mode="HTML",
        reply_markup=_build_package_keyboard(),
    )


async def view_packages_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Callback: show all packages."""
    query = update.callback_query
    if not query:
        return
    await query.answer()
    await query.edit_message_text(
        _build_package_list_text(),
        parse_mode="HTML",
        reply_markup=_build_package_keyboard(),
    )


async def package_detail_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Callback: show detail of a specific package (pkg_300, pkg_500, etc.)."""
    query = update.callback_query
    if not query or not query.data:
        return
    await query.answer()

    tier = query.data.replace("pkg_", "")
    text = _build_package_detail_text(tier)
    if not text:
        await query.edit_message_text("ไม่พบแพ็กเกจที่เลือกค่ะ ลองใหม่นะคะ")
        return

    await query.edit_message_text(
        text,
        parse_mode="HTML",
        reply_markup=_build_detail_keyboard(tier),
    )


async def buy_package_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Callback: user wants to buy a package (buy_300, buy_500, etc.)."""
    query = update.callback_query
    if not query or not query.data:
        return
    await query.answer()

    tier = query.data.replace("buy_", "")
    pkg = next((p for p in PACKAGES if p["tier"] == tier), None)
    if not pkg:
        await query.edit_message_text("ไม่พบแพ็กเกจค่ะ")
        return

    # Store selected package in user context
    context.user_data["selected_tier"] = tier
    context.user_data["selected_price"] = pkg["price"].replace(",", "")

    text = (
        f"✅ <b>ยืนยันสมัคร {pkg['name']}</b>\n\n"
        f"💰 ยอดที่ต้องชำระ: <b>{pkg['price']} บาท</b>\n\n"
        f"📌 <b>วิธีชำระเงิน:</b>\n"
        f"1️⃣ สแกน QR PromptPay ด้านล่าง หรือโอนเงินตามยอด\n"
        f"2️⃣ ส่งสลิปโอนเงิน หรือ ลิงก์ซอง TrueMoney\n"
        f"3️⃣ รอแอดมินตรวจสอบ\n\n"
        f"💳 <b>ช่องทางชำระ:</b>\n"
        f"• PromptPay / โอนธนาคาร → ส่งรูปสลิป\n"
        f"• TrueMoney Wallet → ส่งลิงก์ gift.truemoney.com\n\n"
        f"⚠️ <b>หมายเหตุ:</b> กรุณาโอนตามยอดที่แจ้งเท่านั้นค่ะ"
    )

    keyboard = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📦 เลือกแพ็กเกจอื่น", callback_data="view_packages")],
            [InlineKeyboardButton("🔙 กลับเมนูหลัก", callback_data="back_main")],
        ]
    )

    await query.edit_message_text(text, parse_mode="HTML", reply_markup=keyboard)

    # Send QR code PromptPay
    QR_URL = "https://img2.pic.in.th/-2026-03-15-143743.png"
    try:
        await context.bot.send_photo(
            chat_id=query.message.chat_id,
            photo=QR_URL,
            caption=f"📱 สแกน QR PromptPay เพื่อโอน <b>{pkg['price']} บาท</b>\nแล้วส่งสลิปมาที่แชทนี้เลยค่ะ 🙏",
            parse_mode="HTML",
        )
    except Exception as exc:
        logger.warning("Failed to send QR: %s", exc)


def get_package_handlers() -> list:
    """Return all handlers for the packages module."""
    return [
        CommandHandler("packages", view_packages_command),
        CallbackQueryHandler(view_packages_callback, pattern="^view_packages$"),
        CallbackQueryHandler(package_detail_callback, pattern=r"^pkg_(300|500|1299|2499)$"),
        CallbackQueryHandler(buy_package_callback, pattern=r"^buy_(300|500|1299|2499)$"),
    ]
