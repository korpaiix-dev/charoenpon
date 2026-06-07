"""Referral System - ชวนเพื่อน VIP เจริญพร.

ระบบ:
- เฉพาะ VIP Active ชวนได้
- เพื่อนต้องจ่ายเงินจริง (CONFIRMED) ถึงนับ
- ชวน 1 คน = +7 วัน, ชวน 5 คน = +30 วัน (bonus)
- จำกัด 10 referrals/เดือน, ชวนตัวเองไม่ได้
- Referral Reminder DM: ทุก 3 วัน ส่ง DM VIP ที่ยังไม่เคยชวนเพื่อน
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
import string
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, func, text
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import Forbidden
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

from shared.database import get_session
from shared.models import (
    Subscription,
    SubscriptionStatus,
    User,
)

logger = logging.getLogger(__name__)

from shared.tz import TH_TZ
MONTHLY_REFERRAL_LIMIT = 10
REWARD_PER_REFERRAL = 7  # days
MILESTONE_5_BONUS = 30  # days

ADMIN_GROUP_ID = int(os.environ.get("ADMIN_GROUP_CHAT_ID", ""))
MAX_REMINDER_PER_DAY = 20
REMINDER_DELAY_SECONDS = 3

# ─── DB Migration ────────────────────────────────────────────────────────────

REMINDER_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS referral_reminder_log (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    telegram_id BIGINT NOT NULL,
    sent_at TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_ref_reminder_user_id ON referral_reminder_log(user_id);
"""


async def _ensure_reminder_table() -> None:
    """Create referral_reminder_log table if not exists."""
    try:
        async with get_session() as session:
            await session.execute(text(REMINDER_TABLE_SQL))
            await session.commit()
    except Exception as exc:
        logger.warning("referral_reminder_log migration (may already exist): %s", exc)


# ─── Helpers ──────────────────────────────────────────────────────────────────

async def _is_vip_active(telegram_id: int) -> bool:
    """Check if user has an active VIP subscription."""
    async with get_session() as session:
        result = await session.execute(
            select(Subscription)
            .join(User, Subscription.user_id == User.id)
            .where(
                User.telegram_id == telegram_id,
                Subscription.status == SubscriptionStatus.ACTIVE,
                Subscription.end_date > datetime.utcnow(),
            )
        )
        return result.scalar_one_or_none() is not None


async def _get_or_create_referral_code(user_id: int, telegram_id: int) -> str:
    """Get existing referral code or create new one."""
    async with get_session() as session:
        # Check existing code
        result = await session.execute(
            text("SELECT referral_code FROM referrals WHERE referrer_user_id = :uid AND referred_user_id IS NULL AND status = 'PENDING' LIMIT 1"),
            {"uid": user_id},
        )
        row = result.fetchone()

        # Also check user table for referral_code
        user_result = await session.execute(
            select(User).where(User.id == user_id)
        )
        db_user = user_result.scalar_one_or_none()

        if db_user and db_user.referral_code:
            return db_user.referral_code

        # Generate new code
        code = "REF_" + "".join(random.choices(string.ascii_uppercase + string.digits, k=6))

        # Save to user table
        if db_user:
            db_user.referral_code = code

        await session.flush()
        return code


async def _get_referral_stats(user_id: int) -> dict:
    """Get referral statistics for a user."""
    async with get_session() as session:
        # Completed referrals (all time)
        completed = await session.execute(
            text("SELECT COUNT(*) FROM referrals WHERE referrer_user_id = :uid AND status IN ('COMPLETED', 'REWARDED')"),
            {"uid": user_id},
        )
        completed_count = completed.scalar() or 0

        # Total reward days
        reward_result = await session.execute(
            text("SELECT COALESCE(SUM(reward_days), 0) FROM referrals WHERE referrer_user_id = :uid AND status IN ('COMPLETED', 'REWARDED')"),
            {"uid": user_id},
        )
        total_reward_days = reward_result.scalar() or 0

        # This month's referrals
        now = datetime.utcnow()
        first_of_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        monthly = await session.execute(
            text("SELECT COUNT(*) FROM referrals WHERE referrer_user_id = :uid AND created_at >= :start"),
            {"uid": user_id, "start": first_of_month},
        )
        monthly_count = monthly.scalar() or 0

        # Pending referrals
        pending = await session.execute(
            text("SELECT COUNT(*) FROM referrals WHERE referrer_user_id = :uid AND status = 'PENDING' AND referred_user_id IS NOT NULL"),
            {"uid": user_id},
        )
        pending_count = pending.scalar() or 0

        return {
            "completed": completed_count,
            "total_reward_days": total_reward_days,
            "monthly_count": monthly_count,
            "remaining_monthly": max(0, MONTHLY_REFERRAL_LIMIT - monthly_count),
            "pending": pending_count,
        }


