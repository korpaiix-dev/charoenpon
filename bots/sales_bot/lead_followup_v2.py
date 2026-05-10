"""Lead Followup v2 — DM leads ที่ /start แต่ยังไม่ซื้อ พร้อม preview + ส่วนลด.

- รอบ 1 (2 ชม.): ส่ง preview photo + แนะนำ VIP
- รอบ 2 (24 ชม.): preview + 20% discount, promo code 48 ชม.
- รอบ 3 (72 ชม.): preview + 30% discount, last chance
- Rate limit: 50 DM/ชม., delay 2 วินาที
- Skip: purchased, banned, already sent this round
"""

from __future__ import annotations

import asyncio
import logging
import os
import secrets
import string
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import text
from telegram import Bot
from telegram.error import Forbidden
from telegram.ext import ContextTypes

from shared.database import get_session

logger = logging.getLogger(__name__)

TH_TZ = timezone(timedelta(hours=7))

# Config
MAX_DM_PER_HOUR = 50
DM_DELAY_SECONDS = 2
ROUND1_WAIT_HOURS = 2
ROUND2_WAIT_HOURS = 24
ROUND3_WAIT_HOURS = 72
PROMO_EXPIRY_HOURS = 48
BASE_PRICE = Decimal("300")

ADMIN_GROUP_ID = int(os.environ.get("ADMIN_GROUP_CHAT_ID", "-1003830920430"))

# ─── DB Migration ────────────────────────────────────────────────────────────

ALTER_TABLE_SQL = """
ALTER TABLE lead_followup_log ADD COLUMN IF NOT EXISTS variant VARCHAR(10);
"""


async def ensure_tables() -> None:
    """Ensure lead_followup_log table has variant column."""
    try:
        async with get_session() as session:
            await session.execute(text(ALTER_TABLE_SQL))
            await session.commit()
    except Exception as exc:
        logger.warning("lead_followup_v2 migration (may already exist): %s", exc)


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _generate_promo_code() -> str:
    """Generate 8-char promo code."""
    chars = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(chars) for _ in range(8))


async def _get_random_preview() -> str | None:
    """Get a random preview_file_id from content_previews."""
    async with get_session() as session:
        result = await session.execute(
            text("SELECT preview_file_id FROM content_previews ORDER BY RANDOM() LIMIT 1")
        )
        row = result.fetchone()
        return row.preview_file_id if row else None


async def _has_purchased(user_id: int) -> bool:
    """Check if user has any confirmed payment."""
    async with get_session() as session:
        result = await session.execute(
            text("SELECT 1 FROM payments WHERE user_id = :uid AND status = 'CONFIRMED' LIMIT 1"),
            {"uid": user_id},
        )
        return result.fetchone() is not None


async def _already_sent(user_id: int, dm_round: int) -> bool:
    """Check if DM already sent for this round."""
    async with get_session() as session:
        result = await session.execute(
            text("SELECT 1 FROM lead_followup_log WHERE user_id = :uid AND round = :round LIMIT 1"),
            {"uid": user_id, "round": dm_round},
        )
        return result.fetchone() is not None


async def _log_dm(user_id: int, telegram_id: int, dm_round: int, variant: str | None = None) -> None:
    """Log sent DM."""
    async with get_session() as session:
        await session.execute(
            text("""
                INSERT INTO lead_followup_log (user_id, telegram_id, round, variant)
                VALUES (:uid, :tgid, :round, :var)
            """),
            {"uid": user_id, "tgid": telegram_id, "round": dm_round, "var": variant},
        )
        await session.commit()


# ─── Get Leads per Round ─────────────────────────────────────────────────────

