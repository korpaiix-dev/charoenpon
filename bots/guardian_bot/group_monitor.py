"""Group Monitor - Guardian Bot (ยาม).

ตรวจสมาชิกทุกกลุ่ม:
- kick ผู้ที่ไม่มีสิทธิ์ทันที
- Lifetime (duration_days=NULL) ห้ามแตะ
- บันทึก log ทุก action
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import and_, select
from telegram import Bot
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


async def _get_authorized_telegram_ids(group_slug: str) -> set[int]:
    """Get set of telegram_ids authorized to be in a specific group.

    A user is authorized if they have an ACTIVE subscription to a package
    that includes this group slug. Lifetime subs (duration_days=NULL)
    are always authorized.
    """
    now = datetime.now(timezone.utc)
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


async def check_and_kick_unauthorized(bot: Bot) -> dict[str, int]:
    """Check all active groups and kick unauthorized members.

    Returns dict with counts: groups_checked, members_checked, kicked, errors.
    """
    stats = {"groups_checked": 0, "members_checked": 0, "kicked": 0, "errors": 0}

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

        # Get authorized users for this group
        authorized_ids = await _get_authorized_telegram_ids(slug)

        # Get current group members via getChatAdministrators
        # Note: Telegram API doesn't provide a way to list all members.
        # We use chat member updates and a polling approach via getChatMember
        # for known users. For new joins, we rely on ChatMemberUpdated events.

        # Strategy: check all known users in DB who are NOT authorized
        async with get_session() as session:
            # Get all users from DB
            all_users_result = await session.execute(
                select(User.telegram_id, User.id, User.username).where(
                    User.is_banned == False  # noqa: E712
                )
            )
            all_users = all_users_result.all()

        for tg_id, user_db_id, username in all_users:
            # Skip admins and bot itself
            if tg_id in admin_ids or tg_id == bot_user_id:
                continue

            # Skip authorized users
            if tg_id in authorized_ids:
                continue

            stats["members_checked"] += 1

            # Check if user is actually in the group
            try:
                member = await bot.get_chat_member(
                    chat_id=group.chat_id, user_id=tg_id
                )
            except BadRequest:
                # User not in group or other error — skip
                continue
            except Exception as exc:
                logger.debug("Error checking member %s in %s: %s", tg_id, slug, exc)
                continue

            if member.status in ("member", "restricted"):
                # Unauthorized member found — kick!
                try:
                    await bot.ban_chat_member(
                        chat_id=group.chat_id, user_id=tg_id
                    )
                    await bot.unban_chat_member(
                        chat_id=group.chat_id,
                        user_id=tg_id,
                        only_if_banned=True,
                    )
                    stats["kicked"] += 1

                    await log_admin_action(
                        admin_id=GUARDIAN_BOT_ID,
                        action="kick_unauthorized",
                        target_type="user",
                        target_id=user_db_id,
                        details=f"tg={tg_id} group={slug} username={username}",
                    )

                    logger.info(
                        "Kicked unauthorized user %s (@%s) from group %s",
                        tg_id,
                        username,
                        slug,
                    )

                    # Notify user
                    try:
                        await bot.send_message(
                            chat_id=tg_id,
                            text=(
                                f"⚠️ คุณถูกนำออกจากกลุ่ม {group.title} "
                                "เนื่องจากไม่มี subscription ที่ active ครับ\n\n"
                                "หากต้องการเข้าใหม่ สามารถสมัครแพ็กเกจได้ที่ @CharoenponBot ครับ"
                            ),
                        )
                    except Exception:
                        pass  # User may have blocked bot

                except Forbidden:
                    logger.warning(
                        "No permission to kick %s from %s", tg_id, slug
                    )
                    stats["errors"] += 1
                except BadRequest as e:
                    logger.error("Error kicking %s from %s: %s", tg_id, slug, e)
                    stats["errors"] += 1
                except Exception as exc:
                    logger.error(
                        "Unexpected error kicking %s from %s: %s", tg_id, slug, exc
                    )
                    stats["errors"] += 1

    logger.info(
        "Unauthorized check: groups=%d members_checked=%d kicked=%d errors=%d",
        stats["groups_checked"],
        stats["members_checked"],
        stats["kicked"],
        stats["errors"],
    )

    return stats
