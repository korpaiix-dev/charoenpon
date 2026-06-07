"""Broadcast handlers - ส่งข้อความถึงสมาชิกผ่าน Sales Bot."""

from __future__ import annotations

import asyncio
import csv
import logging
import json
import os
import time
from datetime import datetime, timedelta, timezone
from io import StringIO
from typing import Any

import telegram
from sqlalchemy import select, text
from telegram import (
    Bot,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ConversationHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from shared.database import get_session
from shared.models import (
    GroupSlug,
    Subscription,
    SubscriptionStatus,
    User,
)

logger = logging.getLogger(__name__)

from shared.tz import TH_TZ

# Admin group chat ID
ADMIN_GROUP_ID = -1003830920430

# Conversation states
ASK_MESSAGE, CONFIRM_SEND = range(2)

# CSV path inside container
CSV_PATH = os.environ.get("CSV_PATH", "/app/data/members2_latest.csv")

# Rate limit: 0.05 seconds between messages (20/sec, safe under Telegram's 30/sec)
SEND_DELAY = 0.05


def _get_sales_bot() -> Bot:
    """Create a Sales Bot instance from env."""
    token = os.environ.get("SALES_BOT_TOKEN", "")
    if not token:
        raise ValueError("SALES_BOT_TOKEN not set")
    return Bot(token=token)


# FIX 2025-05-21 (Phase 2g): Admin user whitelist — chat_id alone is not enough.
# Anyone who is added to the admin group could run /broadcast before. Now we also
# require the sender's telegram user id to be in ADMIN_USER_IDS (comma-separated env).
# FIX 2025-05-21 (Phase 2g v2): reuse existing ADMIN_TELEGRAM_IDS env var
# (already configured in .env, no need for separate ADMIN_USER_IDS)
_ADMIN_USER_IDS: frozenset[int] = frozenset(
    int(x.strip()) for x in (
        os.environ.get("ADMIN_USER_IDS")
        or os.environ.get("ADMIN_TELEGRAM_IDS")
        or ""
    ).split(",")
    if x.strip().isdigit()
)


def _is_admin_group(update: Update) -> bool:
    """Check if message is from the admin group AND sender is a whitelisted admin."""
    chat_ok = (
        update.effective_chat is not None
        and update.effective_chat.id == ADMIN_GROUP_ID
    )
    if not chat_ok:
        return False
    # If whitelist is empty (not configured), fall back to chat-only check —
    # but log a warning so this gets noticed and fixed.
    if not _ADMIN_USER_IDS:
        logger.warning(
            "ADMIN_USER_IDS env not set — broadcast accessible to anyone in admin group. "
            "Set ADMIN_USER_IDS=<comma-separated tg_ids> to lock down."
        )
        return True
    user = update.effective_user
    user_ok = user is not None and user.id in _ADMIN_USER_IDS
    if not user_ok:
        logger.warning(
            "BLOCKED: non-admin user %s (%s) tried broadcast command",
            user.id if user else "?",
            user.username if user else "?",
        )
    return user_ok


# ─── CSV Helpers ──────────────────────────────────────────────────────────────

def _read_csv_users() -> list[dict[str, str]]:
    """Read members CSV and return list of dicts."""
    try:
        with open(CSV_PATH, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            return list(reader)
    except FileNotFoundError:
        logger.warning("CSV file not found: %s", CSV_PATH)
        return []
    except Exception as e:
        logger.error("Error reading CSV: %s", e)
        return []


def _csv_user_ids_by_status(statuses: list[str]) -> set[int]:
    """Get telegram user IDs from CSV filtered by status."""
    result = set()
    for row in _read_csv_users():
        status = (row.get("Status") or "").strip()
        user_id = (row.get("User ID") or "").strip()
        if status in statuses and user_id.isdigit():
            result.add(int(user_id))
    return result


def _csv_permanent_user_ids() -> set[int]:
    """Get permanent user IDs from CSV."""
    result = set()
    for row in _read_csv_users():
        status = (row.get("Status") or "").strip()
        user_id = (row.get("User ID") or "").strip()
        expiry = (row.get("Expiry Date") or "").strip()
        if user_id.isdigit() and (
            status == "Permanent" or expiry.startswith("3000-")
        ):
            result.add(int(user_id))
    return result


def _csv_expiring_user_ids(days: int = 7) -> set[int]:
    """Get user IDs expiring within N days from CSV."""
    result = set()
    now = datetime.now(TH_TZ)
    cutoff = now + timedelta(days=days)
    for row in _read_csv_users():
        status = (row.get("Status") or "").strip()
        user_id = (row.get("User ID") or "").strip()
        expiry_str = (row.get("Expiry Date") or "").strip()
        if status == "Expired" or not user_id.isdigit():
            continue
        try:
            expiry = datetime.strptime(expiry_str[:19], "%Y-%m-%d %H:%M:%S")
            expiry = expiry.replace(tzinfo=TH_TZ)
            if now < expiry <= cutoff:
                result.add(int(user_id))
        except (ValueError, IndexError):
            continue
    return result


def _csv_expired_user_ids() -> set[int]:
    """Get expired user IDs from CSV."""
    return _csv_user_ids_by_status(["Expired"])


def _csv_all_user_ids() -> set[int]:
    """Get all non-expired user IDs from CSV."""
    result = set()
    for row in _read_csv_users():
        status = (row.get("Status") or "").strip()
        user_id = (row.get("User ID") or "").strip()
        if user_id.isdigit() and status != "Expired":
            result.add(int(user_id))
    return result


# ─── DB Helpers ───────────────────────────────────────────────────────────────

async def _db_all_user_telegram_ids() -> set[int]:
    """Get ALL user telegram IDs from DB (สำหรับ broadcast ต้องส่งทุกคน)."""
    async with get_session() as session:
        result = await session.execute(
            select(User.telegram_id)
            .where(User.is_banned == False)  # noqa: E712
            .distinct()
        )
        return {row[0] for row in result.all() if row[0]}


async def _db_group_user_ids(group_slug: str) -> set[int]:
    """Get user telegram IDs with active subscriptions that have access to a specific group."""
    from shared.models import Package
    async with get_session() as session:
        # Find packages that include this group
        result = await session.execute(select(Package).where(Package.is_active == True))
        packages = result.scalars().all()
        
        pkg_ids = []
        for pkg in packages:
            if group_slug in pkg.group_list:
                pkg_ids.append(pkg.id)
        
        if not pkg_ids:
            return set()
        
        # Find users with active subscriptions for those packages
        result = await session.execute(
            select(User.telegram_id)
            .join(Subscription, Subscription.user_id == User.id)
            .where(
                Subscription.status == SubscriptionStatus.ACTIVE,
                Subscription.end_date > datetime.utcnow(),
                Subscription.package_id.in_(pkg_ids),
            )
            .distinct()
        )
        return {row[0] for row in result.all()}


async def _db_expiring_user_ids(days: int = 7) -> set[int]:
    """Get user telegram IDs with subscriptions expiring within N days."""
    now = datetime.utcnow()
    cutoff = now + timedelta(days=days)
    async with get_session() as session:
        result = await session.execute(
            select(User.telegram_id)
            .join(Subscription, Subscription.user_id == User.id)
            .where(
                Subscription.status == SubscriptionStatus.ACTIVE,
                Subscription.end_date > now,
                Subscription.end_date <= cutoff,
            )
            .distinct()
        )
        return {row[0] for row in result.all()}


async def _db_expired_user_ids() -> set[int]:
    """Get user telegram IDs with expired subscriptions."""
    async with get_session() as session:
        result = await session.execute(
            select(User.telegram_id)
            .join(Subscription, Subscription.user_id == User.id)
            .where(Subscription.status == SubscriptionStatus.EXPIRED)
            .distinct()
        )
        return {row[0] for row in result.all()}


async def _db_permanent_user_ids() -> set[int]:
    """Get permanent user telegram IDs from DB (end_date far in future)."""
    cutoff = datetime(2099, 1, 1)
    async with get_session() as session:
        result = await session.execute(
            select(User.telegram_id)
            .join(Subscription, Subscription.user_id == User.id)
            .where(
                Subscription.status == SubscriptionStatus.ACTIVE,
                Subscription.end_date > cutoff,
            )
            .distinct()
        )
        return {row[0] for row in result.all()}


async def _db_user_by_username(username: str) -> int | None:
    """Find user telegram_id by username."""
    username = username.lstrip("@")
    async with get_session() as session:
        result = await session.execute(
            select(User.telegram_id).where(User.username == username)
        )
        row = result.first()
        return row[0] if row else None


# ─── Broadcast Record ────────────────────────────────────────────────────────

async def _create_broadcast_record(
    message_text: str | None,
    photo_id: str | None,
    target_type: str,
    target_value: str | None,
    total_count: int,
    sent_by: int,
    sent_by_username: str | None,
    user_ids: "set[int] | list[int] | None" = None,
) -> int:
    """Insert a broadcast record and return its ID."""
    async with get_session() as session:
        result = await session.execute(
            text("""
                INSERT INTO broadcasts 
                (message_text, message_photo_id, target_type, target_value,
                 total_count, sent_by, sent_by_username,
                 status, target_user_ids)
                VALUES (:msg, :photo, :tt, :tv, :tc, :sb, :sbu,
                        'PENDING', CAST(:tuids AS JSONB))
                RETURNING id
            """),
            {
                "msg": message_text,
                "photo": photo_id,
                "tt": target_type,
                "tv": target_value,
                "tc": total_count,
                "sb": sent_by,
                "sbu": sent_by_username,
            "tuids": json.dumps(list(user_ids or [])),
            },
        )
        row = result.first()
        return row[0]


async def _update_broadcast_record(
    broadcast_id: int,
    success_count: int,
    failed_count: int,
    duration_seconds: int,
) -> None:
    """Stub — broadcast-worker container handles updates now (status, counts, completed_at)."""
    logger.info("_update_broadcast_record inline call ignored (id=%s) — worker handles updates", broadcast_id)
    return

async def _send_broadcast(
    user_ids,
    message_text: str | None,
    photo_id: str | None,
) -> tuple[int, int]:
    """Stub — broadcast-worker container handles sending now (status=PENDING in DB)."""
    logger.info("_send_broadcast inline call ignored — broadcast-worker queue handles it. users=%d", len(user_ids))
    return (0, 0)

async def _notify_discord_broadcast(
    target_type: str,
    target_value: str | None,
    success: int,
    failed: int,
    duration: int,
    admin_name: str,
) -> None:
    """Send broadcast result to Discord #alerts."""
    import httpx

    discord_token = os.environ.get("DISCORD_BOT_TOKEN", "")
    discord_ch = os.environ.get("DISCORD_CH_ALERTS", "")
    if not discord_token or not discord_ch:
        return
    try:
        now_th = datetime.now(TH_TZ)
        target_display = target_value or "ทั้งหมด"
        embed = {
            "title": "📢 Broadcast เสร็จสิ้น",
            "description": (
                f"🎯 ประเภท: {target_type}\n"
                f"📍 เป้าหมาย: {target_display}\n"
                f"✅ สำเร็จ: {success} คน\n"
                f"❌ ล้มเหลว: {failed} คน\n"
                f"⏱️ ใช้เวลา: {duration} วินาที\n"
                f"📤 ส่งโดย: {admin_name}"
            ),
            "color": 0x3498DB,
            "footer": {"text": f"⊙ เจริญพร | {now_th.strftime('%d/%m/%Y %H:%M')}"},
        }
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(
                f"https://discord.com/api/v10/channels/{discord_ch}/messages",
                headers={
                    "Authorization": f"Bot {discord_token}",
                    "Content-Type": "application/json",
                },
                json={"embeds": [embed]},
            )
    except Exception as e:
        logger.warning("Discord notify failed: %s", e)


# ─── Helper: Format Duration ─────────────────────────────────────────────────

def _format_duration(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds} วินาที"
    minutes = seconds // 60
    secs = seconds % 60
    return f"{minutes} นาที {secs} วินาที"


# ─── Conversation Handlers ────────────────────────────────────────────────────

async def cmd_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """/broadcast — ส่งถึงทุกคน."""
    if not _is_admin_group(update):
        return ConversationHandler.END

    context.user_data["bc_target_type"] = "all"
    context.user_data["bc_target_value"] = None

    # Count users
    db_ids = await _db_all_user_telegram_ids()
    csv_ids = _csv_all_user_ids()
    all_ids = db_ids | csv_ids
    context.user_data["bc_user_ids"] = all_ids

    await update.effective_message.reply_text(
        f"📢 <b>Broadcast ถึงทุกคน</b>\n"
        f"👥 จำนวน: {len(all_ids)} คน\n\n"
        f"📝 พิมพ์ข้อความที่ต้องการส่ง (หรือส่งรูป+caption):\n"
        f"พิมพ์ /cancel เพื่อยกเลิก",
        parse_mode="HTML",
    )
    return ASK_MESSAGE


async def cmd_broadcast_group(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """/broadcast_group — ส่งเฉพาะกลุ่ม VIP."""
    if not _is_admin_group(update):
        return ConversationHandler.END

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("G300", callback_data="bc_group_G300"),
            InlineKeyboardButton("G500", callback_data="bc_group_G500"),
            InlineKeyboardButton("SSS", callback_data="bc_group_SSS"),
        ],
        [
            InlineKeyboardButton("VGOD", callback_data="bc_group_VGOD"),
            InlineKeyboardButton("INTER", callback_data="bc_group_INTER"),
            InlineKeyboardButton("SERIES", callback_data="bc_group_SERIES"),
        ],
    ])
    await update.effective_message.reply_text(
        "📢 <b>เลือกกลุ่มที่ต้องการส่ง:</b>",
        parse_mode="HTML",
        reply_markup=keyboard,
    )
    return ASK_MESSAGE


