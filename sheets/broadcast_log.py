"""Sheet 'Broadcast Log' - บันทึกทุกครั้งที่ส่ง broadcast."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from shared.database import get_session
from shared.models import BroadcastLog as BroadcastLogModel, User
from sheets.manager import SheetsManager

logger = logging.getLogger(__name__)

TH_TZ = timezone(timedelta(hours=7))


class BroadcastLogSheet:
    """Manages the 'Broadcast Log' worksheet."""

    SHEET_NAME = "Broadcast Log"

    @classmethod
    async def log_broadcast(
        cls,
        broadcast_id: int | None = None,
        broadcast_type: str = "text",
        target_group: str = "ทั้งหมด",
        total_sent: int = 0,
        success: int = 0,
        blocked: int = 0,
        never_started: int = 0,
        errors: int = 0,
        admin_name: str = "",
    ) -> None:
        """Append a broadcast log entry to Google Sheets."""
        now_th = datetime.now(TH_TZ)
        date_str = now_th.strftime("%Y-%m-%d")
        time_str = now_th.strftime("%H:%M:%S")

        # If broadcast_id provided, fetch details from DB
        if broadcast_id is not None:
            async with get_session() as session:
                result = await session.execute(
                    select(BroadcastLogModel, User)
                    .join(User, BroadcastLogModel.admin_id == User.id)
                    .where(BroadcastLogModel.id == broadcast_id)
                )
                row = result.first()
                if row:
                    broadcast, admin = row
                    total_sent = broadcast.total_sent
                    errors = broadcast.total_failed
                    success = total_sent - errors
                    admin_name = admin.username or admin.first_name or str(admin.telegram_id)

                    if broadcast.target_tier:
                        target_group = f"Tier {broadcast.target_tier.value}"
                    elif broadcast.target_group:
                        target_group = broadcast.target_group.value
                    else:
                        target_group = "ทั้งหมด"

                    if broadcast.media_file_id:
                        broadcast_type = "media"
                    else:
                        broadcast_type = "text"

        try:
            ws = SheetsManager.get_sheet(cls.SHEET_NAME)

            row_data = [
                date_str,
                time_str,
                broadcast_type,
                target_group,
                total_sent,
                success,
                blocked,
                never_started,
                errors,
                admin_name,
            ]

            SheetsManager.append_row(ws, row_data)
            logger.info(
                "Logged broadcast: %s sent=%d success=%d blocked=%d errors=%d",
                broadcast_type, total_sent, success, blocked, errors,
            )

        except Exception as exc:
            logger.error("Failed to log broadcast to sheet: %s", exc)
            SheetsManager.reset_client()
            raise

    @classmethod
    async def sync_from_db(cls, limit: int = 100) -> int:
        """Sync recent broadcast logs from DB to sheet. Returns count."""
        async with get_session() as session:
            result = await session.execute(
                select(BroadcastLogModel, User)
                .join(User, BroadcastLogModel.admin_id == User.id)
                .order_by(BroadcastLogModel.created_at.desc())
                .limit(limit)
            )
            rows = result.all()

        if not rows:
            return 0

        try:
            ws = SheetsManager.get_sheet(cls.SHEET_NAME)

            count = 0
            for broadcast, admin in reversed(rows):
                created_th = broadcast.created_at.replace(tzinfo=timezone.utc).astimezone(TH_TZ)

                if broadcast.target_tier:
                    target = f"Tier {broadcast.target_tier.value}"
                elif broadcast.target_group:
                    target = broadcast.target_group.value
                else:
                    target = "ทั้งหมด"

                btype = "media" if broadcast.media_file_id else "text"
                admin_name = admin.username or admin.first_name or str(admin.telegram_id)
                success = broadcast.total_sent - broadcast.total_failed

                row_data = [
                    created_th.strftime("%Y-%m-%d"),
                    created_th.strftime("%H:%M:%S"),
                    btype,
                    target,
                    broadcast.total_sent,
                    success,
                    0,  # blocked (not tracked in DB)
                    0,  # never_started (not tracked in DB)
                    broadcast.total_failed,
                    admin_name,
                ]

                SheetsManager.append_row(ws, row_data)
                count += 1

            logger.info("Synced %d broadcast logs to sheet", count)
            return count

        except Exception as exc:
            logger.error("Failed to sync broadcast logs: %s", exc)
            SheetsManager.reset_client()
            raise
