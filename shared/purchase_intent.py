"""Purchase intent — ตั๋วซื้อจาก Mini App.

ก่อนลูกค้าโอนเงิน Mini App จะสร้าง intent บอกระบบว่า:
- ลูกค้าคนไหนกำลังจะซื้อ
- ซื้อ tier อะไร ราคาเท่าไหร่ ใช้โปรอะไร (ถ้ามี)

ตอน sales bot รับสลิป — ถ้า user ไม่กดเลือก tier ในบอท ก็ดูจาก intent

Uses raw asyncpg (same pattern as shared/promotion_service.py) so works
in BOTH sales bot AND dashboard containers without shared pool.
"""
from __future__ import annotations

import logging
import os
from decimal import Decimal
from typing import Optional

logger = logging.getLogger(__name__)


def _conn_str() -> str:
    return os.environ.get("DATABASE_URL", "").replace("postgresql+asyncpg://", "postgresql://")


async def _connect():
    import asyncpg
    return await asyncpg.connect(_conn_str())


async def create_intent(
    tg_id: int,
    tier: str,
    original_price: float | int | Decimal,
    final_price: float | int | Decimal,
    promo_id: Optional[int] = None,
    source: str = "miniapp",
    ttl_minutes: int = 30,
) -> Optional[int]:
    """สร้าง intent ใหม่ + return intent_id (None on failure — caller does not crash)."""
    try:
        conn = await _connect()
        try:
            row = await conn.fetchrow(
                """
                INSERT INTO purchase_intents (
                    user_telegram_id, tier, original_price, final_price,
                    promo_id, source, created_at, expires_at
                )
                VALUES ($1, $2, $3, $4, $5, $6, NOW(), NOW() + ($7 || ' minutes')::interval)
                RETURNING id
                """,
                int(tg_id), str(tier),
                Decimal(str(original_price)), Decimal(str(final_price)),
                promo_id, source, str(ttl_minutes),
            )
        finally:
            await conn.close()
        intent_id = row["id"] if row else None
        logger.info(
            "INTENT_CREATED: id=%s tg=%s tier=%s final=%s promo_id=%s source=%s ttl=%sm",
            intent_id, tg_id, tier, final_price, promo_id, source, ttl_minutes,
        )
        return intent_id
    except Exception as exc:
        logger.warning("INTENT_CREATE_FAIL: tg=%s tier=%s err=%s", tg_id, tier, exc)
        return None


async def find_latest_pending(tg_id: int) -> Optional[dict]:
    """หา intent ล่าสุดที่ยังใช้ได้ของลูกค้า (unconsumed + not expired).

    Returns: {id, tier, original_price, final_price, promo_id, source, created_at, expires_at}
    หรือ None ถ้าไม่มี
    """
    try:
        conn = await _connect()
        try:
            row = await conn.fetchrow(
                """
                SELECT id, tier, original_price, final_price, promo_id, source,
                       created_at, expires_at, receiver_account_id
                FROM purchase_intents
                WHERE user_telegram_id = $1
                  AND consumed_at IS NULL
                  AND expires_at > NOW()
                ORDER BY created_at DESC
                LIMIT 1
                """,
                int(tg_id),
            )
        finally:
            await conn.close()
        if row:
            return dict(row)
        return None
    except Exception as exc:
        logger.warning("INTENT_FIND_FAIL: tg=%s err=%s", tg_id, exc)
        return None


async def consume_intent(intent_id: int, payment_id: Optional[int] = None) -> bool:
    """Mark intent as consumed (ใช้ตั๋วแล้ว) — ผูกกับ payment_id ถ้ามี."""
    try:
        conn = await _connect()
        try:
            result = await conn.execute(
                """
                UPDATE purchase_intents
                SET consumed_at = NOW(),
                    consumed_payment_id = $2
                WHERE id = $1 AND consumed_at IS NULL
                """,
                int(intent_id), payment_id,
            )
        finally:
            await conn.close()
        ok = "UPDATE 1" in result
        logger.info("INTENT_CONSUMED: id=%s payment_id=%s ok=%s", intent_id, payment_id, ok)
        return ok
    except Exception as exc:
        logger.warning("INTENT_CONSUME_FAIL: id=%s err=%s", intent_id, exc)
        return False

async def set_intent_receiver(intent_id: int, account_id: int) -> bool:
    """บันทึกบัญชีที่ระบบสุ่มให้ลูกค้าตอนซื้อ — ใช้ตอนนับยอดแม้ Slip2Go อ่านไม่ออก."""
    try:
        conn = await _connect()
        try:
            await conn.execute(
                "UPDATE purchase_intents SET receiver_account_id = $2 WHERE id = $1",
                int(intent_id), int(account_id),
            )
        finally:
            await conn.close()
        return True
    except Exception as exc:
        logger.warning("INTENT_SET_RECEIVER_FAIL: id=%s err=%s", intent_id, exc)
        return False