async def bc_group_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle group selection for broadcast_group."""
    query = update.callback_query
    await query.answer()

    slug = query.data.replace("bc_group_", "")
    context.user_data["bc_target_type"] = "group"
    context.user_data["bc_target_value"] = slug

    # Get users for this group
    db_ids = await _db_group_user_ids(slug)
    # CSV doesn't have group info, so we include all non-expired CSV users
    # only if slug is a commonly shared group; for specificity, use DB only
    csv_non_expired = _csv_user_ids_by_status(["Active", "Updated", "Paid", "Renewed", "Permanent"])
    # Combine DB + CSV (CSV users may not be in DB)
    all_ids = db_ids | csv_non_expired
    context.user_data["bc_user_ids"] = all_ids

    await query.edit_message_text(
        f"📢 <b>Broadcast กลุ่ม {slug}</b>\n"
        f"👥 จำนวน: {len(all_ids)} คน\n\n"
        f"📝 พิมพ์ข้อความที่ต้องการส่ง (หรือส่งรูป+caption):\n"
        f"พิมพ์ /cancel เพื่อยกเลิก",
        parse_mode="HTML",
    )
    return ASK_MESSAGE


async def cmd_broadcast_filter(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """/broadcast_filter — ส่งเฉพาะสถานะ."""
    logger.info("cmd_broadcast_filter called by %s in chat %s", update.effective_user.id if update.effective_user else "?", update.effective_chat.id if update.effective_chat else "?")
    if not _is_admin_group(update):
        logger.info("Not admin group, ending")
        return ConversationHandler.END

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Active", callback_data="bc_filter_active"),
            InlineKeyboardButton("⏳ ใกล้หมดอายุ (7 วัน)", callback_data="bc_filter_expiring"),
        ],
        [
            InlineKeyboardButton("❌ หมดอายุแล้ว", callback_data="bc_filter_expired"),
            InlineKeyboardButton("♾️ Permanent", callback_data="bc_filter_permanent"),
        ],
    ])
    await update.effective_message.reply_text(
        "📢 <b>เลือกสถานะที่ต้องการส่ง:</b>",
        parse_mode="HTML",
        reply_markup=keyboard,
    )
    return ASK_MESSAGE


async def bc_filter_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle filter selection for broadcast_filter."""
    query = update.callback_query
    await query.answer()

    filter_type = query.data.replace("bc_filter_", "")
    context.user_data["bc_target_type"] = "filter"
    context.user_data["bc_target_value"] = filter_type

    # Get users based on filter
    if filter_type == "active":
        db_ids = await _db_all_user_telegram_ids()
        csv_ids = _csv_user_ids_by_status(["Active", "Updated", "Paid", "Renewed"])
        all_ids = db_ids | csv_ids
        label = "✅ Active"
    elif filter_type == "expiring":
        db_ids = await _db_expiring_user_ids(7)
        csv_ids = _csv_expiring_user_ids(7)
        all_ids = db_ids | csv_ids
        label = "⏳ ใกล้หมดอายุ (7 วัน)"
    elif filter_type == "expired":
        db_ids = await _db_expired_user_ids()
        csv_ids = _csv_expired_user_ids()
        all_ids = db_ids | csv_ids
        label = "❌ หมดอายุแล้ว"
    elif filter_type == "permanent":
        db_ids = await _db_permanent_user_ids()
        csv_ids = _csv_permanent_user_ids()
        all_ids = db_ids | csv_ids
        label = "♾️ Permanent"
    else:
        all_ids = set()
        label = "???"

    context.user_data["bc_user_ids"] = all_ids

    await query.edit_message_text(
        f"📢 <b>Broadcast สถานะ: {label}</b>\n"
        f"👥 จำนวน: {len(all_ids)} คน\n\n"
        f"📝 พิมพ์ข้อความที่ต้องการส่ง (หรือส่งรูป+caption):\n"
        f"พิมพ์ /cancel เพื่อยกเลิก",
        parse_mode="HTML",
    )
    return ASK_MESSAGE


