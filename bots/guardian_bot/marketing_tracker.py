"""Marketing invite link tracker.

When someone joins PROMO_HUB or PROMO_NEWS via a tracked invite link,
record the join in marketing_invite_joins for attribution.

Hooked from handle_chat_member_update in main.py.
"""
from __future__ import annotations

import logging
from typing import Optional

from shared.discord_notify import notify_marketer_join as _discord_notify_join

from sqlalchemy import text

from shared.database import get_session

logger = logging.getLogger(__name__)

# Slugs of groups that use marketing attribution
_MARKETING_GROUP_SLUGS = {"PROMO_HUB", "PROMO_NEWS"}

# Dedup window for rejoins. Must match sales_bot deep-link tracker (24h).
# Telegram may fire chat_member multiple times during status transitions,
# and a user could legitimately leave+rejoin within a day — count as same join.
_REJOIN_DEDUP_SECONDS = 86400  # 24 hours


async def track_marketing_join(
    *,
    group_slug: str,
    invite_link_name: Optional[str],
    telegram_id: int,
    tg_username: Optional[str],
    tg_first_name: Optional[str],
    tg_last_name: Optional[str],
) -> None:
    """Record a join from a tracked marketing link.

    Idempotent: if same (link_id, telegram_id, joined within last 24h) already
    exists, skip. (Telegram may fire chat_member twice during status transitions.)
    """
    if group_slug not in _MARKETING_GROUP_SLUGS:
        return  # Not a marketing-tracked group
    if not invite_link_name:
        return  # Joined via main group link, no attribution

    try:
        async with get_session() as session:
            row = (await session.execute(text(
                """
                SELECT id, marketer, platform, group_slug
                FROM marketing_invite_links
                WHERE name_tag = :name_tag
                  AND is_revoked = false
                LIMIT 1
                """
            ), {"name_tag": invite_link_name})).first()

            if row is None:
                logger.info(
                    "marketing: invite name=%r does not match any tracked link",
                    invite_link_name,
                )
                return

            link_id, marketer, platform, link_group_slug = row

            ur = (await session.execute(text(
                "SELECT id FROM users WHERE telegram_id = :tg"
            ), {"tg": telegram_id})).first()
            user_id = ur[0] if ur else None

            dup = (await session.execute(text(
                """
                SELECT 1 FROM marketing_invite_joins
                WHERE link_id = :lid AND telegram_id = :tg
                  AND joined_at > now() - make_interval(secs => :win)
                LIMIT 1
                """
            ), {"lid": link_id, "tg": telegram_id, "win": _REJOIN_DEDUP_SECONDS})).first()
            if dup:
                logger.info(
                    "marketing: dedupe join link_id=%s tg=%s (within %ss)",
                    link_id, telegram_id, _REJOIN_DEDUP_SECONDS,
                )
                return

            await session.execute(text(
                """
                INSERT INTO marketing_invite_joins
                  (link_id, telegram_id, user_id, tg_username, tg_first_name, tg_last_name)
                VALUES (:lid, :tg, :uid, :un, :fn, :ln)
                """
            ), {
                "lid": link_id, "tg": telegram_id, "uid": user_id,
                "un": tg_username, "fn": tg_first_name, "ln": tg_last_name,
            })
            await session.commit()

            logger.info(
                "marketing join tracked: link_id=%s marketer=%s platform=%s tg=%s",
                link_id, marketer, platform, telegram_id,
            )

            # Get group title + total joins for this link (for Discord notification)
            try:
                meta = (await session.execute(text(
                    """
                    SELECT
                      (SELECT title FROM group_registry WHERE slug = l.group_slug) AS group_title,
                      (SELECT COUNT(*) FROM marketing_invite_joins WHERE link_id = l.id) AS join_count
                    FROM marketing_invite_links l WHERE l.id = :lid
                    """
                ), {"lid": link_id})).first()
                if meta:
                    # Fire-and-forget Discord notification (do not await — must not block)
                    import asyncio as _asyncio
                    _asyncio.create_task(_discord_notify_join(
                        marketer=marketer, platform=platform,
                        group_title=meta.group_title or "",
                        telegram_id=telegram_id, tg_username=tg_username,
                        tg_first_name=tg_first_name, link_id=link_id,
                        total_joins_for_link=int(meta.join_count or 1),
                    ))
            except Exception as _nx:
                logger.warning("discord notify failed (non-fatal): %s", _nx)
    except Exception as exc:
        logger.exception("marketing_tracker error: %s", exc)


async def track_marketing_leave(
    *,
    group_slug: str,
    telegram_id: int,
) -> None:
    """Record a leave for an active marketing-tracked join row.

    Called when chat_member event reports new_status in (left, kicked).
    Silent on error: a user may leave from a group they did not join via a
    tracked link, or there may be no matching row (e.g. legacy joins, races,
    user already counted as left).

    Updates the most-recent un-left row for this (link, telegram_id) pair —
    a user who left + rejoined multiple times will only have their currently
    active row marked left.
    """
    if group_slug not in _MARKETING_GROUP_SLUGS:
        return

    try:
        async with get_session() as session:
            await session.execute(text(
                """
                UPDATE marketing_invite_joins
                   SET left_at = now()
                 WHERE id = (
                     SELECT j.id FROM marketing_invite_joins j
                     JOIN marketing_invite_links l ON l.id = j.link_id
                     WHERE j.telegram_id = :tg
                       AND j.left_at IS NULL
                       AND l.group_slug = :slug
                     ORDER BY j.joined_at DESC
                     LIMIT 1
                 )
                """
            ), {"tg": telegram_id, "slug": group_slug})
            await session.commit()
            logger.info(
                "marketing leave tracked: tg=%s group=%s",
                telegram_id, group_slug,
            )
    except Exception as exc:
        logger.warning("track_marketing_leave non-fatal error: %s", exc)
