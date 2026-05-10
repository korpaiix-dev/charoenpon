"""Sheet 'รายรับ' — บันทึกทุก payment ที่เกิดขึ้น (real-time)."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from shared.database import get_session
from shared.models import Package, Payment, User
from sheets.manager import SheetsManager

logger = logging.getLogger(__name__)

TH_TZ = timezone(timedelta(hours=7))

METHOD_MAP = {
    "SLIP": "สลิปโอน",
    "PROMPTPAY": "พร้อมเพย์",
    "TRUEWALLET": "ซองทรู",
    "CRYPTO": "Crypto",
}

STATUS_MAP = {
    "CONFIRMED": "✅ อนุมัติ",
}

# Admin/test Telegram IDs — never write to sheets
EXCLUDED_TELEGRAM_IDS = {8502597269, 8370054523, 8567926841, 8116134249}


class IncomeLogSheet:
    """Manages the 'รายรับ' worksheet — one row per payment."""

    SHEET_NAME = "รายรับ"

    @classmethod
    async def log_payment(
        cls,
        payment_id: int,
        approved_by: str = "-",
    ) -> None:
        """Append or update a CONFIRMED payment row in the 'รายรับ' sheet.

        Only CONFIRMED payments are written. PENDING/REJECTED are skipped.
        Admin/test users are also excluded.
        """
        async with get_session() as session:
            result = await session.execute(
                select(Payment, User, Package)
                .join(User, Payment.user_id == User.id)
                .join(Package, Payment.package_id == Package.id)
                .where(Payment.id == payment_id)
            )
            row = result.first()
            if not row:
                logger.warning("Payment %d not found", payment_id)
                return

            payment, user, package = row

        # Only write CONFIRMED payments to sheet
        if payment.status.value != "CONFIRMED":
            logger.info(
                "Skipping payment #%d — status=%s (only CONFIRMED goes to sheet)",
                payment_id, payment.status.value,
            )
            return

        # Skip admin/test users
        if user.telegram_id in EXCLUDED_TELEGRAM_IDS:
            logger.info(
                "Skipping payment #%d — telegram_id=%d is admin/test",
                payment_id, user.telegram_id,
            )
            return

        created_th = payment.created_at.replace(tzinfo=timezone.utc).astimezone(TH_TZ)
        display_name = user.first_name or user.username or str(user.telegram_id)

        row_data = [
            created_th.strftime("%Y-%m-%d"),
            created_th.strftime("%H:%M"),
            display_name,
            str(float(payment.amount)),
            package.name,
            METHOD_MAP.get(payment.method.value, payment.method.value),
            STATUS_MAP.get(payment.status.value, payment.status.value),
            approved_by,
            str(user.telegram_id),
            f"#PAY{payment.id}",
        ]

        try:
            ws = SheetsManager.get_sheet(cls.SHEET_NAME)

            # Find existing row by #PAY{id}
            ref = f"#PAY{payment.id}"
            existing = SheetsManager.find_row_by_value(ws, 10, ref)
            if existing:
                SheetsManager.update_row(ws, existing, row_data)
                logger.info("Updated income row for payment #%d", payment_id)
            else:
                SheetsManager.append_row(ws, row_data)
                logger.info("Appended income row for payment #%d", payment_id)

        except Exception as exc:
            logger.error("Failed to log payment #%d to sheet: %s", payment_id, exc)
            SheetsManager.reset_client()