async def _get_round_leads(dm_round: int, wait_hours: int, limit: int) -> list[dict]:
    """Get leads eligible for a specific round."""
    cutoff = datetime.utcnow() - timedelta(hours=wait_hours)

    if dm_round == 1:
        # Round 1: /start > wait_hours ago, never sent round 1
        async with get_session() as session:
            result = await session.execute(
                text("""
                    SELECT u.id as user_id, u.telegram_id, u.first_name, u.username
                    FROM leads l
                    JOIN users u ON u.id = l.user_id
                    WHERE l.status = 'NEW'
                      AND l.created_at < :cutoff
                      AND u.is_banned = false
                      AND u.id NOT IN (SELECT user_id FROM lead_followup_log WHERE round = :round)
                      AND u.id NOT IN (SELECT user_id FROM payments WHERE status = 'CONFIRMED')
                    ORDER BY l.created_at ASC
                    LIMIT :lim
                """),
                {"cutoff": cutoff, "round": dm_round, "lim": limit},
            )
            rows = result.fetchall()
    else:
        # Round 2+: sent previous round > wait_hours ago, not sent this round
        prev_round = dm_round - 1
        async with get_session() as session:
            result = await session.execute(
                text("""
                    SELECT u.id as user_id, u.telegram_id, u.first_name, u.username
                    FROM lead_followup_log lf
                    JOIN users u ON u.id = lf.user_id
                    WHERE lf.round = :prev_round
                      AND lf.purchased = false
                      AND lf.sent_at < :cutoff
                      AND u.is_banned = false
                      AND u.id NOT IN (SELECT user_id FROM lead_followup_log WHERE round = :round)
                      AND u.id NOT IN (SELECT user_id FROM payments WHERE status = 'CONFIRMED')
                    ORDER BY lf.sent_at ASC
                    LIMIT :lim
                """),
                {"prev_round": prev_round, "cutoff": cutoff, "round": dm_round, "lim": limit},
            )
            rows = result.fetchall()

    seen = set()
    leads = []
    for row in rows:
        if row.user_id in seen:
            continue
        seen.add(row.user_id)
        leads.append({
            "user_id": row.user_id,
            "telegram_id": row.telegram_id,
            "first_name": row.first_name or row.username or "คุณ",
        })
    return leads


# ─── Messages ────────────────────────────────────────────────────────────────

def _round1_message(first_name: str) -> str:
    return (
        f"สวัสดีค่ะ คุณ {first_name} 😊\n"
        f"\n"
        f"ตอนนี้มีคลิปใหม่เพียบ! ดูตัวอย่างด้านบนได้เลย 👆\n"
        f"\n"
        f"สมัคร VIP ดูเต็มไม่เบลอ 10,000+ คลิป\n"
        f"เริ่มต้นแค่ ฿300/เดือน\n"
        f"\n"
        f'👉 <a href="tg://resolve?domain=NamwarnJarern_bot&start=packages">ดูแพ็กเกจ VIP</a>'
    )


def _round2_message(first_name: str, promo_code: str) -> str:
    price = int(BASE_PRICE * 80 / 100)  # 20% off
    return (
        f"คุณ {first_name} ยังสนใจอยู่ไหมคะ? 🔥\n"
        f"\n"
        f"วันนี้มีส่วนลดพิเศษ <b>20%</b> ให้คุณ!\n"
        f"VIP 30 วัน ฿{price} (จาก ฿300)\n"
        f"\n"
        f"🎟 โค้ด: <code>{promo_code}</code>\n"
        f"⏰ ใช้ได้ 48 ชม.\n"
        f"\n"
        f'👉 <a href="tg://resolve?domain=NamwarnJarern_bot&start=comeback_{promo_code}">สมัครเลย</a>'
    )


def _round3_message(first_name: str, promo_code: str) -> str:
    price = int(BASE_PRICE * 70 / 100)  # 30% off
    return (
        f"โอกาสสุดท้ายแล้วค่ะ คุณ {first_name}! 🥺\n"
        f"\n"
        f"ลด <b>30%</b> เฉพาะคุณเท่านั้น!\n"
        f"VIP 30 วัน ฿{price} (จาก ฿300)\n"
        f"\n"
        f"🎟 โค้ด: <code>{promo_code}</code>\n"
        f"⏰ ใช้ได้ 48 ชม. สุดท้ายจริงๆ\n"
        f"\n"
        f'👉 <a href="tg://resolve?domain=NamwarnJarern_bot&start=comeback_{promo_code}">สมัครก่อนหมดเขต</a>'
    )


# ─── Save Promo Code ────────────────────────────────────────────────────────

async def _save_promo_code(user_id: int, telegram_id: int, promo_code: str, discount_pct: int, dm_round: int) -> None:
    """Save promo code to comeback_dm_log for reuse."""
    async with get_session() as session:
        await session.execute(
            text("""
                INSERT INTO comeback_dm_log (user_id, telegram_id, discount_pct, promo_code, round, variant)
                VALUES (:uid, :tgid, :disc, :code, :round, :var)
            """),
            {
                "uid": user_id,
                "tgid": telegram_id,
                "disc": discount_pct,
                "code": promo_code,
                "round": dm_round + 100,  # offset to distinguish from comeback_dm rounds
                "var": f"lead_v2_r{dm_round}",
            },
        )
        await session.commit()


# ─── Send DM ────────────────────────────────────────────────────────────────

