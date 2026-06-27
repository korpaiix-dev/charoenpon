"""Exit Survey — DM ลูกค้าที่หมดอายุ 1 วันแล้วยังไม่ต่อ.

Flow:
1. Cron run รายวัน 11:00 BKK
2. หา user ที่ subscription หมด 24-48h ที่แล้ว ยังไม่ต่อ ยังไม่ได้ DM
3. DM ส่ง 4 ปุ่ม: ของไม่ถูกใจ / แพงไป / ใช้ไม่คุ้ม / ไม่สะดวก
4. กดปุ่ม → save reason + ส่งโค้ดลดตาม tier (50/40/30/20%)
5. ลูกค้าจ่ายเงิน → payment.py grant 3 gacha spins (round=400)

ส่วนลดไล่ลด %:
- VIP 300  → 50% off → 150
- GOLD 500 → 40% off → 295
- MAS 1299 → 30% off → 909
- GOD 2499 → 20% off → 1997
"""
from __future__ import annotations

import logging
import secrets
from datetime import datetime, timezone, timedelta

from sqlalchemy import text as _t
from shared.database import get_session

logger = logging.getLogger(__name__)

EXIT_ROUND = 400
PROMO_VALID_HOURS = 48

# tier code → (discount_pct, final_amount, label)
TIER_DISCOUNT = {
    "300":  (50, 150,  "VIP"),
    "500":  (40, 295,  "GOLD"),
    "1299": (30, 909,  "MAS"),
    "2499": (20, 1997, "GOD"),
}


# Phase B.6 (2026-06-27): DB-backed config
async def _es_cfg(key: str, default):
    """Read promo_config if flag exit_survey_config_from_db is ON. Errors -> default."""
    try:
        from shared.feature_flags import is_flag_enabled
        if not await is_flag_enabled("exit_survey_config_from_db"):
            return default
        from shared.promo_config import get_promo_config
        v = await get_promo_config(key, default=default)
        return v if v is not None else default
    except Exception:
        return default


async def is_exit_survey_enabled() -> bool:
    return bool(await _es_cfg("exit_survey_enabled", True))


async def get_tier_discount(tier_str: str) -> tuple[int, int, str] | None:
    """Get (discount_pct, final_amount, label) for tier. DB if flag ON else hardcoded."""
    default = TIER_DISCOUNT.get(str(tier_str))
    if default is None:
        return None
    key_map = {"300": "exit_survey_tier_300_pct", "500": "exit_survey_tier_500_pct",
               "1299": "exit_survey_tier_1299_pct", "2499": "exit_survey_tier_2499_pct"}
    k = key_map.get(str(tier_str))
    if not k:
        return default
    pct = int(await _es_cfg(k, default[0]))
    base = int(tier_str)
    final_amount = int(base * (100 - pct) / 100)
    return (pct, final_amount, default[2])

REASON_LABELS = {
    "content":   "🎬 ของไม่ถูกใจ",
    "price":     "💸 แพงไป",
    "not_use":   "📅 ใช้ไม่คุ้ม",
    "no_say":    "🤐 ไม่สะดวกบอก",
}


def _generate_code() -> str:
    return "EX" + secrets.token_hex(4).upper()


async def find_pending_users() -> list[dict]:
    """หา user ที่ subscription หมด 24-48h ที่แล้ว ยังไม่ต่อ ยังไม่ได้รับ Exit Survey."""
    async with get_session() as s:
        r = await s.execute(_t("""
            SELECT DISTINCT ON (u.id)
                u.id AS user_id,
                u.telegram_id,
                u.first_name,
                p.name AS last_tier_name,
                p.price AS last_tier_price
            FROM users u
            JOIN subscriptions s ON s.user_id = u.id
            JOIN packages p      ON p.id = s.package_id
            WHERE u.is_blocked_bot = false
              AND s.end_date BETWEEN NOW() - interval '48 hours'
                                   AND NOW() - interval '24 hours'
              AND NOT EXISTS (
                  SELECT 1 FROM subscriptions s2
                  WHERE s2.user_id = u.id
                    AND s2.end_date > NOW()
                    AND s2.status = 'ACTIVE'
              )
              AND NOT EXISTS (
                  SELECT 1 FROM exit_survey_log e
                  WHERE e.telegram_id = u.telegram_id
                    AND e.sent_at > NOW() - interval '30 days'
              )
            ORDER BY u.id, s.end_date DESC
            LIMIT 200
        """))
        rows = r.fetchall()
    return [dict(r._mapping) for r in rows]


def _price_to_tier(price: float) -> str:
    """แม็ป price → tier code."""
    p = int(price or 0)
    if p >= 2000: return "2499"
    if p >= 1000: return "1299"
    if p >= 400:  return "500"
    return "300"


