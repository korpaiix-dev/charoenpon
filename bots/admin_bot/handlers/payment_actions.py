"""payment_actions.py — extracted from bots/admin_bot/handlers/approval.py.

Holds the slip-approve-by-price flow:
- _verify_approve_amount — pre-flight guard against amount mismatch
- approve_by_price_callback — admin approves slip by choosing price button

Strangler Fig Round 6 extraction. Logic UNCHANGED — only moved.
Helpers used by these functions (_is_admin) are imported from approval.py.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, func
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from shared.database import get_session
from shared.models import (
    Package,
    Payment,
    PaymentStatus,
    Subscription,
    SubscriptionStatus,
    User,
)
from shared.endmonth_vip_promo import (
    PROMO_2499_PRICE, PROMO_PRICE, is_endmonth_vip_promo_active,
    PROMO_500_PRICE, PROMO_1299_PRICE, is_may_combo_promo_active,
)
from shared.songkran_promo import get_group_display_title, is_songkran_bonus_slug
from shared.utils import format_datetime_thai, format_thb, log_admin_action
from shared.admin_alert import _admin_group_id

# Inlined from approval.py to avoid circular import
import os
from shared.admin_perms import is_admin_for_bot
def _admin_ids() -> list[int]:
    raw = os.environ.get("ADMIN_TELEGRAM_IDS", "")
    ids: list[int] = []
    for tok in raw.split(","):
        tok = tok.strip()
        if tok:
            try:
                ids.append(int(tok))
            except ValueError:
                pass
    return ids

def _is_admin(user_id: int) -> bool:
    """Migrated to shared.admin_perms (DB-first with env fallback)."""
    return is_admin_for_bot(user_id, "admin_bot")

def _build_manual_invite_alert_keyboard(user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔄 ส่งใหม่", callback_data=f"sos_resend_{user_id}", api_kwargs={"style": "primary"}),
            InlineKeyboardButton("📋 คัดลอกลิงก์", callback_data=f"copy_invites_{user_id}", api_kwargs={"style": "secondary"}),
        ]
    ])

logger = logging.getLogger(__name__)


async def _verify_approve_amount(payment: "Payment", button_amount) -> tuple[bool, str]:
    """Verify button amount matches payment amount.

    Returns (ok, warning_msg). When ``payment.amount`` is None we cannot verify
    so we treat it as a soft mismatch (ok=False) and let admin confirm.
    """
    from decimal import Decimal
    if payment is None or payment.amount is None:
        return False, (
            f"⚠️ AMOUNT MISMATCH: button={button_amount} payment=<unknown> "
            "(no recorded amount on slip). Please verify slip again."
        )
    try:
        btn = Decimal(str(button_amount))
        pay = Decimal(str(payment.amount))
    except Exception:
        return False, (
            f"⚠️ AMOUNT MISMATCH: button={button_amount} payment={payment.amount} "
            "(parse error). Please verify slip again."
        )
    if pay == btn:
        return True, ""
    # # >>> HOTFIX_PROMO_PAY_MATCH <<<
    # Accept promo-price button against full-price payment record:
    # button 349 vs payment 500 → OK during may combo promo (TIER_500 → 349)
    # button 999 vs payment 1299 → OK during may combo promo (TIER_1299 → 999)
    try:
        from shared.endmonth_vip_promo import is_may_combo_promo_active
        # >>> BUG8_BACKDATE_PROMO <<<
        # Use payment.created_at — promo-active state when slip was submitted,
        # not when admin reviews. Handles late-review after promo ends.
        _at = getattr(payment, "created_at", None)
        if is_may_combo_promo_active(_at):
            if (btn == Decimal("349") and pay == Decimal("500")) or \
               (btn == Decimal("999") and pay == Decimal("1299")):
                return True, ""
        from shared.endmonth_vip_promo import is_endmonth_vip_promo_active
        if is_endmonth_vip_promo_active(_at):
            if (btn == Decimal("200") and pay == Decimal("300")) or \
               (btn == Decimal("2000") and pay == Decimal("2499")):
                return True, ""
    except Exception:
        pass
    diff = abs(pay - btn)
    return False, (
        f"⚠️ AMOUNT MISMATCH: button={btn} payment={pay} "
        f"(diff={diff}). Possible misclick — please verify slip again."
    )


async def approve_by_price_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """อนุมัติสลิปโดยเลือกราคา — approve_300_userid format.

    Supports ``approve_<price>_<user_id>`` and the override form
    ``approve_<price>_<user_id>_force`` (Phase 2f) which bypasses the
    amount-mismatch guard when admin has explicitly confirmed.
    """
    query = update.callback_query
    await query.answer()

    if not query.from_user or not _is_admin(query.from_user.id):
        await query.answer("⛔ คุณไม่มีสิทธิ์", show_alert=True)
        return

    parts = query.data.split("_")  # approve_300_12345  or  approve_300_12345_force
    price = parts[1]
    target_user_id = int(parts[2])
    # FIX 2025-05-21 (Phase 2f): support force override via trailing "_force"
    force_override = (len(parts) >= 4 and parts[3] == "force")

    import os
    import telegram as tg
    from datetime import timedelta
    from decimal import Decimal
    from bots.guardian_bot.group_monitor import generate_invite_links_for_user

    # FIX 2025-05-21 (Phase 2f): pre-flight amount verification.
    # Look up the most-recent PENDING payment for this telegram user and
    # compare against the button amount. If they disagree and admin has NOT
    # passed the ``force`` flag, refuse to proceed and ask for confirmation.
    try:
        # Compute the button's nominal baht value
        if price == "ADD500":
            button_amount = Decimal("500")
        else:
            try:
                button_amount = Decimal(price)
            except Exception:
                button_amount = None

        verify_payment = None
        async with get_session() as _vsess:
            _vu = await _vsess.execute(
                select(User).where(User.telegram_id == target_user_id)
            )
            _vu_row = _vu.scalar_one_or_none()
            if _vu_row is not None:
                _vp = await _vsess.execute(
                    select(Payment)
                    .where(
                        Payment.user_id == _vu_row.id,
                        Payment.status == PaymentStatus.PENDING,
                    )
                    .order_by(Payment.created_at.desc())
                    .limit(1)
                )
                verify_payment = _vp.scalar_one_or_none()

        if button_amount is not None and verify_payment is not None and not force_override:
            ok, warn_msg = await _verify_approve_amount(verify_payment, button_amount)
            if not ok:
                logger.warning(
                    "approve_by_price amount mismatch: admin=%s user_tg=%s "
                    "button=%s payment_id=%s payment_amount=%s",
                    query.from_user.id, target_user_id, button_amount,
                    getattr(verify_payment, "id", None),
                    getattr(verify_payment, "amount", None),
                )
                # log so we have an audit trail of the warning the admin saw
                try:
                    await log_admin_action(
                        admin_id=query.from_user.id,
                        action="approve_amount_mismatch_warned",
                        target_type="user",
                        target_id=target_user_id,
                        details=(
                            f"button={button_amount} "
                            f"payment_id={getattr(verify_payment, 'id', None)} "
                            f"payment_amount={getattr(verify_payment, 'amount', None)}"
                        ),
                    )
                except Exception:
                    pass

                confirm_kb = tg.InlineKeyboardMarkup([
                    [
                        tg.InlineKeyboardButton(
                            f"✅ ยืนยัน {price} (force)",
                            callback_data=f"approve_{price}_{target_user_id}_force",
                            api_kwargs={"style": "danger"},
                        ),
                        tg.InlineKeyboardButton(
                            "❌ ยกเลิก",
                            callback_data=f"reject_{target_user_id}",
                            api_kwargs={"style": "secondary"},
                        ),
                    ]
                ])
                try:
                    await query.message.reply_text(
                        warn_msg + "\n\nกด ✅ ยืนยัน (force) เฉพาะเมื่อสลิปตรงจริง.",
                        reply_markup=confirm_kb,
                    )
                except Exception as _exc_warn:
                    logger.warning("Could not post mismatch warning: %s", _exc_warn)
                return
    except Exception as _exc_verify:
        # NEVER block approval if the verification itself crashes — just log.
        logger.warning("approve amount verify guard failed (non-fatal): %s", _exc_verify)

    try:
        # Find package by price
        async with get_session() as session:
            from shared.models import Package, PackageTier
            from shared.pricing import admin_callback_tier_map
            tier_map = admin_callback_tier_map()
            # >>> BUG2_TIER_MAP_GATE <<<
            # Reject promo-price callbacks if their promo is no longer active.
            # Otherwise admin clicking stale buttons after promo end would assign
            # full-tier access at promo price (revenue leak).
            if price in ("349", "999") and not is_may_combo_promo_active():
                await query.answer("⛔ โปรพ.ค. หมดเขตแล้ว — ใช้ปุ่มราคาเต็ม", show_alert=True)
                return
            if price in ("200", "2000") and not is_endmonth_vip_promo_active():
                await query.answer("⛔ โปรเม.ย. หมดเขตแล้ว — ใช้ปุ่มราคาเต็ม", show_alert=True)
                return
            tier = tier_map.get(price)
            if not tier:
                await query.answer(f"❌ ราคา {price} ไม่ถูกต้อง", show_alert=True)
                return

            # GACHA tiers: handle separately (not a Package)
            if tier.startswith("GACHA_"):
                _spins_map = {"GACHA_1": 1, "GACHA_3": 3, "GACHA_10": 10}
                _spins = _spins_map.get(tier, 0)
                if _spins <= 0:
                    await query.answer(f"❌ Gacha tier {tier} unknown", show_alert=True)
                    return
                from sqlalchemy import text as _gt
                async with get_session() as _gs:
                    _gu = await _gs.execute(select(User).where(User.telegram_id == target_user_id))
                    _gurow = _gu.scalar_one_or_none()
                    if not _gurow:
                        await query.answer("❌ User not found", show_alert=True)
                        return
                    await _gs.execute(_gt(
                        "INSERT INTO gachapon_credits (user_id, telegram_id, credits, total_purchased) "
                        "VALUES (:uid, :tg, :sp, :sp) "
                        "ON CONFLICT (user_id) DO UPDATE SET "
                        "credits = gachapon_credits.credits + :sp, "
                        "total_purchased = gachapon_credits.total_purchased + :sp, "
                        "updated_at = NOW()"
                    ), {"uid": _gurow.id, "tg": target_user_id, "sp": _spins})
                    from shared.pricing import TIER_PRICES as _TP
                    _amt = float(_TP.get(tier, 0))
                    await _gs.execute(_gt(
                        "INSERT INTO payments (user_id, package_id, amount, method, status, auto_approved, verified_at, verified_by, created_at) "
                        "VALUES (:uid, 1, :amt, 'SLIP', 'CONFIRMED', false, NOW(), :admin, NOW())"
                    ), {"uid": _gurow.id, "amt": _amt, "admin": query.from_user.id if query.from_user else None})
                    await _gs.commit()
                try:
                    # FIX 2026-06-29 (P0#4): use sales_bot token (customer's /start'ed bot)
                    # not context.bot (admin_bot) — admin_bot DM → "Chat not found" silent fail
                    from telegram import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
                    _kb = InlineKeyboardMarkup([[InlineKeyboardButton(
                        f"🎰 หมุนเลย! (มี {_spins} สิทธิ์)",
                        web_app=WebAppInfo(url="https://telebord.net/gacha/"))]])
                    _gacha_msg = "🎉 <b>ได้รับสิทธิ์หมุนกาชาปอง " + str(_spins) + " ครั้ง!</b>\n\nกดปุ่มด้านล่างเริ่มหมุนเลยค่ะ 🎁"
                    from shared.customer_dm import send_to_customer
                    await send_to_customer(
                        telegram_id=target_user_id,
                        text=_gacha_msg,
                        parse_mode="HTML",
                        reply_markup=_kb,
                    )
                except Exception as _exc_gacha_dm:
                    import logging as _logging
                    _logging.getLogger(__name__).warning("gacha DM failed: %s", _exc_gacha_dm)
                try:
                    actor = query.from_user.username or query.from_user.first_name or "admin"
                    new_marker = "\n\n✅ <b>GACHA APPROVED</b> (" + str(_spins) + " spins) by @" + str(actor)
                    if query.message and query.message.caption:
                        await query.edit_message_caption(
                            caption=(query.message.caption + new_marker),
                            parse_mode="HTML", reply_markup=None,
                        )
                    elif query.message and query.message.text:
                        await query.edit_message_text(
                            text=(query.message.text + new_marker),
                            parse_mode="HTML", reply_markup=None,
                        )
                except Exception:
                    pass
                return

            # Resolve tier string to enum safely (handles TIER_100 inconsistency)
            from bots.sales_bot.payment_util.utils import _resolve_tier
            _tier_enum = _resolve_tier(tier)
            if not _tier_enum:
                await query.answer(f"❌ tier {tier} ไม่รู้จัก", show_alert=True)
                return
            pkg_result = await session.execute(
                select(Package).where(Package.tier == _tier_enum)
            )
            package = pkg_result.scalar_one_or_none()
            if not package:
                await query.answer("❌ ไม่พบแพ็กเกจ", show_alert=True)
                return

            # # >>> DOUBLE_APPROVE_GUARD_BOT <<<
            # Guard: if user has a CONFIRMED payment in last 15 min already
            # (e.g. just approved via Dashboard), refuse to avoid double-charge.
            from datetime import datetime as _dt, timedelta as _td
            _u_for_check = await session.execute(
                select(User).where(User.telegram_id == target_user_id)
            )
            _u_row_chk = _u_for_check.scalar_one_or_none()
            if _u_row_chk is not None:
                # >>> BUG5_GUARD_PKG_MATCH <<< (UPDATED 2026-06-16)
                # Block any CONFIRMED payment in 15min EXCEPT when buying ADD500 add-on
                # legitimate add-on: lifetime + ADD500 within 5 minutes
                _is_addon_purchase = (tier == "ADD500")
                _guard_query = select(Payment).where(
                    Payment.user_id == _u_row_chk.id,
                    Payment.status == PaymentStatus.CONFIRMED,
                    Payment.created_at >= _dt.utcnow() - _td(minutes=15),
                )
                if _is_addon_purchase:
                    # When admin clicks ADD500: only block if there\u0027s already an ADD500 in 15min
                    _guard_query = _guard_query.where(Payment.package_id == package.id)
                _recent = await session.execute(_guard_query.order_by(Payment.created_at.desc()).limit(1))
                _recent_pay = _recent.scalar_one_or_none()
                if _recent_pay is not None:
                    await query.answer(
                        f"\u26d4 \u0e25\u0e39\u0e01\u0e04\u0e49\u0e32\u0e19\u0e35\u0e49\u0e40\u0e1e\u0e34\u0e48\u0e07\u0e16\u0e39\u0e01\u0e2d\u0e19\u0e38\u0e21\u0e31\u0e15\u0e34\u0e41\u0e25\u0e49\u0e27 (payment #{_recent_pay.id}, \u0e3f{_recent_pay.amount}, pkg={_recent_pay.package_id}). \u0e2d\u0e22\u0e48\u0e32\u0e01\u0e14\u0e0b\u0e49\u0e33",
                        show_alert=True,
                    )
                    return

            # Find or create user
            user_result = await session.execute(
                select(User).where(User.telegram_id == target_user_id)
            )
            db_user = user_result.scalar_one_or_none()
            if not db_user:
                db_user = User(telegram_id=target_user_id, first_name="ลูกค้า")
                session.add(db_user)
                await session.flush()

            # Check for existing active subscription (prevent duplicates)
            # BUT skip lifetime subs when buying add-on packages
            from decimal import Decimal
            is_addon = tier == 'ADD500'
            if is_addon:
                # Add-on: only expire subs for the SAME add-on package
                from sqlalchemy import update as sa_update_sub
                await session.execute(
                    sa_update_sub(Subscription)
                    .where(
                        Subscription.user_id == db_user.id,
                        Subscription.status == SubscriptionStatus.ACTIVE,
                        Subscription.package_id == package.id,
                    )
                    .values(status=SubscriptionStatus.EXPIRED)
                )
            else:
                # >>> BUG7_LIFETIME_GUARD <<<
                # Protect lifetime (TIER_2499) — never expire it when buying
                # add-on or another tier. Same logic as Sales Bot Phase 2b.
                from sqlalchemy import update as sa_update_sub
                from shared.models import Package as _Pkg
                _lifetime_pkgs_subq = select(_Pkg.id).where(_Pkg.tier == PackageTier.TIER_2499)
                await session.execute(
                    sa_update_sub(Subscription)
                    .where(
                        Subscription.user_id == db_user.id,
                        Subscription.status == SubscriptionStatus.ACTIVE,
                        Subscription.package_id.notin_(_lifetime_pkgs_subq),
                    )
                    .values(status=SubscriptionStatus.EXPIRED)
                )

            # ⚠️ WARNING 2026-06-28: This path creates Subscription WITHOUT a Payment row!
            # → Causes ORPHAN subs (revenue missing from reports).
            # → MIGRATE to shared.payment_approval.apply_payment_approval() ASAP.
            # → For now, payment_id will be NULL — backfill via daily watchdog.
            # Create subscription
            now = datetime.utcnow()
            # Trial 24 ชม.: ใช้ hours=24 แทน days=1
            if package.tier == PackageTier.TIER_99:
                end_date = now + timedelta(hours=24)
            else:
                end_date = now + timedelta(days=package.duration_days)
            subscription = Subscription(
                user_id=db_user.id,
                package_id=package.id,
                status=SubscriptionStatus.ACTIVE,
                start_date=now,
                end_date=end_date,
            )
            session.add(subscription)
            # >>> BUG4_AMOUNT_FROM_PAY <<<
            # Source of truth for amount_paid: actual slip amount (verify_payment.amount)
            # if available. Falls back to button-derived promo price.
            # Prevents revenue understatement when customer pays FULL price during promo.
            _slip_amt = getattr(verify_payment, "amount", None) if "verify_payment" in locals() else None
            if price == "200" and tier == "300" and is_endmonth_vip_promo_active():
                amount_paid = _slip_amt if _slip_amt and _slip_amt >= PROMO_PRICE else PROMO_PRICE
            elif price == "2000" and tier == "2499" and is_endmonth_vip_promo_active():
                amount_paid = _slip_amt if _slip_amt and _slip_amt >= PROMO_2499_PRICE else PROMO_2499_PRICE
            elif price == "349" and tier == "500" and is_may_combo_promo_active():
                amount_paid = _slip_amt if _slip_amt and _slip_amt >= PROMO_500_PRICE else PROMO_500_PRICE
            elif price == "999" and tier == "1299" and is_may_combo_promo_active():
                amount_paid = _slip_amt if _slip_amt and _slip_amt >= PROMO_1299_PRICE else PROMO_1299_PRICE
            else:
                amount_paid = Decimal(str(price if price != "ADD500" else "500"))
            # FIX2_TRUST_TRIGGER total_spent maintained by DB trigger

            # Mark teaser clicks as converted for this user
            from sqlalchemy import update as sa_update
            from shared.models import TeaserClick
            await session.execute(
                sa_update(TeaserClick)
                .where(TeaserClick.user_id == target_user_id, TeaserClick.converted == False)
                .values(converted=True)
            )

            await session.flush()
            pkg_name = package.name
            duration = package.duration_days
            pkg_id = package.id

        # Flash Sale: increment sold_slots if active
        try:
            from bots.sales_bot.handlers.flash_sale import increment_sold_slot
            if tier == "300":
                success, sold, total = await increment_sold_slot(pkg_id)
                if success:
                    logger.info("Flash sale slot incremented: %d/%d", sold, total)
        except Exception as exc_fs:
            logger.warning("Flash sale slot increment failed (non-critical): %s", exc_fs)

        # Generate invite links using Guardian Bot (must be admin in all VIP groups)
        guardian_bot = tg.Bot(token=os.environ.get("GUARDIAN_BOT_TOKEN", ""))
        await guardian_bot.initialize()
        sales_bot = tg.Bot(token=os.environ.get("SALES_BOT_TOKEN", ""))
        await sales_bot.initialize()
        invite_links = await generate_invite_links_for_user(guardian_bot, target_user_id, pkg_id)

        links_list = []
        async with get_session() as session:
            from shared.models import GroupRegistry
            for slug, link in invite_links.items():
                if is_songkran_bonus_slug(slug):
                    title = get_group_display_title(slug)
                else:
                    grp_result = await session.execute(
                        select(GroupRegistry).where(GroupRegistry.slug == slug)
                    )
                    group = grp_result.scalar_one_or_none()
                    title = group.title if group else get_group_display_title(slug)
                links_list.append({"text": f"🚀 {title}", "url": link})

        # Send invite links to customer (2 buttons per row)
        link_buttons = [links_list[i:i+2] for i in range(0, len(links_list), 2)]

        from shared.tz import now_th as _now_th_; expire_date = (_now_th_() + timedelta(days=duration)).strftime("%d/%m/%Y")
        msg = (
            f"✅ <b>อนุมัติยอด {price} บาท เรียบร้อยค่ะ</b>\n"
            f"📦 แพ็กเกจ: {pkg_name}\n"
            f"📅 หมดอายุ: {expire_date}\n\n"
            f"👇 <b>กดเข้ากลุ่มที่ปุ่มด้านล่างได้เลย</b>\n\n"
            f"🆓 <b>ห้องฟรี:</b> https://t.me/addlist/w0YSyuHC_aE2ZGVl"
        )
        keyboard = tg.InlineKeyboardMarkup(
            [[tg.InlineKeyboardButton(b["text"], url=b["url"]) for b in row]
             for row in link_buttons]
        )
        try:
            await sales_bot.send_message(chat_id=target_user_id, text=msg, parse_mode="HTML", reply_markup=keyboard)
        except Exception as exc_send:
            logger.error("Failed to send invite links to %s: %s", target_user_id, exc_send)
            admin_group_id = _admin_group_id()
            flat_links = "\n".join([f"• {b['text']}: {b['url']}" for b in links_list]) or "(ไม่มีลิงก์)"
            await context.bot.send_message(
                chat_id=admin_group_id,
                text=(
                    f"🚨 <b>ส่งลิงก์ลูกค้าไม่สำเร็จ</b>\n"
                    f"🆔 TG ID: <code>{target_user_id}</code>\n"
                    f"📦 แพ็กเกจ: {pkg_name}\n"
                    f"💰 ยอด: {price} บาท\n"
                    f"❗ Error: {type(exc_send).__name__}: {exc_send}\n\n"
                    f"🔗 ลิงก์ที่สร้างไว้แล้ว:\n{flat_links}"
                ),
                parse_mode="HTML",
                reply_markup=_build_manual_invite_alert_keyboard(target_user_id),
            )

        # Update admin message — keep chat button only when username is available
        safe_admin = query.from_user.first_name or "Admin"
        old_caption = query.message.caption or ""
        new_caption = f"{old_caption}\n\n✅ <b>สถานะ: อนุมัติ ({price}บ.) โดย {safe_admin}</b>"
        post_keyboard = None
        if db_user and db_user.username:
            post_keyboard = tg.InlineKeyboardMarkup([[
                tg.InlineKeyboardButton(
                    f"💬 @{db_user.username}",
                    url=f"https://t.me/{db_user.username}",
                    api_kwargs={"style": "primary"},
                )
            ]])
        try:
            await query.edit_message_caption(
                caption=new_caption[:1024],
                parse_mode="HTML",
                reply_markup=post_keyboard,
            )
        except Exception as e:
            logger.error("Failed to edit approval caption: %s", e)

        # Notify Discord
        await _notify_discord_alert(
            f"✅ อนุมัติ {price} บาท",
            f"👤 ลูกค้า: TG ID {target_user_id}\n📦 แพ็กเกจ: {pkg_name}\n👮 โดย: {safe_admin}",
            color=0x2ECC71,
        )

        # ── Confirm existing PENDING payment (ไม่สร้างใหม่ — แก้ duplicate bug) ──
        try:
            async with get_session() as session:
                from shared.models import PaymentMethod
                # หา PENDING payment ล่าสุดของ user นี้ (ตัวที่ Sales Bot สร้างตอนรับสลิป)
                pending_result = await session.execute(
                    select(Payment).where(
                        Payment.user_id == db_user.id,
                        Payment.status == PaymentStatus.PENDING,
                    ).order_by(Payment.created_at.desc()).limit(1)
                )
                pending_payment = pending_result.scalar_one_or_none()
                if pending_payment:
                    # Update ตัว PENDING เดิมเป็น CONFIRMED
                    pending_payment.status = PaymentStatus.CONFIRMED
                    pending_payment.verified_by = query.from_user.id
                    pending_payment.verified_at = datetime.utcnow()
                    pending_payment.amount = amount_paid
                    pending_payment.package_id = pkg_id
                    await session.flush()
                    new_payment_id = pending_payment.id
                    logger.info("Existing PENDING payment #%d confirmed for user %d", new_payment_id, target_user_id)
                else:
                    # ไม่มี PENDING (edge case) — เช็ค CONFIRMED ซ้ำก่อนสร้างใหม่
                    dedup_cutoff = datetime.utcnow() - timedelta(minutes=10)
                    dup_check = await session.execute(
                        select(Payment).where(
                            Payment.user_id == db_user.id,
                            Payment.amount == amount_paid,
                            Payment.status == PaymentStatus.CONFIRMED,
                            Payment.created_at >= dedup_cutoff,
                        )
                    )
                    if dup_check.scalar_one_or_none():
                        logger.warning("Duplicate approval payment skipped: user_id=%s", db_user.id)
                        new_payment_id = 0
                    else:
                        new_payment = Payment(
                            user_id=db_user.id,
                            package_id=pkg_id,
                            amount=amount_paid,
                            method=PaymentMethod.SLIP,
                            status=PaymentStatus.CONFIRMED,
                            verified_by=query.from_user.id,
                            verified_at=datetime.utcnow(),
                        )
                        session.add(new_payment)
                        await session.flush()
                        new_payment_id = new_payment.id
        except Exception as exc_p:
            logger.warning("Failed to confirm/create payment record: %s", exc_p)
            new_payment_id = 0

        # ── Sync Google Sheets ──
        try:
            from sheets.daily_revenue import DailyRevenueSheet
            from sheets.members import MembersSheet
            from sheets.income_log import IncomeLogSheet
            await DailyRevenueSheet.update()
            from sheets.daily_summary import DailySummarySheet
            await DailySummarySheet.update()
            await IncomeLogSheet.log_payment(new_payment_id, approved_by=safe_admin)
            await MembersSheet.update_member(db_user.id)
            logger.info("Sheets synced for approve_by_price user %d", target_user_id)
            try:
                from shared.notify import notify as _notify
                await _notify("payment_approved",
                             title=f"✅ Payment Approved (admin button)",
                             body=f"User {target_user_id} approved")
            except Exception:
                pass
        except Exception as exc_s:
            logger.warning("Sheets sync failed: %s", exc_s)

        # ── Mark comeback promo as purchased (if this user had one) ──
        try:
            from bots.sales_bot.comeback_dm import mark_promo_purchased
            from shared.models import ComebackDmLog
            async with get_session() as session:
                cb_result = await session.execute(
                    select(ComebackDmLog).where(
                        ComebackDmLog.user_id == db_user.id,
                        ComebackDmLog.purchased == False,  # noqa: E712
                    ).order_by(ComebackDmLog.sent_at.desc()).limit(1)
                )
                cb_log = cb_result.scalar_one_or_none()
                if cb_log:
                    cb_log.purchased = True
                    cb_log.responded = True
                    logger.info("Comeback promo %s marked purchased via admin approval", cb_log.promo_code)
        except Exception as exc_cb:
            logger.warning("Comeback promo mark failed (non-critical): %s", exc_cb)

        # ── Process referral reward ──
        try:
            from bots.sales_bot.handlers.referral import process_referral_reward
            await process_referral_reward(target_user_id, sales_bot)
        except Exception as exc_ref:
            logger.warning("Referral reward failed for user %d: %s", target_user_id, exc_ref)

        # ── Welcome referral DM หลัง 3 วินาที ──
        try:
            import asyncio
            await asyncio.sleep(3)
            await sales_bot.send_message(
                chat_id=target_user_id,
                text=(
                    '🎉 ยินดีต้อนรับสู่ VIP เจริญพร! 💕\n'
                    '\n'
                    '💡 รู้มั้ย? ชวนเพื่อนมาสมัคร = ได้ VIP ฟรีเพิ่ม!\n'
                    '\n'
                    '🎯 ชวน 1 คน = +7 วัน VIP ฟรี\n'
                    '🎯 ชวน 5 คน = +30 วัน VIP ฟรี!\n'
                    '\n'
                    '━━━━━━━━━━━━━━━━━━\n'
                    '📩 <b>รับลิงก์ชวนเพื่อนเลย 👇</b>\n'
                    '👉 <a href="tg://resolve?domain=NamwarnJarern_bot&start=invite">🎁 กดรับลิงก์ชวนเพื่อน</a>\n'
                    '━━━━━━━━━━━━━━━━━━'
                ),
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
            logger.info("Welcome referral DM sent to %d (approve_by_price)", target_user_id)
        except Exception as exc_w:
            logger.warning("Welcome referral DM failed for user %d: %s", target_user_id, exc_w)

        # FIX 2025-05-21 (Phase 2f): audit log every approval with payment_id + button_amount
        try:
            await log_admin_action(
                admin_id=query.from_user.id,
                action="approve_by_price",
                target_type="user",
                target_id=target_user_id,
                details=(
                    f"button={price} amount_paid={amount_paid} "
                    f"payment_id={new_payment_id} pkg={pkg_name} "
                    f"force={force_override}"
                ),
            )
        except Exception as exc_log:
            logger.warning("log_admin_action failed for approve_by_price: %s", exc_log)

    except Exception as exc:
        logger.error("approve_by_price error: %s", exc)
        await query.answer(f"❌ Error: {str(exc)[:100]}", show_alert=True)


# ── Round 7: inspect_payment_callback + approve_promo_callback ──
async def inspect_payment_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """ดูรายละเอียด payment เพิ่มเติม."""
    query = update.callback_query
    await query.answer()

    if not query.from_user or not _is_admin(query.from_user.id):
        await query.answer("⛔ คุณไม่มีสิทธิ์", show_alert=True)
        return

    payment_id = int(query.data.split(":")[1])

    async with get_session() as session:
        payment = await session.get(Payment, payment_id)
        if not payment:
            await query.answer(f"❌ ไม่พบ Payment #{payment_id}", show_alert=True)
            return

        user = await session.get(User, payment.user_id)
        package = await session.get(Package, payment.package_id)

    info = (
        f"🔍 รายละเอียด #PAY{payment_id}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"สถานะ: {payment.status.value}\n"
        f"ยอด: {format_thb(payment.amount)}\n"
        f"วิธี: {payment.method.value}\n"
    )
    if user:
        info += f"ลูกค้า: @{user.username or user.first_name} (TG: {user.telegram_id})\n"
    if package:
        info += f"แพ็กเกจ: {package.name} ({format_thb(package.price)})\n"
    info += f"สร้างเมื่อ: {str(payment.created_at)[:19]}"

    await query.answer(info[:200], show_alert=True)


async def approve_promo_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """อนุมัติสลิปด้วยราคาโปรโมชั่น — approve_promo_userid format.

    ดึง active promo จาก comeback_dm_log แล้วใช้ discounted_price + package tier 300.
    """
    query = update.callback_query
    await query.answer()

    if not query.from_user or not _is_admin(query.from_user.id):
        await query.answer("⛔ คุณไม่มีสิทธิ์", show_alert=True)
        return

    parts = query.data.split("_")  # approve_promo_12345
    target_user_id = int(parts[2])

    import os
    import telegram as tg
    from datetime import timedelta
    from decimal import Decimal
    from bots.guardian_bot.group_monitor import generate_invite_links_for_user
    from shared.models import ComebackDmLog

    try:
        # Find active promo for this user
        async with get_session() as session:
            from sqlalchemy import select as sa_select
            cb_result = await session.execute(
                sa_select(ComebackDmLog).where(
                    ComebackDmLog.telegram_id == target_user_id,
                    ComebackDmLog.purchased == False,  # noqa: E712
                ).order_by(ComebackDmLog.sent_at.desc()).limit(1)
            )
            cb_log = cb_result.scalar_one_or_none()

        if not cb_log:
            await query.answer("❌ ไม่พบโปรโมชั่นที่ใช้งานได้", show_alert=True)
            return

        # Check expiry (48 hours)
        expiry = cb_log.sent_at + timedelta(hours=48)
        if datetime.utcnow() > expiry:
            await query.answer("❌ โปรโมชั่นหมดอายุแล้ว", show_alert=True)
            return

        discount_pct = cb_log.discount_pct
        from bots.sales_bot.comeback_dm import _calculate_discounted_price
        discounted_price = _calculate_discounted_price(discount_pct)
        promo_code = cb_log.promo_code

        # Determine source label
        dm_round = cb_log.round
        if dm_round >= 200:
            source = "Retention"
        elif dm_round >= 100:
            source = "Lead Followup"
        else:
            source = "Comeback"

        # Use the standard approve flow with discounted price
        # Find package — default to tier 300 (VIP 30 วัน)
        async with get_session() as session:
            from shared.models import Package, PackageTier
            pkg_result = await session.execute(
                select(Package).where(Package.tier == PackageTier.TIER_300)
            )
            package = pkg_result.scalar_one_or_none()
            if not package:
                await query.answer("❌ ไม่พบแพ็กเกจ", show_alert=True)
                return

            # Find or create user
            user_result = await session.execute(
                select(User).where(User.telegram_id == target_user_id)
            )
            db_user = user_result.scalar_one_or_none()
            if not db_user:
                db_user = User(telegram_id=target_user_id, first_name="ลูกค้า")
                session.add(db_user)
                await session.flush()

            # Expire existing active subscriptions
            from sqlalchemy import update as sa_update_sub
            await session.execute(
                sa_update_sub(Subscription)
                .where(Subscription.user_id == db_user.id, Subscription.status == SubscriptionStatus.ACTIVE)
                .values(status=SubscriptionStatus.EXPIRED)
            )

            # Create subscription
            now = datetime.utcnow()
            end_date = now + timedelta(days=package.duration_days)
            subscription = Subscription(
                user_id=db_user.id,
                package_id=package.id,
                status=SubscriptionStatus.ACTIVE,
                start_date=now,
                end_date=end_date,
            )
            session.add(subscription)
            # FIX2_TRUST_TRIGGER total_spent maintained by DB trigger

            # Mark teaser clicks as converted
            from sqlalchemy import update as sa_update
            from shared.models import TeaserClick
            await session.execute(
                sa_update(TeaserClick)
                .where(TeaserClick.user_id == target_user_id, TeaserClick.converted == False)
                .values(converted=True)
            )

            await session.flush()
            pkg_name = package.name
            duration = package.duration_days
            pkg_id = package.id

        # Mark promo as purchased
        try:
            async with get_session() as session:
                cb_result2 = await session.execute(
                    select(ComebackDmLog).where(ComebackDmLog.promo_code == promo_code)
                )
                cb_update = cb_result2.scalar_one_or_none()
                if cb_update:
                    cb_update.purchased = True
                    cb_update.responded = True
                    await session.flush()
            logger.info("Promo %s marked purchased via admin approval", promo_code)
        except Exception as exc_cb:
            logger.warning("Promo mark purchased failed: %s", exc_cb)

        # Generate invite links
        guardian_bot = tg.Bot(token=os.environ.get("GUARDIAN_BOT_TOKEN", ""))
        await guardian_bot.initialize()
        sales_bot = tg.Bot(token=os.environ.get("SALES_BOT_TOKEN", ""))
        await sales_bot.initialize()
        invite_links = await generate_invite_links_for_user(guardian_bot, target_user_id, pkg_id)

        links_list = []
        async with get_session() as session:
            from shared.models import GroupRegistry
            for slug, link in invite_links.items():
                if is_songkran_bonus_slug(slug):
                    title = get_group_display_title(slug)
                else:
                    grp_result = await session.execute(
                        select(GroupRegistry).where(GroupRegistry.slug == slug)
                    )
                    group = grp_result.scalar_one_or_none()
                    title = group.title if group else get_group_display_title(slug)
                links_list.append({"text": f"🚀 {title}", "url": link})

        # Send invite links to customer
        link_buttons = [links_list[i:i+2] for i in range(0, len(links_list), 2)]
        from shared.tz import now_th as _now_th_; expire_date = (_now_th_() + timedelta(days=duration)).strftime("%d/%m/%Y")
        msg = (
            f"✅ <b>อนุมัติยอด {discounted_price} บาท ({source} ลด {discount_pct}%) เรียบร้อยค่ะ</b>\n"
            f"📦 แพ็กเกจ: {pkg_name}\n"
            f"📅 หมดอายุ: {expire_date}\n\n"
            f"👇 <b>กดเข้ากลุ่มที่ปุ่มด้านล่างได้เลย</b>\n\n"
            f"🆓 <b>ห้องฟรี:</b> https://t.me/addlist/w0YSyuHC_aE2ZGVl"
        )
        invite_keyboard = tg.InlineKeyboardMarkup(
            [[tg.InlineKeyboardButton(b["text"], url=b["url"]) for b in row]
             for row in link_buttons]
        )
        try:
            await sales_bot.send_message(chat_id=target_user_id, text=msg, parse_mode="HTML", reply_markup=invite_keyboard)
        except Exception as exc_send:
            logger.error("Failed to send promo invite links to %s: %s", target_user_id, exc_send)
            admin_group_id = _admin_group_id()
            flat_links = "\n".join([f"• {b['text']}: {b['url']}" for b in links_list]) or "(ไม่มีลิงก์)"
            await context.bot.send_message(
                chat_id=admin_group_id,
                text=(
                    f"🚨 <b>ส่งลิงก์ลูกค้าไม่สำเร็จ</b>\n"
                    f"🆔 TG ID: <code>{target_user_id}</code>\n"
                    f"📦 แพ็กเกจ: {pkg_name}\n"
                    f"💰 โปร: {discounted_price} บาท ({source} -{discount_pct}%)\n"
                    f"❗ Error: {type(exc_send).__name__}: {exc_send}\n\n"
                    f"🔗 ลิงก์ที่สร้างไว้แล้ว:\n{flat_links}"
                ),
                parse_mode="HTML",
                reply_markup=_build_manual_invite_alert_keyboard(target_user_id),
            )

        # Update admin message
        safe_admin = query.from_user.first_name or "Admin"
        old_caption = query.message.caption or ""
        new_caption = f"{old_caption}\n\n✅ <b>สถานะ: อนุมัติ โปร {discounted_price}บ. ({source} -{discount_pct}%) โดย {safe_admin}</b>"
        post_keyboard = None
        if db_user and db_user.username:
            post_keyboard = tg.InlineKeyboardMarkup([[
                tg.InlineKeyboardButton(
                    f"💬 @{db_user.username}",
                    url=f"https://t.me/{db_user.username}",
                    api_kwargs={"style": "primary"},
                )
            ]])
        try:
            await query.edit_message_caption(caption=new_caption[:1024], parse_mode="HTML", reply_markup=post_keyboard)
        except Exception as e:
            logger.error("Failed to edit promo approval caption: %s", e)

        # ── Confirm existing PENDING payment (ไม่สร้างใหม่ — แก้ duplicate bug) ──
        try:
            async with get_session() as session:
                from shared.models import PaymentMethod
                pending_result = await session.execute(
                    select(Payment).where(
                        Payment.user_id == db_user.id,
                        Payment.status == PaymentStatus.PENDING,
                    ).order_by(Payment.created_at.desc()).limit(1)
                )
                pending_payment = pending_result.scalar_one_or_none()
                if pending_payment:
                    pending_payment.status = PaymentStatus.CONFIRMED
                    pending_payment.verified_by = query.from_user.id
                    pending_payment.verified_at = datetime.utcnow()
                    pending_payment.amount = Decimal(str(discounted_price))
                    pending_payment.package_id = pkg_id
                    await session.flush()
                    new_payment_id = pending_payment.id
                    logger.info("Existing PENDING payment #%d confirmed (promo) for user %d", new_payment_id, target_user_id)
                else:
                    dedup_cutoff = datetime.utcnow() - timedelta(minutes=10)
                    dup_check = await session.execute(
                        select(Payment).where(
                            Payment.user_id == db_user.id,
                            Payment.amount == Decimal(str(discounted_price)),
                            Payment.status == PaymentStatus.CONFIRMED,
                            Payment.created_at >= dedup_cutoff,
                        )
                    )
                    if dup_check.scalar_one_or_none():
                        logger.warning("Duplicate promo payment skipped: user_id=%s", db_user.id)
                        new_payment_id = 0
                    else:
                        new_payment = Payment(
                            user_id=db_user.id,
                            package_id=pkg_id,
                            amount=Decimal(str(discounted_price)),
                            method=PaymentMethod.SLIP,
                            status=PaymentStatus.CONFIRMED,
                            verified_by=query.from_user.id,
                            verified_at=datetime.utcnow(),
                        )
                        session.add(new_payment)
                        await session.flush()
                        new_payment_id = new_payment.id
        except Exception as exc_p:
            logger.warning("Failed to confirm/create promo payment record: %s", exc_p)
            new_payment_id = 0

        # Sync Sheets
        try:
            from sheets.daily_revenue import DailyRevenueSheet
            from sheets.members import MembersSheet
            from sheets.income_log import IncomeLogSheet
            await DailyRevenueSheet.update()
            from sheets.daily_summary import DailySummarySheet
            await DailySummarySheet.update()
            await IncomeLogSheet.log_payment(new_payment_id, approved_by=safe_admin)
            await MembersSheet.update_member(db_user.id)
        except Exception as exc_s:
            logger.warning("Sheets sync failed for promo approval: %s", exc_s)

        # Process referral reward
        try:
            from bots.sales_bot.handlers.referral import process_referral_reward
            await process_referral_reward(target_user_id, sales_bot)
        except Exception as exc_ref:
            logger.warning("Referral reward failed for promo user %d: %s", target_user_id, exc_ref)

        # Welcome referral DM
        try:
            import asyncio
            await asyncio.sleep(3)
            await sales_bot.send_message(
                chat_id=target_user_id,
                text=(
                    '🎉 ยินดีต้อนรับสู่ VIP เจริญพร! 💕\n'
                    '\n'
                    '💡 รู้มั้ย? ชวนเพื่อนมาสมัคร = ได้ VIP ฟรีเพิ่ม!\n'
                    '\n'
                    '🎯 ชวน 1 คน = +7 วัน VIP ฟรี\n'
                    '🎯 ชวน 5 คน = +30 วัน VIP ฟรี!\n'
                    '\n'
                    '━━━━━━━━━━━━━━━━━━\n'
                    '📩 <b>รับลิงก์ชวนเพื่อนเลย 👇</b>\n'
                    '👉 <a href="tg://resolve?domain=NamwarnJarern_bot&start=invite">🎁 กดรับลิงก์ชวนเพื่อน</a>\n'
                    '━━━━━━━━━━━━━━━━━━'
                ),
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
        except Exception as exc_w:
            logger.warning("Welcome DM failed for promo user %d: %s", target_user_id, exc_w)

        logger.info(
            "[%s] [ADMIN_BOT] [APPROVE_PROMO] [%s] [user=%d promo=%s price=%d]",
            datetime.now(timezone.utc).isoformat(),
            query.from_user.id,
            target_user_id,
            promo_code,
            discounted_price,
        )

    except Exception as exc:
        logger.error("approve_promo error: %s", exc)
        await query.answer(f"❌ Error: {str(exc)[:100]}", show_alert=True)


# FIX 2025-05-21 (Phase 2f): Verify slip amount matches the button admin clicked.
# Prevents the case where admin misclicks "2499" while the slip is actually 99
# and a customer gets lifetime access for 99 baht.
# ── Strangler Fig Round 6: payment-approve actions moved out ──
# Now lives in bots/admin_bot/handlers/payment_actions.py — logic unchanged.
from bots.admin_bot.handlers.payment_actions import (
    _verify_approve_amount,
    approve_by_price_callback,
)


# ── Round 8: approve_payment_callback + reject_payment_callback ──
async def approve_payment_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """อนุมัติ payment — สร้าง subscription ให้สมาชิก."""
    query = update.callback_query
    await query.answer()

    if not query.from_user or not _is_admin(query.from_user.id):
        await query.edit_message_text("⛔ คุณไม่มีสิทธิ์")
        return

    payment_id = int(query.data.split(":")[1])

    async with get_session() as session:
        payment = await session.get(Payment, payment_id)
        if not payment:
            await query.edit_message_text(f"❌ ไม่พบ Payment #{payment_id}")
            return

        if payment.status != PaymentStatus.PENDING:
            await query.edit_message_text(
                f"⚠️ Payment #{payment_id} สถานะเป็น {payment.status.value} แล้ว"
            )
            return

        # Update payment status
        payment.status = PaymentStatus.CONFIRMED
        payment.verified_by = query.from_user.id
        payment.verified_at = datetime.utcnow()

        # Get package for duration
        package = await session.get(Package, payment.package_id)
        duration_days = package.duration_days if package else 30

        # Expire existing active subscriptions (prevent duplicates)
        # BUT skip lifetime subs (duration >= 36500 days) when buying add-on packages
        from datetime import timedelta
        from sqlalchemy import update as sa_update_sub
        is_addon = package and package.tier.value == 'ADD500'
        if is_addon:
            # Add-on: only expire subs for the SAME package (don't touch GOD MODE etc)
            await session.execute(
                sa_update_sub(Subscription)
                .where(
                    Subscription.user_id == payment.user_id,
                    Subscription.status == SubscriptionStatus.ACTIVE,
                    Subscription.package_id == payment.package_id,
                )
                .values(status=SubscriptionStatus.EXPIRED)
            )
        else:
            await session.execute(
                sa_update_sub(Subscription)
                .where(Subscription.user_id == payment.user_id, Subscription.status == SubscriptionStatus.ACTIVE)
                .values(status=SubscriptionStatus.EXPIRED)
            )

        # Create subscription
        now = datetime.utcnow()
        subscription = Subscription(
            user_id=payment.user_id,
            package_id=payment.package_id,
            status=SubscriptionStatus.ACTIVE,
            start_date=now,
            end_date=now + timedelta(days=duration_days),
            payment_id=payment.id,
        )
        session.add(subscription)

        # Update user total spent
        user = await session.get(User, payment.user_id)
        if user:
            # FIX2_TRUST_TRIGGER total_spent maintained by DB trigger

            # Mark teaser clicks as converted for this user
            from sqlalchemy import update as sa_update
            from shared.models import TeaserClick
            await session.execute(
                sa_update(TeaserClick)
                .where(TeaserClick.user_id == user.telegram_id, TeaserClick.converted == False)
                .values(converted=True)
            )

        await session.flush()

    # Log admin action
    await log_admin_action(
        admin_id=query.from_user.id,
        action="approve_payment",
        target_type="payment",
        target_id=payment_id,
        details=f"Approved payment #{payment_id}, amount={payment.amount}",
    )

    # Send invite links to customer
    invite_text = ""
    if user:
        try:
            from bots.guardian_bot.group_monitor import generate_invite_links_for_user
            import os
            import telegram as tg
            sales_bot = tg.Bot(token=os.environ.get("SALES_BOT_TOKEN", ""))
            await sales_bot.initialize()
            invite_links = await generate_invite_links_for_user(
                sales_bot, user.telegram_id, payment.package_id
            )
            links_list = []
            async with get_session() as session:
                from shared.models import GroupRegistry
                for slug, link in invite_links.items():
                    if is_songkran_bonus_slug(slug):
                        title = get_group_display_title(slug)
                    else:
                        grp_result = await session.execute(
                            select(GroupRegistry).where(GroupRegistry.slug == slug)
                        )
                        group = grp_result.scalar_one_or_none()
                        title = group.title if group else get_group_display_title(slug)
                    links_list.append(f"• {title}: {link}")
            links_text = "\n".join(links_list) if links_list else "ไม่สามารถสร้างลิงก์ได้"

            await sales_bot.send_message(
                chat_id=user.telegram_id,
                text=(
                    f"✅ <b>ชำระเงินสำเร็จค่ะ!</b>\n\n"
                    f"🔗 <b>ลิงก์เข้ากลุ่ม VIP:</b>\n{links_text}\n\n"
                    f"⚠️ ลิงก์แต่ละลิงก์ใช้ได้ 1 ครั้ง หมดอายุ 24 ชม.\n"
                    f"กรุณากดเข้าร่วมโดยเร็วนะคะ 🙏"
                ),
                parse_mode="HTML",
            )
            invite_text = "\n📩 ส่งลิงก์ให้ลูกค้าแล้ว"
        except Exception as exc:
            logger.error("Failed to send invite links: %s", exc)
            invite_text = "\n⚠️ ส่งลิงก์ไม่สำเร็จ"
            try:
                admin_group_id = _admin_group_id()
                await context.bot.send_message(
                    chat_id=admin_group_id,
                    text=(
                        f"🚨 <b>ส่งลิงก์ลูกค้าไม่สำเร็จ</b>\n"
                        f"👤 ลูกค้า: {user.first_name or '-'} @{user.username or '-'}\n"
                        f"🆔 TG ID: <code>{user.telegram_id}</code>\n"
                        f"📦 แพ็กเกจ: {package_name}\n"
                        f"💰 ยอด: {format_thb(payment.amount)}\n"
                        f"❗ Error: {type(exc).__name__}: {exc}\n\n"
                        f"🔗 ลิงก์ที่สร้างไว้แล้ว:\n{links_text}"
                    ),
                    parse_mode="HTML",
                    reply_markup=_build_manual_invite_alert_keyboard(user.telegram_id),
                )
            except Exception as notify_exc:
                logger.error("Failed to notify admin group about invite failure: %s", notify_exc)

            # ส่ง DM ยินดีต้อนรับ + แนะนำชวนเพื่อน หลัง 3 วินาที
            try:
                import asyncio
                await asyncio.sleep(3)
                await sales_bot.send_message(
                    chat_id=user.telegram_id,
                    text=(
                        '🎉 ยินดีต้อนรับสู่ VIP เจริญพร! 💕\n'
                        '\n'
                        '💡 รู้มั้ย? ชวนเพื่อนมาสมัคร = ได้ VIP ฟรีเพิ่ม!\n'
                        '\n'
                        '🎯 ชวน 1 คน = +7 วัน VIP ฟรี\n'
                        '🎯 ชวน 5 คน = +30 วัน VIP ฟรี!\n'
                        '\n'
                        '━━━━━━━━━━━━━━━━━━\n'
                        '📩 <b>รับลิงก์ชวนเพื่อนเลย 👇</b>\n'
                        '👉 <a href="tg://resolve?domain=NamwarnJarern_bot&start=invite">🎁 กดรับลิงก์ชวนเพื่อน</a>\n'
                        '━━━━━━━━━━━━━━━━━━'
                    ),
                    parse_mode="HTML",
                    disable_web_page_preview=True,
                )
                logger.info("Welcome referral DM sent to %s (admin approval)", user.telegram_id)
            except Exception as exc_w:
                logger.warning("Welcome referral DM failed: %s", exc_w)

        except Exception as exc:
            logger.error("Failed to send invite links: %s", exc)
            invite_text = "\n⚠️ ส่งลิงก์ไม่สำเร็จ"

            # ส่ง DM ยินดีต้อนรับ + แนะนำชวนเพื่อน หลัง 3 วินาที
            try:
                import asyncio
                await asyncio.sleep(3)
                await sales_bot.send_message(
                    chat_id=user.telegram_id,
                    text=(
                        '🎉 ยินดีต้อนรับสู่ VIP เจริญพร! 💕\n'
                        '\n'
                        '💡 รู้มั้ย? ชวนเพื่อนมาสมัคร = ได้ VIP ฟรีเพิ่ม!\n'
                        '\n'
                        '🎯 ชวน 1 คน = +7 วัน VIP ฟรี\n'
                        '🎯 ชวน 5 คน = +30 วัน VIP ฟรี!\n'
                        '\n'
                        '━━━━━━━━━━━━━━━━━━\n'
                        '📩 <b>รับลิงก์ชวนเพื่อนเลย 👇</b>\n'
                        '👉 <a href="tg://resolve?domain=NamwarnJarern_bot&start=invite">🎁 กดรับลิงก์ชวนเพื่อน</a>\n'
                        '━━━━━━━━━━━━━━━━━━'
                    ),
                    parse_mode="HTML",
                    disable_web_page_preview=True,
                )
                logger.info("Welcome referral DM sent to %s (admin approval)", user.telegram_id)
            except Exception as exc_w:
                logger.warning("Welcome referral DM failed: %s", exc_w)

        except Exception as exc:
            logger.error("Failed to send invite links: %s", exc)
            invite_text = "\n⚠️ ส่งลิงก์ไม่สำเร็จ"

    package_name = package.name if package else "N/A"
    try:
        await query.edit_message_caption(
            caption=(
                f"✅ <b>อนุมัติ Payment #{payment_id}</b>\n"
                f"📦 แพ็กเกจ: {package_name}\n"
                f"💰 จำนวน: {format_thb(payment.amount)}\n"
                f"⏱ ระยะเวลา: {duration_days} วัน\n"
                f"👤 อนุมัติโดย: {query.from_user.first_name}"
                f"{invite_text}"
            ),
            parse_mode="HTML",
        )
    except Exception as e:
        logger.error("Failed to edit approval caption: %s", e)

    # ── Sync Google Sheets ──
    try:
        from sheets.daily_revenue import DailyRevenueSheet
        from sheets.members import MembersSheet
        from sheets.income_log import IncomeLogSheet
        await DailyRevenueSheet.update()
        from sheets.daily_summary import DailySummarySheet
        await DailySummarySheet.update()
        await IncomeLogSheet.log_payment(payment_id, approved_by=query.from_user.first_name or "Admin")
        if user:
            await MembersSheet.update_member(user.id)
        logger.info("Sheets synced for payment #%d", payment_id)
    except Exception as exc:
        logger.warning("Sheets sync failed for payment #%d: %s", payment_id, exc)
        logger.warning("Sheets sync failed for payment #%d: %s", payment_id, exc)

    # ── Process referral reward ──
    if user:
        try:
            import telegram as tg
            sales_bot = tg.Bot(token=os.environ.get("SALES_BOT_TOKEN", ""))
            await sales_bot.initialize()
            from bots.sales_bot.handlers.referral import process_referral_reward
            await process_referral_reward(user.telegram_id, sales_bot)
        except Exception as exc_ref:
            logger.warning("Referral reward failed for payment #%d: %s", payment_id, exc_ref)

    logger.info(
        "[%s] [ADMIN_BOT] [APPROVE_PAYMENT] [%s] [payment_id=%d amount=%s]",
        datetime.now(timezone.utc).isoformat(),
        query.from_user.id,
        payment_id,
        payment.amount,
    )


async def reject_payment_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """ไม่อนุมัติ payment."""
    query = update.callback_query
    await query.answer()

    if not query.from_user or not _is_admin(query.from_user.id):
        await query.edit_message_text("⛔ คุณไม่มีสิทธิ์")
        return

    payment_id = int(query.data.split(":")[1])

    async with get_session() as session:
        payment = await session.get(Payment, payment_id)
        if not payment:
            await query.edit_message_text(f"❌ ไม่พบ Payment #{payment_id}")
            return

        if payment.status != PaymentStatus.PENDING:
            await query.edit_message_text(
                f"⚠️ Payment #{payment_id} สถานะเป็น {payment.status.value} แล้ว"
            )
            return

        payment.status = PaymentStatus.REJECTED
        payment.verified_by = query.from_user.id
        payment.verified_at = datetime.utcnow()
        payment.reject_reason = "ไม่อนุมัติโดยแอดมิน"

        await session.flush()

    await log_admin_action(
        admin_id=query.from_user.id,
        action="reject_payment",
        target_type="payment",
        target_id=payment_id,
        details=f"Rejected payment #{payment_id}",
    )

    await query.edit_message_text(
        f"❌ <b>ไม่อนุมัติ Payment #{payment_id}</b>\n"
        f"👤 โดย: {query.from_user.first_name}",
        parse_mode="HTML",
    )

    logger.info(
        "[%s] [ADMIN_BOT] [REJECT_PAYMENT] [%s] [payment_id=%d]",
        datetime.now(timezone.utc).isoformat(),
        query.from_user.id,
        payment_id,
    )


# ─── Pending Broadcasts ──────────────────────────────────────────────────────

