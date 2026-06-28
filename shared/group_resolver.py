"""Group resolver — DB-first lookup with GroupSlug enum fallback.

Goal: allow admin to add new groups (FREE20, FREE21...) via Dashboard
without code changes. Existing code that uses GroupSlug enum continues to work.

Usage:
    from shared.group_resolver import resolve_slug
    slug = await resolve_slug("FREE20")  # returns string "FREE20" even if not in enum
    chat_id = await resolve_chat_id("FREE19")  # int from DB
"""
from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)


def _conn_str() -> str:
    return os.environ.get("DATABASE_URL", "").replace("postgresql+asyncpg://", "postgresql://")


async def _connect():
    import asyncpg
    return await asyncpg.connect(_conn_str())


async def resolve_slug(name: str) -> Optional[str]:
    """Return canonical slug string if it exists in DB or enum.

    Returns None if neither found.
    """
    if not name:
        return None
    # Check DB first
    try:
        conn = await _connect()
        try:
            row = await conn.fetchrow(
                "SELECT slug::text FROM group_registry WHERE slug::text = $1 LIMIT 1",
                str(name),
            )
            if row:
                return row["slug"]
        finally:
            await conn.close()
    except Exception as exc:
        logger.warning("group resolve_slug DB lookup failed: %s", exc)

    # Fallback to enum
    try:
        from shared.models import GroupSlug
        for member in GroupSlug:
            if member.value == name or member.name == name:
                return member.value
    except Exception:
        pass
    return None


async def resolve_chat_id(name: str) -> Optional[int]:
    """Look up the Telegram chat_id for a group slug."""
    if not name:
        return None
    try:
        conn = await _connect()
        try:
            row = await conn.fetchrow(
                "SELECT chat_id FROM group_registry WHERE slug::text = $1 AND is_active = TRUE LIMIT 1",
                str(name),
            )
            if row and row["chat_id"]:
                return int(row["chat_id"])
        finally:
            await conn.close()
    except Exception as exc:
        logger.warning("group resolve_chat_id failed: %s", exc)
    return None


async def list_active_slugs(category: Optional[str] = None) -> list[str]:
    """List all active group slugs, optionally filter by category (FREE/VIP)."""
    try:
        conn = await _connect()
        try:
            if category == "FREE":
                rows = await conn.fetch(
                    "SELECT slug::text FROM group_registry WHERE is_active = TRUE AND min_tier = 'FREE' ORDER BY slug"
                )
            elif category == "VIP":
                rows = await conn.fetch(
                    "SELECT slug::text FROM group_registry WHERE is_active = TRUE AND min_tier != 'FREE' ORDER BY slug"
                )
            else:
                rows = await conn.fetch(
                    "SELECT slug::text FROM group_registry WHERE is_active = TRUE ORDER BY slug"
                )
        finally:
            await conn.close()
        return [r["slug"] for r in rows]
    except Exception as exc:
        logger.warning("group list_active_slugs failed: %s", exc)
        return []
