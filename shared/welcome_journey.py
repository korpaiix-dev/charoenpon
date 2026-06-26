"""Welcome Journey V2 — 4-stage new-customer DM sequence.

Triggers:
  • Stage 0 (instant): triggered from /start handler — ทักทันที + ส่วนลด 25%
  • Stage 1 (3h):  3-4 ชม. หลัง /start — "ส่วนลดยังอยู่"
  • Stage 2 (12h): 12-13 ชม. หลัง /start — "เหลือ 12 ชม."
  • Stage 3 (23h): 23-24 ชม. — "ชั่วโมงสุดท้าย"

Rule: Skip if user already paid OR is_blocked_bot OR has already received that stage.

Stage tracking via comeback_dm_log.round:
  301 = stage 0 instant
  302 = stage 1 (3h)
  303 = stage 2 (12h)
  304 = stage 3 (23h)

Discount: 25% off (saved as comeback_dm_log.discount_pct)
"""
from __future__ import annotations

import asyncio
import logging
import secrets
import os

from sqlalchemy import text as _t

logger = logging.getLogger(__name__)

WELCOME_DISCOUNT_PCT = 25
WELCOME_VALID_HOURS = 24

STAGE_INSTANT = (301, "instant")


# Phase B.6 (2026-06-27): DB-backed config with feature flag
async def _wj_cfg(key: str, default):
    """Read promo_config from DB if flag welcome_config_from_db is ON. Errors -> default."""
    try:
        from shared.feature_flags import is_flag_enabled
        if not await is_flag_enabled("welcome_config_from_db"):
            return default
        from shared.promo_config import get_promo_config
        v = await get_promo_config(key, default=default)
        return v if v is not None else default
    except Exception:
        return default


async def get_welcome_discount_pct() -> int:
    """Get current welcome discount %. Default 25 (hardcoded)."""
    return int(await _wj_cfg("welcome_discount_pct", WELCOME_DISCOUNT_PCT))


async def get_welcome_valid_hours() -> int:
    """Get current promo validity in hours. Default 24."""
    return int(await _wj_cfg("welcome_valid_hours", WELCOME_VALID_HOURS))


async def is_welcome_enabled() -> bool:
    """Master switch for welcome system. Default True."""
    v = await _wj_cfg("welcome_enabled", True)
    return bool(v)
STAGE_3H      = (302, "3h")
STAGE_12H     = (303, "12h")
STAGE_23H     = (304, "23h")


def _generate_code() -> str:
    return "WJ" + secrets.token_hex(4).upper()


def _deep_link(promo_code: str) -> str:
    return f"tg://resolve?domain=NamwarnJarern_bot&start=welcome_{promo_code}"


# ─── Message builders ───────────────────────────────────────────────────

def build_instant(first_name: str, promo_code: str) -> str:
    name = first_name or "คุณ"
    return (
        f"สวัสดี <b>คุณ {name}</b> 👋\n"
        f"แพรเองค่ะ ผู้ช่วย VIP เจริญพร\n\n"
        f"━━━━━━━━━━━━━━━\n"
        f"🎁 <b>ของขวัญต้อนรับสมาชิกใหม่</b>\n\n"
        f"💵 ส่วนลด <b>25%</b> ใช้กับแพ็กไหนก็ได้\n"
        f"⏰ ใช้ได้ <b>24 ชม.</b> เท่านั้น\n"
        f"━━━━━━━━━━━━━━━\n\n"
        f"📦 <b>ในห้อง VIP มี:</b>\n"
        f"🎬 คลิป HD อัปทุกวัน\n"
        f"🏠 งานทางบ้าน · 🤫 แอบถ่าย · 🎒 นักเรียน\n"
        f"👥 ชุมชนคุณภาพ 10,000+ คน\n\n"
        f'👉 <a href="{_deep_link(promo_code)}">สมัครเลย — รับส่วนลด 25%</a>'
    )


def build_3h(first_name: str, promo_code: str) -> str:
    name = first_name or "คุณ"
    return (
        f"คุณ {name} 💬\n\n"
        f"เห็นคุณยังไม่ได้สมัครเลย — มาดูแลเรื่องนี้ให้นะคะ\n\n"
        f"💵 <b>ส่วนลด 25%</b> ยังใช้ได้ <b>เหลืออีก 21 ชม.</b>\n\n"
        f"แพ็กคุ้มสุด:\n"
        f"📦 VIP 30 วัน — เพียง <b>฿225</b> (จากเดิม ฿300)\n"
        f"💎 VIP OnlyFans — เพียง <b>฿375</b> (จากเดิม ฿500)\n\n"
        f'👉 <a href="{_deep_link(promo_code)}">กดเลย ใช้ส่วนลด</a>'
    )


def build_12h(first_name: str, promo_code: str) -> str:
    name = first_name or "คุณ"
    return (
        f"⏰ คุณ {name} <b>เหลือ 12 ชม.</b>\n\n"
        f"ส่วนลด 25% ของคุณกำลังจะหมด —\n"
        f"พลาดแล้วต้องจ่ายเต็มราคา 😔\n\n"
        f"⚡ <b>วันนี้คนอื่นในห้อง:</b>\n"
        f"📊 ออเดอร์เข้ามา <b>15+ คน</b>\n"
        f"🎬 คลิปใหม่อัปเช้านี้ <b>23 คลิป</b>\n\n"
        f'👉 <a href="{_deep_link(promo_code)}">รีบใช้ส่วนลด</a>'
    )


