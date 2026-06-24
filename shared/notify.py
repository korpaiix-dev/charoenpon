"""Notification Hub — single entry point for ALL system alerts.

Replaces scattered `_notify_discord(...)` + `admin_bot.send_message(...)` patterns.
One function (`notify`) routes each event to the right channels based on
the ROUTES table below.

Usage:
    from shared.notify import notify

    await notify("payment_approved",
                 title="✅ Payment Approved",
                 body=f"User {name} paid ฿{amt}")

    await notify("payment_wrong_receiver",
                 title="⚠️ Wrong Receiver",
                 body=details,
                 photo=slip_bytes,           # for telegram
                 reply_markup=admin_kb)      # for telegram

Routing rule format:
    "discord:<channel>"   — post embed to that Discord channel
    "telegram:admin"      — post to Telegram admin group
    "log:<level>"         — write to bot log (info/warning/error)

Adding a new event:
    1. Add row to ROUTES below.
    2. Call `notify("your_event_key", title=..., body=...)` from your code.
    3. Done — no inline Discord webhook calls needed.

NOTE: Existing inline call sites are NOT auto-migrated yet. This is the
target hub; migration is incremental (low-risk events first).
"""
from __future__ import annotations

import logging
from typing import Any, Iterable

from shared.discord_alert import notify_discord
from shared.admin_alert import (
    notify_admin_group,
    notify_admin_photo,
    notify_admin_report,
    notify_admin_report_photo,
)

logger = logging.getLogger(__name__)


# ─── Routing table — event_key → list of "channel:target" routes ──────────
# Edit ONLY this table to change where alerts go. Code stays the same.
ROUTES: dict[str, list[str]] = {
    # ─ Payment events ─
    "payment_approved":        ["discord:payment", "telegram:admin", "log:info"],
    "payment_rejected":        ["discord:payment", "telegram:admin", "log:warning"],
    "payment_wrong_receiver":  ["discord:payment", "telegram:admin", "log:warning"],
    "payment_duplicate":       ["discord:payment", "log:warning"],
    "payment_refunded":        ["discord:payment", "telegram:admin", "log:info"],
    "slip_received":           ["telegram:admin"],                     # ปุ่ม approve in TG
    "truemoney_received":      ["telegram:admin"],                     # ปุ่ม approve in TG
    "truemoney_timeout":       ["telegram:admin", "log:warning"],      # admin redeem manual
    "slip2go_no_tier":         ["telegram:admin", "log:warning"],      # admin classify

    # ─ Subscription / membership ─
    "member_kicked":           ["discord:members"],
    "member_expiring_soon":    ["discord:members"],                    # not admin (would spam)
    "member_restored":         ["discord:members"],

    # ─ Broadcast events ─
    "broadcast_enqueued":      ["discord:broadcast", "log:info"],
    "broadcast_paused":        ["discord:broadcast", "telegram:report", "log:warning"],
    "broadcast_completed":     ["discord:broadcast", "log:info"],
    "broadcast_preview":       ["telegram:report"],                    # send preview to boss
    "broadcast_429":           ["discord:broadcast", "log:warning"],   # Telegram throttle

    # ─ Content distributor ─
    "content_distributed":     ["discord:content"],
    "content_distribute_fail": ["discord:content", "log:warning"],

    # ─ Bot health / system ─
    "bot_crash":               ["discord:system", "telegram:report", "log:error"],
    "bot_restart":             ["discord:system", "log:info"],
    "ai_circuit_open":         ["discord:system", "telegram:report", "log:warning"],
    "ai_circuit_closed":       ["discord:system", "log:info"],
    "ai_fail":                 ["discord:system", "log:warning"],
    "slip2go_balance_low":     ["discord:alerts", "telegram:report", "log:warning"],

    # ─ Spam / abuse ─
    "spam_filter_hit":         ["discord:alerts", "log:info"],         # not admin (would spam)
    "abuse_detected":          ["discord:alerts", "telegram:report", "log:warning"],

    # ─ Daily / weekly reports ─
    "daily_report":            ["telegram:report", "discord:report"],
    "weekly_report":           ["telegram:report", "discord:report"],
    "daily_expiry_report":     ["telegram:report"],
    "daily_content_report":    ["telegram:report", "discord:content"],

    # ─ Manager-agent insights ─
    "manager_insight":         ["discord:manager"],                    # exec-only

    # ─ Sheets sync ─

    # ─ SOS / urgent ─
    "sos":                     ["discord:alerts", "telegram:admin", "log:error"],

    # ─ Default fallback ─
    "_default":                ["discord:alerts", "log:info"],
}


