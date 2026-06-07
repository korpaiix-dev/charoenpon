"""Caption Hub — load campaign captions from DB.

Reads `promotion_campaigns` table as single source of truth.
Falls back to a built-in CAMPAIGNS dict (legacy) if the row is missing.

Usage:
    from shared.captions import load_caption

    spec = await load_caption("lucky66")
    # spec.image_path, spec.user_broadcast_caption, spec.group_caption, ...
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class CampaignCaption:
    key: str
    name: str
    image_path: str
    group_caption: Optional[str]
    user_broadcast_caption: Optional[str]
    bot_sales_text: Optional[str]
    bot_badge: Optional[str]


# ── Built-in fallback (legacy CAMPAIGNS dict, will be removed once DB is fully migrated) ──
BOT_URL = "https://t.me/NamwarnJarern_bot?start=packages"

LEGACY_FALLBACK: dict[str, dict] = {
    "welcome": {
        "name": "Welcome",
        "image": "/app/assets/campaigns/01_welcome.png",
        "broadcast": (
            "🎉 <b>ยินดีต้อนรับสู่ VIP เจริญพร</b>\n"
            "═══════════════\n\n"
            "💎 คลิป HD ครบทุกห้อง 10,000+ ชิ้น\n"
            "🔥 อัพเดทคลิปใหม่ทุกวัน — ไม่มีโฆษณา\n"
            "⚡ เริ่มต้นเพียง ฿300 / 30 วัน\n\n"
            f'👉 <a href="{BOT_URL}">ดูแพ็คเกจทั้งหมด</a>'
        ),
    },
    "referral": {
        "name": "Referral",
        "image": "/app/assets/campaigns/02_referral.png",
        "broadcast": (
            "🎁 <b>ชวนเพื่อนมา VIP เจริญพร</b>\n"
            "═══════════════\n\n"
            "✨ ชวน 1 คน = +7 วัน VIP <b>ฟรี</b> (มูลค่า ฿100)\n"
            "🔥 ครบ 3 คน รับ VIP 30 วัน <b>ฟรี</b>!\n\n"
            "✅ ระบบให้ลิ้งชวนเฉพาะของคุณ\n"
            "✅ เพื่อนสมัครแพ็คเกจไหนก็ได้\n\n"
            f'👉 <a href="{BOT_URL}">รับลิ้งชวนเพื่อน</a>'
        ),
    },
    "flash1": {
        "name": "Mid-Month Flash 48h",
        "image": "/app/assets/campaigns/03_flash1.png",
        "broadcast": (
            "⚡ <b>FLASH SALE 48 ชั่วโมง</b> ⚡\n"
            "═══════════════\n\n"
            "🔥 ลดทุก tier — หมดเขตเร็วๆ นี้!\n\n"
            "💎 VIP 30 วัน    <s>฿300</s> <b>฿199</b>  (-33%)\n"
            "🔥 OF+VIP 30วัน  <s>฿500</s> <b>฿349</b>  (-30%)\n"
            "👑 GOD 90 วัน    <s>฿1,299</s> <b>฿999</b> (-23%)\n\n"
            "⏰ จำกัดเวลา 48 ชั่วโมง — รีบกดด่วน!\n"
            f'👉 <a href="{BOT_URL}">กดสมัคร Flash Sale</a>'
        ),
    },
    "flash2": {
        "name": "Bonus Days",
        "image": "/app/assets/campaigns/04_flash2.png",
        "broadcast": (
            "🎁 <b>BONUS DAYS — ซื้อตอนนี้รับวันฟรี!</b>\n"
            "═══════════════\n\n"
            "💎 VIP / OF+VIP รับ <b>+7 วัน ฟรี</b>\n"
            "👑 GOD รับ <b>+14 วัน ฟรี</b>\n\n"
            "✨ Bonus จำกัดเวลา — สมัครเลย\n"
            f'👉 <a href="{BOT_URL}">รับโบนัส +วันฟรี</a>'
        ),
    },
    "winback": {
        "name": "Win-back / Comeback",
        "image": "/app/assets/campaigns/05_winback.png",
        "broadcast": (
            "💔 <b>ต้อนรับกลับมา!</b>\n"
            "═══════════════\n\n"
            "🎁 ส่วนลดเฉพาะคุณ <b>-30%</b>\n"
            "💎 VIP 30 วัน  <s>฿300</s> <b>฿210</b>\n\n"
            "⏰ ส่วนลดนี้หมดอายุใน 48 ชั่วโมง\n"
            f'👉 <a href="{BOT_URL}">รับส่วนลด ฿210</a>'
        ),
    },
    "lucky66": {
        "name": "Lucky 6.6 Sale",
        "image": "/app/assets/campaigns/06_lucky66.png",
        "broadcast": (
            "🍀 <b>LUCKY 6.6 SALE — วันนี้วันเดียว!</b> 🍀\n"
            "═══════════════\n\n"
            "🔥 ลดสุดทุก tier — 24 ชั่วโมงเท่านั้น!\n\n"
            "💎 VIP 30 วัน    <s>฿300</s> <b>฿166</b>  (-45%)\n"
            "🔥 OF+VIP 30วัน  <s>฿500</s> <b>฿266</b>  (-47%)\n"
            "👑 GOD 90 วัน    <s>฿1,299</s> <b>฿666</b> (-49%)\n"
            "🍀 GOD ถาวร      <s>฿2,499</s> <b>฿2,266</b>\n\n"
            "✨ Bonus: ทุก tier ได้ <b>+6 วัน ฟรี!</b>\n\n"
            "⏰ หมดเขต 23:59 คืนนี้\n"
            f'👉 <a href="{BOT_URL}">กดสมัครเลย — Lucky 6.6</a>'
        ),
    },
    "birthday": {
        "name": "Birthday เฮียตั๋ง",
        "image": "/app/assets/campaigns/07_birthday.png",
        "broadcast": (
            "🎂 <b>เดือนเกิดเฮียตั๋ง เจริญพร</b> 🎉\n"
            "═══════════════\n\n"
            "🎁 แจกใหญ่ <b>GOD MODE ถาวร</b>\n"
            "💎 มูลค่า ฿2,499 — สิทธิ์ตลอดชีพ\n\n"
            "📋 <b>กติกา:</b>\n"
            "✅ ซื้อ OF+VIP 30 วัน ฿500\n"
            "✅ ระบบเข้าจับฉลากให้อัตโนมัติ\n\n"
            "📅 ประกาศผล <b>10 มิ.ย. 18:00 น.</b>\n"
            "⏰ ปิดรับสมัคร 10 มิ.ย. 12:00\n\n"
            f'👉 <a href="{BOT_URL}">สมัครเลย — ลุ้น GOD ถาวร</a>'
        ),
    },
}


async def load_caption(key: str) -> Optional[CampaignCaption]:
    """Load a campaign caption from DB; fall back to LEGACY_FALLBACK if missing.

    Returns None only if key is unknown both in DB and fallback.
    """
    try:
        from shared.database import get_session
        from sqlalchemy import text
        async with get_session() as s:
            r = await s.execute(text("""
                SELECT name, image_path, group_caption, user_broadcast_caption,
                       bot_sales_text, bot_badge
                FROM promotion_campaigns
                WHERE name = :key AND is_active = true
                ORDER BY created_at DESC
                LIMIT 1
            """), {"key": key})
            row = r.fetchone()
            if row:
                return CampaignCaption(
                    key=key,
                    name=row[0] or key,
                    image_path=row[1] or "",
                    group_caption=row[2],
                    user_broadcast_caption=row[3],
                    bot_sales_text=row[4],
                    bot_badge=row[5],
                )
    except Exception as exc:
        logger.warning("load_caption(%s) DB lookup failed: %s — falling back", key, exc)

    # Fallback
    spec = LEGACY_FALLBACK.get(key)
    if spec:
        return CampaignCaption(
            key=key,
            name=spec["name"],
            image_path=spec["image"],
            group_caption=spec["broadcast"],
            user_broadcast_caption=spec["broadcast"],
            bot_sales_text=None,
            bot_badge=None,
        )
    return None


def load_caption_sync(key: str) -> Optional[CampaignCaption]:
    """Sync fallback — useful for non-async scripts. Reads only LEGACY_FALLBACK."""
    spec = LEGACY_FALLBACK.get(key)
    if spec:
        return CampaignCaption(
            key=key,
            name=spec["name"],
            image_path=spec["image"],
            group_caption=spec["broadcast"],
            user_broadcast_caption=spec["broadcast"],
            bot_sales_text=None,
            bot_badge=None,
        )
    return None


__all__ = ["CampaignCaption", "load_caption", "load_caption_sync", "LEGACY_FALLBACK"]