async def receive_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receive the broadcast message (text or photo+caption)."""
    if not _is_admin_group(update):
        return ConversationHandler.END

    msg = update.effective_message

    # Extract content
    if msg.photo:
        context.user_data["bc_photo_id"] = msg.photo[-1].file_id
        context.user_data["bc_text"] = msg.caption or ""
    elif msg.text:
        context.user_data["bc_photo_id"] = None
        context.user_data["bc_text"] = msg.text
    else:
        await msg.reply_text("⚠️ รองรับเฉพาะข้อความหรือรูป+caption เท่านั้น")
        return ASK_MESSAGE

    # Show preview
    user_ids: set[int] = context.user_data.get("bc_user_ids", set())
    target_type = context.user_data.get("bc_target_type", "all")
    target_value = context.user_data.get("bc_target_value")
    admin_username = update.effective_user.username or update.effective_user.first_name

    msg_text = context.user_data["bc_text"]
    preview_text = msg_text[:200] + "..." if len(msg_text) > 200 else msg_text

    target_display = {
        "all": "ทั้งหมด",
        "group": f"กลุ่ม {target_value}",
        "filter": f"สถานะ {target_value}",
        "user": f"User {target_value}",
    }.get(target_type, target_type)

    photo_indicator = "🖼 มีรูปแนบ\n" if context.user_data.get("bc_photo_id") else ""

    preview = (
        f"📢 <b>ตัวอย่าง Broadcast</b>\n\n"
        f"📝 ข้อความ:\n{preview_text}\n\n"
        f"{photo_indicator}"
        f"🎯 เป้าหมาย: {target_display}\n"
        f"👥 ส่งถึง: {len(user_ids)} คน\n"
        f"📤 ส่งโดย: @{admin_username}\n"
    )

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ ส่งเลย", callback_data="bc_confirm_send"),
            InlineKeyboardButton("📝 แก้ไข", callback_data="bc_confirm_edit"),
            InlineKeyboardButton("❌ ยกเลิก", callback_data="bc_confirm_cancel"),
        ]
    ])

    await msg.reply_text(preview, parse_mode="HTML", reply_markup=keyboard)
    return CONFIRM_SEND


async def confirm_send_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle confirm/edit/cancel for broadcast."""
    query = update.callback_query
    await query.answer()

    action = query.data.replace("bc_confirm_", "")

    if action == "cancel":
        await query.edit_message_text("❌ ยกเลิก Broadcast แล้ว")
        context.user_data.clear()
        return ConversationHandler.END

    if action == "edit":
        await query.edit_message_text(
            "📝 พิมพ์ข้อความใหม่ (หรือส่งรูป+caption):\n"
            "พิมพ์ /cancel เพื่อยกเลิก"
        )
        return ASK_MESSAGE

    # action == "send"
    user_ids: set[int] = context.user_data.get("bc_user_ids", set())
    msg_text = context.user_data.get("bc_text")
    photo_id = context.user_data.get("bc_photo_id")
    target_type = context.user_data.get("bc_target_type", "all")
    target_value = context.user_data.get("bc_target_value")
    admin_user = query.from_user
    admin_username = admin_user.username or admin_user.first_name

    if not user_ids:
        await query.edit_message_text("⚠️ ไม่มีผู้รับ — ยกเลิก Broadcast")
        context.user_data.clear()
        return ConversationHandler.END

    # Create DB record
    broadcast_id = await _create_broadcast_record(
        message_text=msg_text,
        photo_id=photo_id,
        target_type=target_type,
        target_value=target_value,
        total_count=len(user_ids),
        sent_by=admin_user.id,
        sent_by_username=admin_username,
    user_ids=user_ids,
    )

    await query.edit_message_text(
        f"📤 กำลังส่ง Broadcast #{broadcast_id}...\n"
        f"👥 ส่งถึง {len(user_ids)} คน\n"
        f"⏳ กรุณารอสักครู่..."
    )

    # Send broadcast
    start_time = time.time()
    success, failed = await _send_broadcast(user_ids, msg_text, photo_id)
    duration = int(time.time() - start_time)

    # Update DB record
    await _update_broadcast_record(broadcast_id, success, failed, duration)

    # Report result
    result_text = (
            f"📤 <b>Broadcast #{broadcast_id} เข้าคิวแล้ว</b>\n\n"
            f"👥 ส่งถึง {len(user_ids) if isinstance(user_ids, (list, set, tuple)) else '?'} คน\n"
            f"⏳ Worker กำลังประมวลผลในเบื้องหลัง (5-15 นาที)\n"
        f"⏱️ ใช้เวลา: {_format_duration(duration)}\n"
        f"📤 ส่งโดย: @{admin_username}\n\n"
        f"━━━━━━━━━━━━━━━\n"
        f"🏢 เจริญพร Official"
    )
    await context.bot.send_message(
        chat_id=ADMIN_GROUP_ID,
        text=result_text,
        parse_mode="HTML",
    )

    # Discord notify
    await _notify_discord_broadcast(
        target_type, target_value, success, failed, duration, admin_username
    )

    context.user_data.clear()
    return ConversationHandler.END


