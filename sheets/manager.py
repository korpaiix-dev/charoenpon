"""SheetsManager base class - gspread Service Account client for บริษัทเจริญพร."""

from __future__ import annotations

import logging
import os
from typing import Any

import gspread
from google.oauth2.service_account import Credentials

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

SPREADSHEET_NAME = "เจริญพร Dashboard"
CREDENTIALS_PATH = os.environ.get(
    "GOOGLE_SHEETS_CREDENTIALS", "credentials/google_sheets_sa.json"
)

SHEET_DEFINITIONS: dict[str, dict[str, Any]] = {
    "รายได้รายวัน": {
        "headers": [
            "วันที่", "พร้อมเพย์", "ซองทรู", "รวม",
            "แพ็ก 300", "แพ็ก 500", "แพ็ก 1299", "แพ็ก 2499",
            "จำนวนขาย", "สมาชิกใหม่", "Churn", "Active",
        ],
    },
    "รายได้รายเดือน": {
        "headers": [
            "เดือน", "รายรับ", "รายจ่าย", "กำไร", "Margin%",
            "MRR", "สมาชิกใหม่", "Churn", "Active", "CAC", "ROAS",
        ],
    },
    "ค่าใช้จ่าย API": {
        "headers": [
            "วันที่", "เวลา", "Service", "Agent", "Input Tokens",
            "Output Tokens", "USD", "THB", "หมายเหตุ",
        ],
    },
    "Facebook Ads Performance": {
        "headers": [
            "วันที่", "Campaign", "Budget", "ใช้ไป", "Reach",
            "Impressions", "Clicks", "CTR%", "Leads", "CPL",
            "Conversions", "Revenue", "ROAS", "คำแนะนำจากแมน",
        ],
    },
    "สมาชิก": {
        "headers": [
            "User ID", "Telegram ID", "ชื่อ", "Username", "แพ็กเกจ",
            "ราคา", "วันเริ่ม", "วันหมด", "สถานะ", "วิธีชำระ",
            "Source", "ต่ออายุกี่ครั้ง", "ใช้จ่ายรวม",
        ],
    },
    "Broadcast Log": {
        "headers": [
            "วันที่", "เวลา", "ประเภท", "กลุ่มเป้าหมาย",
            "ส่งทั้งหมด", "สำเร็จ", "Blocked", "ไม่เคยเริ่ม",
            "Error", "Admin",
        ],
    },
    "Weekly Summary": {
        "headers": [
            "สัปดาห์", "วันที่เริ่ม", "วันที่สิ้นสุด", "รายรับ",
            "รายจ่าย", "กำไร", "สมาชิกใหม่", "Churn", "Active",
            "CAC", "CPL ดีสุด", "แอดที่ดีสุด", "Insight", "Action Plan",
        ],
    },
}


class SheetsManager:
    """Base class for Google Sheets operations.

    Manages gspread client initialization, spreadsheet access, and
    automatic sheet creation with predefined headers.
    """

    _client: gspread.Client | None = None
    _spreadsheet: gspread.Spreadsheet | None = None

    @classmethod
    def get_client(cls) -> gspread.Client:
        """Get or create the gspread client using Service Account credentials."""
        if cls._client is None:
            creds = Credentials.from_service_account_file(
                CREDENTIALS_PATH, scopes=SCOPES
            )
            cls._client = gspread.authorize(creds)
            logger.info("Google Sheets client initialized")
        return cls._client

    @classmethod
    def get_spreadsheet(cls) -> gspread.Spreadsheet:
        """Get the main spreadsheet, creating it if needed."""
        if cls._spreadsheet is None:
            client = cls.get_client()
            try:
                cls._spreadsheet = client.open(SPREADSHEET_NAME)
                logger.info("Opened spreadsheet: %s", SPREADSHEET_NAME)
            except gspread.SpreadsheetNotFound:
                cls._spreadsheet = client.create(SPREADSHEET_NAME)
                logger.info("Created spreadsheet: %s", SPREADSHEET_NAME)
        return cls._spreadsheet

    @classmethod
    def get_sheet(cls, sheet_name: str) -> gspread.Worksheet:
        """Get a worksheet by name from the main spreadsheet."""
        spreadsheet = cls.get_spreadsheet()
        try:
            return spreadsheet.worksheet(sheet_name)
        except gspread.WorksheetNotFound:
            definition = SHEET_DEFINITIONS.get(sheet_name, {})
            headers = definition.get("headers", [])
            worksheet = spreadsheet.add_worksheet(
                title=sheet_name, rows=1000, cols=max(len(headers), 20)
            )
            if headers:
                worksheet.update([headers], "A1")
                worksheet.format(
                    "A1:{}1".format(gspread.utils.rowcol_to_a1(1, len(headers))[:-1]),
                    {
                        "textFormat": {"bold": True},
                        "backgroundColor": {"red": 0.2, "green": 0.4, "blue": 0.8},
                        "horizontalAlignment": "CENTER",
                    },
                )
            logger.info("Created worksheet: %s", sheet_name)
            return worksheet

    @classmethod
    def create_sheets_if_not_exist(cls) -> dict[str, gspread.Worksheet]:
        """Ensure all predefined sheets exist. Returns dict of name->worksheet."""
        sheets = {}
        for sheet_name in SHEET_DEFINITIONS:
            sheets[sheet_name] = cls.get_sheet(sheet_name)
        logger.info("All %d sheets verified/created", len(sheets))
        return sheets

    @classmethod
    def find_row_by_value(
        cls, worksheet: gspread.Worksheet, col: int, value: str
    ) -> int | None:
        """Find the first row where column `col` matches `value`. Returns 1-based row or None."""
        try:
            col_values = worksheet.col_values(col)
            for idx, cell_val in enumerate(col_values, start=1):
                if cell_val == value:
                    return idx
        except Exception as exc:
            logger.warning("Error searching column %d for '%s': %s", col, value, exc)
        return None

    @classmethod
    def append_row(cls, worksheet: gspread.Worksheet, row: list[Any]) -> None:
        """Append a row to the worksheet."""
        str_row = [str(v) if v is not None else "" for v in row]
        worksheet.append_row(str_row, value_input_option="USER_ENTERED")

    @classmethod
    def update_row(
        cls, worksheet: gspread.Worksheet, row_num: int, row: list[Any]
    ) -> None:
        """Update an existing row (1-based) in the worksheet."""
        str_row = [str(v) if v is not None else "" for v in row]
        end_col = gspread.utils.rowcol_to_a1(row_num, len(str_row))
        start_col = gspread.utils.rowcol_to_a1(row_num, 1)
        worksheet.update([str_row], f"{start_col}:{end_col}", value_input_option="USER_ENTERED")

    @classmethod
    def reset_client(cls) -> None:
        """Reset the cached client and spreadsheet (e.g., on auth error)."""
        cls._client = None
        cls._spreadsheet = None
        logger.info("Sheets client reset")
