"""Sheet 'ค่าใช้จ่าย API' - บันทึกทุกครั้งที่เรียก AI."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import func, select

from shared.database import get_session
from shared.models import ApiCostLog, Payment, PaymentStatus
from sheets.manager import SheetsManager

logger = logging.getLogger(__name__)

from shared.tz import TH_TZ

SUMMARY_TODAY_LABEL = "📊 รวมวันนี้"
SUMMARY_MONTH_LABEL = "📊 รวมเดือนนี้"
SUMMARY_PERCENT_LABEL = "📊 % ของรายรับ"


class ApiCostsSheet:
    """Manages the 'ค่าใช้จ่าย API' worksheet."""

    SHEET_NAME = "ค่าใช้จ่าย API"

    @classmethod
    async def log_api_call(
        cls,
        service: str,
        agent: str,
        input_tokens: int,
        output_tokens: int,
        cost_usd: float,
        cost_thb: float,
        note: str = "",
    ) -> None:
        """Append a single API call row and update summary rows."""
        now_th = datetime.now(TH_TZ)
        date_str = now_th.strftime("%Y-%m-%d")
        time_str = now_th.strftime("%H:%M:%S")

        try:
            ws = SheetsManager.get_sheet(cls.SHEET_NAME)

            row = [
                date_str,
                time_str,
                service,
                agent,
                f"{input_tokens:,}",
                f"{output_tokens:,}",
                f"{cost_usd:.6f}",
                f"{cost_thb:.4f}",
                note,
            ]

            # Remove existing summary rows before appending
            cls._remove_summary_rows(ws)

            SheetsManager.append_row(ws, row)

            # Re-add summary rows at the bottom
            await cls._update_summary_rows(ws, now_th)

            logger.info(
                "Logged API cost: %s/%s $%.6f (฿%.4f)",
                service, agent, cost_usd, cost_thb,
            )

        except Exception as exc:
            logger.error("Failed to log API cost to sheet: %s", exc)
            SheetsManager.reset_client()
            raise

    @classmethod
    def _remove_summary_rows(cls, ws) -> None:
        """Remove summary rows (rows starting with 📊) from the sheet."""
        all_values = ws.get_all_values()
        rows_to_delete = []
        for idx, row in enumerate(all_values, start=1):
            if row and row[0].startswith("📊"):
                rows_to_delete.append(idx)

        # Delete from bottom to top to preserve indices
        for row_idx in reversed(rows_to_delete):
            ws.delete_rows(row_idx)

    @classmethod
    async def _update_summary_rows(cls, ws, now_th: datetime) -> None:
        """Add summary rows at the bottom of the sheet."""
        today_start = now_th.replace(hour=0, minute=0, second=0, microsecond=0)
        today_end = today_start + timedelta(days=1)
        month_start = now_th.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

        today_start_utc = today_start.astimezone(timezone.utc)
        today_end_utc = today_end.astimezone(timezone.utc)
        month_start_utc = month_start.astimezone(timezone.utc)

        async with get_session() as session:
            # Today's totals
            today_q = await session.execute(
                select(
                    func.count(ApiCostLog.id).label("calls"),
                    func.coalesce(func.sum(ApiCostLog.prompt_tokens), 0).label("input_t"),
                    func.coalesce(func.sum(ApiCostLog.completion_tokens), 0).label("output_t"),
                    func.coalesce(func.sum(ApiCostLog.cost_usd), 0).label("usd"),
                    func.coalesce(func.sum(ApiCostLog.cost_thb), 0).label("thb"),
                ).where(
                    ApiCostLog.created_at >= today_start_utc,
                    ApiCostLog.created_at < today_end_utc,
                )
            )
            today = today_q.one()

            # Month totals
            month_q = await session.execute(
                select(
                    func.count(ApiCostLog.id).label("calls"),
                    func.coalesce(func.sum(ApiCostLog.prompt_tokens), 0).label("input_t"),
                    func.coalesce(func.sum(ApiCostLog.completion_tokens), 0).label("output_t"),
                    func.coalesce(func.sum(ApiCostLog.cost_usd), 0).label("usd"),
                    func.coalesce(func.sum(ApiCostLog.cost_thb), 0).label("thb"),
                ).where(
                    ApiCostLog.created_at >= month_start_utc,
                    ApiCostLog.created_at < today_end_utc,
                )
            )
            month = month_q.one()

            # This month's revenue for percentage calculation
            revenue_q = await session.execute(
                select(func.coalesce(func.sum(Payment.amount), 0)).where(
                    Payment.status == PaymentStatus.CONFIRMED,
                    Payment.amount > 0,
                    Payment.created_at >= month_start_utc,
                    Payment.created_at < today_end_utc,
                )
            )
            monthly_revenue = float(revenue_q.scalar() or 0)

        month_thb = float(month.thb)
        pct_of_revenue = (month_thb / monthly_revenue * 100) if monthly_revenue > 0 else 0.0

        # Summary row: today
        today_row = [
            SUMMARY_TODAY_LABEL,
            f"{today.calls} calls",
            "",
            "",
            f"{int(today.input_t):,}",
            f"{int(today.output_t):,}",
            f"{float(today.usd):.6f}",
            f"{float(today.thb):.4f}",
            "",
        ]

        # Summary row: month
        month_row = [
            SUMMARY_MONTH_LABEL,
            f"{month.calls} calls",
            "",
            "",
            f"{int(month.input_t):,}",
            f"{int(month.output_t):,}",
            f"{float(month.usd):.6f}",
            f"{float(month.thb):.4f}",
            "",
        ]

        # Summary row: percentage of revenue
        pct_row = [
            SUMMARY_PERCENT_LABEL,
            f"{pct_of_revenue:.2f}%",
            f"รายรับเดือนนี้: ฿{monthly_revenue:,.2f}",
            f"ค่า API เดือนนี้: ฿{month_thb:,.4f}",
            "",
            "",
            "",
            "",
            "",
        ]

        SheetsManager.append_row(ws, today_row)
        SheetsManager.append_row(ws, month_row)
        SheetsManager.append_row(ws, pct_row)

    @classmethod
    async def sync_from_db(cls) -> None:
        """Full sync: rebuild the sheet from database records for today."""
        now_th = datetime.now(TH_TZ)
        today_start = now_th.replace(hour=0, minute=0, second=0, microsecond=0)
        today_end = today_start + timedelta(days=1)

        today_start_utc = today_start.astimezone(timezone.utc)
        today_end_utc = today_end.astimezone(timezone.utc)

        async with get_session() as session:
            result = await session.execute(
                select(ApiCostLog)
                .where(
                    ApiCostLog.created_at >= today_start_utc,
                    ApiCostLog.created_at < today_end_utc,
                )
                .order_by(ApiCostLog.created_at.asc())
            )
            logs = result.scalars().all()

        try:
            ws = SheetsManager.get_sheet(cls.SHEET_NAME)

            for log in logs:
                created_th = log.created_at.replace(tzinfo=timezone.utc).astimezone(TH_TZ)
                row = [
                    created_th.strftime("%Y-%m-%d"),
                    created_th.strftime("%H:%M:%S"),
                    log.model,
                    log.caller or "",
                    f"{log.prompt_tokens:,}",
                    f"{log.completion_tokens:,}",
                    f"{float(log.cost_usd):.6f}",
                    f"{float(log.cost_thb):.4f}",
                    "",
                ]
                SheetsManager.append_row(ws, row)

            await cls._update_summary_rows(ws, now_th)
            logger.info("Synced %d API cost records to sheet", len(logs))

        except Exception as exc:
            logger.error("Failed to sync API costs: %s", exc)
            SheetsManager.reset_client()
            raise
