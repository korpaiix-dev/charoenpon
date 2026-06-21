"""Slip Review — admin manually approves/rejects slips that fell through
auto-verification.

Triggered when Slip2Go confirms a real slip but receiver doesn't match our
pool, AND Layer 2 (Gemini Vision) also fails to confirm. Instead of hard
rejecting the customer, we:

1. Insert payment with status=PENDING + reject_reason='REVIEW_NEEDED'
2. DM customer: "กำลังตรวจสอบสลิป — แอดมินจะตอบกลับใน 5-10 นาที"
3. Admin alert with inline buttons [Approve] [Reject]
4. Approve → apply_payment_approval() handles everything
   Reject → mark + DM customer

Callback pattern: ^slipReview:(approve|reject):(payment_id)$

REFACTOR 2026-06-18: Approval routes through single canonical service
shared.payment_approval.apply_payment_approval — fixes wrong-bot DM,
missing audit logs, missing receiver record, etc.
"""
from __future__ import annotations

import html
import logging
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select, text as _t
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CallbackQueryHandler

from shared.database import get_session
from shared.models import Payment, PaymentStatus, User

logger = logging.getLogger(__name__)

REVIEW_MARKER = "REVIEW_NEEDED"


def build_admin_review_buttons(
    payment_id: int,
    telegram_id: int | None = None,
    ban_reason: str = "scam_via_review",
) -> InlineKeyboardMarkup:
    rows = [[
        InlineKeyboardButton("✅ Approve", callback_data=f"slipReview:approve:{payment_id}"),
        InlineKeyboardButton("❌ Reject",  callback_data=f"slipReview:reject:{payment_id}"),
    ]]
    if telegram_id:
        rows.append([InlineKeyboardButton(
            "🔨 แบนถาวร (ครอบทุกระบบ)",
            callback_data=f"ban_user:{telegram_id}:{ban_reason[:40]}",
        )])
    return InlineKeyboardMarkup(rows)


async def insert_pending_review_payment(
    user_db_id: int,
    package_id: int,
    amount: float,
    slip_trans_ref: str | None,
    sender_name: str | None,
    sender_bank_name: str | None,
    sender_bank_account: str | None,
) -> int:
    """Insert payment with PENDING status + REVIEW marker. Returns payment.id."""
    async with get_session() as s:
        r = await s.execute(_t("""
            INSERT INTO payments (
                user_id, package_id, amount, method, status, reject_reason,
                slip_trans_ref, sender_name, sender_bank_name, sender_bank_account,
                created_at
            ) VALUES (
                :uid, :pid, :amt, 'SLIP', 'PENDING', :marker,
                :tref, :sn, :sbn, :sba,
                NOW()
            )
            RETURNING id
        """), {
            "uid": user_db_id,
            "pid": package_id,
            "amt": amount,
            "marker": REVIEW_MARKER,
            "tref": (slip_trans_ref or "")[:64] or None,
            "sn": (sender_name or "")[:255] or None,
            "sbn": (sender_bank_name or "")[:64] or None,
            "sba": (sender_bank_account or "")[:64] or None,
        })
        pid = r.scalar()
        await s.commit()
    return int(pid)


async def _load_payment_and_user(payment_id: int):
    """Return (payment_obj, user_telegram_id) or (None, None) if not found."""
    async with get_session() as s:
        rp = await s.execute(select(Payment).where(Payment.id == payment_id))
        p = rp.scalar_one_or_none()
        if not p:
            return None, None
        ru = await s.execute(select(User).where(User.id == p.user_id))
        u = ru.scalar_one_or_none()
        if not u:
            return p, None
        return p, u.telegram_id