async def save_exit_promo(
    user_id: int, telegram_id: int, tier_code: str,
    discount_pct: int, promo_code: str,
) -> int:
    """Insert exit_survey_log row, return id."""
    async with get_session() as s:
        r = await s.execute(_t("""
            INSERT INTO exit_survey_log
                (user_id, telegram_id, last_tier, discount_pct, promo_code, round)
            VALUES (:u, :tg, :tier, :pct, :code, :rnd)
            RETURNING id
        """), {
            "u": user_id, "tg": telegram_id, "tier": tier_code,
            "pct": discount_pct, "code": promo_code, "rnd": EXIT_ROUND,
        })
        new_id = r.scalar()
        await s.commit()
    return new_id


async def save_exit_reason(
    log_id: int, reason_code: str,
) -> dict | None:
    """ลูกค้ากดปุ่มแล้ว — save reason, ตอบกลับด้วยข้อมูล tier+promo."""
    async with get_session() as s:
        await s.execute(_t("""
            UPDATE exit_survey_log
            SET reason_code = :rc, answered_at = NOW()
            WHERE id = :id AND answered_at IS NULL
        """), {"rc": reason_code, "id": log_id})
        await s.commit()

        r = await s.execute(_t("""
            SELECT id, telegram_id, last_tier, discount_pct, promo_code, reason_code
            FROM exit_survey_log WHERE id = :id
        """), {"id": log_id})
        row = r.fetchone()
        if not row:
            return None
        return dict(row._mapping)


def build_exit_survey_message(first_name: str, last_tier: str) -> str:
    name = first_name or "คุณ"
    tier_info = TIER_DISCOUNT.get(last_tier, TIER_DISCOUNT["300"])
    pct, final_amt, tier_label = tier_info

    return (
        f"คุณ {name}~ 🙏\n"
        f"\n"
        f"แพรเองค่ะ ผู้ช่วยฝ่ายขาย VIP เจริญพร\n"
        f"\n"
        f"เห็นเมื่อวานหมดอายุไปแล้ว — ก่อนไปอยากถาม 1 ข้อค่ะ\n"
        f"<b>ทำไมถึงยังไม่ต่อแพ็ค {tier_label}?</b>\n"
        f"\n"
        f"คำตอบของคุณจะช่วยให้แพรปรับให้ดีขึ้นจริงๆ\n"
        f"\n"
        f"━━━━━━━━━━━━━━━\n"
        f"💝 <b>ตอบ 1 ข้อ — รับโค้ดลด {pct}% ทันที</b>\n"
        f"\n"
        f"🎰 + กาชาปอง <b>3 หมุน</b> ฟรี ตอนสมัคร\n"
        f"⏰ ใช้ได้ภายใน {PROMO_VALID_HOURS} ชม.\n"
        f"━━━━━━━━━━━━━━━\n"
        f"\n"
        f"👇 กดเลือกเหตุผลที่ใกล้เคียงที่สุดค่ะ"
    )


def build_thanks_message(reason_code: str, last_tier: str, promo_code: str) -> str:
    tier_info = TIER_DISCOUNT.get(last_tier, TIER_DISCOUNT["300"])
    pct, final_amt, tier_label = tier_info

    deep_link = f"tg://resolve?domain=NamwarnJarern_bot&start=comeback_{promo_code}"

    reason_responses = {
        "content": "ขอบคุณค่ะ — แพรจะแจ้งทีมหาคลิปใหม่ ๆ ที่ตรงสไตล์เพิ่มครับ",
        "price":   "เข้าใจค่ะ — เลยจัดส่วนลดพิเศษไว้ให้แล้ว",
        "not_use": "เข้าใจค่ะ — ลองช่วงโปรนี้ดูใหม่อีกครั้งนะคะ",
        "no_say":  "ไม่เป็นไรค่ะ — แพรเข้าใจ ขอบคุณที่สละเวลาตอบนะคะ",
    }
    resp = reason_responses.get(reason_code, "ขอบคุณค่ะ")

    original_price = int(last_tier)  # tier code IS the original price (300/500/1299/2499)

    return (
        f"{resp} 🙏\n"
        f"\n"
        f"━━━━━━━━━━━━━━━\n"
        f"🎁 <b>โค้ดส่วนลดของคุณ — รอบกลับมา</b>\n"
        f"\n"
        f"💎 {tier_label} <s>฿{original_price}</s> → <b>฿{final_amt}</b> (ลด {pct}%)\n"
        f"🎰 + กาชาปอง <b>3 หมุน</b> ฟรี\n"
        f"\n"
        f"⏰ ใช้ได้ {PROMO_VALID_HOURS} ชม.\n"
        f"━━━━━━━━━━━━━━━\n"
        f"\n"
        f'👉 <a href="{deep_link}">เริ่มต่ออายุเลย — ลด {pct}% + กาชา 3 หมุน</a>\n'
        f"\n"
        f"ดีใจที่ได้ดูแลคุณนะคะ 💝"
    )




