"""Group Monitor - Guardian Bot (ยาม).

ตรวจสมาชิกทุกกลุ่ม:
- เช็ค 3 ชั้นก่อนเตะ: DB → CSV → แจ้ง Admin
- Lifetime (duration_days=NULL) ห้ามแตะ
- สร้าง one-time invite link สำหรับลูกค้าที่ชำระเงินแล้ว
- บันทึก log ทุก action
"""

from __future__ import annotations

import csv
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

TH_TZ = timezone(timedelta(hours=7))
from sqlalchemy import select
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest, Forbidden

from shared.database import get_session
from shared.models import (
    GroupRegistry,
    Package,
    Subscription,
    SubscriptionStatus,
    User,
)
from shared.utils import log_admin_action

logger = logging.getLogger(__name__)

GUARDIAN_BOT_ID = 0  # System admin ID

# --- CSV whitelist (ชั้นที่ 2) ---
_csv_whitelist: set[int] = set()
_csv_loaded = False

CSV_PATH = Path(os.environ.get("CSV_WHITELIST_PATH", "/app/data/members2_latest.csv"))


def load_csv_whitelist() -> set[int]:
    """Load CSV whitelist — เฉพาะคนที่ยังไม่หมดอายุ.

    Permanent → whitelist เสมอ
    Active + Expiry Date ยังไม่ถึง → whitelist
    Expired หรือ Expiry Date ผ่านแล้ว → ไม่ whitelist
    """
    global _csv_whitelist, _csv_loaded
    if _csv_loaded:
        return _csv_whitelist

    whitelist: set[int] = set()
    now = datetime.utcnow()

    try:
        with open(CSV_PATH, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                status = (row.get("Status") or "").strip().lower()
                expiry_str = (row.get("Expiry Date") or "").strip()
                uid_str = (row.get("User ID") or "0").strip()

                try:
                    uid = int(uid_str)
                    if not uid:
                        continue
                except (ValueError, TypeError):
                    continue

                # Expired status → ไม่ whitelist
                if status == "expired":
                    continue

                # Permanent → whitelist เสมอ
                if status == "permanent":
                    whitelist.add(uid)
                    continue

                # เช็ค Expiry Date
                if expiry_str:
                    try:
                        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
                            try:
                                expiry_date = datetime.strptime(expiry_str.split(".")[0], fmt)
                                break
                            except ValueError:
                                continue
                        else:
                            # parse ไม่ได้ → ให้ whitelist ไว้ก่อน (benefit of doubt)
                            whitelist.add(uid)
                            continue

                        # 3000+ = Permanent
                        if expiry_date.year >= 2999:
                            whitelist.add(uid)
                            continue

                        # ยังไม่หมดอายุ → whitelist
                        if expiry_date >= now:
                            whitelist.add(uid)
                        # หมดอายุแล้ว → ไม่ whitelist (จะถูกเตะ)
                    except Exception:
                        whitelist.add(uid)  # parse error → ให้ whitelist ไว้ก่อน
                else:
                    # ไม่มี Expiry Date → whitelist ไว้ก่อน
                    whitelist.add(uid)

        logger.info("CSV whitelist loaded: %d users from %s", len(whitelist), CSV_PATH)
    except FileNotFoundError:
        logger.warning("CSV whitelist file not found: %s", CSV_PATH)
    except Exception as exc:
        logger.error("Error loading CSV whitelist: %s", exc)

    _csv_whitelist = whitelist
    _csv_loaded = True
    return _csv_whitelist


def is_in_csv_whitelist(user_id: int) -> bool:
    """Check if user_id is in CSV whitelist."""
    load_csv_whitelist()
    return user_id in _csv_whitelist


# --- CSV Expired list (สำหรับเตะอัตโนมัติ) ---
_csv_expired: set[int] = set()
_csv_expired_loaded = False


def load_csv_expired() -> set[int]:
    """Load CSV users ที่หมดอายุแล้ว — ทั้ง status=Expired และ Expiry Date ผ่านแล้ว."""
    global _csv_expired, _csv_expired_loaded
    if _csv_expired_loaded:
        return _csv_expired

    expired: set[int] = set()
    now = datetime.utcnow()

    try:
        with open(CSV_PATH, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                status = (row.get("Status") or "").strip().lower()
                expiry_str = (row.get("Expiry Date") or "").strip()
                uid_str = (row.get("User ID") or "0").strip()

                try:
                    uid = int(uid_str)
                    if not uid:
                        continue
                except (ValueError, TypeError):
                    continue

                # Permanent → ไม่เตะเด็ดขาด
                if status == "permanent":
                    continue

                # Status = Expired → เตะ
                if status == "expired":
                    expired.add(uid)
                    continue

                # เช็ค Expiry Date — ถ้าผ่านไปแล้ว = หมดอายุ
                if expiry_str:
                    try:
                        # รองรับหลาย format
                        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
                            try:
                                expiry_date = datetime.strptime(expiry_str.split(".")[0], fmt)
                                break
                            except ValueError:
                                continue
                        else:
                            continue

                        # ถ้าวันหมด 3000 = Permanent (บอทเก่าใส่)
                        if expiry_date.year >= 2999:
                            continue

                        # ถ้าหมดอายุแล้ว → เตะ
                        if expiry_date < now:
                            expired.add(uid)
                    except Exception:
                        continue

        logger.info("CSV expired loaded: %d users (status=Expired + Expiry Date ผ่านแล้ว)", len(expired))
    except FileNotFoundError:
        logger.warning("CSV file not found: %s", CSV_PATH)
    except Exception as exc:
        logger.error("Error loading CSV expired: %s", exc)

    _csv_expired = expired
    _csv_expired_loaded = True
    return _csv_expired


def is_csv_expired(user_id: int) -> bool:
    """Check if user_id is expired — ทั้ง status=Expired และ Expiry Date ผ่านแล้ว."""
    load_csv_expired()
    return user_id in _csv_expired


# --- Pending admin decisions (for 20-min timeout) ---
# key: f"{user_id}_{chat_id}", value: job name
pending_guardian_decisions: dict[str, str] = {}
_PENDING_MAX_SIZE = 500  # Prevent unbounded growth


async def _send_discord(content: str) -> None:
    """Send notification to Discord #ยาม-สมาชิก via Bot API."""
    token = os.environ.get("DISCORD_BOT_TOKEN", "")
    ch = os.environ.get("DISCORD_CH_MEMBER_EXPIRING", "")
    if not token or not ch:
        return
    try:
        now_th = datetime.now(TH_TZ)
        embed = {
            "title": "🛡️ Guardian Bot — ยาม",
            "description": content,
            "color": 0xE74C3C,
            "footer": {"text": f"⊙ เจริญพร | {now_th.strftime('%d/%m/%Y %H:%M')}"},
        }
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(
                f"https://discord.com/api/v10/channels/{ch}/messages",
                headers={"Authorization": f"Bot {token}", "Content-Type": "application/json"},
                json={"embeds": [embed]},
            )
    except Exception:
        pass


GUARDIAN_LOG_GROUP = -1003668900287  # ห้อง log เตะ/เข้ากลุ่ม


async def _log_kick_action(bot, content: str) -> None:
    """แจ้งเตือนการเตะไป Discord #system-logs + Telegram Log Group."""
    # Discord #system-logs
    token = os.environ.get("DISCORD_BOT_TOKEN", "")
    ch = os.environ.get("DISCORD_CH_SYSTEM_LOGS", "")
    if token and ch:
        try:
            now_th = datetime.now(TH_TZ)
            embed = {
                "title": "🔒 Guardian — เตะสมาชิก",
                "description": content,
                "color": 0xFF6B00,
                "footer": {"text": f"⊙ เจริญพร | {now_th.strftime('%d/%m/%Y %H:%M')}"},
            }
            async with httpx.AsyncClient(timeout=10) as client:
                await client.post(
                    f"https://discord.com/api/v10/channels/{ch}/messages",
                    headers={"Authorization": f"Bot {token}", "Content-Type": "application/json"},
                    json={"embeds": [embed]},
                )
        except Exception as exc:
            logger.warning("Discord log failed: %s", exc)

    # Telegram Log Group
    if bot:
        try:
            await bot.send_message(
                chat_id=GUARDIAN_LOG_GROUP,
                text=f"🔒 <b>เตะสมาชิก</b>\n\n{content}",
                parse_mode="HTML",
            )
        except Exception as exc:
            logger.warning("Telegram log failed: %s", exc)


async def _log_member_join(bot, user_id: int, username: str, name: str, group_title: str, status: str) -> None:
    """แจ้งเตือนคนเข้ากลุ่มไป Telegram Log Group."""
    if not bot:
        return
    now_th = datetime.now(TH_TZ)
    text = (
        f"🟢 <b>สมาชิกเข้ากลุ่ม</b>\n\n"
        f"👤 ชื่อ: {name}\n"
        f"🆔 TG ID: <code>{user_id}</code>\n"
        f"📛 Username: @{username or '-'}\n"
        f"📍 กลุ่ม: {group_title}\n"
        f"📋 สถานะ: {status}\n"
        f"🕒 {now_th.strftime('%d/%m/%Y %H:%M')}"
    )
    try:
        await bot.send_message(
            chat_id=GUARDIAN_LOG_GROUP,
            text=text,
            parse_mode="HTML",
        )
    except Exception as exc:
        logger.warning("Telegram join log failed: %s", exc)


async def notify_admin_for_decision(
    bot: Bot,
    user_id: int,
    username: str | None,
    chat_id: int,
    group_title: str,
    job_queue,
) -> None:
    """ชั้น 3: แจ้ง Admin group พร้อมปุ่ม ✅ ปล่อย / ❌ เตะ + timeout 20 นาที."""
    admin_group_id = int(os.environ.get("TG_GROUP_ADMIN", "0"))
    admin_bot_token = os.environ.get("ADMIN_BOT_TOKEN", "")
    if not admin_group_id or not admin_bot_token:
        logger.error("TG_GROUP_ADMIN or ADMIN_BOT_TOKEN not set, cannot notify admin")
        return

    display_name = f"@{username}" if username else f"ID:{user_id}"
    text = (
        f"⚠️ พบผู้ใช้ไม่มีสิทธิ์ในกลุ่ม\n\n"
        f"👤 User: {display_name} ({user_id})\n"
        f"📍 กลุ่ม: {group_title}\n"
        f"🔍 ไม่พบใน DB และ CSV\n\n"
        f"กรุณาตัดสินใจ:"
    )

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ ปล่อย", callback_data=f"guardian_keep_{user_id}_{chat_id}"),
            InlineKeyboardButton("❌ เตะ", callback_data=f"guardian_kick_{user_id}_{chat_id}"),
        ]
    ])

    try:
        admin_bot = Bot(token=admin_bot_token)
        msg = await admin_bot.send_message(
            chat_id=admin_group_id,
            text=text,
            reply_markup=keyboard,
        )

        # Set 20-minute timeout — auto-kick if no decision
        decision_key = f"{user_id}_{chat_id}"
        job_name = f"guardian_timeout_{decision_key}"

        job_queue.run_once(
            _guardian_timeout_kick,
            when=1200,  # 20 minutes
            data={
                "user_id": user_id,
                "chat_id": chat_id,
                "admin_group_id": admin_group_id,
                "admin_bot_token": admin_bot_token,
                "message_id": msg.message_id,
                "username": username,
            },
            name=job_name,
        )
        pending_guardian_decisions[decision_key] = job_name

        logger.info(
            "Sent admin decision request for user %s in chat %s (timeout 20min)",
            user_id, chat_id,
        )
    except Exception as exc:
        logger.error("Failed to notify admin group: %s", exc)