async def _get_user_by_telegram_id(telegram_id: int) -> tuple[int, str | None] | None:
    """Get user id and username by telegram_id."""
    async with get_session() as session:
        result = await session.execute(
            select(User).where(User.telegram_id == telegram_id)
        )
        user = result.scalar_one_or_none()
        if user:
            return user.id, user.username
        return None


# ─── Referral Analytics ──────────────────────────────────────────────────────

async def analyze_referral_performance() -> dict:
    """วิเคราะห์ performance ของ Referral System.

    Returns dict:
    {
        "total_codes": 10,
        "total_referred": 5,
        "total_completed": 3,
        "conversion_rate": 0.6,
        "total_reward_days": 21,
        "top_referrers": [{"user_id": 1, "telegram_id": 123, "count": 3}, ...],
    }
    """
    async with get_session() as session:
        # Total unique referrers who have codes
        codes_result = await session.execute(
            text("SELECT COUNT(DISTINCT referrer_user_id) FROM referrals")
        )
        total_codes = codes_result.scalar() or 0

        # Total referred (have referred_user_id)
        referred_result = await session.execute(
            text("SELECT COUNT(*) FROM referrals WHERE referred_user_id IS NOT NULL")
        )
        total_referred = referred_result.scalar() or 0

        # Total completed
        completed_result = await session.execute(
            text("SELECT COUNT(*) FROM referrals WHERE status IN ('COMPLETED', 'REWARDED')")
        )
        total_completed = completed_result.scalar() or 0

        # Total reward days
        reward_result = await session.execute(
            text("SELECT COALESCE(SUM(reward_days), 0) FROM referrals WHERE status IN ('COMPLETED', 'REWARDED')")
        )
        total_reward_days = reward_result.scalar() or 0

        # Top referrers
        top_result = await session.execute(
            text("""
                SELECT r.referrer_user_id, r.referrer_telegram_id, COUNT(*) as cnt
                FROM referrals r
                WHERE r.status IN ('COMPLETED', 'REWARDED')
                GROUP BY r.referrer_user_id, r.referrer_telegram_id
                ORDER BY cnt DESC
                LIMIT 5
            """)
        )
        top_rows = top_result.fetchall()

    conversion_rate = total_completed / total_referred if total_referred > 0 else 0

    return {
        "total_codes": total_codes,
        "total_referred": total_referred,
        "total_completed": total_completed,
        "conversion_rate": round(conversion_rate, 4),
        "total_reward_days": total_reward_days,
        "top_referrers": [
            {"user_id": r.referrer_user_id, "telegram_id": r.referrer_telegram_id, "count": r.cnt}
            for r in top_rows
        ],
    }


# ─── Referral Reminder DM ───────────────────────────────────────────────────

