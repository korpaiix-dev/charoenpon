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
    # FIX 2026-07-02: accept a lower payment when a gacha/discount intent explains the gap
    # (customer paid final_price < tier price using credit) — not a real mismatch.
    if pay < btn:
        try:
            from sqlalchemy import text as _t2
            async with get_session() as _s2:
                _r2 = await _s2.execute(_t2(
                    "SELECT 1 FROM purchase_intents pi JOIN users u ON u.telegram_id = pi.user_telegram_id "
                    "WHERE u.id = :uid AND pi.final_price = :pay AND pi.discount_credit > 0 "
                    "AND pi.created_at > NOW() - interval '2 days' LIMIT 1"
                ), {"uid": payment.user_id, "pay": pay})
                if _r2.first():
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

            # Resolve target user (for db_user.username + db_user.id used in admin caption later)
            _u_lookup = await session.execute(
                select(User).where(User.telegram_id == target_user_id)
            )
            db_user = _u_lookup.scalar_one_or_none()
            if db_user is None:
                # let unified service create the row
                db_user_id_for_call = None
            else:
                db_user_id_for_call = db_user.id

            pkg_name = package.name
            pkg_id = package.id
            duration = package.duration_days

        # Compute amount_paid (preserve promo-pricing rules from old impl)
        from decimal import Decimal as _Dec
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
            _btn_price = _Dec(str(price if price != "ADD500" else "500"))
            # FIX 2026-07-02 (over-count bug): record the ACTUAL amount transferred (from the
            # pending slip), not the button tier price. Customer may pay less using
            # gacha/discount credit (e.g. slip 250 for a 300 tier). Button sets the TIER;
            # amount reflects real cash into the bank (fixes payments.amount + receiver drift).
            amount_paid = _slip_amt if (_slip_amt and _slip_amt > 0 and _slip_amt <= _btn_price) else _btn_price

        # ── REFACTOR 2026-06-29 (P1 audit #447): route through unified service ──
        # ก่อนหน้านี้ branch นี้ self-contained ~400 บรรทัด — สร้าง Subscription แบบ orphan
        # (payment_id=NULL บางครั้ง), gen invite links เอง, DM ลูกค้าผ่าน sales_bot โดยตรง,
        # ข้าม side-effects (sender_ring, blacklist, dup-slip, birthday bonus, onboarding rewards,
        # shaker, lifetime guard, receiver record, gachapon credits, comeback mark, discount apply).
        #
        # ตอนนี้: apply_payment_approval(ADMIN_BY_PRICE) → side-effect ครบ → return result
        # → ใช้ result.invite_links / expires_at format admin caption เดิม.
        from shared.payment_approval import (
            apply_payment_approval as _apply, ApprovalInput as _ApIn,
            ApprovalSource as _ApSrc,
        )
        try:
            _result = await _apply(_ApIn(
                user_id=db_user_id_for_call,
                telegram_id=target_user_id,
                source=_ApSrc.ADMIN_BY_PRICE,
                amount_paid=_Dec(str(amount_paid)),
                explicit_tier=_tier_enum,
                admin_id=query.from_user.id if query.from_user else None,
                # payment_id: หา PENDING ตัวล่าสุดให้ service update แทน insert ใหม่
                payment_id=(getattr(verify_payment, "id", None) if "verify_payment" in locals() and verify_payment is not None else None),
                method="SLIP",
                force_amount=force_override,
                skip_sender_ring=True,   # admin button = admin already eyeballed
            ))
        except Exception as _exc_unified:
            logger.exception("approve_by_price: unified service crashed: %s", _exc_unified)
            await query.answer(f"❌ ระบบอนุมัติพัง: {str(_exc_unified)[:80]}", show_alert=True)
            return

        if not _result.success:
            _err = _result.error or "unknown"
            logger.warning(
                "approve_by_price: unified service refused tg=%s price=%s err=%s detail=%s",
                target_user_id, price, _err, _result.error_details,
            )
            _msg_map = {
                "user_banned": "⛔ ลูกค้านี้ถูกแบน — /unban ก่อน",
                "blacklisted_sender": "⛔ ผู้ส่งอยู่ใน blacklist",
                "blacklisted_slip": "⛔ สลิปอยู่ใน blacklist",
            }
            _prefix = "dup_transref" if _err.startswith("dup_transref") else ("dup_hash" if _err.startswith("dup_hash") else _err)
            _user_msg = _msg_map.get(_prefix, f"❌ {_err}")
            await query.answer(_user_msg, show_alert=True)
            return

        # ── post-success: format admin caption (preserve original UX) ──
        new_payment_id = _result.payment_id or 0

        # Mark teaser clicks converted (preserve from old impl — was inside the TX)
        try:
            async with get_session() as _ts:
                from sqlalchemy import update as _sa_up
                from shared.models import TeaserClick as _TC
                await _ts.execute(
                    _sa_up(_TC).where(_TC.user_id == target_user_id, _TC.converted == False).values(converted=True)
                )
                await _ts.commit()
        except Exception:
            pass

        # Update admin message caption — preserve UX (button to customer chat if username known)
        safe_admin = query.from_user.first_name or "Admin"
        old_caption = query.message.caption or query.message.text or ""
        new_caption = f"{old_caption}\n\n✅ <b>สถานะ: อนุมัติ ({price}บ.) โดย {safe_admin}</b>"
        post_keyboard = None
        if db_user and getattr(db_user, "username", None):
            post_keyboard = tg.InlineKeyboardMarkup([[
                tg.InlineKeyboardButton(
                    f"💬 @{db_user.username}",
                    url=f"https://t.me/{db_user.username}",
                    api_kwargs={"style": "primary"},
                )
            ]])
        try:
            if query.message and query.message.caption:
                await query.edit_message_caption(
                    caption=new_caption[:1024],
                    parse_mode="HTML",
                    reply_markup=post_keyboard,
                )
            elif query.message and query.message.text:
                await query.edit_message_text(
                    text=new_caption[:4096],
                    parse_mode="HTML",
                    reply_markup=post_keyboard,
                )
        except Exception as e:
            logger.error("Failed to edit approval caption: %s", e)

        # Discord notify (preserve from old impl)
        try:
            await _notify_discord_alert(
                f"✅ อนุมัติ {price} บาท",
                f"👤 ลูกค้า: TG ID {target_user_id}\n📦 แพ็กเกจ: {pkg_name}\n👮 โดย: {safe_admin}",
                color=0x2ECC71,
            )
        except Exception:
            pass

        # Audit log (preserve)
        try:
            await log_admin_action(
                admin_id=query.from_user.id,
                action="approve_by_price",
                target_type="user",
                target_id=target_user_id,
                details=(
                    f"button={price} amount_paid={amount_paid} "
                    f"payment_id={new_payment_id} sub_id={_result.subscription_id} "
                    f"pkg={pkg_name} force={force_override} via=apply_payment_approval"
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
    """อนุมัติสลิปด้วยราคาโปรโมชั่น (approve_promo_<uid>) — route ผ่าน apply_payment_approval.

    REFACTOR 2026-07-04 (P0 audit): เดิม hand-roll subscription (payment_id=NULL) + expire subs
    ทั้งหมด + ไม่ credit บัญชี + dedup 10 นาที. ตอนนี้ route ผ่าน apply_payment_approval(
    ADMIN_PROMO, comeback_dm_log_id=...) -> confirm payment เดิม + sub + credit บัญชี +
    same-package expire + mark promo purchased + กันซ้ำ (STEP 0).
    """
    query = update.callback_query
    await query.answer()
    if not query.from_user or not _is_admin(query.from_user.id):
        await query.answer("⛔ คุณไม่มีสิทธิ์", show_alert=True)
        return
    try:
        target_user_id = int(query.data.split("_")[2])
    except Exception:
        await query.answer("bad callback", show_alert=True)
        return

    from datetime import timedelta
    from decimal import Decimal
    from shared.models import ComebackDmLog, Package, PackageTier, Subscription

    try:
        async with get_session() as session:
            from sqlalchemy import select as sa_select
            cb_log = (await session.execute(
                sa_select(ComebackDmLog).where(
                    ComebackDmLog.telegram_id == target_user_id,
                    ComebackDmLog.purchased == False,  # noqa: E712
                ).order_by(ComebackDmLog.sent_at.desc()).limit(1)
            )).scalar_one_or_none()
        if not cb_log:
            await query.answer("❌ ไม่พบโปรโมชั่นที่ใช้งานได้", show_alert=True)
            return
        if datetime.utcnow() > cb_log.sent_at + timedelta(hours=48):
            await query.answer("❌ โปรโมชั่นหมดอายุแล้ว", show_alert=True)
            return
        discount_pct = cb_log.discount_pct
        from bots.sales_bot.comeback_dm import _calculate_discounted_price
        discounted_price = _calculate_discounted_price(discount_pct)
        _cb_id = cb_log.id
        _round = cb_log.round or 0
        source = "Retention" if _round >= 200 else ("Lead Followup" if _round >= 100 else "Comeback")

        async with get_session() as session:
            package = (await session.execute(
                select(Package).where(Package.tier == PackageTier.TIER_300)
            )).scalar_one_or_none()
            if not package:
                await query.answer("❌ ไม่พบแพ็กเกจ", show_alert=True)
                return
            _pkg_id = package.id
            _pkg_name = package.name
            db_user = (await session.execute(
                select(User).where(User.telegram_id == target_user_id)
            )).scalar_one_or_none()
            _uid = db_user.id if db_user else None
            _uname = db_user.username if db_user else None
            _banned = bool(db_user.is_banned) if db_user else False
            _pid = _ptrans = _phash = _psname = _psbank = _psacct = _psfid = _pmrid = None
            _pending_amount = None
            if db_user:
                pending = (await session.execute(
                    select(Payment).where(
                        Payment.user_id == db_user.id,
                        Payment.status == PaymentStatus.PENDING,
                    ).order_by(Payment.created_at.desc()).limit(1)
                )).scalar_one_or_none()
                # P0-3 fix: only treat the pending slip as THIS comeback's payment if it is
                # recent (within the comeback's own 48h validity). A weeks-stale unpaid pending
                # must not be auto-confirmed nor have its (wrong) amount booked as the sale.
                if pending is not None and getattr(pending, "created_at", None) is not None \
                        and (datetime.utcnow() - pending.created_at) > timedelta(hours=48):
                    pending = None
                if pending:
                    _pid = pending.id
                    _pending_amount = pending.amount
                    _ptrans = pending.slip_trans_ref
                    _phash = pending.slip_hash
                    _psname = pending.sender_name
                    _psbank = pending.sender_bank_name
                    _psacct = pending.sender_bank_account
                    _psfid = pending.slip_file_id
                    _pmrid = getattr(pending, "matched_receiver_account_id", None)
                # P0-3: infer the customer's REAL tier ONLY for retention_alert renewals
                # (round 200-299). Those DMs offer the customer's actual expiring package at that
                # package's own discounted price, so granting that tier + the real slip amount is
                # correct. comeback_dm (round 1-2) and welcome_journey (round 300+) DMs instead
                # offer "VIP 30 วัน" off ฿300 -> they MUST grant TIER_300 (the default set above).
                # Inferring a former-GOD tier there would hand GOD ถาวร for the ฿255 VIP price.
                if 200 <= (_round or 0) <= 299:
                    try:
                        # only REAL base-membership subs — never a shaker(TIER_100)/gacha/trial row
                        # (those also create subscriptions and would renew into the wrong tiny tier).
                        _base_tiers = [PackageTier.TIER_300, PackageTier.TIER_500,
                                       PackageTier.TIER_1299, PackageTier.TIER_2499, PackageTier.TIER_4999]
                        _last_pkg = (await session.execute(
                            select(Package).join(Subscription, Subscription.package_id == Package.id)
                            .where(Subscription.user_id == db_user.id, Package.tier.in_(_base_tiers))
                            .order_by(Subscription.created_at.desc()).limit(1)
                        )).scalars().first()
                        if _last_pkg:
                            _pkg_id = _last_pkg.id
                            _pkg_name = _last_pkg.name
                    except Exception:
                        pass

        if _banned:
            await query.answer("🚫 ลูกค้าถูกแบน — /unban ก่อน", show_alert=True)
            return

        from shared.payment_approval import (
            apply_payment_approval as _apply, ApprovalInput as _ApIn, ApprovalSource as _ApSrc,
        )
        result = await _apply(_ApIn(
            user_id=_uid, telegram_id=target_user_id, source=_ApSrc.ADMIN_PROMO,
            amount_paid=Decimal(str(_pending_amount if _pending_amount else discounted_price)), explicit_package_id=_pkg_id,
            admin_id=query.from_user.id, payment_id=_pid, comeback_dm_log_id=_cb_id,
            slip_trans_ref=_ptrans, slip_hash=_phash, sender_name=_psname,
            sender_bank_name=_psbank, sender_bank_account=_psacct, slip_file_id=_psfid,
            method="SLIP", matched_receiver_account_id=_pmrid, skip_sender_ring=True,
        ))
        if not result.success:
            _ek = (result.error or "").split(":")[0]
            _emap = {"dup_transref": "สลิปนี้เคยถูกใช้แล้ว", "dup_hash": "สลิปนี้เคยถูกใช้แล้ว",
                     "user_banned": "ลูกค้าถูกแบน", "sender_ring": "เข้าข่ายสแกม"}
            await query.answer(f"❌ อนุมัติไม่สำเร็จ: {_emap.get(_ek, result.error or '?')}", show_alert=True)
            return

        import telegram as tg
        safe_admin = query.from_user.first_name or "Admin"
        _dm = "ส่งลิงก์แล้ว" if result.customer_dm_sent else "DM ไม่สำเร็จ (เช็คห้องแอดมิน)"
        old_caption = query.message.caption or ""
        new_caption = f"{old_caption}\n\n✅ <b>อนุมัติ โปร {discounted_price}บ. ({source} -{discount_pct}%) โดย {safe_admin}</b> · {_dm}"
        post_kb = None
        if _uname:
            post_kb = tg.InlineKeyboardMarkup([[tg.InlineKeyboardButton(f"💬 @{_uname}", url=f"https://t.me/{_uname}")]])
        try:
            await query.edit_message_caption(caption=new_caption[:1024], parse_mode="HTML", reply_markup=post_kb)
        except Exception:
            pass

        try:
            from sqlalchemy import update as _sa_upd
            from shared.models import TeaserClick as _TC
            async with get_session() as _s3:
                await _s3.execute(_sa_upd(_TC).where(_TC.user_id == target_user_id, _TC.converted == False).values(converted=True))
                await _s3.commit()
        except Exception:
            pass
        try:
            from sheets.daily_revenue import DailyRevenueSheet
            from sheets.income_log import IncomeLogSheet
            await DailyRevenueSheet.update()
            await IncomeLogSheet.log_payment(result.payment_id or 0, approved_by=safe_admin)
        except Exception as exc_s:
            logger.warning("Sheets sync failed (promo): %s", exc_s)
        try:
            import os as _os_ref
            _sb = tg.Bot(token=_os_ref.environ.get("SALES_BOT_TOKEN", ""))
            await _sb.initialize()
            from bots.sales_bot.handlers.referral import process_referral_reward
            await process_referral_reward(target_user_id, _sb)
        except Exception as exc_ref:
            logger.warning("Referral reward failed (promo) %d: %s", target_user_id, exc_ref)

        logger.info("[ADMIN_BOT][APPROVE_PROMO via canonical] user=%d promo=%s price=%d pid=%s by=%s",
                    target_user_id, cb_log.promo_code, discounted_price, result.payment_id, query.from_user.id)

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
    """อนุมัติ payment (pay_approve:<id>) — route ผ่าน apply_payment_approval (canonical).

    REFACTOR 2026-07-04 (P0 audit): เดิม hand-roll subscription + expire subs ทั้งหมด +
    ไม่เรียก record_payment_received (เงินเข้าแต่ไม่ track บัญชี) + ข้าม dup/ring/blacklist +
    ไม่มี idempotency. ตอนนี้ route ผ่าน apply_payment_approval(ADMIN_BY_PID) เหมือน dashboard
    -> side-effect ครบ + credit บัญชี + same-package expire + กันอนุมัติซ้ำ.
    """
    query = update.callback_query
    await query.answer()
    if not query.from_user or not _is_admin(query.from_user.id):
        await query.edit_message_text("⛔ คุณไม่มีสิทธิ์")
        return
    try:
        payment_id = int(query.data.split(":")[1])
    except Exception:
        await query.answer("bad callback", show_alert=True)
        return

    async with get_session() as session:
        payment = await session.get(Payment, payment_id)
        if not payment:
            await query.edit_message_text(f"❌ ไม่พบ Payment #{payment_id}")
            return
        if payment.status != PaymentStatus.PENDING:
            await query.edit_message_text(f"⚠️ Payment #{payment_id} สถานะเป็น {payment.status.value} แล้ว")
            return
        user = await session.get(User, payment.user_id)
        package = await session.get(Package, payment.package_id)
        _uid = payment.user_id
        _cust_tg = user.telegram_id if user else None
        _pkg_name = package.name if package else "N/A"
        _amt = payment.amount
        _pkg_id = payment.package_id
        _trans = payment.slip_trans_ref
        _hash = payment.slip_hash
        _sname = payment.sender_name
        _sbank = payment.sender_bank_name
        _sacct = payment.sender_bank_account
        _sfid = payment.slip_file_id
        _method = str(payment.method.value if hasattr(payment.method, "value") else (payment.method or "SLIP"))
        _mrid = getattr(payment, "matched_receiver_account_id", None)
        _banned = bool(user.is_banned) if user else False

    if _banned:
        await query.edit_message_text(f"🚫 ลูกค้า (TG {_cust_tg}) ถูกแบน — /unban ก่อน")
        return
    if not _cust_tg:
        await query.edit_message_text("❌ ไม่พบ telegram_id ลูกค้า")
        return

    try:
        from shared.payment_approval import (
            apply_payment_approval as _apply, ApprovalInput as _ApIn, ApprovalSource as _ApSrc,
        )
        from decimal import Decimal as _Dec
        result = await _apply(_ApIn(
            user_id=_uid, telegram_id=_cust_tg, source=_ApSrc.ADMIN_BY_PID,
            amount_paid=_Dec(str(_amt or 0)), explicit_package_id=_pkg_id,
            admin_id=query.from_user.id, payment_id=payment_id,
            slip_trans_ref=_trans, slip_hash=_hash, sender_name=_sname,
            sender_bank_name=_sbank, sender_bank_account=_sacct, slip_file_id=_sfid,
            method=_method, matched_receiver_account_id=_mrid, skip_sender_ring=True,
        ))
    except Exception as exc:
        logger.exception("[approve_payment_callback] service crashed pid=%s: %s", payment_id, exc)
        await query.edit_message_text(f"❌ ระบบขัดข้อง: {str(exc)[:120]}")
        return

    if not result.success:
        _emap = {
            "user_banned": "ลูกค้าถูกแบน", "sender_ring": "เข้าข่ายขบวนการสแกม",
            "blacklisted_sender": "ผู้ส่งอยู่บัญชีดำ", "blacklisted_slip": "สลิปอยู่บัญชีดำ",
            "dup_transref": "สลิปนี้เคยถูกใช้แล้ว", "dup_hash": "สลิปนี้เคยถูกใช้แล้ว",
        }
        _ek = (result.error or "").split(":")[0]
        await query.edit_message_text(f"❌ อนุมัติไม่สำเร็จ: {_emap.get(_ek, result.error or 'unknown')}")
        return

    try:
        from sqlalchemy import update as _sa_upd
        from shared.models import TeaserClick as _TC
        async with get_session() as _s2:
            await _s2.execute(_sa_upd(_TC).where(_TC.user_id == _cust_tg, _TC.converted == False).values(converted=True))
            await _s2.commit()
    except Exception:
        pass

    _dm_txt = "📩 ส่งลิงก์ให้ลูกค้าแล้ว" if result.customer_dm_sent else "⚠️ ส่ง DM ไม่สำเร็จ (ระบบ alert ห้องแอดมินแล้ว)"
    _cap = (
        f"✅ <b>อนุมัติ Payment #{payment_id}</b>\n"
        f"📦 แพ็กเกจ: {_pkg_name}\n"
        f"💰 จำนวน: {format_thb(_amt)}\n"
        f"👤 อนุมัติโดย: {query.from_user.first_name}\n"
        f"{_dm_txt}"
    )
    try:
        await query.edit_message_caption(caption=_cap, parse_mode="HTML")
    except Exception:
        try:
            await query.edit_message_text(_cap, parse_mode="HTML")
        except Exception:
            pass

    try:
        from sheets.daily_revenue import DailyRevenueSheet
        from sheets.income_log import IncomeLogSheet
        await DailyRevenueSheet.update()
        await IncomeLogSheet.log_payment(payment_id, approved_by=query.from_user.first_name or "Admin")
    except Exception as exc:
        logger.warning("Sheets sync failed #%d: %s", payment_id, exc)

    try:
        import telegram as _tg_ref, os as _os_ref
        _sb = _tg_ref.Bot(token=_os_ref.environ.get("SALES_BOT_TOKEN", ""))
        await _sb.initialize()
        from bots.sales_bot.handlers.referral import process_referral_reward
        await process_referral_reward(_cust_tg, _sb)
    except Exception as exc:
        logger.warning("Referral reward failed #%d: %s", payment_id, exc)

    logger.info("[ADMIN_BOT][APPROVE_PAYMENT via canonical] pid=%d amount=%s by=%s",
                payment_id, _amt, query.from_user.id)



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