async def _guardian_timeout_kick(context) -> None:
    """Timeout callback — เตะอัตโนมัติหลัง 20 นาที."""
    data = context.job.data
    user_id = data["user_id"]
    chat_id = data["chat_id"]
    admin_group_id = data["admin_group_id"]
    admin_bot_token = data["admin_bot_token"]
    message_id = data["message_id"]
    username = data.get("username")

    decision_key = f"{user_id}_{chat_id}"
    pending_guardian_decisions.pop(decision_key, None)

    # Kick the user using guardian bot
    try:
        await context.bot.ban_chat_member(chat_id=chat_id, user_id=user_id)
        await context.bot.unban_chat_member(chat_id=chat_id, user_id=user_id, only_if_banned=True)
        logger.info("Timeout kick: user %s from chat %s", user_id, chat_id)

        # Notify user via Sales Bot
        try:
            _sales = Bot(token=os.environ.get("SALES_BOT_TOKEN", ""))
            await _sales.send_message(
                chat_id=user_id,
                text=(
                    f"⚠️ คุณถูกนำออกจากกลุ่มเนื่องจากไม่มี subscription ที่ active ครับ\n\n"
                    "หากต้องการเข้าใหม่ สามารถสมัครแพ็กเกจได้ที่ @NamwarnJarern_bot ครับ"
                ),
            )
        except Exception:
            pass
    except Exception as exc:
        logger.error("Timeout kick failed for user %s: %s", user_id, exc)

    # Edit admin message
    try:
        admin_bot = Bot(token=admin_bot_token)
        await admin_bot.edit_message_text(
            chat_id=admin_group_id,
            message_id=message_id,
            text=f"⏰ หมดเวลา — เตะออกแล้ว\n\n👤 User: @{username} ({user_id})" if username else f"⏰ หมดเวลา — เตะออกแล้ว\n\n👤 User: {user_id}",
        )
    except Exception as exc:
        logger.error("Failed to edit timeout message: %s", exc)