async def send_referral_reminder(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Scheduled job: ส่ง DM เตือน VIP ที่ยังไม่เคยชวนเพื่อน.

    - ทุก 3 วัน
    - ไม่ส่งซ้ำ (check referral_reminder_log: sent_at ใน 3 วันที่ผ่านมา)
    - Rate limit: 20 DM/วัน, delay 3 วินาที
    """
    now_th = datetime.now(TH_TZ)
    logger.info("🔄 Referral reminder job started at %s", now_th.strftime("%Y-%m-%d %H:%M"))

    await _ensure_reminder_table()

    cutoff_3d = datetime.utcnow() - timedelta(days=3)

    async with get_session() as session:
        # VIP Active ที่ยังไม่เคยชวนเพื่อน (ไม่มี record ใน referrals)
        # + ไม่เคยส่ง reminder ใน 3 วันที่ผ่านมา
        result = await session.execute(
            text("""
                SELECT u.id, u.telegram_id, u.first_name, u.username
                FROM users u
                JOIN subscriptions s ON s.user_id = u.id
                WHERE s.status = 'ACTIVE'
                  AND s.end_date > NOW()
                  AND u.is_banned = false
                  AND u.id NOT IN (
                      SELECT DISTINCT referrer_user_id FROM referrals
                      WHERE status IN ('COMPLETED', 'REWARDED')
                  )
                  AND u.id NOT IN (
                      SELECT user_id FROM referral_reminder_log
                      WHERE sent_at > :cutoff
                  )
                ORDER BY s.start_date ASC
                LIMIT :lim
            """),
            {"cutoff": cutoff_3d, "lim": MAX_REMINDER_PER_DAY},
        )
        rows = result.fetchall()

    if not rows:
        logger.info("No VIP users to send referral reminder")
        return

    sales_token = os.environ.get("SALES_BOT_TOKEN", "")
    if not sales_token:
        logger.error("SALES_BOT_TOKEN not set, cannot send referral reminders")
        return

    bot = Bot(token=sales_token)
    await bot.initialize()

    sent = 0
    failed = 0

    for row in rows:
        first_name = row.first_name or row.username or "คุณ"
        msg = (
            f"รู้มั้ย {first_name}? ชวนเพื่อน 1 คน = ได้ VIP ฟรี 7 วัน! 🎁\n"
            f"\n"
            f"ชวนครบ 5 คน = ได้ VIP ฟรี 30 วัน!\n"
            f"\n"
            f"กด /invite เพื่อรับลิงก์ชวนเพื่อนได้เลยค่ะ 🔗"
        )
        try:
            await bot.send_message(
                chat_id=row.telegram_id,
                text=msg,
                parse_mode="HTML",
            )
            # Log reminder
            async with get_session() as session:
                await session.execute(
                    text("INSERT INTO referral_reminder_log (user_id, telegram_id) VALUES (:uid, :tgid)"),
                    {"uid": row.id, "tgid": row.telegram_id},
                )
                await session.commit()
            sent += 1
        except Forbidden:
            logger.info("Cannot DM user %d for referral reminder — blocked", row.telegram_id)
            failed += 1
        except Exception as exc:
            logger.error("Failed to send referral reminder to %d: %s", row.telegram_id, exc)
            failed += 1

        await asyncio.sleep(REMINDER_DELAY_SECONDS)

    logger.info("Referral reminder sent: %d / failed: %d", sent, failed)

    # Admin notification
    if sent > 0:
        admin_token = os.environ.get("ADMIN_BOT_TOKEN", "")
        if admin_token:
            try:
                admin_bot = Bot(token=admin_token)
                await admin_bot.initialize()
                await admin_bot.send_message(
                    chat_id=ADMIN_GROUP_ID,
                    text=(
                        f"🎁 <b>รายงานชวนเพื่อน</b>\n\n"
                        f"ส่ง DM เตือนชวนเพื่อน: <b>{sent}</b> คน\n"
                        f"ส่งไม่ได้: {failed} คน"
                    ),
                    parse_mode="HTML",
                )
            except Exception as exc:
                logger.error("Failed to send referral reminder admin notification: %s", exc)


# ─── /invite Command ─────────────────────────────────────────────────────────

async def invite_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/invite - สร้างลิงก์ชวนเพื่อน."""
    if not update.effective_user or not update.message:
        return

    tg_user = update.effective_user

    # Check VIP Active
    if not await _is_vip_active(tg_user.id):
        await update.message.reply_text(
            "❌ สมัคร VIP ก่อนถึงจะชวนเพื่อนได้ค่ะ\n\n"
            '👉 <a href="tg://resolve?domain=NamwarnJarern_bot&start=packages">📦 ดูแพ็กเกจ VIP เจริญพร</a>',
            parse_mode="HTML",
        )
        return

    # Get user_id
    user_info = await _get_user_by_telegram_id(tg_user.id)
    if not user_info:
        await update.message.reply_text("❌ ไม่พบข้อมูลของคุณในระบบค่ะ กรุณา /start ก่อนนะ")
        return

    user_id, _ = user_info

    # Get or create referral code
    code = await _get_or_create_referral_code(user_id, tg_user.id)

    # Get stats
    stats = await _get_referral_stats(user_id)

    # Calculate next reward
    completed = stats["completed"]
    if completed < 5:
        remaining_to_5 = 5 - completed
        next_reward_text = f"🎯 ชวนอีก {remaining_to_5} คน = ได้ VIP ฟรี 30 วัน!"
    else:
        next_referral_reward = REWARD_PER_REFERRAL
        next_reward_text = f"🎯 ชวนอีก 1 คน = ได้ VIP ฟรี {next_referral_reward} วัน!"

    remaining_text = f"📋 โควตาเดือนนี้: เหลือ {stats['remaining_monthly']}/{MONTHLY_REFERRAL_LIMIT} คน"

    ref_link = f"https://t.me/NamwarnJarern_bot?start=ref_{code}"
    text = (
        "🎁 <b>ชวนเพื่อนมา VIP เจริญพร!</b>\n\n"
        "ชวน 1 คน = ได้ VIP ฟรี 7 วัน\n"
        "ชวน 5 คน = ได้ VIP ฟรี 30 วัน!\n\n"
        "ลิงก์ชวนเพื่อนของคุณ:\n"
        f'👉 <a href="{ref_link}">🔗 กดส่งลิงก์ให้เพื่อน</a>\n\n'
        "📋 <b>ข้อความชวนเพื่อน (กดคัดลอกส่งได้เลย):</b>\n"
        f"<code>มา VIP เจริญพร กัน! คลิปเต็มไม่เบลอ 10,000+ คลิป สมัครที่ {ref_link}</code>\n\n"
        "📊 <b>สถิติของคุณ:</b>\n"
        f"ชวนสำเร็จ: {completed} คน | ได้ฟรี: {stats['total_reward_days']} วัน\n"
    )

    if stats["pending"] > 0:
        text += f"⏳ รอเพื่อนสมัคร: {stats['pending']} คน\n"

    text += f"\n{next_reward_text}\n{remaining_text}"

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📋 ดูรายละเอียดชวนเพื่อน", callback_data="my_referrals")],
        [InlineKeyboardButton("🔙 กลับเมนูหลัก", callback_data="back_main")],
    ])

    await update.message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)


