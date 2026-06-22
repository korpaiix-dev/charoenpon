"""Periodic worker — ส่งลิงก์เข้ากลุ่มให้ลูกค้าที่ได้ subscription prize จากกาชา.

Design:
- รัน periodic ใน sales-bot scheduler (ทุก 2 นาที)
- หา gachapon_pulls ที่ claimed + prize เป็น sub-grant + ยังไม่ส่งลิงก์
- ใช้ admin_log marker 'gacha_sub_link_sent' เป็น idempotency key
- ส่ง DM ผ่าน sales-bot token
- generate invite links ผ่าน guardian-bot token (เหมือน prae_tools.handle_group_access_issue)

ไม่กระทบ flow เก่า — append-only check
"""
from __future__ import annotations
import asyncio
import logging
import os
from datetime import datetime

from sqlalchemy import text as _t
from shared.database import get_session

logger = logging.getLogger(__name__)

SUB_GRANT_PRIZES = ("VIP_300", "OF_500", "GOD_1299", "GOD_2499")

PRIZE_LABELS = {
    "VIP_300": ("VIP 30 วัน", "🚀"),
    "OF_500": ("OnlyFans + VIP 30 วัน", "🔥"),
    "GOD_1299": ("GOD MODE 90 วัน", "💎"),
    "GOD_2499": ("GOD MODE ถาวร", "👑"),
}


async def _find_pending() -> list[dict]:
    """ค้นหา pull ที่ได้ sub prize claimed=true ยังไม่ได้ลิงก์ + ไม่เกิน 24 ชม."""
    async with get_session() as s:
        r = await s.execute(_t("""
            SELECT gp.id AS pull_id, gp.telegram_id, gp.user_id, gp.prize_code,
                   u.first_name, u.is_banned
            FROM gachapon_pulls gp
            JOIN users u ON u.id = gp.user_id
            WHERE gp.claimed = TRUE
              AND gp.prize_code = ANY(:codes)
              AND gp.pulled_at > NOW() - INTERVAL '24 hours'
              AND NOT EXISTS (
                  SELECT 1 FROM admin_logs a
                  WHERE a.action = 'gacha_sub_link_sent'
                    AND a.target_id = gp.id
              )
              AND u.is_banned = FALSE
            ORDER BY gp.pulled_at
            LIMIT 5
        """), {"codes": list(SUB_GRANT_PRIZES)})
        return [dict(row._mapping) for row in r.fetchall()]


async def _mark_sent(pull_id: int, telegram_id: int, link_count: int) -> None:
    """Log marker ป้องกัน double-send."""
    async with get_session() as s:
        await s.execute(_t("""
            INSERT INTO admin_logs (admin_id, action, target_type, target_id, details)
            VALUES (0, 'gacha_sub_link_sent', 'gacha_pull', :pid, :det)
        """), {"pid": pull_id, "det": f"tg={telegram_id} links={link_count}"})
        await s.commit()


async def _deliver_one(pull: dict) -> bool:
    """ส่งลิงก์ + DM ให้ลูกค้า 1 ราย — return True if delivered."""
    from shared.prae_tools import handle_group_access_issue
    from telegram import Bot

    tg = pull["telegram_id"]
    pull_id = pull["pull_id"]
    prize_code = pull["prize_code"]
    label, emoji = PRIZE_LABELS.get(prize_code, (prize_code, "🎁"))

    result = await handle_group_access_issue(tg)
    if result.get("status") != "active":
        logger.info("gacha_link_worker: tg=%s pull=%s status=%s, skipping",
                    tg, pull_id, result.get("status"))
        # Still mark to avoid re-processing
        await _mark_sent(pull_id, tg, 0)
        return False

    links = result.get("invite_links", [])
    if not links:
        logger.warning("gacha_link_worker: tg=%s no links generated", tg)
        await _mark_sent(pull_id, tg, 0)
        return False

    lines = [
        f"🎉 ขอแสดงความยินดีค่ะ! คุณได้รับ <b>{emoji} {label}</b> จากกาชา",
        "",
        "นี่คือลิงก์เข้ากลุ่มของแพ็กเกจคุณค่ะ 👇",
        "",
    ]
    for ln in links:
        lines.append(f"🚀 <a href='{ln['url']}'>{ln['title']}</a>")
    lines += [
        "",
        "⏰ ลิงก์ใช้ครั้งเดียว หมดอายุใน 24 ชม.",
        "ขอบคุณที่อยู่กับเจริญพรนะคะ 💕",
    ]
    msg = "\n".join(lines)

    tok = os.environ.get("SALES_BOT_TOKEN", "")
    b = Bot(token=tok)
    await b.initialize()
    try:
        sent = await b.send_message(
            chat_id=tg, text=msg, parse_mode="HTML",
            disable_web_page_preview=True,
        )
        logger.info("gacha_link_worker: sent tg=%s pull=%s links=%d msg=%s",
                    tg, pull_id, len(links), sent.message_id)
        await _mark_sent(pull_id, tg, len(links))
        return True
    except Exception as e:
        logger.warning("gacha_link_worker: DM fail tg=%s: %s", tg, e)
        # Mark as attempted to prevent infinite retry (admin must intervene)
        await _mark_sent(pull_id, tg, -1)
        return False
    finally:
        try: await b.shutdown()
        except Exception: pass


async def run_gacha_link_delivery_once() -> dict:
    """Public entrypoint — รัน 1 รอบ. Use in scheduler."""
    pending = await _find_pending()
    if not pending:
        return {"checked": 0, "delivered": 0}

    delivered = 0
    for pull in pending:
        ok = await _deliver_one(pull)
        if ok:
            delivered += 1
        await asyncio.sleep(1.0)  # rate limit between customers

    return {"checked": len(pending), "delivered": delivered}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    stats = asyncio.run(run_gacha_link_delivery_once())
    print(f"DONE: {stats}")