async def create_one_time_invite(bot: Bot, chat_id: int, user_id: int) -> str:
    """สร้าง one-time invite link สำหรับกลุ่ม VIP.

    - member_limit=1 เสมอ
    - หมดอายุใน 24 ชั่วโมง
    - บันทึก log ลง admin_logs ทุกครั้ง

    Args:
        bot: Telegram Bot instance (ต้องเป็น admin ในกลุ่ม)
        chat_id: chat_id ของกลุ่ม VIP
        user_id: telegram_id ของลูกค้าที่จะได้รับ link

    Returns:
        invite_link URL string
    """
    expire = datetime.utcnow() + timedelta(hours=24)

    link_obj = await bot.create_chat_invite_link(
        chat_id=chat_id,
        member_limit=1,
        expire_date=expire,
        name=f"user_{user_id}",
    )

    await log_admin_action(
        admin_id=GUARDIAN_BOT_ID,
        action="create_one_time_invite",
        target_type="user",
        target_id=user_id,
        details=f"chat_id={chat_id} link={link_obj.invite_link} expire={expire.isoformat()}",
    )

    logger.info(
        "Created one-time invite for user %s in chat %s (expires %s)",
        user_id, chat_id, expire.isoformat(),
    )

    return link_obj.invite_link


async def generate_invite_links_for_user(
    bot: Bot, user_id: int, package_id: int | None = None
) -> dict[str, str]:
    """สร้าง one-time invite link ทุกกลุ่มที่แพ็กเกจให้สิทธิ์.

    Args:
        bot: Telegram Bot instance (Guardian Bot ต้องเป็น admin ในทุกกลุ่ม)
        user_id: telegram_id ของลูกค้า
        package_id: ID ของแพ็กเกจที่ซื้อ (None = ทุกกลุ่ม สำหรับลูกค้าเก่า)

    Returns:
        dict ของ {group_slug: invite_link} สำหรับทุกกลุ่มที่มีสิทธิ์
    """
    if package_id is not None:
        async with get_session() as session:
            pkg_result = await session.execute(
                select(Package).where(Package.id == package_id)
            )
            package = pkg_result.scalar_one()
            group_slugs = package.group_list
    else:
        # ลูกค้าเก่า — ส่ง invite ทุกกลุ่มที่ active
        async with get_session() as session:
            grps_result = await session.execute(
                select(GroupRegistry.slug).where(GroupRegistry.is_active == True)  # noqa: E712
            )
            group_slugs = [r[0].value if hasattr(r[0], 'value') else str(r[0]) for r in grps_result.all()]

    invite_links: dict[str, str] = {}

    for slug in group_slugs:
        async with get_session() as session:
            grp_result = await session.execute(
                select(GroupRegistry).where(
                    GroupRegistry.slug == slug,
                    GroupRegistry.is_active == True,  # noqa: E712
                )
            )
            group = grp_result.scalar_one_or_none()

        if not group:
            logger.warning("Group slug %s not found or inactive, skipping", slug)
            continue

        try:
            link = await create_one_time_invite(bot, group.chat_id, user_id)
            invite_links[slug] = link
        except Forbidden:
            logger.error(
                "Bot is not admin in group %s (chat_id=%s), cannot create invite",
                slug, group.chat_id,
            )
        except BadRequest as e:
            logger.error(
                "Failed to create invite for group %s: %s", slug, e,
            )
        except Exception as exc:
            logger.error(
                "Unexpected error creating invite for group %s: %s", slug, exc,
            )

    return invite_links


