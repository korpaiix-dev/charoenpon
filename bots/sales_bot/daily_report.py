"""Daily Report — รายงานรายวัน Compact ส่ง Admin Group ทุก 22:00 ไทย.

Max 10 lines. ข้อมูลสำคัญ + 1 สิ่งที่ดี + 1 สิ่งที่ต้องปรับ.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from telegram import Bot
from telegram.ext import ContextTypes

from shared.database import get_session

logger = logging.getLogger(__name__)

TH_TZ = timezone(timedelta(hours=7))
ADMIN_GROUP_ID = int(os.environ.get("ADMIN_GROUP_CHAT_ID", "-1003830920430"))


async def _scalar(sql: str, params: dict | None = None):
    async with get_session() as session:
        result = await session.execute(text(sql), params or {})
        return result.scalar() or 0


async def generate_daily_report() -> str:
    """Generate compact daily report — max 10 lines."""
    now_th = datetime.now(TH_TZ)
    today_start = now_th.replace(hour=0, minute=0, second=0, microsecond=0)
    today_start_utc = today_start.astimezone(timezone.utc).replace(tzinfo=None)
    yesterday_start_utc = today_start_utc - timedelta(days=1)

    # Monday of this week (for weekly sum)
    days_since_monday = now_th.weekday()  # 0=Mon
    week_start_utc = (today_start - timedelta(days=days_since_monday)).astimezone(timezone.utc).replace(tzinfo=None)

    # 1st of this month
    month_start_utc = today_start.replace(day=1).astimezone(timezone.utc).replace(tzinfo=None)

    lines: list[str] = []
    good_thing = ""
    bad_thing = ""

    # --- Revenue ---
    try:
        today_rev = float(await _scalar(
            "SELECT COALESCE(SUM(amount), 0) FROM payments WHERE status = 'confirmed' AND created_at >= :s",
            {"s": today_start_utc},
        ))
        today_orders = int(await _scalar(
            "SELECT COUNT(*) FROM payments WHERE status = 'confirmed' AND created_at >= :s",
            {"s": today_start_utc},
        ))
        yesterday_rev = float(await _scalar(
            "SELECT COALESCE(SUM(amount), 0) FROM payments WHERE status = 'confirmed' AND created_at >= :ys AND created_at < :ts",
            {"ys": yesterday_start_utc, "ts": today_start_utc},
        ))
        week_rev = float(await _scalar(
            "SELECT COALESCE(SUM(amount), 0) FROM payments WHERE status = 'confirmed' AND created_at >= :s",
            {"s": week_start_utc},
        ))
        month_rev = float(await _scalar(
            "SELECT COALESCE(SUM(amount), 0) FROM payments WHERE status = 'confirmed' AND created_at >= :s",
            {"s": month_start_utc},
        ))

        lines.append(f"💰 วันนี้ ฿{today_rev:,.0f} ({today_orders} orders)")
        lines.append(f"📅 สัปดาห์ ฿{week_rev:,.0f} | เดือน ฿{month_rev:,.0f}")

        # Revenue comparison for good/bad
        if yesterday_rev > 0:
            pct = ((today_rev - yesterday_rev) / yesterday_rev) * 100
            if pct > 20:
                good_thing = f"รายได้ขึ้น {pct:.0f}%"
            elif pct < -20:
                bad_thing = f"รายได้ลด {abs(pct):.0f}% จากเมื่อวาน"
    except Exception as exc:
        logger.error("Daily report — revenue failed: %s", exc)
        lines.append("💰 ดึงรายได้ไม่ได้")

    # --- Members ---
    try:
        active = int(await _scalar(
            "SELECT COUNT(*) FROM subscriptions WHERE status = 'active' AND end_date > NOW()"
        ))
        new_today = int(await _scalar(
            "SELECT COUNT(*) FROM subscriptions WHERE created_at >= :s",
            {"s": today_start_utc},
        ))
        expired_today = int(await _scalar(
            "SELECT COUNT(*) FROM subscriptions WHERE status = 'expired' AND end_date >= :s AND end_date < :e",
            {"s": today_start_utc, "e": today_start_utc + timedelta(days=1)},
        ))
        lines.append(f"👥 Active {active} | ใหม่ {new_today} | หมดอายุ {expired_today}")

        if new_today == 0 and not bad_thing:
            bad_thing = "ยังไม่มีลูกค้าใหม่วันนี้"
    except Exception as exc:
        logger.error("Daily report — members failed: %s", exc)
        lines.append("👥 ดึงข้อมูลสมาชิกไม่ได้")

    # --- Content Queue ---
    try:
        remaining = int(await _scalar(
            "SELECT COUNT(*) FROM content_queue WHERE is_used = false"
        ))
        used_7d = int(await _scalar(
            "SELECT COUNT(*) FROM content_queue WHERE is_used = true AND used_at >= :s",
            {"s": today_start_utc - timedelta(days=7)},
        ))
        daily_rate = used_7d / 7 if used_7d > 0 else 0
        est_days = int(remaining / daily_rate) if daily_rate > 0 else 999

        est_text = f"~{est_days} วัน" if est_days < 999 else "เยอะ"
        lines.append(f"📦 คลิปเหลือ {remaining} ({est_text})")

        if remaining < 20 and not bad_thing:
            bad_thing = f"คลิปเหลือ {remaining} ชิ้น ควรเพิ่ม"
    except Exception as exc:
        logger.error("Daily report — content queue failed: %s", exc)

    # --- Determine good/bad things (if not already set) ---
    try:
        if not good_thing:
            # Check comeback success
            cb_purchased = int(await _scalar(
                "SELECT COUNT(*) FROM comeback_dm_log WHERE sent_at >= :s AND purchased = true",
                {"s": today_start_utc},
            ))
            if cb_purchased > 0:
                good_thing = f"Comeback DM ปิดขาย {cb_purchased} คน"

        if not good_thing:
            # Check teaser clicks vs yesterday
            today_clicks = int(await _scalar(
                "SELECT COUNT(*) FROM teaser_clicks WHERE created_at >= :s",
                {"s": today_start_utc},
            ))
            yesterday_clicks = int(await _scalar(
                "SELECT COUNT(*) FROM teaser_clicks WHERE created_at >= :ys AND created_at < :ts",
                {"ys": yesterday_start_utc, "ts": today_start_utc},
            ))
            if today_clicks > yesterday_clicks and yesterday_clicks > 0:
                good_thing = f"Clicks เพิ่มจากเมื่อวาน ({today_clicks} vs {yesterday_clicks})"
            elif not bad_thing and yesterday_clicks > 10 and today_clicks < yesterday_clicks * 0.8:
                bad_thing = "Clicks ลด — ลองเปลี่ยนรูป"

        if not good_thing:
            good_thing = "ระบบทำงานปกติ"
        if not bad_thing:
            bad_thing = "ไม่มีปัญหา"
    except Exception as exc:
        logger.error("Daily report — analysis failed: %s", exc)
        if not good_thing:
            good_thing = "ระบบทำงานปกติ"
        if not bad_thing:
            bad_thing = "-"

    lines.append("")
    lines.append(f"✅ ดี: {good_thing}")
    lines.append(f"❌ ปรับ: {bad_thing}")

    return "\n".join(lines)


async def send_daily_report(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Scheduled job: ส่ง Daily Report ไป Admin Group ทุก 22:00 ไทย."""
    now_th = datetime.now(TH_TZ)
    logger.info("📊 Generating daily report at %s", now_th.strftime("%Y-%m-%d %H:%M"))

    try:
        report = await generate_daily_report()
    except Exception as exc:
        logger.error("Failed to generate daily report: %s", exc)
        report = f"⚠️ Daily Report Error: {exc}"

    admin_token = os.environ.get("ADMIN_BOT_TOKEN", "")
    if not admin_token:
        logger.error("ADMIN_BOT_TOKEN not set, cannot send daily report")
        return

    try:
        bot = Bot(token=admin_token)
        await bot.initialize()
        await bot.send_message(
            chat_id=ADMIN_GROUP_ID,
            text=report,
        )
        logger.info("Daily report sent to admin group")
    except Exception as exc:
        logger.error("Failed to send daily report: %s", exc)
