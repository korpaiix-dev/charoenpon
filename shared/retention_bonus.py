"""Grant gachapon + shaker bonus after retention promo redemption.

Trigger rule (matches retention_alert.py DISCOUNT_TIERS round numbers):
- round=200 (3-days-before, 10%): no bonus
- round=201 (1-day-before, 15%):  +1 gachapon spin
- round=202 (today, 20%):         +3 gachapon spins + 1 shaker ticket
"""
from __future__ import annotations

import logging
import random
from datetime import datetime, timedelta

from sqlalchemy import text as _t
from shared.database import get_session

logger = logging.getLogger(__name__)


BONUS_RULES = {
    200: {"gacha": 0, "shaker": 0, "label": "3-days-before"},
    201: {"gacha": 1, "shaker": 0, "label": "1-day-before"},
    202: {"gacha": 3, "shaker": 1, "label": "today"},
    300: {"gacha": 1, "shaker": 0, "label": "welcome-day-1"},
    400: {"gacha": 3, "shaker": 0, "label": "exit-survey-comeback"},
}


async def grant_retention_bonus(
    user_id: int,
    telegram_id: int,
    promo_code: str,
    payment_id: int,
) -> dict:
    """Check if promo_code is retention-tier; grant bonus accordingly.

    Returns: {"granted": bool, "gacha": int, "shaker": str|None, "round": int}
    """
    if not promo_code:
        return {"granted": False, "reason": "no_code"}

    # Look up promo in comeback_dm_log
    async with get_session() as s:
        r = await s.execute(_t("""
            SELECT round, discount_pct FROM comeback_dm_log
            WHERE promo_code = :code
            UNION ALL
            SELECT round, discount_pct FROM exit_survey_log
            WHERE promo_code = :code
            LIMIT 1
        """), {"code": promo_code})
        row = r.fetchone()
    if not row:
        return {"granted": False, "reason": "code_not_found"}

    round_num = int(row.round or 0)
    if round_num not in BONUS_RULES:
        return {"granted": False, "reason": f"round_{round_num}_not_retention"}

    rule = BONUS_RULES[round_num]
    granted_gacha = 0
    granted_shaker = None

    # Grant gacha credits
    if rule["gacha"] > 0:
        try:
            async with get_session() as s:
                await s.execute(_t("""
                    INSERT INTO gachapon_credits (user_id, telegram_id, credits, total_purchased)
                    VALUES (:u, :tg, :cr, 0)
                    ON CONFLICT (user_id) DO UPDATE SET
                        credits = gachapon_credits.credits + :cr,
                        updated_at = NOW()
                """), {"u": user_id, "tg": telegram_id, "cr": rule["gacha"]})
                await s.commit()
            granted_gacha = rule["gacha"]
            logger.info("Retention bonus: +%d gacha to tg=%s code=%s round=%s",
                        rule["gacha"], telegram_id, promo_code, round_num)
            # Mark redeemed in exit_survey_log if this was an exit survey promo
            if round_num == 400:
                try:
                    async with get_session() as s_red:
                        await s_red.execute(_t(
                            "UPDATE exit_survey_log SET redeemed_at = NOW() "
                            "WHERE promo_code = :c AND redeemed_at IS NULL"
                        ), {"c": promo_code})
                        await s_red.commit()
                except Exception as _e:
                    logger.warning("exit_survey redeemed update failed: %s", _e)
        except Exception as e:
            logger.exception("gacha bonus grant failed: %s", e)

    # Grant shaker ticket (1 random unique number)
    if rule["shaker"] > 0:
        try:
            async with get_session() as s:
                # Get currently used numbers
                ur = await s.execute(_t("""
                    SELECT number FROM shaker_tickets
                    WHERE status = 'ACTIVE' AND expires_at > NOW()
                """))
                used = {row[0] for row in ur.fetchall()}
                # Pick random available number
                available = [f"{i:02d}" for i in range(100) if f"{i:02d}" not in used]
                if available:
                    num = random.choice(available)
                    expires = datetime.utcnow() + timedelta(days=30)
                    await s.execute(_t("""
                        INSERT INTO shaker_tickets
                          (user_id, telegram_id, number, payment_id, purchased_at, expires_at, status)
                        VALUES (:u, :tg, :n, :p, NOW(), :e, 'ACTIVE')
                    """), {"u": user_id, "tg": telegram_id, "n": num,
                           "p": payment_id, "e": expires})
                    await s.commit()
                    granted_shaker = num
                    logger.info("Retention bonus: shaker #%s to tg=%s code=%s",
                                num, telegram_id, promo_code)
        except Exception as e:
            logger.exception("shaker bonus grant failed: %s", e)

    return {
        "granted": True,
        "gacha": granted_gacha,
        "shaker": granted_shaker,
        "round": round_num,
        "label": rule["label"],
    }


def build_bonus_message(result: dict) -> str | None:
    """Build customer-facing bonus confirmation message."""
    if not result.get("granted"):
        return None
    parts = []
    if result.get("gacha"):
        parts.append(f"🎁 กาชาปอง <b>+{result['gacha']} หมุน</b>")
    if result.get("shaker"):
        parts.append(f"🎰 เลขห้องมีคนชัก: <b>{result['shaker']}</b>")
    if not parts:
        return None
    body = "\n".join(parts)
    return (
        f"🎉 <b>ของแถมจัดให้แล้วค่ะ!</b>\n\n"
        f"{body}\n\n"
        f"🎁 หมุนกาชาปองได้เลย — ที่เมนู /start → 🎰 กาชาปอง\n"
        f"🎰 ลุ้นห้องมีคนชักทุกจันทร์ 21:00 ค่ะ"
    )


__all__ = ["grant_retention_bonus", "build_bonus_message", "BONUS_RULES"]