# ─── /myreferrals Command ────────────────────────────────────────────────────

async def myreferrals_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/myreferrals - แสดงรายละเอียด referral ทั้งหมด."""
    if not update.effective_user:
        return

    msg = update.message or (update.callback_query.message if update.callback_query else None)
    if not msg:
        return

    tg_user = update.effective_user
    user_info = await _get_user_by_telegram_id(tg_user.id)
    if not user_info:
        if update.callback_query:
            await update.callback_query.answer("❌ ไม่พบข้อมูลในระบบ", show_alert=True)
        else:
            await msg.reply_text("❌ ไม่พบข้อมูลของคุณในระบบค่ะ")
        return

    user_id, _ = user_info

    async with get_session() as session:
        result = await session.execute(
            text("""
                SELECT r.referral_code, r.referred_telegram_id, r.status, r.reward_days, r.created_at, r.completed_at,
                       u.username, u.first_name
                FROM referrals r
                LEFT JOIN users u ON r.referred_user_id = u.id
                WHERE r.referrer_user_id = :uid
                ORDER BY r.created_at DESC
                LIMIT 20
            """),
            {"uid": user_id},
        )
        rows = result.fetchall()

    if not rows:
        text = (
            "📋 <b>รายการชวนเพื่อน</b>\n\n"
            "ยังไม่มีรายการค่ะ\n\n"
            "กด /invite เพื่อรับลิงก์ชวนเพื่อนได้เลย 🎁"
        )
    else:
        text = "📋 <b>รายการชวนเพื่อนของคุณ</b>\n\n"
        for row in rows:
            status_emoji = {"PENDING": "⏳", "COMPLETED": "✅", "REWARDED": "🎁"}.get(row.status, "❓")
            name = f"@{row.username}" if row.username else (row.first_name or "ลูกค้า")

            if row.referred_telegram_id:
                text += f"{status_emoji} {name}"
                if row.status in ("COMPLETED", "REWARDED"):
                    text += f" (+{row.reward_days} วัน)"
                elif row.status == "PENDING":
                    text += " (รอสมัคร)"
                text += "\n"
            else:
                text += f"⏳ รอเพื่อนกดลิงก์\n"

        stats = await _get_referral_stats(user_id)
        text += f"\n📊 รวม: สำเร็จ {stats['completed']} คน | ได้ฟรี {stats['total_reward_days']} วัน"

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🎁 รับลิงก์ชวนเพื่อน", callback_data="get_invite_link")],
        [InlineKeyboardButton("🔙 กลับเมนูหลัก", callback_data="back_main")],
    ])

    if update.callback_query:
        await update.callback_query.answer()
        try:
            await update.callback_query.edit_message_text(text, parse_mode="HTML", reply_markup=keyboard)
        except Exception:
            await msg.reply_text(text, parse_mode="HTML", reply_markup=keyboard)
    else:
        await msg.reply_text(text, parse_mode="HTML", reply_markup=keyboard)


async def _myreferrals_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Callback wrapper for my_referrals button."""
    await myreferrals_command(update, context)


