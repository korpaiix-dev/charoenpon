"""Spam Filter Middleware - Sales Bot แพร.

กรองทุก message ก่อนส่งต่อ handler อื่น:
- ถ้าไม่เกี่ยวกับสมัคร/ราคา/support → ตอบสั้น
- ส่งข้อความไร้สาระ 3 ครั้ง → soft ignore
- ลิงก์ spam → log + แจ้ง Discord
"""

from __future__ import annotations

import logging
import os
import re
import time
from collections import defaultdict
from typing import Any

import httpx
from telegram import Update
from telegram.ext import BaseHandler, CallbackContext

logger = logging.getLogger(__name__)

DISCORD_WEBHOOK_URL: str = os.environ.get("DISCORD_WEBHOOK_URL", "")

# ---- Spam link patterns ----
SPAM_LINK_PATTERNS = [
    re.compile(r"https?://t\.me/joinchat/", re.IGNORECASE),
    re.compile(r"https?://bit\.ly/", re.IGNORECASE),
    re.compile(r"https?://tinyurl\.com/", re.IGNORECASE),
    re.compile(r"https?://(?:www\.)?(?:casino|bet|slot|porn|xxx)", re.IGNORECASE),
    re.compile(r"@\w+bot\b", re.IGNORECASE),
    re.compile(r"(?:เว็บพนัน|หวย(?:ออนไลน์)|คาสิโน|สล็อต|แทงบอล)", re.IGNORECASE),
]

# ---- Keywords that indicate legitimate intent ----
RELEVANT_KEYWORDS = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"สมัคร",
        r"แพ็[กค]เกจ",
        r"ราคา",
        r"จ่าย",
        r"โอน",
        r"สลิป",
        r"slip",
        r"ชำระ",
        r"ต่ออายุ",
        r"renew",
        r"หมดอายุ",
        r"expire",
        r"กลุ่ม",
        r"group",
        r"vip",
        r"ห้อง",
        r"แอดมิน",
        r"admin",
        r"ช่วย",
        r"help",
        r"support",
        r"ปัญหา",
        r"ติดต่อ",
        r"package",
        r"price",
        r"baht",
        r"บาท",
        r"true\s*money",
        r"true\s*wallet",
        r"prompt\s*pay",
        r"พร้อมเพย์",
        r"ทรูมันนี่",
        r"/start",
        r"/package",
        r"/help",
        r"300",
        r"500",
        r"1299",
        r"2499",
        r"ฟรี",
        r"free",
        r"ทดลอง",
        r"trial",
    ]
]

SHORT_REPLIES = [
    "สวัสดีค่ะ หากสนใจแพ็กเกจ VIP พิมพ์ /start ได้เลยนะคะ 😊",
    "ไม่แน่ใจว่าต้องการอะไรค่ะ ลองพิมพ์ /start เพื่อดูแพ็กเกจได้นะคะ",
    "หากมีคำถามเกี่ยวกับแพ็กเกจ พิมพ์ /help ได้เลยค่ะ",
]

# ---- Per-user nonsense counter: {telegram_id: {"count": int, "last_time": float}} ----
_nonsense_tracker: dict[int, dict[str, Any]] = defaultdict(
    lambda: {"count": 0, "last_time": 0.0}
)

NONSENSE_THRESHOLD = 3
NONSENSE_RESET_SECONDS = 600  # reset counter after 10 minutes of silence


def _is_relevant(text: str) -> bool:
    """Check if message text is related to sales/support topics."""
    if not text:
        return False
    return any(pat.search(text) for pat in RELEVANT_KEYWORDS)


def _is_spam_link(text: str) -> bool:
    """Check if message contains spam links."""
    if not text:
        return False
    return any(pat.search(text) for pat in SPAM_LINK_PATTERNS)


def _has_any_url(text: str) -> bool:
    """Check if message contains any URL."""
    return bool(re.search(r"https?://\S+", text))


async def _notify_discord_spam(user_id: int, username: str | None, text: str) -> None:
    """Send spam alert to Discord channel."""
    if not DISCORD_WEBHOOK_URL:
        logger.warning("DISCORD_WEBHOOK_URL not set, skipping spam notification")
        return

    display_name = f"@{username}" if username else str(user_id)
    content = (
        f"🚨 **Spam Detected - Sales Bot**\n"
        f"👤 User: `{display_name}` (ID: `{user_id}`)\n"
        f"💬 Message: ```{text[:500]}```"
    )

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(
                DISCORD_WEBHOOK_URL,
                json={"content": content},
            )
    except Exception as exc:
        logger.error("Failed to notify Discord about spam: %s", exc)


async def spam_filter_middleware(update: Update, context: CallbackContext) -> bool:
    """Middleware that filters messages before passing to handlers.

    Returns True if message should be BLOCKED (not passed to handlers).
    Returns False if message should CONTINUE to handlers.
    """
    if not update.message or not update.message.text:
        # Non-text messages (photos, etc.) pass through — handlers decide
        return False

    text = update.message.text.strip()
    user = update.effective_user
    if not user:
        return False

    user_id = user.id
    username = user.username

    # --- Commands always pass through ---
    if text.startswith("/"):
        _reset_nonsense(user_id)
        return False

    # --- Callback queries always pass through ---
    if update.callback_query:
        return False

    # --- TrueMoney gift link — always pass through ---
    if "gift.truemoney.com" in text:
        _reset_nonsense(user_id)
        return False

    # --- Check for spam links ---
    if _is_spam_link(text):
        logger.warning("Spam link detected from user %s: %s", user_id, text[:200])
        await _notify_discord_spam(user_id, username, text)
        await update.message.reply_text(
            "ข้อความนี้ถูกระบบตรวจพบว่าเป็น spam ค่ะ 🚫"
        )
        return True

    # --- Check relevance ---
    if _is_relevant(text):
        _reset_nonsense(user_id)
        return False

    # --- Nonsense / irrelevant message ---
    tracker = _nonsense_tracker[user_id]
    now = time.time()

    # Reset counter if user was quiet for a while
    if now - tracker["last_time"] > NONSENSE_RESET_SECONDS:
        tracker["count"] = 0

    tracker["count"] += 1
    tracker["last_time"] = now

    if tracker["count"] >= NONSENSE_THRESHOLD:
        # Soft ignore — don't reply at all
        logger.info(
            "Soft-ignoring user %s (nonsense count: %d)", user_id, tracker["count"]
        )
        return True

    # Reply with a short redirect message
    reply_idx = (tracker["count"] - 1) % len(SHORT_REPLIES)
    await update.message.reply_text(SHORT_REPLIES[reply_idx])
    return True


def _reset_nonsense(user_id: int) -> None:
    """Reset the nonsense counter for a user."""
    if user_id in _nonsense_tracker:
        _nonsense_tracker[user_id]["count"] = 0


def cleanup_nonsense_tracker() -> int:
    """Remove stale entries from nonsense tracker to prevent memory leak.
    
    Call periodically (e.g., every hour). Returns number of entries removed.
    """
    now = time.time()
    stale_keys = [
        uid for uid, data in _nonsense_tracker.items()
        if now - data["last_time"] > NONSENSE_RESET_SECONDS * 6  # 1 hour
    ]
    for uid in stale_keys:
        del _nonsense_tracker[uid]
    return len(stale_keys)
