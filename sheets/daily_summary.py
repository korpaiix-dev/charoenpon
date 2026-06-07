"""Sheet 'สรุปรายวัน' — อัปเดตทุกครั้งที่มี payment confirmed."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import func, select

from shared.database import get_session
from shared.models import (
    Payment, PaymentStatus, Subscription, SubscriptionStatus, User
)
from sheets.manager import SheetsManager

logger = logging.getLogger(__name__)

from shared.tz import TH_TZ

# Admin/test Telegram IDs — exclude from summary calculations
EXCLUDED_TELEGRAM_IDS = {8502597269, 8567926841, 8116134249, 7387557933}


class DailySummarySheet:
    """Manages the 'สรุปรายวัน' worksheet."""

    SHEET_NAME = "สรุปรายวัน"

    @classmethod
    async def update(cls, date: datetime | None = None) -> None:
        """Update or insert daily summary row."""
        if date is None:
            date = datetime.now(TH_TZ)

        day_start = date.replace(hour=0, minute=0, second=0, microsecond=0)
        day_end = day_start + timedelta(days=1)

        # Strip timezone for DB query (naive)
        day_start_utc = day_start.astimezone(timezone.utc).replace(tzinfo=None)
        day_end_utc = day_end.astimezone(timezone.utc).replace(tzinfo=None)
        day_str = day_start.strftime("%Y-%m-%d")

        async with get_session() as session:
            # Revenue (exclude admin/test users)
            rev_q = await session.execute(
                select(
                    func.count(Payment.id).label("orders"),
                    func.coalesce(func.sum(Payment.amount), 0).label("total"),
                )
                .join(User, Payment.user_id == User.id)
                .where(
                    Payment.status == PaymentStatus.CONFIRMED,
                    Payment.verified_at >= day_start_utc,
                    Payment.verified_at < day_end_utc,
                    User.telegram_id.notin_(EXCLUDED_TELEGRAM_IDS),
                )
            )
            rev = rev_q.one()

            # New members
            new_q = await session.execute(
                select(func.count(Subscription.id)).where(
                    Subscription.start_date >= day_start_utc,
                    Subscription.start_date < day_end_utc,
                    Subscription.status == SubscriptionStatus.ACTIVE,
                )
            )
            new_members = new_q.scalar() or 0

            # Churn
            churn_q = await session.execute(
                select(func.count(Subscription.id)).where(
                    Subscription.status == SubscriptionStatus.EXPIRED,
                    Subscription.end_date >= day_start_utc,
                    Subscription.end_date < day_end_utc,
                )
            )
            churn = churn_q.scalar() or 0

            # Active
            now_utc = datetime.utcnow()
            active_q = await session.execute(
                select(func.count(Subscription.id)).where(
                    Subscription.status == SubscriptionStatus.ACTIVE,
                    Subscription.end_date > now_utc,
                )
            )
            active = active_q.scalar() or 0

        revenue = float(rev.total)
        orders = rev.orders

        row = [
            day_str,
            f"{revenue:,.2f}",
            str(orders),
            str(new_members),
            str(churn),
            str(active),
            "0",  # API cost
            "0",  # Ad cost
            f"{revenue:,.2f}",  # Profit
            "",
        ]

        try:
            ws = SheetsManager.get_sheet(cls.SHEET_NAME)
            existing = SheetsManager.find_row_by_value(ws, 1, day_str)
            if existing:
                SheetsManager.update_row(ws, existing, row)
            else:
                SheetsManager.append_row(ws, row)
            logger.info("Daily summary updated for %s: ฿%s (%d orders)", day_str, revenue, orders)
        except Exception as exc:
            logger.error("Failed to update daily summary: %s", exc)
            SheetsManager.reset_client()