async def _get_invite_link_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Callback wrapper to invoke /invite."""
    query = update.callback_query
    if not query:
        return
    await query.answer()

    tg_user = update.effective_user
    if not tg_user:
        return

    # Check VIP Active
    if not await _is_vip_active(tg_user.id):
        await query.edit_message_text(
            "❌ สมัคร VIP ก่อนถึงจะชวนเพื่อนได้ค่ะ\n\n"
            '👉 <a href="tg://resolve?domain=NamwarnJarern_bot&start=packages">📦 ดูแพ็กเกจ VIP เจริญพร</a>',
            parse_mode="HTML",
        )
        return

    user_info = await _get_user_by_telegram_id(tg_user.id)
    if not user_info:
        await query.answer("❌ ไม่พบข้อมูลในระบบ", show_alert=True)
        return

    user_id, _ = user_info
    code = await _get_or_create_referral_code(user_id, tg_user.id)
    stats = await _get_referral_stats(user_id)

    completed = stats["completed"]
    earned_days = stats.get("total_reward_days", 0)
    remaining_to_3 = max(0, 3 - completed)

    if remaining_to_3 > 0:
        next_milestone = f"🎯 ชวนอีก <b>{remaining_to_3} คน</b> = รับ VIP 30 วัน ฟรี! (มูลค่า ฿300)"
    else:
        next_milestone = "🎉 ครบ 3 คนแล้ว! ทุกคนต่อจากนี้ = +7 วัน VIP ฟรี"

    ref_link = f"https://t.me/NamwarnJarern_bot?start=ref_{code}"
    text = (
        "🎁 <b>ชวนเพื่อน รับ ฿100</b> (= +7 วัน VIP ฟรี)\n\n"
        "💎 ชวน 1 คน = +7 วัน VIP ฟรี\n"
        "👑 ครบ 3 คน = รับ VIP 30 วัน ฟรี! (โบนัส)\n"
        "🏆 ครบ 5 คน = รับ VIP 30 วัน ฟรี (อีกครั้ง!)\n\n"
        "📊 <b>สถิติของคุณ:</b>\n"
        f"   ✅ ชวนสำเร็จ: <b>{completed} คน</b>\n"
        f"   🎁 ได้ฟรี: <b>{earned_days} วัน</b>\n\n"
        f"{next_milestone}\n\n"
        "🔗 <b>ลิงก์ชวนเพื่อนของคุณ:</b>\n"
        f"<code>{ref_link}</code>\n\n"
        "📋 <b>ข้อความตัวอย่าง (กดคัดลอกส่งได้เลย):</b>\n"
        f"<code>มา VIP เจริญพร กัน! คลิปเต็มไม่เบลอ 10,000+ คลิป สมัครที่ {ref_link}</code>"
    )

    # Single share CTA + back — no sub-menu needed
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(
            "📨 ส่งลิงก์ให้เพื่อนเลย",
            url=f"https://t.me/share/url?url={ref_link}&text=มา%20VIP%20เจริญพร%20กัน!%20คลิปเต็มไม่เบลอ%2010%2C000%2B%20คลิป",
        )],
        [InlineKeyboardButton("🔙 กลับเมนูหลัก", callback_data="back_main")],
    ])

    # REFERRAL_V3_IMG — send photo with caption (no raw URL exposure)
    try:
        from bots.sales_bot.handlers.social_proof import pick_campaign_image
        img_path = pick_campaign_image("referral")
    except Exception:
        img_path = None
    try:
        # Delete prev text msg so we can send photo
        try:
            await query.message.delete()
        except Exception:
            pass
        if img_path and img_path.exists():
            with open(img_path, "rb") as f:
                await query.message.chat.send_photo(
                    photo=f, caption=text, parse_mode="HTML", reply_markup=keyboard,
                )
        else:
            await query.message.chat.send_message(
                text, parse_mode="HTML", reply_markup=keyboard, disable_web_page_preview=True,
            )
    except Exception:
        try:
            await query.message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)
        except Exception:
            pass


# ─── Deep Link Handler ────────────────────────────────────────────────────────

async def handle_referral_start(update: Update, context: ContextTypes.DEFAULT_TYPE, ref_code: str) -> bool:
    """Handle /start ref_{CODE} deep link. Returns True if handled."""
    if not update.effective_user or not update.message:
        return False

    tg_user = update.effective_user

    async with get_session() as session:
        # Find referrer by code
        referrer_result = await session.execute(
            select(User).where(User.referral_code == ref_code)
        )
        referrer = referrer_result.scalar_one_or_none()

        if not referrer:
            logger.warning("Invalid referral code: %s", ref_code)
            return False  # Let normal /start handle it

        # Self-referral check
        if referrer.telegram_id == tg_user.id:
            await update.message.reply_text("❌ ไม่สามารถชวนตัวเองได้ค่ะ 😅")
            return False  # Still show normal menu

        # Check if this user was already referred
        existing = await session.execute(
            text("SELECT id FROM referrals WHERE referred_telegram_id = :tg_id"),
            {"tg_id": tg_user.id},
        )
        if existing.fetchone():
            logger.info("User %d already referred, skipping", tg_user.id)
            return False  # Already referred, show normal menu

        # Check monthly limit for referrer
        now = datetime.utcnow()
        first_of_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        monthly_result = await session.execute(
            text("SELECT COUNT(*) FROM referrals WHERE referrer_user_id = :uid AND created_at >= :start"),
            {"uid": referrer.id, "start": first_of_month},
        )
        monthly_count = monthly_result.scalar() or 0
        if monthly_count >= MONTHLY_REFERRAL_LIMIT:
            logger.info("Referrer %d hit monthly limit", referrer.id)
            return False  # Show normal menu

        # Get or create referred user
        referred_user_result = await session.execute(
            select(User).where(User.telegram_id == tg_user.id)
        )
        referred_user = referred_user_result.scalar_one_or_none()
        if not referred_user:
            referred_user = User(
                telegram_id=tg_user.id,
                username=tg_user.username,
                first_name=tg_user.first_name,
                last_name=tg_user.last_name,
            )
            session.add(referred_user)
            await session.flush()

        # Create referral record
        await session.execute(
            text("""
                INSERT INTO referrals (referrer_user_id, referrer_telegram_id, referral_code,
                                       referred_user_id, referred_telegram_id, status)
                VALUES (:referrer_uid, :referrer_tgid, :code, :referred_uid, :referred_tgid, 'PENDING')
            """),
            {
                "referrer_uid": referrer.id,
                "referrer_tgid": referrer.telegram_id,
                "code": ref_code,
                "referred_uid": referred_user.id,
                "referred_tgid": tg_user.id,
            },
        )
        await session.commit()

        logger.info(
            "Referral recorded: referrer=%d referred=%d code=%s",
            referrer.telegram_id, tg_user.id, ref_code,
        )

    return False  # Show normal /start menu (packages)


# ─── Reward Processing (called after payment CONFIRMED) ─────────────────────

async def process_referral_reward(referred_telegram_id: int, bot) -> None:
    """Process referral reward when a referred user's payment is confirmed.

    Called from payment.py and approval.py after payment status = CONFIRMED.
    """
    async with get_session() as session:
        # Find pending referral for this user
        ref_result = await session.execute(
            text("""
                SELECT r.id, r.referrer_user_id, r.referrer_telegram_id, r.referral_code
                FROM referrals r
                WHERE r.referred_telegram_id = :tg_id AND r.status = 'PENDING'
                LIMIT 1
            """),
            {"tg_id": referred_telegram_id},
        )
        ref_row = ref_result.fetchone()

        if not ref_row:
            return  # No referral pending

        ref_id = ref_row.id
        referrer_user_id = ref_row.referrer_user_id
        referrer_telegram_id = ref_row.referrer_telegram_id

        # Update referral status
        await session.execute(
            text("""
                UPDATE referrals
                SET status = 'COMPLETED', reward_days = :days, completed_at = NOW()
                WHERE id = :ref_id
            """),
            {"days": REWARD_PER_REFERRAL, "ref_id": ref_id},
        )

        # Count total completed referrals for milestone check
        count_result = await session.execute(
            text("SELECT COUNT(*) FROM referrals WHERE referrer_user_id = :uid AND status IN ('COMPLETED', 'REWARDED')"),
            {"uid": referrer_user_id},
        )
        total_completed = count_result.scalar() or 0

        # Extend subscription for referrer
        reward_days = REWARD_PER_REFERRAL
        bonus_days = 0

        # Check milestone: exactly 5 completed = +30 bonus
        if total_completed == 5:
            bonus_days = MILESTONE_5_BONUS

        total_days = reward_days + bonus_days

        # Extend active subscription
        sub_result = await session.execute(
            select(Subscription).join(User, Subscription.user_id == User.id).where(
                User.id == referrer_user_id,
                Subscription.status == SubscriptionStatus.ACTIVE,
            ).order_by(Subscription.end_date.desc())
        )
        active_sub = sub_result.scalar_one_or_none()

        if active_sub:
            active_sub.end_date = active_sub.end_date + timedelta(days=total_days)
            logger.info(
                "Extended subscription for user_id=%d by %d days (referral reward)",
                referrer_user_id, total_days,
            )

        # Mark as REWARDED
        await session.execute(
            text("UPDATE referrals SET status = 'REWARDED', reward_days = :days WHERE id = :ref_id"),
            {"days": total_days, "ref_id": ref_id},
        )

        await session.commit()

    # DM referrer
    try:
        reward_text = f"🎉 <b>เพื่อนคุณสมัคร VIP เจริญพร แล้ว!</b>\n\nได้ VIP ฟรี <b>{reward_days} วัน</b>"

        if bonus_days > 0:
            reward_text += f"\n\n🏆 <b>โบนัส!</b> ชวนครบ 5 คน ได้เพิ่มอีก <b>{bonus_days} วัน!</b>\nรวมรอบนี้ได้ <b>{total_days} วัน</b>"

        reward_text += f"\n\n📊 รวมชวนสำเร็จ: {total_completed} คน\nกด /invite เพื่อดูลิงก์ชวนเพิ่มได้เลย 🎁"

        await bot.send_message(
            chat_id=referrer_telegram_id,
            text=reward_text,
            parse_mode="HTML",
        )
    except Exception as exc:
        logger.warning("Failed to DM referrer %d: %s", referrer_telegram_id, exc)

    # Notify admin
    try:
        import telegram as tg
        admin_bot = tg.Bot(token=os.environ.get("ADMIN_BOT_TOKEN", ""))
        await admin_bot.initialize()
        admin_group = int(os.environ.get("ADMIN_GROUP_CHAT_ID", ""))
        await admin_bot.send_message(
            chat_id=admin_group,
            text=(
                f"🎁 <b>ชวนเพื่อนสำเร็จ!</b>\n\n"
                f"👤 ผู้ชวน: TG ID <code>{referrer_telegram_id}</code>\n"
                f"👥 เพื่อน: TG ID <code>{referred_telegram_id}</code>\n"
                f"🎯 ชวนสำเร็จรวม: {total_completed} คน\n"
                f"📅 รางวัล: +{total_days} วัน"
                + (f" (รวม bonus 30 วัน)" if bonus_days > 0 else "")
            ),
            parse_mode="HTML",
        )
    except Exception as exc:
        logger.warning("Failed to notify admin about referral: %s", exc)


# ─── Handlers ─────────────────────────────────────────────────────────────────

def get_referral_handlers() -> list:
    """Return all handlers for the referral module."""
    return [
        CommandHandler("invite", invite_command),
        CommandHandler("myreferrals", myreferrals_command),
        CallbackQueryHandler(_myreferrals_callback, pattern="^my_referrals$"),
        CallbackQueryHandler(_get_invite_link_callback, pattern="^get_invite_link$"),
    ]
