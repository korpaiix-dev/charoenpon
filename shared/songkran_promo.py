"""Songkran promo helpers for temporary bonus-group access."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from sqlalchemy import select

from shared.database import get_session
from shared.models import Package, PackageTier, Subscription, SubscriptionStatus, User

from shared.tz import TH_TZ
UTC = timezone.utc

PROMO_SONGKRAN_SLUG = "PROMO_SONGKRAN_2026"
PROMO_SONGKRAN_CHAT_ID = -1003970513277
PROMO_SONGKRAN_TITLE = "โปรโมชั่นสงกรานต์"
PROMO_SONGKRAN_PACKAGE_TIERS = {PackageTier.TIER_1299}

PROMO_SONGKRAN_START_TH = datetime(2026, 4, 14, 3, 0, 0, tzinfo=TH_TZ)
PROMO_SONGKRAN_END_TH = PROMO_SONGKRAN_START_TH + timedelta(days=7)
PROMO_SONGKRAN_START_UTC = PROMO_SONGKRAN_START_TH.astimezone(UTC).replace(tzinfo=None)
PROMO_SONGKRAN_END_UTC = PROMO_SONGKRAN_END_TH.astimezone(UTC).replace(tzinfo=None)


def _normalize_slug(slug: object) -> str:
    if hasattr(slug, "value"):
        return str(getattr(slug, "value"))
    return str(slug)


def is_songkran_promo_window(now: datetime | None = None) -> bool:
    """DISABLED 2026-06-28: boss reset all legacy promos."""
    return False



def is_songkran_bonus_slug(slug: object) -> bool:
    return _normalize_slug(slug) == PROMO_SONGKRAN_SLUG


def get_songkran_special_group() -> SimpleNamespace:
    return SimpleNamespace(
        slug=PROMO_SONGKRAN_SLUG,
        chat_id=PROMO_SONGKRAN_CHAT_ID,
        title=PROMO_SONGKRAN_TITLE,
        is_active=True,
    )


def get_group_display_title(slug: object) -> str:
    if is_songkran_bonus_slug(slug):
        return PROMO_SONGKRAN_TITLE
    return _normalize_slug(slug)


async def package_is_songkran_bonus_eligible(package_id: int) -> bool:
    async with get_session() as session:
        result = await session.execute(select(Package.tier).where(Package.id == package_id))
        tier = result.scalar_one_or_none()
    return tier in PROMO_SONGKRAN_PACKAGE_TIERS


async def user_has_songkran_bonus_access(user_telegram_id: int, now: datetime | None = None) -> bool:
    now = now or datetime.utcnow()
    async with get_session() as session:
        result = await session.execute(
            select(Subscription.id)
            .join(User, Subscription.user_id == User.id)
            .join(Package, Subscription.package_id == Package.id)
            .where(
                User.telegram_id == user_telegram_id,
                Subscription.status == SubscriptionStatus.ACTIVE,
                Subscription.end_date > now,
                Subscription.start_date >= PROMO_SONGKRAN_START_UTC,
                Subscription.start_date < PROMO_SONGKRAN_END_UTC,
                Package.tier.in_(PROMO_SONGKRAN_PACKAGE_TIERS),
            )
            .limit(1)
        )
        return result.scalar_one_or_none() is not None


async def should_include_songkran_bonus_group(user_telegram_id: int, package_id: int | None = None) -> bool:
    if await user_has_songkran_bonus_access(user_telegram_id):
        return True
    if package_id is None or not is_songkran_promo_window():
        return False
    return await package_is_songkran_bonus_eligible(package_id)


async def get_songkran_bonus_authorized_ids(now: datetime | None = None) -> set[int]:
    now = now or datetime.utcnow()
    async with get_session() as session:
        result = await session.execute(
            select(User.telegram_id)
            .join(Subscription, Subscription.user_id == User.id)
            .join(Package, Subscription.package_id == Package.id)
            .where(
                Subscription.status == SubscriptionStatus.ACTIVE,
                Subscription.end_date > now,
                Subscription.start_date >= PROMO_SONGKRAN_START_UTC,
                Subscription.start_date < PROMO_SONGKRAN_END_UTC,
                Package.tier.in_(PROMO_SONGKRAN_PACKAGE_TIERS),
            )
            .distinct()
        )
        return {row[0] for row in result.all() if row[0]}
