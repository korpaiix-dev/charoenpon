# >>> FIX_TZ_VERIFIED_AT <<<  # standardized on verified_at (revenue recognition)
"""Sheet 'รายได้รายวัน' - อัปเดต real-time ทุกครั้งที่ชำระเงิน."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import func, select

from shared.database import get_session
from shared.models import (
    Package,
    Payment,
    PaymentMethod,
    PaymentStatus,
    Subscription,
    SubscriptionStatus,
    User,
)
from sheets.manager import SheetsManager

logger = logging.getLogger(__name__)

TH_TZ = timezone(timedelta(hours=7))

# Admin/test Telegram IDs — exclude from revenue calculations
EXCLUDED_TELEGRAM_IDS = {8502597269, 8567926841, 8116134249, 7387557933}


class DailyRevenueSheet:
    """Manages the 'รายได้รายวัน' worksheet."""

    SHEET_NAME = "รายได้รายวัน"

    @classmethod
    async def get_daily_data(cls, date: datetime | None = None) -> dict:
        """Query daily revenue data from the database."""
        if date is None:
            date = datetime.now(TH_TZ)

        day_start = date.replace(hour=0, minute=0, second=0, microsecond=0)
        day_end = day_start + timedelta(days=1)

        # DB uses naive datetime (UTC), strip timezone info
        day_start_utc = day_start.astimezone(timezone.utc).replace(tzinfo=None)
        day_end_utc = day_end.astimezone(timezone.utc).replace(tzinfo=None)

        async with get_session() as session:
            # Revenue by payment method (exclude admin/test users)
            method_q = await session.execute(
                select(
                    Payment.method,
                    func.coalesce(func.sum(Payment.amount), 0).label("total"),
                )
                .join(User, Payment.user_id == User.id)
                .where(
                    Payment.status == PaymentStatus.CONFIRMED,
                    Payment.verified_at >= day_start_utc,
                    Payment.verified_at < day_end_utc,
                    User.telegram_id.notin_(EXCLUDED_TELEGRAM_IDS),
                )
                .group_by(Payment.method)
            )
            method_totals = {row.method: float(row.total) for row in method_q.all()}

            promptpay_total = method_totals.get(PaymentMethod.PROMPTPAY, 0.0) + method_totals.get(PaymentMethod.SLIP, 0.0)
            truewallet_total = method_totals.get(PaymentMethod.TRUEWALLET, 0.0)
            crypto_total = method_totals.get(PaymentMethod.CRYPTO, 0.0)
            grand_total = promptpay_total + truewallet_total + crypto_total

            # Revenue per package tier (exclude admin/test users)
            pkg_q = await session.execute(
                select(
                    Package.tier,
                    func.coalesce(func.sum(Payment.amount), 0).label("total"),
                )
                .join(Package, Payment.package_id == Package.id)
                .join(User, Payment.user_id == User.id)
                .where(
                    Payment.status == PaymentStatus.CONFIRMED,
                    Payment.verified_at >= day_start_utc,
                    Payment.verified_at < day_end_utc,
                    User.telegram_id.notin_(EXCLUDED_TELEGRAM_IDS),
                )
                .group_by(Package.tier)
            )
            tier_totals = {row.tier.value: float(row.total) for row in pkg_q.all()}

            # Sales count (exclude admin/test users)
            sales_q = await session.execute(
                select(func.count(Payment.id))
                .join(User, Payment.user_id == User.id)
                .where(
                    Payment.status == PaymentStatus.CONFIRMED,
                    Payment.verified_at >= day_start_utc,
                    Payment.verified_at < day_end_utc,
                    User.telegram_id.notin_(EXCLUDED_TELEGRAM_IDS),
                )
            )
            sales_count = sales_q.scalar() or 0

            # New members today
            new_members_q = await session.execute(
                select(func.count(Subscription.id)).where(
                    Subscription.created_at >= day_start_utc,
                    Subscription.created_at < day_end_utc,
                )
            )
            new_members = new_members_q.scalar() or 0

            # Churn today (expired today)
            churn_q = await session.execute(
                select(func.count(Subscription.id)).where(
                    Subscription.status == SubscriptionStatus.EXPIRED,
                    Subscription.end_date >= day_start_utc,
                    Subscription.end_date < day_end_utc,
                )
            )
            churn = churn_q.scalar() or 0

            # Active subscriptions
            active_q = await session.execute(
                select(func.count(Subscription.id)).where(
                    Subscription.status == SubscriptionStatus.ACTIVE,
                )
            )
            active = active_q.scalar() or 0

        return {
            "date": day_start.strftime("%Y-%m-%d"),
            "promptpay": promptpay_total,
            "truewallet": truewallet_total,
            "total": grand_total,
            "tier_300": tier_totals.get("300", 0.0),
            "tier_500": tier_totals.get("500", 0.0),
            "tier_1299": tier_totals.get("1299", 0.0),
            "tier_2499": tier_totals.get("2499", 0.0),
            "sales_count": sales_count,
            "new_members": new_members,
            "churn": churn,
            "active": active,
        }

    @classmethod
    async def update(cls, date: datetime | None = None) -> None:
        """Update or insert daily revenue row in Google Sheets."""
        data = await cls.get_daily_data(date)

        try:
            ws = SheetsManager.get_sheet(cls.SHEET_NAME)

            row = [
                data["date"],
                f"{data['promptpay']:,.2f}",
                f"{data['truewallet']:,.2f}",
                f"{data['total']:,.2f}",
                f"{data['tier_300']:,.2f}",
                f"{data['tier_500']:,.2f}",
                f"{data['tier_1299']:,.2f}",
                f"{data['tier_2499']:,.2f}",
                data["sales_count"],
                data["new_members"],
                data["churn"],
                data["active"],
            ]

            existing_row = SheetsManager.find_row_by_value(ws, 1, data["date"])
            if existing_row:
                SheetsManager.update_row(ws, existing_row, row)
                logger.info("Updated daily revenue row for %s", data["date"])
            else:
                SheetsManager.append_row(ws, row)
                logger.info("Appended daily revenue row for %s", data["date"])

        except Exception as exc:
            logger.error("Failed to update daily revenue sheet: %s", exc)
            SheetsManager.reset_client()
            raise