# ─── /broadcast_user ──────────────────────────────────────────────────────────

async def cmd_broadcast_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """/broadcast_user <user_id or @username> — ส่งเฉพาะคน."""
    if not _is_admin_group(update):
        return ConversationHandler.END

    args = context.args
    if not args:
        await update.effective_message.reply_text(
            "⚠️ ใช้: /broadcast_user <user_id หรือ @username>"
        )
        return ConversationHandler.END

    target = args[0]
    if target.startswith("@"):
        telegram_id = await _db_user_by_username(target)
        if not telegram_id:
            await update.effective_message.reply_text(
                f"❌ ไม่พบ user: {target}"
            )
            return ConversationHandler.END
    else:
        try:
            telegram_id = int(target)
        except ValueError:
            await update.effective_message.reply_text(
                "⚠️ user_id ต้องเป็นตัวเลข หรือใช้ @username"
            )
            return ConversationHandler.END

    context.user_data["bc_target_type"] = "user"
    context.user_data["bc_target_value"] = str(telegram_id)
    context.user_data["bc_user_ids"] = {telegram_id}
    context.user_data["bc_single_user"] = True

    await update.effective_message.reply_text(
        f"📢 <b>Broadcast ถึง User {telegram_id}</b>\n\n"
        f"📝 พิมพ์ข้อความที่ต้องการส่ง (หรือส่งรูป+caption):\n"
        f"พิมพ์ /cancel เพื่อยกเลิก",
        parse_mode="HTML",
    )
    return ASK_MESSAGE