async def generate_invite_links_for_csv_user(
    bot: Bot, user_id: int
) -> dict[str, str]:
    """สร้าง invite link เฉพาะกลุ่มที่ CSV user เป็นสมาชิกอยู่แล้ว.

    ตรวจสอบ membership จริงในแต่ละกลุ่ม VIP ก่อนสร้าง invite
    เพื่อไม่ให้ส่งลิงก์กลุ่มที่ลูกค้าไม่มีสิทธิ์เข้า

    Args:
        bot: Telegram Bot instance (Guardian Bot)
        user_id: telegram_id ของลูกค้า

    Returns:
        dict ของ {group_slug: invite_link} เฉพาะกลุ่มที่เป็นสมาชิก
    """
    invite_links: dict[str, str] = {}

    async with get_session() as session:
        grps_result = await session.execute(
            select(GroupRegistry).where(GroupRegistry.is_active == True)  # noqa: E712
        )
        groups = grps_result.scalars().all()

    for group in groups:
        slug = group.slug.value if hasattr(group.slug, "value") else str(group.slug)
        try:
            member = await bot.get_chat_member(chat_id=group.chat_id, user_id=user_id)
            if member.status in ("member", "restricted", "administrator", "creator"):
                # ลูกค้าเป็นสมาชิกกลุ่มนี้อยู่แล้ว → สร้าง invite link
                link = await create_one_time_invite(bot, group.chat_id, user_id)
                invite_links[slug] = link
                logger.info(
                    "CSV user %s is member of %s, created invite link", user_id, slug
                )
            else:
                logger.info(
                    "CSV user %s is NOT member of %s (status=%s), skipping",
                    user_id, slug, member.status,
                )
        except BadRequest:
            logger.debug("User %s not found in group %s", user_id, slug)
        except Exception as exc:
            logger.warning(
                "Error checking CSV user %s membership in %s: %s", user_id, slug, exc
            )

    if not invite_links:
        # ถ้าไม่พบว่าเป็นสมาชิกกลุ่มไหนเลย ส่งเฉพาะ G300 (minimum)
        logger.warning(
            "CSV user %s not found in any group, defaulting to G300", user_id
        )
        async with get_session() as session:
            g300_result = await session.execute(
                select(GroupRegistry).where(
                    GroupRegistry.slug == "G300",
                    GroupRegistry.is_active == True,  # noqa: E712
                )
            )
            g300 = g300_result.scalar_one_or_none()
        if g300:
            try:
                link = await create_one_time_invite(bot, g300.chat_id, user_id)
                slug = g300.slug.value if hasattr(g300.slug, "value") else str(g300.slug)
                invite_links[slug] = link
            except Exception as exc:
                logger.error("Failed to create default G300 invite for CSV user %s: %s", user_id, exc)

    return invite_links


