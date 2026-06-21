"""Single helper for sending alerts to the Telegram admin group.

Replaces 33 hardcoded `int(os.environ.get("ADMIN_GROUP_CHAT_ID", "-1003830920430"))`
+ inline `tg.Bot(token=os.environ.get("ADMIN_BOT_TOKEN", ""))` patterns.

Usage:
    from shared.admin_alert import notify_admin_group, notify_admin_photo

    await notify_admin_group("⚠️ Payment failed for user X", parse_mode="HTML")

    await notify_admin_photo(
        photo=img_bytes,
        caption="Slip received",
        reply_markup=keyboard,
    )
"""
from __future__ import annotations

import logging
import os
from typing import Any

import telegram as tg
from telegram import InlineKeyboardMarkup

logger = logging.getLogger(__name__)


def _admin_group_id() -> int:
    """Get admin payment group chat_id (ห้อง "ยืนยันสลิป").
    Payment/slip-related alerts only."""
    return int(os.environ.get("ADMIN_GROUP_CHAT_ID", "-1003830920430"))


def _admin_report_group_id() -> int:
    """Get admin report group chat_id (ห้อง "Report").
    Daily/weekly reports, monitoring, system alerts, non-payment notifications."""
    return int(os.environ.get("ADMIN_REPORT_GROUP_CHAT_ID", "-1004426430362"))


def _admin_bot_token() -> str:
    tok = os.environ.get("ADMIN_BOT_TOKEN", "")
    if not tok:
        raise RuntimeError("ADMIN_BOT_TOKEN not set")
    return tok


async def notify_admin_group(
    text: str,
    *,
    parse_mode: str | None = "HTML",
    reply_markup: InlineKeyboardMarkup | None = None,
    disable_web_page_preview: bool = True,
    silent_on_error: bool = True,
) -> tg.Message | None:
    """Send text message to admin group. Returns Message on success, None on failure
    (failures logged but not raised by default — set silent_on_error=False to raise)."""
    # GUARD 2026-06-21: skip ถ้า test mode (ป้องกัน test spam ห้องจริง)
    if os.environ.get("CHAROENPON_TEST_MODE") == "1":
        logger.debug("notify_admin_group skipped (test mode)")
        return None
    try:
        bot = tg.Bot(token=_admin_bot_token())
        await bot.initialize()
        try:
            return await bot.send_message(
                chat_id=_admin_group_id(),
                text=text,
                parse_mode=parse_mode,
                reply_markup=reply_markup,
                disable_web_page_preview=disable_web_page_preview,
            )
        finally:
            try:
                await bot.shutdown()
            except Exception:
                pass
    except Exception as exc:
        logger.warning("notify_admin_group failed: %s", exc)
        if not silent_on_error:
            raise
        return None


async def notify_admin_photo(
    photo: Any,  # bytes / file-like / file_id
    *,
    caption: str | None = None,
    parse_mode: str | None = "HTML",
    reply_markup: InlineKeyboardMarkup | None = None,
    silent_on_error: bool = True,
) -> tg.Message | None:
    """Send photo to admin group."""
    try:
        bot = tg.Bot(token=_admin_bot_token())
        await bot.initialize()
        try:
            return await bot.send_photo(
                chat_id=_admin_group_id(),
                photo=photo,
                caption=caption,
                parse_mode=parse_mode,
                reply_markup=reply_markup,
            )
        finally:
            try:
                await bot.shutdown()
            except Exception:
                pass
    except Exception as exc:
        logger.warning("notify_admin_photo failed: %s", exc)
        if not silent_on_error:
            raise
        return None


async def notify_admin_report(
    text: str,
    *,
    parse_mode: str | None = "HTML",
    reply_markup: InlineKeyboardMarkup | None = None,
    disable_web_page_preview: bool = True,
    silent_on_error: bool = True,
) -> tg.Message | None:
    """Send text message to admin REPORT group (ห้อง Report — non-payment).
    For daily/weekly reports, monitoring, system alerts."""
    if os.environ.get("CHAROENPON_TEST_MODE") == "1":
        logger.debug("notify_admin_report skipped (test mode)")
        return None
    try:
        bot = tg.Bot(token=_admin_bot_token())
        await bot.initialize()
        try:
            return await bot.send_message(
                chat_id=_admin_report_group_id(),
                text=text,
                parse_mode=parse_mode,
                reply_markup=reply_markup,
                disable_web_page_preview=disable_web_page_preview,
            )
        finally:
            try:
                await bot.shutdown()
            except Exception:
                pass
    except Exception as exc:
        logger.warning("notify_admin_report failed: %s", exc)
        if not silent_on_error:
            raise
        return None


async def notify_admin_report_photo(
    photo,
    *,
    caption: str | None = None,
    parse_mode: str | None = "HTML",
    reply_markup: InlineKeyboardMarkup | None = None,
    silent_on_error: bool = True,
) -> tg.Message | None:
    """Send photo to admin REPORT group (ห้อง Report)."""
    try:
        bot = tg.Bot(token=_admin_bot_token())
        await bot.initialize()
        try:
            return await bot.send_photo(
                chat_id=_admin_report_group_id(),
                photo=photo,
                caption=caption,
                parse_mode=parse_mode,
                reply_markup=reply_markup,
            )
        finally:
            try:
                await bot.shutdown()
            except Exception:
                pass
    except Exception as exc:
        logger.warning("notify_admin_report_photo failed: %s", exc)
        if not silent_on_error:
            raise
        return None


__all__ = [
    "notify_admin_group",
    "notify_admin_photo",
    "notify_admin_report",
    "notify_admin_report_photo",
]