async def receive_message_single(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receive message for single-user broadcast and send immediately."""
    if not _is_admin_group(update):
        return ConversationHandler.END

    msg = update.effective_message

    if msg.photo:
        photo_id = msg.photo[-1].file_id
        msg_text = msg.caption or ""
    elif msg.text:
        photo_id = None
        msg_text = msg.text
    else:
        await msg.reply_text("⚠️ รองรับเฉพาะข้อความหรือรูป+caption เท่านั้น")
        return ASK_MESSAGE

    user_ids = context.user_data.get("bc_user_ids", set())
    target_value = context.user_data.get("bc_target_value")
    admin_username = update.effective_user.username or update.effective_user.first_name

    # Send immediately (single user, no preview)
    broadcast_id = await _create_broadcast_record(
        message_text=msg_text,
        photo_id=photo_id,
        target_type="user",
        target_value=target_value,
        total_count=1,
        sent_by=update.effective_user.id,
        sent_by_username=admin_username,
    user_ids=user_ids,
    )

    start_time = time.time()
    success, failed = await _send_broadcast(user_ids, msg_text, photo_id)
    duration = int(time.time() - start_time)

    await _update_broadcast_record(broadcast_id, success, failed, duration)

    status_emoji = "✅" if success > 0 else "❌"
    await msg.reply_text(
        f"{status_emoji} ส่งถึง User {target_value} {'สำเร็จ' if success > 0 else 'ล้มเหลว'}\n"
        f"📤 ส่งโดย: @{admin_username}",
        parse_mode="HTML",
    )

    context.user_data.clear()
    return ConversationHandler.END


# ─── /broadcast_status ────────────────────────────────────────────────────────

async def cmd_broadcast_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/broadcast_status — ดูสถานะ broadcast ล่าสุด."""
    if not _is_admin_group(update):
        return

    async with get_session() as session:
        result = await session.execute(
            text("""
                SELECT id, target_type, target_value, total_count, success_count,
                       failed_count, sent_by_username, started_at, completed_at,
                       duration_seconds
                FROM broadcasts
                ORDER BY id DESC LIMIT 1
            """)
        )
        row = result.first()

    if not row:
        await update.effective_message.reply_text("📭 ยังไม่มี broadcast")
        return

    (bid, tt, tv, total, suc, fail, admin, started, completed, dur) = row
    status = "✅ เสร็จสิ้น" if completed else "⏳ กำลังส่ง..."
    dur_text = _format_duration(dur) if dur else "-"
    tv_display = tv or "ทั้งหมด"

    text_msg = (
        f"📢 <b>Broadcast ล่าสุด #{bid}</b>\n\n"
        f"📊 สถานะ: {status}\n"
        f"🎯 ประเภท: {tt} ({tv_display})\n"
        f"👥 ทั้งหมด: {total} คน\n"
        f"✅ สำเร็จ: {suc} คน\n"
        f"❌ ล้มเหลว: {fail} คน\n"
        f"⏱️ ใช้เวลา: {dur_text}\n"
        f"📤 ส่งโดย: @{admin or 'N/A'}\n"
        f"🕐 เริ่ม: {str(started)[:19]}"
    )
    await update.effective_message.reply_text(text_msg, parse_mode="HTML")


# ─── /broadcast_history ───────────────────────────────────────────────────────

async def cmd_broadcast_history(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/broadcast_history — ประวัติ broadcast ย้อนหลัง 10 รายการ."""
    if not _is_admin_group(update):
        return

    async with get_session() as session:
        result = await session.execute(
            text("""
                SELECT id, target_type, target_value, total_count, success_count,
                       failed_count, sent_by_username, started_at, duration_seconds
                FROM broadcasts
                ORDER BY id DESC LIMIT 10
            """)
        )
        rows = result.all()

    if not rows:
        await update.effective_message.reply_text("📭 ยังไม่มี broadcast")
        return

    lines = ["📢 <b>ประวัติ Broadcast (10 ล่าสุด)</b>\n"]
    for bid, tt, tv, total, suc, fail, admin, started, dur in rows:
        tv_short = tv[:10] if tv else "ทุกคน"
        dur_text = f"{dur}s" if dur else "-"
        started_str = str(started)[:16] if started else "-"
        lines.append(
            f"#{bid} | {tt}:{tv_short} | "
            f"✅{suc}/❌{fail}/{total} | "
            f"⏱{dur_text} | @{admin or '?'}\n"
            f"   🕐 {started_str}"
        )

    await update.effective_message.reply_text(
        "\n".join(lines), parse_mode="HTML"
    )


# ─── /cancel ──────────────────────────────────────────────────────────────────

async def cancel_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancel broadcast conversation."""
    context.user_data.clear()
    await update.effective_message.reply_text("❌ ยกเลิก Broadcast แล้ว")
    return ConversationHandler.END


# ─── Register Handlers ───────────────────────────────────────────────────────

def get_broadcast_handlers() -> list:
    """Return all broadcast-related handlers to register with the application."""

    # Filter: only in admin group, not commands
    admin_group_filter = filters.Chat(chat_id=ADMIN_GROUP_ID)
    msg_filter = admin_group_filter & ~filters.COMMAND & (filters.TEXT | filters.PHOTO)

    # ConversationHandler for /broadcast (all)
    broadcast_all_conv = ConversationHandler(
        entry_points=[
            CommandHandler("broadcast", cmd_broadcast, filters=admin_group_filter),
            CommandHandler("bc", cmd_broadcast, filters=admin_group_filter),
        ],
        states={
            ASK_MESSAGE: [
                CallbackQueryHandler(bc_group_callback, pattern=r"^bc_group_"),
                CallbackQueryHandler(bc_filter_callback, pattern=r"^bc_filter_"),
                MessageHandler(msg_filter, receive_message),
            ],
            CONFIRM_SEND: [
                CallbackQueryHandler(confirm_send_callback, pattern=r"^bc_confirm_"),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel_broadcast)],
        per_user=True,
        per_chat=True,
        name="broadcast_all",
    )

    # ConversationHandler for /broadcast_group
    broadcast_group_conv = ConversationHandler(
        entry_points=[
            CommandHandler("broadcast_group", cmd_broadcast_group, filters=admin_group_filter),
            CommandHandler("bcg", cmd_broadcast_group, filters=admin_group_filter),
        ],
        states={
            ASK_MESSAGE: [
                CallbackQueryHandler(bc_group_callback, pattern=r"^bc_group_"),
                MessageHandler(msg_filter, receive_message),
            ],
            CONFIRM_SEND: [
                CallbackQueryHandler(confirm_send_callback, pattern=r"^bc_confirm_"),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel_broadcast)],
        per_user=True,
        per_chat=True,
        name="broadcast_group",
    )

    # ConversationHandler for /broadcast_filter
    broadcast_filter_conv = ConversationHandler(
        entry_points=[
            CommandHandler("broadcast_filter", cmd_broadcast_filter, filters=admin_group_filter),
            CommandHandler("bcf", cmd_broadcast_filter, filters=admin_group_filter),
        ],
        states={
            ASK_MESSAGE: [
                CallbackQueryHandler(bc_filter_callback, pattern=r"^bc_filter_"),
                MessageHandler(msg_filter, receive_message),
            ],
            CONFIRM_SEND: [
                CallbackQueryHandler(confirm_send_callback, pattern=r"^bc_confirm_"),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel_broadcast)],
        per_user=True,
        per_chat=True,
        name="broadcast_filter",
    )

    # ConversationHandler for /broadcast_user
    broadcast_user_conv = ConversationHandler(
        entry_points=[
            CommandHandler("broadcast_user", cmd_broadcast_user, filters=admin_group_filter),
            CommandHandler("bcu", cmd_broadcast_user, filters=admin_group_filter),
        ],
        states={
            ASK_MESSAGE: [
                MessageHandler(msg_filter, receive_message_single),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel_broadcast)],
        per_user=True,
        per_chat=True,
        name="broadcast_user",
    )

    # Simple command handlers (no conversation)
    status_handler = CommandHandler(
        ["broadcast_status", "bcs"], cmd_broadcast_status, filters=admin_group_filter
    )
    history_handler = CommandHandler(
        ["broadcast_history", "bch"], cmd_broadcast_history, filters=admin_group_filter
    )

    return [
        broadcast_all_conv,
        broadcast_group_conv,
        broadcast_filter_conv,
        broadcast_user_conv,
        status_handler,
        history_handler,
    ]
