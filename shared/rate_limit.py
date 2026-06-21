"""Rate limiter middleware for sales/admin bots.

In-memory token-bucket; survives restart loss is acceptable.
Use as: from shared.rate_limit import check_rate_limit

Default: 20 messages / minute per user.
Bursts up to 30, then 1 token/3s refill.
"""
from __future__ import annotations
import time
import logging
from collections import deque
from typing import Optional

logger = logging.getLogger(__name__)

# In-memory state
_buckets: dict[int, deque] = {}  # user_id -> deque of timestamps
_blocked_until: dict[int, float] = {}

DEFAULT_MAX = 20    # messages per window
DEFAULT_WINDOW = 60  # seconds
HARD_LIMIT = 30     # absolute burst
BLOCK_SECONDS = 120  # 2 min cooldown if abuse


def check_rate_limit(user_id: int, max_per_window: int = DEFAULT_MAX,
                     window_seconds: int = DEFAULT_WINDOW) -> tuple[bool, Optional[str]]:
    """Returns (allowed, reason_if_blocked)."""
    if not user_id:
        return True, None
    now = time.time()

    # Already blocked
    if user_id in _blocked_until:
        if now < _blocked_until[user_id]:
            remaining = int(_blocked_until[user_id] - now)
            return False, f"blocked for {remaining}s (abuse)"
        else:
            del _blocked_until[user_id]

    # Get/create bucket
    bucket = _buckets.setdefault(user_id, deque())

    # Drop old entries
    cutoff = now - window_seconds
    while bucket and bucket[0] < cutoff:
        bucket.popleft()

    # Hard burst limit
    if len(bucket) >= HARD_LIMIT:
        _blocked_until[user_id] = now + BLOCK_SECONDS
        logger.warning("rate_limit BLOCK user=%s msgs=%d", user_id, len(bucket))
        return False, f"rate burst exceeded ({HARD_LIMIT}+ in {window_seconds}s)"

    # Soft window limit
    if len(bucket) >= max_per_window:
        return False, f"slow down (>{max_per_window} msgs/{window_seconds}s)"

    bucket.append(now)
    return True, None


def reset_user(user_id: int):
    _buckets.pop(user_id, None)
    _blocked_until.pop(user_id, None)


def stats() -> dict:
    return {
        "tracked_users": len(_buckets),
        "blocked_users": len(_blocked_until),
        "top_active": sorted(
            ((uid, len(b)) for uid, b in _buckets.items()),
            key=lambda x: -x[1]
        )[:5],
    }