async def _send_dm_with_preview(
    bot: Bot,
    lead: dict,
    dm_round: int,
    message: str,
    preview_file_id: str | None,
) -> bool:
    """Send DM with optional preview photo. Returns True if sent."""
    try:
        if preview_file_id:
            await bot.send_photo(
                chat_id=lead["telegram_id"],
                photo=preview_file_id,
                caption=message,
                parse_mode="HTML",
            )
        else:
            await bot.send_message(
                chat_id=lead["telegram_id"],
                text=message,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
        return True
    except Forbidden:
        logger.info("Cannot DM lead %d — blocked bot", lead["telegram_id"])
        return False
    except Exception as exc:
        logger.error("Failed to DM lead %d: %s", lead["telegram_id"], exc)
        return False


# ─── Scheduler Job ───────────────────────────────────────────────────────────

async def run_lead_followup_v2_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Scheduled job: send lead followup DMs with previews + discounts."""
    bot = context.bot
    now_th = datetime.now(TH_TZ)
    logger.info("📬 Lead followup v2 job started at %s", now_th.strftime("%Y-%m-%d %H:%M"))

    await ensure_tables()

    dm_budget = MAX_DM_PER_HOUR
    total_sent = 0
    round_counts = {1: 0, 2: 0, 3: 0}

    # Get a preview for sending
    preview_file_id = await _get_random_preview()

    # ---- Round 1: 2 hours after /start ----
    round1_leads = await _get_round_leads(1, ROUND1_WAIT_HOURS, dm_budget)
    for lead in round1_leads:
        if dm_budget <= 0:
            break
        msg = _round1_message(lead["first_name"])
        success = await _send_dm_with_preview(bot, lead, 1, msg, preview_file_id)
        if success:
            await _log_dm(lead["user_id"], lead["telegram_id"], 1, "v2")
            round_counts[1] += 1
            total_sent += 1
            dm_budget -= 1
        await asyncio.sleep(DM_DELAY_SECONDS)

    # ---- Round 2: 24 hours, 20% discount ----
    if dm_budget > 0:
        round2_leads = await _get_round_leads(2, ROUND2_WAIT_HOURS, dm_budget)
        preview2 = await _get_random_preview()  # Different preview
        for lead in round2_leads:
            if dm_budget <= 0:
                break
            promo_code = _generate_promo_code()
            msg = _round2_message(lead["first_name"], promo_code)
            success = await _send_dm_with_preview(bot, lead, 2, msg, preview2)
            if success:
                await _save_promo_code(lead["user_id"], lead["telegram_id"], promo_code, 20, 2)
                await _log_dm(lead["user_id"], lead["telegram_id"], 2, "v2")
                round_counts[2] += 1
                total_sent += 1
                dm_budget -= 1
            await asyncio.sleep(DM_DELAY_SECONDS)

    # ---- Round 3: 72 hours, 30% discount (NEW) ----
    if dm_budget > 0:
        round3_leads = await _get_round_leads(3, ROUND3_WAIT_HOURS, dm_budget)
        preview3 = await _get_random_preview()
        for lead in round3_leads:
            if dm_budget <= 0:
                break
            promo_code = _generate_promo_code()
            msg = _round3_message(lead["first_name"], promo_code)
            success = await _send_dm_with_preview(bot, lead, 3, msg, preview3)
            if success:
                await _save_promo_code(lead["user_id"], lead["telegram_id"], promo_code, 30, 3)
                await _log_dm(lead["user_id"], lead["telegram_id"], 3, "v2")
                round_counts[3] += 1
                total_sent += 1
                dm_budget -= 1
            await asyncio.sleep(DM_DELAY_SECONDS)

    # ---- Summary ----
    summary = (
        f"📊 Lead Followup v2 ({now_th.strftime('%d/%m/%Y %H:%M')})\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"R1 (2h): {round_counts[1]}\n"
        f"R2 (24h, -20%): {round_counts[2]}\n"
        f"R3 (72h, -30%): {round_counts[3]}\n"
        f"รวม: {total_sent}"
    )
    logger.info(summary)

    if total_sent > 0:
        admin_token = os.environ.get("ADMIN_BOT_TOKEN", "")
        if admin_token:
            try:
                admin_bot = Bot(token=admin_token)
                await admin_bot.initialize()
                await admin_bot.send_message(
                    chat_id=ADMIN_GROUP_ID,
                    text=f"📬 <b>Lead Followup v2</b>\n\n{summary}",
                    parse_mode="HTML",
                )
            except Exception as exc:
                logger.error("Failed to send admin notification: %s", exc)