def build_23h(first_name: str, promo_code: str) -> str:
    name = first_name or "คุณ"
    return (
        f"🚨 คุณ {name} — <b>ชั่วโมงสุดท้าย!</b>\n\n"
        f"ส่วนลด 25% จะหมดในไม่กี่ชั่วโมง 😱\n\n"
        f"<b>เปิดประตูครั้งสุดท้าย:</b>\n"
        f"📦 VIP 30 วัน <b>฿225</b> (ปกติ ฿300)\n"
        f"💎 VIP OnlyFans <b>฿375</b> (ปกติ ฿500)\n"
        f"🌟 GOD MODE 90 วัน <b>฿974</b> (ปกติ ฿1,299)\n\n"
        f"🎁 <b>โบนัสเฉพาะวันนี้:</b> กาชาฟรี 1 หมุน\n\n"
        f'👉 <a href="{_deep_link(promo_code)}">คลิกก่อนหมดเวลา</a>'
    )


_BUILDERS = {301: build_instant, 302: build_3h, 303: build_12h, 304: build_23h}


# ─── DB helpers ─────────────────────────────────────────────────────────

async def _save_log(user_id: int, telegram_id: int, promo_code: str, round_id: int) -> None:
    from shared.database import get_session
    async with get_session() as s:
        await s.execute(_t(
            "INSERT INTO comeback_dm_log "
            "(user_id, telegram_id, discount_pct, promo_code, round, variant) "
            "VALUES (:u, :tg, :pct, :code, :rnd, 'wj_v2') ON CONFLICT DO NOTHING"
        ), {"u": user_id, "tg": telegram_id, "pct": WELCOME_DISCOUNT_PCT,
            "code": promo_code, "rnd": round_id})
        await s.commit()


async def _has_received_round(telegram_id: int, round_id: int) -> bool:
    from shared.database import get_session
    async with get_session() as s:
        r = await s.execute(_t(
            "SELECT 1 FROM comeback_dm_log "
            "WHERE telegram_id = :tg AND round = :rnd LIMIT 1"
        ), {"tg": telegram_id, "rnd": round_id})
        return r.scalar() is not None


async def _user_has_paid(user_id: int) -> bool:
    from shared.database import get_session
    async with get_session() as s:
        r = await s.execute(_t(
            "SELECT 1 FROM payments WHERE user_id = :u AND status = 'CONFIRMED' LIMIT 1"
        ), {"u": user_id})
        return r.scalar() is not None


# ─── Public: called from /start handler ─────────────────────────────────

async def send_instant_welcome(user_id: int, telegram_id: int,
                               first_name: str, bot) -> bool:
    """Send Stage 0 instant welcome DM. Returns True if sent."""
    if await _user_has_paid(user_id):
        return False
    if await _has_received_round(telegram_id, 301):
        return False

    code = _generate_code()
    try:
        await _save_log(user_id, telegram_id, code, 301)
        msg = build_instant(first_name, code)
        await bot.send_message(
            chat_id=telegram_id, text=msg,
            parse_mode="HTML", disable_web_page_preview=True,
        )
        return True
    except Exception as e:
        logger.warning("welcome instant DM failed tg=%s: %s", telegram_id, e)
        return False


# ─── Scheduled: scan + send stages 1-3 ──────────────────────────────────

async def _find_eligible_int(round_id: int, hour_start: int, hour_end: int) -> list[dict]:
    """Users created within hour window who haven't paid + haven't got this round.

    FIX 2026-06-20: Use make_interval to avoid asyncpg type coercion bug
    (was: $1 || ' hours' which failed when $1 was int).
    """
    from shared.database import get_session
    async with get_session() as s:
        r = await s.execute(_t("""
            SELECT u.id AS user_id, u.telegram_id, u.first_name
            FROM users u
            WHERE u.created_at BETWEEN NOW() - make_interval(hours => :he)
                                   AND NOW() - make_interval(hours => :hs)
              AND u.is_blocked_bot = false
              AND NOT EXISTS (
                  SELECT 1 FROM payments p
                  WHERE p.user_id = u.id AND p.status = 'CONFIRMED'
              )
              AND NOT EXISTS (
                  SELECT 1 FROM comeback_dm_log
                  WHERE telegram_id = u.telegram_id AND round = :rnd
              )
            ORDER BY u.created_at
            LIMIT 300
        """), {"hs": hour_start, "he": hour_end, "rnd": round_id})
        return [dict(row._mapping) for row in r.fetchall()]


async def run_welcome_journey_job(context) -> dict:
    """Scheduled job — every 1 hour. Sends stages 1-3."""
    bot = context.bot if hasattr(context, "bot") else None
    if bot is None:
        return {"error": "no bot"}

    total_sent = 0
    total_failed = 0
    breakdown = {}

    STAGES = [(302, 3, 4, "3h"), (303, 12, 13, "12h"), (304, 23, 24, "23h")]
    for round_id, hs, he, label in STAGES:
        users = await _find_eligible_int(round_id, hs, he)
        builder = _BUILDERS[round_id]
        sent = 0; fail = 0
        for u in users:
            code = _generate_code()
            try:
                await _save_log(u["user_id"], u["telegram_id"], code, round_id)
                msg = builder(u["first_name"], code)
                await bot.send_message(
                    chat_id=u["telegram_id"], text=msg,
                    parse_mode="HTML", disable_web_page_preview=True,
                )
                sent += 1
                await asyncio.sleep(0.5)
            except Exception as e:
                fail += 1
                logger.warning("welcome %s DM fail tg=%s: %s",
                               label, u["telegram_id"], e)
        breakdown[label] = {"sent": sent, "fail": fail}
        total_sent += sent
        total_failed += fail

    logger.info("welcome_journey: sent=%d failed=%d breakdown=%s",
                total_sent, total_failed, breakdown)
    return {"sent": total_sent, "failed": total_failed, "breakdown": breakdown}


__all__ = ["send_instant_welcome", "run_welcome_journey_job",
           "WELCOME_DISCOUNT_PCT"]
