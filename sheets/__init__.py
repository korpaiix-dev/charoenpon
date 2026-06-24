"""Google Sheets integration — REMOVED 2026-06-24 (boss request).

This module now provides no-op stubs to keep existing imports from breaking,
but all sync operations are silently disabled. Backup saved as sheets.bak.20260624/
"""
from __future__ import annotations
import logging

logger = logging.getLogger(__name__)
_warned = False

def _warn_once():
    global _warned
    if not _warned:
        logger.info("Google Sheets integration is disabled — calls are no-ops")
        _warned = True


class _NoOpSheet:
    """Base no-op sheet class — all methods return None or empty."""
    @classmethod
    async def update(cls, *args, **kwargs):
        _warn_once()
        return None
    @classmethod
    async def append(cls, *args, **kwargs):
        _warn_once()
        return None
    @classmethod
    async def log(cls, *args, **kwargs):
        _warn_once()
        return None
    @classmethod
    async def sync(cls, *args, **kwargs):
        _warn_once()
        return None
    @classmethod
    async def write(cls, *args, **kwargs):
        _warn_once()
        return None


class SheetsManager(_NoOpSheet):
    pass


class DailyRevenueSheet(_NoOpSheet):
    pass


class MonthlySummarySheet(_NoOpSheet):
    pass


class WeeklySummarySheet(_NoOpSheet):
    pass


class DailySummarySheet(_NoOpSheet):
    pass


class ApiCostsSheet(_NoOpSheet):
    pass


class AdPerformanceSheet(_NoOpSheet):
    pass


class MembersSheet(_NoOpSheet):
    pass


class BroadcastLogSheet(_NoOpSheet):
    pass


class IncomeLogSheet(_NoOpSheet):
    pass


__all__ = [
    "SheetsManager",
    "DailyRevenueSheet",
    "MonthlySummarySheet",
    "WeeklySummarySheet",
    "DailySummarySheet",
    "ApiCostsSheet",
    "AdPerformanceSheet",
    "MembersSheet",
    "BroadcastLogSheet",
    "IncomeLogSheet",
]
