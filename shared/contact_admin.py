"""Centralized "Contact Admin" button — used everywhere the bot tells a
customer to message an admin.

Why this exists:
  Customers without a Telegram username can't be DM'd by an admin directly
  (Telegram limitation). So instead of telling admin to reach out, we put a
  clickable button on the customer side. Single source of truth = the admin
  username from env (ADMIN_CONTACT_USERNAME) — change it in one place.

Usage:
    from shared.contact_admin import contact_admin_button, contact_admin_kb

    # Single button alone
    kb = contact_admin_kb()

    # Append to existing keyboard
    rows = [[your_other_button], contact_admin_button()]
    kb = InlineKeyboardMarkup(rows)
"""
from __future__ import annotations

import os

from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def _admin_username() -> str:
    """Return admin username without @, e.g. 'sperm6969'."""
    u = os.environ.get("ADMIN_CONTACT_USERNAME", "sperm6969").strip().lstrip("@")
    return u or "sperm6969"


def contact_admin_url() -> str:
    """t.me deep-link to admin DM."""
    return f"https://t.me/{_admin_username()}"


def contact_admin_button(label: str = "💬 ทักแอดมิน") -> list[InlineKeyboardButton]:
    """One-row button list — pass as a row inside InlineKeyboardMarkup."""
    return [InlineKeyboardButton(label, url=contact_admin_url())]


def contact_admin_kb(label: str = "💬 ทักแอดมิน") -> InlineKeyboardMarkup:
    """Single-button keyboard (ready to pass as reply_markup)."""
    return InlineKeyboardMarkup([contact_admin_button(label)])


__all__ = [
    "contact_admin_url",
    "contact_admin_button",
    "contact_admin_kb",
]