async def _get_authorized_telegram_ids(group_slug: str) -> set[int]:
    """Get set of telegram_ids authorized to be in a specific group.

    A user is authorized if they have an ACTIVE subscription to a package
    that includes this group slug. Lifetime subs (duration_days=NULL)
    are always authorized.
    """
    now = datetime.utcnow()
    authorized: set[int] = set()

    async with get_session() as session:
        # Find all active subscriptions where the package grants access to this group
        result = await session.execute(
            select(User.telegram_id, Package.duration_days)
            .join(Subscription, Subscription.user_id == User.id)
            .join(Package, Subscription.package_id == Package.id)
            .where(
                Subscription.status == SubscriptionStatus.ACTIVE,
            )
        )

        for tg_id, duration_days in result.all():
            # We need to check if this package includes the group
            # Re-query to get groups_access (can't filter in SQL easily with comma-separated)
            pass

        # Better approach: get all active subs with their packages
        subs_result = await session.execute(
            select(User.telegram_id, Package.groups_access, Package.duration_days, Subscription.end_date)
            .join(Subscription, Subscription.user_id == User.id)
            .join(Package, Subscription.package_id == Package.id)
            .where(
                Subscription.status == SubscriptionStatus.ACTIVE,
            )
        )

        for tg_id, groups_access, duration_days, end_date in subs_result.all():
            group_list = [g.strip() for g in groups_access.split(",") if g.strip()]
            if group_slug not in group_list:
                continue

            # Lifetime subscription — always authorized
            if duration_days is None:
                authorized.add(tg_id)
                continue

            # Check if subscription hasn't expired
            if end_date and end_date > now:
                authorized.add(tg_id)

    return authorized


