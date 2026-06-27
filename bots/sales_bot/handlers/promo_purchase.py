"""DAY 0 (2026-06-28): promo_buy callback handler.

When customer clicks a "Buy" button in the promo deep-link landing page
(callback_data: promo_buy:<promo_id>:<pkg_id>):

1. Record a promotion_click in DB (valid for promo.valid_hours)
2. Stash promo context in user_data so slip handler can match
3. Pick a receiver from the pool
4. Show payment instructions + QR with the DISCOUNTED amount

The slip handler (separately wired) reads pending promotion_click for the
user, applies the right tier + amount on approval, then consumes the click.
"""
from __future__ import annotations
import logging
from typing import Optional

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CallbackQueryHandler

from shared.promotion_service import get_promotion, calculate_price, record_click
from shared.receiver_pool import pick_random
from shared.contact_admin import contact_admin_kb

logger = logging.getLogger(__name__)


async def _lookup_package(pkg_id: int) -> Optional[dict]:
    """Get package details from DB."""
    import os, asyncpg
    url = os.environ.get("DATABASE_URL", "").replace("postgresql+asyncpg://", "postgresql://")
    if not url:
        return None
    try:
        conn = await asyncpg.connect(url)
        try:
            row = await conn.fetchrow(
                "SELECT id, name, tier::text AS tier_str, price, duration_days "
                "FROM packages WHERE id = $1 AND is_active = TRUE",
                pkg_id,
            )
        finally:
            await conn.close()
        return dict(row) if row else None
    except Exception as exc:
        logger.warning("package lookup failed: %s", exc)
        return None


async def promo_buy_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle promo_buy:<promo_id>:<pkg_id> callback.

    Records click + shows payment instructions with discounted amount.
    """
    query = update.callback_query
    if not query or not query.data:
        return
    try:
        await query.answer()
    except Exception:
        pass

    # Parse callback_data
    parts = query.data.split(":")
    if len(parts) != 3:
        logger.warning("promo_buy bad cb_data: %s", query.data)
        return
    _, promo_id_str, pkg_id_str = parts
    try:
        promo_id = int(promo_id_str)
        pkg_id = int(pkg_id_str)
    except ValueError:
        logger.warning("promo_buy non-int ids: %s", query.data)
        return

    # Load promo + package
    promo = None
    try:
        # get_promotion by code — but we have id; let's reload via direct sql
        import os, asyncpg
        url = os.environ.get("DATABASE_URL", "").replace("postgresql+asyncpg://", "postgresql://")
        conn = await asyncpg.connect(url)
        try:
            r = await conn.fetchrow(
                "SELECT id, code, name, package_codes, discount_type, discount_value, "
                "valid_hours "
                "FROM promotions WHERE id = $1 AND is_active = TRUE",
                promo_id,
            )
            promo = dict(r) if r else None
        finally:
            await conn.close()
    except Exception as exc:
        logger.warning("promo_buy lookup failed: %s", exc)

    if not promo:
        await query.message.reply_text(
            "⚠️ โปรนี้ไม่พร้อมใช้งานแล้ว",
            reply_markup=contact_admin_kb(),
        )
        return

    pkg = await _lookup_package(pkg_id)
    if not pkg:
        await query.message.reply_text(
            "⚠️ แพ็คเกจไม่พร้อมใช้งาน",
            reply_markup=contact_admin_kb(),
        )
        return

    # Calculate discounted price
    price_calc = calculate_price(promo, pkg["tier_str"], float(pkg["price"]))
    if not price_calc["applied"]:
        # Package not eligible for this promo (shouldn't happen if button was rendered)
        logger.warning("promo_buy package not eligible: promo=%s pkg=%s", promo_id, pkg_id)
        await query.message.reply_text(
            "⚠️ แพ็คเกจไม่ตรงกับโปร",
            reply_markup=contact_admin_kb(),
        )
        return

    final_price = int(price_calc["discounted"])

    # Record click
    user_id = query.from_user.id
    click = await record_click(promo_id, user_id, pkg["tier_str"])
    if "error" in click:
        logger.warning("promo_buy record_click failed: %s", click["error"])
        await query.message.reply_text(
            "⚠️ ระบบสั่งซื้อมีปัญหา กรุณาลองอีกครั้ง",
            reply_markup=contact_admin_kb(),
        )
        return

    # Stash context (helps slip handler match without DB lookup)
    context.user_data["selected_tier"] = pkg["tier_str"]
    context.user_data["promo_id"] = promo_id
    context.user_data["promo_code"] = promo["code"]
    context.user_data["promo_click_id"] = click["click_id"]
    context.user_data["expected_amount"] = final_price
    context.user_data["package_id"] = pkg_id

    # Pick receiver
    acct = await pick_random()
    if not acct:
        logger.warning("promo_buy no receiver available")
        await query.message.reply_text(
            "⚠️ ระบบรับเงินไม่พร้อม กรุณาทักแอดมิน",
            reply_markup=contact_admin_kb(),
        )
        return

    # Compose message
    promo_name = promo.get("name") or "โปรพิเศษ"
    pkg_name = pkg.get("name") or pkg["tier_str"]
    expires_hours = int(promo.get("valid_hours") or 48)

    body = (
        f"💳 <b>คำสั่งซื้อ: {pkg_name}</b>\n"
        f"🏷 โปร: <b>{promo_name}</b>\n"
        "━━━━━━━━━━━━━━━\n\n"
        f"💰 ยอดที่ต้องโอน: <b>฿{final_price:,}</b>\n\n"
        f"🏦 ธนาคาร: <b>{acct.get('bank_name_th', '')}</b>\n"
        f"👤 ชื่อบัญชี: <code>{acct.get('owner_name', '')}</code>\n"
        f"🔢 เลขบัญชี: <code>{acct.get('account_no', '')}</code>\n"
    )
    if acct.get("promptpay_number"):
        body += f"📱 PromptPay: <code>{acct['promptpay_number']}</code>\n"
    body += (
        "\n━━━━━━━━━━━━━━━\n"
        f"📸 ส่ง <b>สลิปการโอน</b> ในแชทนี้\n"
        f"⏰ ใช้ได้ภายใน <b>{expires_hours} ชั่วโมง</b>\n"
        "⚡ ระบบจะอัปเกรดอัตโนมัติทันที"
    )

    await query.message.reply_text(body, parse_mode="HTML")

    # Send QR
    qr_url = acct.get("qr_url") or ""
    if qr_url:
        try:
            await context.bot.send_photo(
                chat_id=query.message.chat_id,
                photo=qr_url,
                caption=f"📱 สแกน QR เพื่อโอน <b>฿{final_price:,}</b>\nแล้วส่งสลิปกลับมาในแชทนี้",
                parse_mode="HTML",
            )
        except Exception as exc:
            logger.warning("promo_buy QR send failed: %s", exc)




async def promo_noop_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Silent ack for visual separator buttons."""
    try:
        await update.callback_query.answer()
    except Exception:
        pass


def get_promo_purchase_handlers():
    """Return CallbackQueryHandler list for promo_buy: pattern."""
    return [
        CallbackQueryHandler(promo_buy_callback, pattern=r"^promo_buy:"),
        CallbackQueryHandler(promo_noop_callback, pattern=r"^promo_noop$"),
    ]
