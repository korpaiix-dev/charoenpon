"""
Broadcast VIP 300 promo to FREE groups + (optionally) DM customers.
Triggered by VPS cron at 06:00 Bangkok time on 2026-06-18.
"""
import asyncio
import os
import sys
import logging
from datetime import datetime

import httpx

# Configuration
IMAGE_PATH = "/root/charoenpon/marketing/vip_300_morning_promo.jpg"  # Boss will replace
SALES_BOT_TOKEN = os.environ.get("SALES_BOT_TOKEN") or os.environ.get("BOT_TOKEN", "")

# Marketing caption
CAPTION = (
    "👑 <b>เปิดรับสมาชิกใหม่ — กลุ่ม VIP เจริญพร</b>\n\n"
    "💎 สาวสวยพร้อมเสิร์ฟทุกวัน — งานเด็ดอัปใหม่ตลอด\n"
    "🎁 <b>เพียง 300 บาท / 30 วัน</b>\n\n"
    "📲 สมัครสมาชิกได้ที่:\n"
    "👉 <a href=\"https://t.me/NamwarnJarern_bot\">@NamwarnJarern_bot</a>\n\n"
    "<i>⏰ โปรเช้านี้เท่านั้น</i>"
)

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("broadcast_vip300")


async def get_free_groups():
    """Get all active FREE-tier groups from DB."""
    import asyncpg
    db = os.environ.get("DATABASE_URL", "").replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(db)
    try:
        rows = await conn.fetch(
            "SELECT chat_id, title FROM group_registry "
            "WHERE is_active = TRUE AND min_tier::text = 'FREE'"
        )
    finally:
        await conn.close()
    return [{"chat_id": r["chat_id"], "title": r["title"]} for r in rows]


async def send_photo(client, chat_id, title):
    if not os.path.exists(IMAGE_PATH):
        log.error("Image not found: %s — skipping %s", IMAGE_PATH, title)
        return False
    try:
        with open(IMAGE_PATH, "rb") as f:
            files = {"photo": (os.path.basename(IMAGE_PATH), f, "image/jpeg")}
            data = {
                "chat_id": str(chat_id),
                "caption": CAPTION,
                "parse_mode": "HTML",
            }
            r = await client.post(
                f"https://api.telegram.org/bot{SALES_BOT_TOKEN}/sendPhoto",
                data=data, files=files
            )
            if r.status_code == 200:
                log.info("✅ %s (%s)", title, chat_id)
                return True
            log.warning("❌ %s — %s %s", title, r.status_code, r.text[:120])
            return False
    except Exception as e:
        log.exception("error %s: %s", title, e)
        return False


async def main():
    if not SALES_BOT_TOKEN:
        log.error("SALES_BOT_TOKEN not set")
        sys.exit(1)
    if not os.path.exists(IMAGE_PATH):
        log.error("Image not found: %s", IMAGE_PATH)
        sys.exit(1)

    groups = await get_free_groups()
    log.info("Broadcasting to %d FREE groups", len(groups))

    success = failed = 0
    async with httpx.AsyncClient(timeout=30.0) as client:
        for g in groups:
            ok = await send_photo(client, g["chat_id"], g["title"])
            if ok: success += 1
            else: failed += 1
            await asyncio.sleep(2)  # rate-limit safe

    log.info("===== DONE: success=%d failed=%d =====", success, failed)

asyncio.run(main())
