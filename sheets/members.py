"""Sheet 'สมาชิก' - อัปเดต real-time."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from shared.database import get_session
from shared.models import (
    Lead,
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

# Admin/test Telegram IDs — never write to sheets
EXCLUDED_TELEGRAM_IDS = {8502597269, 8567926841, 8116134249}


class MembersSheet:
    """Manages the 'สมาชิก' worksheet."""

    SHEET_NAME = "สมาชิก"

    @classmethod
    async def get_member_data(cls, user_id: int) -> dict | None:
        """Get member data for a specific user."""
        async with get_session() as session:
            user_q = await session.execute(
                select(User).where(User.id == user_id)
            )
            user = user_q.scalar_one_or_none()
            if not user:
                return None

            # Get active or latest subscription
            sub_q = await session.execute(
                select(Subscription, Package)
                .join(Package, Subscription.package_id == Package.id)
                .where(Subscription.user_id == user.id)
                .order_by(Subscription.created_at.desc())
                .limit(1)
            )
            sub_row = sub_q.first()

            # Count renewals (total confirmed payments)
            renewal_q = await session.execute(
                select(func.count(Payment.id)).where(
                    Payment.user_id == user.id,
                    Payment.status == PaymentStatus.CONFIRMED,
                )
            )
            renewal_count = (renewal_q.scalar() or 1) - 1  # First payment is not a renewal
            if renewal_count < 0:
                renewal_count = 0

            # Get latest payment method
            method_q = await session.execute(
                select(Payment.method)
                .where(
                    Payment.user_id == user.id,
                    Payment.status == PaymentStatus.CONFIRMED,
                )
                .order_by(Payment.created_at.desc())
                .limit(1)
            )
            latest_method = method_q.scalar()

            # Get source from leads
            lead_q = await session.execute(
                select(Lead.source)
                .where(Lead.user_id == user.id)
                .order_by(Lead.created_at.asc())
                .limit(1)
            )
            source = lead_q.scalar() or "direct"

            display_name = " ".join(
                filter(None, [user.first_name, user.last_name])
            ) or user.username or str(user.telegram_id)

            if sub_row:
                sub, pkg = sub_row
                start_th = sub.start_date.replace(tzinfo=timezone.utc).astimezone(TH_TZ)
                end_th = sub.end_date.replace(tzinfo=timezone.utc).astimezone(TH_TZ)

                status_map = {
                    SubscriptionStatus.ACTIVE: "✅ Active",
                    SubscriptionStatus.EXPIRED: "❌ Expired",
                    SubscriptionStatus.CANCELLED: "🚫 Cancelled",
                    SubscriptionStatus.SUSPENDED: "⏸️ Suspended",
                }

                method_map = {
                    PaymentMethod.SLIP: "สลิป",
                    PaymentMethod.PROMPTPAY: "พร้อมเพย์",
                    PaymentMethod.TRUEWALLET: "ซองทรู",
                    PaymentMethod.CRYPTO: "Crypto",
                }

                return {
                    "user_id": user.id,
                    "telegram_id": user.telegram_id,
                    "name": display_name,
                    "username": user.username or "",
                    "package": pkg.name,
                    "price": float(pkg.price),
                    "start_date": start_th.strftime("%Y-%m-%d"),
                    "end_date": end_th.strftime("%Y-%m-%d"),
                    "status": status_map.get(sub.status, str(sub.status)),
                    "payment_method": method_map.get(latest_method, str(latest_method)) if latest_method else "-",
                    "source": source,
                    "renewal_count": renewal_count,
                    "total_spent": float(user.total_spent),
                }
            else:
                return {
                    "user_id": user.id,
                    "telegram_id": user.telegram_id,
                    "name": display_name,
                    "username": user.username or "",
                    "package": "-",
                    "price": 0.0,
                    "start_date": "-",
                    "end_date": "-",
                    "status": "ไม่มีแพ็กเกจ",
                    "payment_method": "-",
                    "source": source,
                    "renewal_count": 0,
                    "total_spent": float(user.total_spent),
                }

    @classmethod
    async def update_member(cls, user_id: int) -> None:
        """Update or insert a single member row in Google Sheets.

        Admin/test users (EXCLUDED_TELEGRAM_IDS) are skipped.
        """
        data = await cls.get_member_data(user_id)
        if not data:
            logger.warning("User %d not found, cannot update member sheet", user_id)
            return

        # Skip admin/test users
        if data["telegram_id"] in EXCLUDED_TELEGRAM_IDS:
            logger.info(
                "Skipping member %d — telegram_id=%d is admin/test",
                user_id, data["telegram_id"],
            )
            return

        try:
            ws = SheetsManager.get_sheet(cls.SHEET_NAME)

            row = [
                data["user_id"],
                data["telegram_id"],
                data["name"],
                data["username"],
                data["package"],
                f"{data['price']:,.2f}",
                data["start_date"],
                data["end_date"],
                data["status"],
                data["payment_method"],
                data["source"],
                data["renewal_count"],
                f"{data['total_spent']:,.2f}",
            ]

            # Find existing row by User ID (column 1)
            existing_row = SheetsManager.find_row_by_value(ws, 1, str(data["user_id"]))
            if existing_row:
                SheetsManager.update_row(ws, existing_row, row)
                logger.info("Updated member row for user %d", user_id)
            else:
                SheetsManager.append_row(ws, row)
                logger.info("Appended member row for user %d", user_id)

        except Exception as exc:
            logger.error("Failed to update member sheet for user %d: %s", user_id, exc)
            SheetsManager.reset_client()
            raise

    @classmethod
    async def sync_all_members(cls) -> int:
        """Full sync: update all members in the sheet. Returns count."""
        import asyncio

        async with get_session() as session:
            result = await session.execute(select(User.id).order_by(User.id))
            user_ids = [row[0] for row in result.all()]

        count = 0
        for uid in user_ids:
            try:
                await cls.update_member(uid)
                count += 1
            except Exception as exc:
                logger.warning("Skipped member %d: %s", uid, exc)
            # Rate limit: Google Sheets API = 60 requests/min
            await asyncio.sleep(1.5)

        logger.info("Synced %d members to sheet", count)
        return count