async def cb_slip_review_approve(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin clicked Approve.

    Delegates entirely to apply_payment_approval():
      - flips Payment.status PENDING → CONFIRMED
      - creates Subscription
      - records receiver cumulative
      - generates invite links via Guardian
      - DMs customer via SALES bot
      - logs audit
      - alerts admin on partial fail
    """
    q = update.callback_query
    if not q:
        return
    try:
        await q.answer("Approving...")
    except Exception:
        pass

    parts = (q.data or "").split(":")
    if len(parts) != 3:
        return
    try:
        payment_id = int(parts[2])
    except ValueError:
        return

    payment, tg_id = await _load_payment_and_user(payment_id)
    if not payment:
        try:
            await q.edit_message_caption(caption="⚠️ Payment not found", reply_markup=None)
        except Exception:
            try: await q.edit_message_text("⚠️ Payment not found")
            except Exception: pass
        return

    if payment.status != PaymentStatus.PENDING:
        try:
            await q.answer(f"Already {payment.status.value}", show_alert=True)
        except Exception:
            pass
        return

    from shared.payment_approval import (
        apply_payment_approval, ApprovalInput, ApprovalSource,
    )

    actor_id = q.from_user.id if q.from_user else None

    try:
        result = await apply_payment_approval(ApprovalInput(
            user_id=payment.user_id,
            telegram_id=tg_id or 0,
            source=ApprovalSource.SLIP_REVIEW,
            amount_paid=Decimal(str(payment.amount)),
            explicit_package_id=payment.package_id,
            admin_id=actor_id,
            payment_id=payment_id,
            slip_trans_ref=payment.slip_trans_ref,
            slip_hash=getattr(payment, "slip_hash", None),
            sender_name=payment.sender_name,
            sender_bank_name=payment.sender_bank_name,
            sender_bank_account=payment.sender_bank_account,
            slip_file_id=getattr(payment, "slip_file_id", None),
            method="SLIP",
            skip_dup_check=True,           # this row IS the existing one
            skip_sender_ring=True,         # admin already reviewed it
        ))
    except Exception as exc:
        logger.exception("slip_review approve crashed: %s", exc)
        try:
            await q.answer(f"❌ Crash: {str(exc)[:50]}", show_alert=True)
        except Exception:
            pass
        return

    if not result.success:
        logger.error("slip_review apply returned: %s (%s)",
                     result.error, result.error_details)
        try:
            await q.answer(f"❌ {result.error}", show_alert=True)
        except Exception:
            pass
        return

    # Update admin alert message
    actor = q.from_user.username or q.from_user.first_name or "admin"
    actor_safe = html.escape(str(actor))
    bonus_note = f" (โบนัส +{result.bonus_days} วัน)" if result.bonus_days else ""
    dm_note = "" if result.customer_dm_sent else " <i>(ส่งข้อความหาลูกค้าไม่ได้)</i>"
    new_marker = (
        f"\n\n✅ <b>อนุมัติแล้ว</b> โดย @{actor_safe}{bonus_note}"
        f"\n\U0001f517 {len(result.invite_links)} links{dm_note}"
    )
    try:
        if q.message and q.message.caption is not None:
            await q.edit_message_caption(
                caption=(q.message.caption or "") + new_marker,
                parse_mode="HTML", reply_markup=None,
            )
        elif q.message and q.message.text is not None:
            await q.edit_message_text(
                text=(q.message.text or "") + new_marker,
                parse_mode="HTML", reply_markup=None,
            )
    except Exception as exc:
        logger.warning("slip_review approve edit alert failed: %s", exc)


async def cb_slip_review_reject(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin clicked Reject."""
    q = update.callback_query
    if not q:
        return
    try:
        await q.answer("Rejecting...")
    except Exception:
        pass

    parts = (q.data or "").split(":")
    if len(parts) != 3:
        return
    try:
        payment_id = int(parts[2])
    except ValueError:
        return

    payment, tg_id = await _load_payment_and_user(payment_id)
    if not payment:
        try:
            await q.edit_message_caption(caption="⚠️ Payment not found", reply_markup=None)
        except Exception:
            try: await q.edit_message_text("⚠️ Payment not found")
            except Exception: pass
        return

    if payment.status != PaymentStatus.PENDING:
        try:
            await q.answer(f"Already {payment.status.value}", show_alert=True)
        except Exception:
            pass
        return

    # Mark rejected
    async with get_session() as s:
        await s.execute(_t("""
            UPDATE payments SET
                status = 'REJECTED',
                reject_reason = COALESCE(reject_reason, '') || ' | admin_rejected',
                verified_at = NOW(),
                verified_by = :admin
            WHERE id = :pid
        """), {"pid": payment_id, "admin": (q.from_user.id if q.from_user else None)})
        await s.commit()

    # Audit log
    try:
        from shared.utils import log_admin_action
        actor = q.from_user.username or q.from_user.first_name or "admin"
        await log_admin_action(
            admin_id=q.from_user.id if q.from_user else 0,
            action="slip_review_rejected",
            target_type="payment",
            target_id=payment_id,
            details=f"by @{actor} tg={tg_id}",
        )
    except Exception as exc:
        logger.warning("slip_review reject audit log failed: %s", exc)

    # DM customer politely via SALES bot (NOT admin bot)
    if tg_id:
        try:
            from shared.customer_dm import send_to_customer
            text = (
                "❌ <b>ไม่สามารถยืนยันสลิปนี้ได้ค่ะ</b> \U0001f64f\n\n"
                "อาจเป็นเพราะ:\n"
                "• สลิปไม่ชัด หรือไม่สมบูรณ์\n"
                "• ยอดโอนไม่ตรงกับแพ็กเกจ\n"
                "• โอนผิดบัญชี/ผิดธนาคาร\n\n"
                "หากมั่นใจว่าโอนถูก กรุณาทักแอดมินที่ /support เพื่อช่วยตรวจสอบนะคะ"
            )
            await send_to_customer(telegram_id=tg_id, text=text)
        except Exception as exc:
            logger.warning("slip_review reject DM failed: %s", exc)

    # Update admin alert message
    actor = q.from_user.username or q.from_user.first_name or "admin"
    actor_safe = html.escape(str(actor))
    new_marker = f"\n\n❌ <b>ปฏิเสธ</b> โดย @{actor_safe}"
    try:
        if q.message and q.message.caption is not None:
            await q.edit_message_caption(
                caption=(q.message.caption or "") + new_marker,
                parse_mode="HTML", reply_markup=None,
            )
        elif q.message and q.message.text is not None:
            await q.edit_message_text(
                text=(q.message.text or "") + new_marker,
                parse_mode="HTML", reply_markup=None,
            )
    except Exception as exc:
        logger.warning("slip_review reject edit alert failed: %s", exc)


def get_slip_review_handlers() -> list:
    return [
        CallbackQueryHandler(cb_slip_review_approve, pattern=r"^slipReview:approve:\d+$"),
        CallbackQueryHandler(cb_slip_review_reject,  pattern=r"^slipReview:reject:\d+$"),
    ]


__all__ = [
    "build_admin_review_buttons",
    "insert_pending_review_payment",
    "cb_slip_review_approve",
    "cb_slip_review_reject",
    "get_slip_review_handlers",
    "REVIEW_MARKER",
]
