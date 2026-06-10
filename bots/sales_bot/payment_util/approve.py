"""Approval helpers extracted from handlers/payment.py (Round 4 strangler-fig).
Core _approve_payment writes the payment/subscription row, generates invite links,
and sends the confirmation DM. Used by both slip handler and TrueMoney handler.
"""
from __future__ import annotations

import asyncio
import html as _html
import logging
import os
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional

from sqlalchemy import select, update as _upd
from shared.database import get_session
from shared.songkran_promo import get_group_display_title
from shared.models import (
    User, Payment, PaymentStatus, PaymentMethod,
    Subscription, SubscriptionStatus,
    Package, PackageTier,
    GroupRegistry,
)
import telegram as tg

logger = logging.getLogger(__name__)

async def _approve_payment(
    payment: Payment,
    user_telegram_id: int,
    bot,
) -> list[str]:
    """Approve payment: create subscription and generate one-time invite links.

    ใช้ Guardian Bot สร้าง one-time invite link (member_limit=1, expire 24h)
    สำหรับทุกกลุ่มที่แพ็กเกจให้สิทธิ์
    """
    import telegram as tg
    from bots.guardian_bot.group_monitor import generate_invite_links_for_user

    invite_links: list[str] = []
    package_id: int = 0

    async with get_session() as session:
        # Update payment status
        result = await session.execute(
            select(Payment).where(Payment.id == payment.id)
        )
        db_payment = result.scalar_one()
        db_payment.status = PaymentStatus.CONFIRMED
        db_payment.verified_at = datetime.utcnow()

        # Get package
        pkg_result = await session.execute(
            select(Package).where(Package.id == db_payment.package_id)
        )
        package = pkg_result.scalar_one()
        package_id = package.id

        # ─── Birthday Upgrade bonus: calc days remaining in TIER_500 before expiring it ───
        _birthday_bonus_days = 0
        _birthday_offer_id = None
        try:
            from sqlalchemy import text as _sql_text
            _r = await session.execute(_sql_text("""
                SELECT bo.id AS offer_id,
                       GREATEST(0, EXTRACT(DAY FROM sub.end_date - NOW()))::int AS days
                FROM birthday_upgrade_offers bo
                JOIN users u ON u.id = bo.user_id
                LEFT JOIN subscriptions sub ON sub.user_id = bo.user_id
                     AND sub.status = 'ACTIVE'
                     AND sub.end_date > NOW()
                LEFT JOIN packages pk ON pk.id = sub.package_id AND pk.tier = 'TIER_500'
                WHERE u.id = :uid AND bo.expires_at > NOW() AND bo.upgraded_to_tier IS NULL
                ORDER BY sub.end_date DESC LIMIT 1
            """), {"uid": db_payment.user_id})
            _row = _r.fetchone()
            if _row and _row.offer_id:
                _birthday_offer_id = int(_row.offer_id)
                _birthday_bonus_days = int(_row.days or 0)
        except Exception as _exc_bd:
            logger.warning("Birthday bonus lookup failed: %s", _exc_bd)

        # Expire existing active subscriptions (prevent duplicates)
        # BUT skip lifetime subs when buying add-on packages
        from sqlalchemy import update as sa_update_dup
        is_addon = package and package.tier == PackageTier.TIER_ADD500 if hasattr(PackageTier, 'TIER_ADD500') else (package and package.tier.value == 'ADD500')
        if is_addon:
            await session.execute(
                sa_update_dup(Subscription)
                .where(
                    Subscription.user_id == db_payment.user_id,
                    Subscription.status == SubscriptionStatus.ACTIVE,
                    Subscription.package_id == db_payment.package_id,
                )
                .values(status=SubscriptionStatus.EXPIRED)
            )
        else:
            # FIX 2025-05-21 (Phase 2b): Protect lifetime (TIER_2499) — only
            # expire non-lifetime active subs. Otherwise a customer who already
            # paid 2499 forever loses access when buying any add-on / re-buy,
            # and guardian-bot kicks them out of the lifetime groups.
            sub_ids_result = await session.execute(
                select(Subscription.id)
                .join(Package, Subscription.package_id == Package.id)
                .where(
                    Subscription.user_id == db_payment.user_id,
                    Subscription.status == SubscriptionStatus.ACTIVE,
                    Package.tier != PackageTier.TIER_2499,
                )
            )
            non_lifetime_ids = [row[0] for row in sub_ids_result]
            if non_lifetime_ids:
                await session.execute(
                    sa_update_dup(Subscription)
                    .where(Subscription.id.in_(non_lifetime_ids))
                    .values(status=SubscriptionStatus.EXPIRED)
                )

        # Create subscription
        now = datetime.utcnow()
        # Trial 24 ชม.: ใช้ hours=24 แทน days=1 เพื่อให้แม่นยำ
        if package.tier == PackageTier.TIER_99:
            end_date = now + timedelta(hours=24)
        else:
            end_date = now + timedelta(days=package.duration_days)
        # Apply Birthday bonus (rolling-over remaining days from TIER_500)
        if _birthday_bonus_days > 0 and package.tier == PackageTier.TIER_1299:
            end_date = end_date + timedelta(days=_birthday_bonus_days)
            logger.info("Birthday upgrade: +%s days bonus (TIER_500 remaining) for user %s",
                       _birthday_bonus_days, db_payment.user_id)
        sub = Subscription(
            user_id=db_payment.user_id,
            package_id=package.id,
            status=SubscriptionStatus.ACTIVE,
            start_date=now,
            end_date=end_date,
            payment_id=db_payment.id,
        )
        session.add(sub)
        await session.flush()

        # SHAKER lottery: assign unique numbers per ticket purchased
        if package.tier == PackageTier.TIER_100:
            try:
                from bots.sales_bot.handlers.shaker import assign_shaker_numbers
                # Number of tickets = payment.amount / 100 (1 baht = 0.01 ticket)
                ticket_count = max(1, int(float(db_payment.amount) // 100))
                _shaker_nums = await assign_shaker_numbers(
                    db_payment.user_id, user_telegram_id, ticket_count, db_payment.id,
                )
                logger.info("SHAKER: user %s got %s number(s): %s",
                           db_payment.user_id, ticket_count, _shaker_nums)
                # stash for caller to include in confirmation msg
                invite_links.insert(0, f"🎫 เลขลุ้น: {' '.join(_shaker_nums)}")
            except Exception as _exc_sh:
                logger.error("SHAKER number assignment failed: %s", _exc_sh)

        # Mark birthday offer as used (if applicable)
        if _birthday_offer_id and package.tier in (PackageTier.TIER_1299, PackageTier.TIER_2499):
            try:
                await session.execute(_sql_text("""
                    UPDATE birthday_upgrade_offers
                    SET upgraded_to_tier = :tier,
                        upgraded_at = NOW(),
                        payment_id = :pid
                    WHERE id = :oid
                """), {
                    "tier": package.tier.value,
                    "pid": db_payment.id,
                    "oid": _birthday_offer_id,
                })
                logger.info("Birthday offer %s marked upgraded → %s", _birthday_offer_id, package.tier.value)
            except Exception as _exc_mark:
                logger.warning("Birthday offer mark fail: %s", _exc_mark)

    # สร้าง one-time invite link ผ่าน Guardian Bot
    # ใช้ Guardian Bot (ที่เป็น admin ของกลุ่ม) สร้าง invite link
    guardian_token = os.environ.get("GUARDIAN_BOT_TOKEN", "")
    guardian_bot = tg.Bot(token=guardian_token) if guardian_token else bot
    await guardian_bot.initialize()
    links_dict = await generate_invite_links_for_user(
        guardian_bot, user_telegram_id, package_id
    )

    # จับคู่ slug กับ title สำหรับแสดงผล
    for slug, link in links_dict.items():
        async with get_session() as session:
            grp_result = await session.execute(
                select(GroupRegistry).where(GroupRegistry.slug == slug)
            )
            group = grp_result.scalar_one_or_none()
            title = group.title if group else get_group_display_title(slug)
        invite_links.append(f"• {title}: {link}")

    return invite_links


WELCOME_REFERRAL_DM = (
    '✅ สมัครสำเร็จ! ชวนเพื่อน 1 คน ได้ VIP ฟรี 7 วัน\n'
    '\n'
    '👉 /invite\n'
    '\n'
    'ข้อความชวนเพื่อน (คัดลอกส่งได้เลย):\n'
    '<code>มา VIP เจริญพร กัน! คลิปเต็มไม่เบลอทุกวัน 10,000+ คลิป สมัครที่ @NamwarnJarern_bot</code>'
)