async def _get_admin_telegram_ids() -> set[int]:
    """Get telegram IDs of all admins (never kick admins)."""
    async with get_session() as session:
        result = await session.execute(
            select(User.telegram_id).where(User.is_admin == True)  # noqa: E712
        )
        return {row[0] for row in result.all()}


async def check_and_kick_unauthorized(bot: Bot, job_queue=None) -> dict[str, int]:
    """Check all active groups and kick unauthorized members.

    3-tier check:
    1. DB subscription active → ไม่เตะ
    2. CSV whitelist → ไม่เตะ
    3. ไม่เจอเลย → แจ้ง Admin + ปุ่ม + timeout 20 นาที

    Returns dict with counts: groups_checked, members_checked, notified, errors.
    """
    # Load CSV whitelist at startup
    load_csv_whitelist()

    stats = {"groups_checked": 0, "members_checked": 0, "notified": 0, "csv_whitelisted": 0, "errors": 0}

    # Get all active groups
    async with get_session() as session:
        groups_result = await session.execute(
            select(GroupRegistry).where(GroupRegistry.is_active == True)  # noqa: E712
        )
        groups = groups_result.scalars().all()

    admin_ids = await _get_admin_telegram_ids()

    # Get bot's own user ID to skip
    try:
        bot_info = await bot.get_me()
        bot_user_id = bot_info.id
    except Exception:
        bot_user_id = 0

    for group in groups:
        stats["groups_checked"] += 1
        slug = group.slug.value if hasattr(group.slug, "value") else str(group.slug)

        # ชั้น 1: Get authorized users from DB
        authorized_ids = await _get_authorized_telegram_ids(slug)

        # Strategy: check all known users in DB who are NOT authorized
        async with get_session() as session:
            all_users_result = await session.execute(
                select(User.telegram_id, User.id, User.username).where(
                    User.is_banned == False  # noqa: E712
                )
            )
            all_users = all_users_result.all()

        # Filter candidates BEFORE calling Telegram API
        candidates = []
        for tg_id, user_db_id, username in all_users:
            if tg_id in admin_ids or tg_id == bot_user_id:
                continue
            if tg_id in authorized_ids:
                continue
            if is_in_csv_whitelist(tg_id):
                stats["csv_whitelisted"] += 1
                continue
            candidates.append((tg_id, user_db_id, username))

        # Limit API calls per group per run (avoid rate limit)
        import asyncio
        MAX_CHECKS_PER_GROUP = 30

        for tg_id, user_db_id, username in candidates[:MAX_CHECKS_PER_GROUP]:
            stats["members_checked"] += 1

            # Check if user is actually in the group
            try:
                await asyncio.sleep(0.5)  # Rate limit: max 2 calls/sec
                member = await bot.get_chat_member(
                    chat_id=group.chat_id, user_id=tg_id
                )
            except BadRequest:
                continue
            except Exception as exc:
                logger.debug("Error checking member %s in %s: %s", tg_id, slug, exc)
                continue

            if member.status in ("member", "restricted"):
                # ── CSV Expired → เตะอัตโนมัติ ──
                if is_csv_expired(tg_id):
                    try:
                        await bot.ban_chat_member(chat_id=group.chat_id, user_id=tg_id)
                        await bot.unban_chat_member(chat_id=group.chat_id, user_id=tg_id, only_if_banned=True)
                        stats["notified"] += 1
                        log_msg = (
                            f"👤 TG ID: <code>{tg_id}</code> (@{username or '-'})\n"
                            f"📍 กลุ่ม: {group.title}\n"
                            f"📋 สาเหตุ: CSV status = Expired\n"
                            f"✅ เตะ + unban (สมัครใหม่ได้)"
                        )
                        await _log_kick_action(bot, log_msg)
                        logger.info("Auto-kicked CSV expired user %s from %s", tg_id, slug)
                    except Exception as exc:
                        logger.error("Failed to kick CSV expired %s: %s", tg_id, exc)
                        stats["errors"] += 1
                    continue

                # ชั้น 3: ไม่เจอเลย → แจ้ง Admin พร้อมปุ่ม (ไม่เตะทันที!)
                decision_key = f"{tg_id}_{group.chat_id}"
                if decision_key in pending_guardian_decisions:
                    # Already pending — skip
                    continue

                if job_queue:
                    await notify_admin_for_decision(
                        bot=bot,
                        user_id=tg_id,
                        username=username,
                        chat_id=group.chat_id,
                        group_title=group.title,
                        job_queue=job_queue,
                    )
                    stats["notified"] += 1
                else:
                    logger.warning(
                        "No job_queue available, cannot set timeout for user %s in %s",
                        tg_id, slug,
                    )
                    stats["errors"] += 1

    logger.info(
        "Unauthorized check: groups=%d checked=%d notified=%d csv_whitelisted=%d errors=%d",
        stats["groups_checked"],
        stats["members_checked"],
        stats["notified"],
        stats["csv_whitelisted"],
        stats["errors"],
    )

    # ส่ง summary ไปทั้ง Discord + Telegram log group
    summary_msg = (
        f"📋 <b>Guardian: ตรวจสมาชิกรอบ {datetime.now(TH_TZ).strftime('%H:%M')}</b>\n\n"
        f"🔍 กลุ่มที่เช็ค: {stats['groups_checked']}\n"
        f"👥 คนที่เช็ค: {stats['members_checked']}\n"
        f"⚠️ แจ้ง Admin: {stats['notified']}\n"
        f"✅ CSV whitelist: {stats['csv_whitelisted']}\n"
        f"❌ Errors: {stats['errors']}"
    )

    if stats["notified"] > 0 or stats["members_checked"] > 0:
        await _send_discord(summary_msg.replace('<b>','**').replace('</b>','**'))

        # ส่ง Telegram log group
        try:
            await bot.send_message(
                chat_id=GUARDIAN_LOG_GROUP,
                text=summary_msg,
                parse_mode="HTML",
            )
        except Exception as exc:
            logger.warning("Telegram summary log failed: %s", exc)

    return stats