# 2026-06-28: DB-driven message templates (fallback to hardcoded above)
_DB_EXIT_KEYS = {
    "survey": "journey_exit_survey_question",
    "thanks": "journey_exit_thanks",
}


async def _build_exit_survey_from_db_or_fallback(first_name: str, last_tier: str) -> str:
    try:
        from shared.bot_messages import get_bot_message, render_placeholders
        template = await get_bot_message(_DB_EXIT_KEYS["survey"])
        if template:
            return render_placeholders(template, first_name=first_name or "คุณ", last_tier=last_tier or "VIP")
    except Exception:
        pass
    return build_exit_survey_message(first_name, last_tier)


async def _build_exit_thanks_from_db_or_fallback(reason_code: str, last_tier: str, promo_code: str, discount_pct: int = 30) -> str:
    try:
        from shared.bot_messages import get_bot_message, render_placeholders
        template = await get_bot_message(_DB_EXIT_KEYS["thanks"])
        if template:
            return render_placeholders(template,
                first_name=last_tier or "คุณ",
                discount_pct=discount_pct,
                promo_code=promo_code,
                last_tier=last_tier or "VIP",
            )
    except Exception:
        pass
    return build_thanks_message(reason_code, last_tier, promo_code)
async def run_exit_survey_job(context) -> dict:
    """Cron job — รายวัน 11:00 BKK."""
    bot = context.bot if hasattr(context, "bot") else None
    if bot is None:
        return {"error": "no bot"}

    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    import asyncio as _a

    users = await find_pending_users()
    logger.info("Exit Survey: %d candidates", len(users))

    sent = 0
    failed = 0
    for u in users:
        tier_code = _price_to_tier(u.get("last_tier_price") or 300)
        discount_pct, _, _ = TIER_DISCOUNT[tier_code]
        promo_code = _generate_code()

        try:
            log_id = await save_exit_promo(
                u["user_id"], u["telegram_id"],
                tier_code, discount_pct, promo_code,
            )

            msg = build_exit_survey_message(u["first_name"], tier_code)
            buttons = [
                [InlineKeyboardButton(REASON_LABELS["content"],
                                      callback_data=f"exitsv:{log_id}:content")],
                [InlineKeyboardButton(REASON_LABELS["price"],
                                      callback_data=f"exitsv:{log_id}:price")],
                [InlineKeyboardButton(REASON_LABELS["not_use"],
                                      callback_data=f"exitsv:{log_id}:not_use")],
                [InlineKeyboardButton(REASON_LABELS["no_say"],
                                      callback_data=f"exitsv:{log_id}:no_say")],
            ]
            await bot.send_message(
                chat_id=u["telegram_id"], text=msg,
                parse_mode="HTML", disable_web_page_preview=True,
                reply_markup=InlineKeyboardMarkup(buttons),
            )
            sent += 1
            await _a.sleep(0.6)
        except Exception as e:
            failed += 1
            logger.warning("exit survey DM failed for tg=%s: %s",
                          u["telegram_id"], e)

    logger.info("Exit Survey done: sent=%d failed=%d", sent, failed)
    return {"sent": sent, "failed": failed, "candidates": len(users)}


async def handle_exit_survey_callback(update, context):
    """User กดปุ่ม → save reason + ส่งโค้ด."""
    query = update.callback_query
    if not query:
        return

    try:
        await query.answer()
    except Exception:
        pass

    data = (query.data or "").split(":")
    if len(data) != 3 or data[0] != "exitsv":
        return

    try:
        log_id = int(data[1])
    except ValueError:
        return

    reason_code = data[2]
    if reason_code not in REASON_LABELS:
        return

    row = await save_exit_reason(log_id, reason_code)
    if not row:
        return

    thanks = build_thanks_message(reason_code, row["last_tier"], row["promo_code"])
    try:
        await query.edit_message_text(
            thanks, parse_mode="HTML", disable_web_page_preview=True,
        )
    except Exception as e:
        logger.warning("edit thanks failed: %s", e)
        # Fallback: send new message
        try:
            await context.bot.send_message(
                chat_id=row["telegram_id"], text=thanks,
                parse_mode="HTML", disable_web_page_preview=True,
            )
        except Exception:
            pass


__all__ = [
    "run_exit_survey_job",
    "handle_exit_survey_callback",
    "EXIT_ROUND",
    "TIER_DISCOUNT",
]
