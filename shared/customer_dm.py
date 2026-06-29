"""Unified Customer DM helper — ALWAYS use Sales Bot (@NamwarnJarern_bot).

WHY THIS EXISTS:
  Customers only ever /start the Sales Bot. If admin-side handlers send DMs
  using context.bot (= Admin Bot @jarern2_bot), Telegram returns
  "Chat not found" because the customer has no chat with that bot.
  This was the cause of the "approved but no link" complaint for months.

SINGLE SOURCE OF TRUTH:
  Every customer-facing DM (invite link, payment confirmation, rejection
  notice, SOS reply, retention DM, etc.) MUST go through this module.

USAGE:
    from shared.customer_dm import send_to_customer
    ok = await send_to_customer(telegram_id=12345, text="...")
    if not ok:
        # already logged + admin alerted + is_blocked_bot mark if applicable
        # caller can fire-and-forget
        ...

BEHAVIOR ON FAILURE:
  - Telegram "Forbidden: bot was blocked by the user" → mark
    users.is_blocked_bot = TRUE + log info (do NOT spam admin group)
  - Telegram "Chat not found" → log warning + admin alert (this is a bug —
    means we picked wrong bot OR the user never /started us)
  - Network/timeout → 2x retry with backoff, then admin alert
  - Any other → log error + admin alert
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

import telegram as tg
from telegram.error import BadRequest, Forbidden, NetworkError, RetryAfter, TimedOut

logger = logging.getLogger(__name__)

_SALES_BOT_INSTANCE: tg.Bot | None = None
_SALES_BOT_LOCK = asyncio.Lock()


def _sales_bot_token() -> str:
    tok = os.environ.get("SALES_BOT_TOKEN") or os.environ.get("BOT_TOKEN", "")
    if not tok:
        raise RuntimeError("SALES_BOT_TOKEN not configured")
    return tok


async def _get_sales_bot() -> tg.Bot:
    """Lazy-initialised singleton Sales Bot client.

    Reuses one Bot instance across calls so we don't open/close connections
    on every DM. The instance is initialised on first use and never shut down
    explicitly — Python GC cleans up on process exit.
    """
    global _SALES_BOT_INSTANCE
    if _SALES_BOT_INSTANCE is not None:
        return _SALES_BOT_INSTANCE
    async with _SALES_BOT_LOCK:
        if _SALES_BOT_INSTANCE is None:
            bot = tg.Bot(token=_sales_bot_token())
            await bot.initialize()
            _SALES_BOT_INSTANCE = bot
    return _SALES_BOT_INSTANCE


async def _mark_blocked_bot(telegram_id: int) -> None:
    """Set users.is_blocked_bot = TRUE so future jobs skip this user."""
    try:
        from shared.database import get_session
        from sqlalchemy import text as _t
        async with get_session() as s:
            await s.execute(_t(
                "UPDATE users "
                "SET is_blocked_bot = TRUE, blocked_bot_at = NOW() "
                "WHERE telegram_id = :tg AND is_blocked_bot IS NOT TRUE"
            ), {"tg": telegram_id})
            await s.commit()
        logger.info("customer_dm: marked is_blocked_bot for tg=%s", telegram_id)
    except Exception as exc:
        logger.warning("customer_dm: mark blocked_bot failed for tg=%s: %s", telegram_id, exc)


async def _alert_admin_on_dm_failure(
    telegram_id: int,
    err: Exception,
    text_preview: str,
) -> None:
    """Best-effort alert to admin group. Throttled to avoid spam."""
    try:
        from shared.admin_alert import notify_admin_report
        msg = (
            f"⚠️ <b>DM ลูกค้าไม่สำเร็จ</b>\n"
            f"\U0001f194 tg=<code>{telegram_id}</code>\n"
            f"❌ {type(err).__name__}: {str(err)[:120]}\n"
            f"\U0001f4ac <i>{text_preview[:200]}</i>"
        )
        await notify_admin_report(msg, parse_mode="HTML")
    except Exception as exc:
        logger.warning("customer_dm: admin alert failed: %s", exc)


async def send_to_customer(
    telegram_id: int,
    text: str,
    *,
    reply_markup: Any | None = None,
    parse_mode: str | None = "HTML",
    disable_web_page_preview: bool = True,
    photo: bytes | str | None = None,        # bytes / file_id / URL
    photo_caption: str | None = None,
    alert_on_fail: bool = True,
    max_retries: int = 2,
) -> bool:
    """Send a DM to customer via SALES bot. Returns True on success.

    Behavior:
      - photo set → sendPhoto with caption (text param ignored if caption given)
      - photo None → sendMessage
      - Forbidden → mark is_blocked_bot, return False (no admin alert)
      - "Chat not found" / BadRequest → admin alert, return False
      - NetworkError / TimedOut → retry up to max_retries with 2s/4s backoff
      - Other → log + admin alert (if alert_on_fail), return False
    """
    if not telegram_id:
        logger.warning("customer_dm: empty telegram_id, skip")
        return False

    bot = await _get_sales_bot()
    last_err: Exception | None = None
    _ra_tries = 0  # AUDIT FIX: cap RetryAfter

    for attempt in range(max_retries + 1):
        try:
            if photo is not None:
                caption = photo_caption or text
                if isinstance(photo, bytes):
                    import io as _io
                    photo_arg = _io.BytesIO(photo)
                    photo_arg.name = "image.jpg"
                else:
                    photo_arg = photo
                await bot.send_photo(
                    chat_id=telegram_id,
                    photo=photo_arg,
                    caption=caption,
                    parse_mode=parse_mode,
                    reply_markup=reply_markup,
                )
            else:
                await bot.send_message(
                    chat_id=telegram_id,
                    text=text,
                    parse_mode=parse_mode,
                    reply_markup=reply_markup,
                    disable_web_page_preview=disable_web_page_preview,
                )
            return True

        except Forbidden as exc:
            # Bot was blocked by user — terminal, mark and move on
            logger.info("customer_dm: tg=%s blocked bot (%s)", telegram_id, exc)
            await _mark_blocked_bot(telegram_id)
            return False

        except BadRequest as exc:
            msg = str(exc).lower()
            if "chat not found" in msg:
                # Bug — wrong bot used, or user never /started us
                logger.error("customer_dm: Chat not found tg=%s — using wrong bot?", telegram_id)
                if alert_on_fail:
                    await _alert_admin_on_dm_failure(telegram_id, exc, text)
                return False
            if "user is deactivated" in msg:
                logger.info("customer_dm: tg=%s deactivated", telegram_id)
                await _mark_blocked_bot(telegram_id)
                return False
            # Other BadRequest = bad payload (parse_mode error, message too long, etc.)
            logger.error("customer_dm: BadRequest tg=%s: %s", telegram_id, exc)
            if alert_on_fail:
                await _alert_admin_on_dm_failure(telegram_id, exc, text)
            return False

        except RetryAfter as exc:
            # Telegram flood control — sleep retry_after, then retry (silent, no admin alert)
            ra = getattr(exc, "retry_after", 5)
            logger.warning(
                "customer_dm: tg=%s RetryAfter %ss — sleeping then retrying",
                telegram_id, ra,
            )
            try:
                _ra_tries += 1
                if _ra_tries > 5:
                    logger.error("customer_dm: tg=%s gave up after RetryAfter x%d", telegram_id, _ra_tries)
                    return False
                await asyncio.sleep(min(float(ra) + 0.5, 30.0))
            except Exception:
                pass
            # Don't count against max_retries — flood control is not a real failure
            continue

        except (NetworkError, TimedOut) as exc:
            last_err = exc
            if attempt < max_retries:
                delay = 2 ** (attempt + 1)  # 2s, 4s
                logger.warning(
                    "customer_dm: tg=%s attempt %d/%d failed (%s) — retry in %ds",
                    telegram_id, attempt + 1, max_retries + 1, exc, delay
                )
                await asyncio.sleep(delay)
                continue
            logger.error(
                "customer_dm: tg=%s exhausted %d retries: %s",
                telegram_id, max_retries + 1, exc
            )
            if alert_on_fail:
                await _alert_admin_on_dm_failure(telegram_id, exc, text)
            return False

        except Exception as exc:
            last_err = exc
            logger.exception("customer_dm: unexpected error tg=%s: %s", telegram_id, exc)
            if alert_on_fail:
                await _alert_admin_on_dm_failure(telegram_id, exc, text)
            return False

    return False


async def send_invite_links_dm(
    telegram_id: int,
    first_name: str | None,
    package_name: str,
    invite_links: list[tuple[str, str]],     # [(group_title, url), ...]
    *,
    expires_text: str | None = None,
    extra_top_text: str = "",
    extra_bottom_text: str = "",
) -> bool:
    """High-level helper: standard "subscription approved + here are your links" DM.

    Used by all 9 approval paths so format is identical everywhere.
    """
    name = first_name or "ลูกค้า"
    text = (
        f"\U0001f389 <b>ยืนยันสมาชิกเรียบร้อยค่ะ คุณ {name}</b>\n\n"
        f"\U0001f4e6 แพ็กเกจ: <b>{package_name}</b>\n"
    )
    if expires_text:
        text += f"⏰ หมดอายุ: {expires_text}\n"
    if extra_top_text:
        text += f"\n{extra_top_text}\n"
    text += (
        f"\n━━━━━━━━━━\n"
        f"\U0001f517 <b>ลิงก์เข้ากลุ่ม</b> (ใช้ได้ครั้งเดียว):\n\n"
    )
    if invite_links:
        for title, url in invite_links:
            text += f"\U0001f4cc <a href=\"{url}\">{title}</a>\n"
    else:
        text += "<i>(ระบบกำลังสร้างลิงก์ — ทักแอดมินถ้าเกิน 5 นาทีไม่ได้)</i>\n"
    text += (
        f"\n⚠️ <i>ลิงก์ใช้ได้คนเดียวเท่านั้น ห้ามแชร์</i>\n"
    )
    if extra_bottom_text:
        text += f"\n{extra_bottom_text}\n"
    text += "\nขอบคุณค่ะ \U0001f48e"
    return await send_to_customer(telegram_id, text)


__all__ = [
    "send_to_customer",
    "send_invite_links_dm",
]