# Map ROUTES "log:<level>" → logger method
_LOG_LEVELS = {
    "info":    logger.info,
    "warning": logger.warning,
    "error":   logger.error,
    "debug":   logger.debug,
}


async def notify(
    event_key: str,
    *,
    title: str,
    body: str = "",
    photo: Any = None,
    reply_markup: Any = None,
    extra_routes: Iterable[str] | None = None,
    silent_on_error: bool = True,
) -> dict[str, bool]:
    """Send notification according to ROUTES table.

    Args:
        event_key: key in ROUTES (e.g. "payment_approved"). Unknown keys
                   fall back to ROUTES["_default"].
        title:     short headline (used as Discord embed.title and
                   first line of Telegram message).
        body:      longer text (Discord embed.description + Telegram body).
        photo:     bytes/file_id for Telegram photo (optional).
        reply_markup: InlineKeyboardMarkup for Telegram (optional).
        extra_routes: ad-hoc routes to add on top of ROUTES (optional).
        silent_on_error: log + swallow exceptions per channel (default True).

    Returns:
        dict mapping each route → True/False (success per channel).
    """
    routes = list(ROUTES.get(event_key, ROUTES["_default"]))
    if extra_routes:
        routes.extend(extra_routes)

    results: dict[str, bool] = {}
    telegram_text = title if not body else f"{title}\n\n{body}"

    for route in routes:
        try:
            if ":" not in route:
                continue
            kind, target = route.split(":", 1)
            if kind == "discord":
                ok = await notify_discord(target, title, body,
                                          silent_on_error=silent_on_error)
                results[route] = bool(ok)
            elif kind == "telegram" and target == "admin":
                if photo is not None:
                    msg = await notify_admin_photo(
                        photo, caption=telegram_text,
                        reply_markup=reply_markup,
                        silent_on_error=silent_on_error,
                    )
                else:
                    msg = await notify_admin_group(
                        telegram_text, reply_markup=reply_markup,
                        silent_on_error=silent_on_error,
                    )
                results[route] = msg is not None
            elif kind == "telegram" and target == "report":
                if photo is not None:
                    msg = await notify_admin_report_photo(
                        photo, caption=telegram_text,
                        reply_markup=reply_markup,
                        silent_on_error=silent_on_error,
                    )
                else:
                    msg = await notify_admin_report(
                        telegram_text, reply_markup=reply_markup,
                        silent_on_error=silent_on_error,
                    )
                results[route] = msg is not None
            elif kind == "log":
                fn = _LOG_LEVELS.get(target, logger.info)
                fn("[%s] %s | %s", event_key, title, body[:200])
                results[route] = True
            else:
                logger.debug("notify: unknown route %s", route)
                results[route] = False
        except Exception as exc:
            if not silent_on_error:
                raise
            logger.warning("notify(%s) route=%s failed: %s",
                           event_key, route, exc)
            results[route] = False

    return results


def list_events() -> list[tuple[str, list[str]]]:
    """Return ROUTES as a sorted list for inspection (e.g. /where command)."""
    return sorted([(k, v) for k, v in ROUTES.items() if k != "_default"])


def where(event_key: str) -> list[str]:
    """Inspect which routes a given event_key triggers."""
    return list(ROUTES.get(event_key, ROUTES["_default"]))


__all__ = ["notify", "list_events", "where", "ROUTES"]
