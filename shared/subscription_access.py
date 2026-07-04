# -*- coding: utf-8 -*-
"""SINGLE SOURCE OF TRUTH for a customer's *current* group access.

A user can hold several subscriptions at once (e.g. GOD MODE ถาวร lifetime + a short GOD-90).
Any decision about access — kicking on expiry, "your sub is expiring" warnings, unauthorized-member
checks — MUST look at ALL of the user's active subscriptions, not one in isolation. Otherwise a
paying lifetime customer gets kicked / warned when an unrelated shorter sub lapses.

Use these helpers everywhere instead of reasoning about a single subscription.
"""
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


async def user_active_group_slugs(user_id: int, exclude_sub_id: int | None = None) -> set[str]:
    """Union of group slugs the user currently has access to via ALL active (non-expired) subs.

    Pass exclude_sub_id to ask 'what would they still have if THIS sub were gone'.
    Returns an empty set on any error (caller decides how strict to be).
    """
    from shared.database import get_session
    from shared.models import Subscription, Package, SubscriptionStatus
    from sqlalchemy import select

    now = datetime.utcnow()
    covered: set[str] = set()
    try:
        async with get_session() as s:
            q = (
                select(Package)
                .join(Subscription, Subscription.package_id == Package.id)
                .where(
                    Subscription.user_id == user_id,
                    Subscription.status == SubscriptionStatus.ACTIVE,
                    Subscription.end_date > now,
                )
            )
            if exclude_sub_id is not None:
                q = q.where(Subscription.id != exclude_sub_id)
            for pkg in (await s.execute(q)).scalars().all():
                for g in (pkg.group_list or []):
                    covered.add(g)
    except Exception as exc:
        logger.warning("user_active_group_slugs(%s) failed: %s", user_id, exc)
    return covered


async def still_covered_if_sub_expires(user_id: int, sub_id: int) -> bool:
    """True if — after subscription `sub_id` expires — the user STILL has access to ALL of that
    sub's groups via their OTHER active subscriptions. Use to suppress false expiry kicks/warnings.
    Fail-safe: returns False on error (so we don't accidentally suppress a real expiry)."""
    from shared.database import get_session
    from shared.models import Subscription, Package, SubscriptionStatus
    from sqlalchemy import select

    try:
        async with get_session() as s:
            this_pkg = (
                await s.execute(
                    select(Package)
                    .join(Subscription, Subscription.package_id == Package.id)
                    .where(Subscription.id == sub_id)
                )
            ).scalars().first()
            if not this_pkg:
                return False
            this_groups = set(this_pkg.group_list or [])
        if not this_groups:
            return True  # sub grants no groups → nothing lost
        covered = await user_active_group_slugs(user_id, exclude_sub_id=sub_id)
        return this_groups <= covered
    except Exception as exc:
        logger.warning("still_covered_if_sub_expires(%s,%s) failed: %s", user_id, sub_id, exc)
        return False
