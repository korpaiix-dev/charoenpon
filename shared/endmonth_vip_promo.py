"""End-month VIP G300 promotion helpers.

Promotion requested by boss (2026-04-28):
- VIP เจริญพร 18+ / G300 normally 300 THB
- Promo price 200 THB
- Visible through 30 April 2026; expires automatically at 2026-05-01 00:00 Asia/Bangkok
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

TH_TZ = timezone(timedelta(hours=7))
PROMO_TIER = "300"
PROMO_NORMAL_PRICE = Decimal("300")
PROMO_PRICE = Decimal("200")
PROMO_2499_TIER = "2499"
PROMO_2499_NORMAL_PRICE = Decimal("2499")
PROMO_2499_PRICE = Decimal("2000")
PROMO_END_TH = datetime(2026, 5, 1, 0, 0, 0, tzinfo=TH_TZ)
PROMO_TITLE = "VIP เจริญพร 18+"
PROMO_2499_TITLE = "GOD MODE ถาวร"
PROMO_DATE_TEXT = "ถึงวันที่ 30 เมษายนนี้เท่านั้น"
SALES_BOT_DEEPLINK = "tg://resolve?domain=NamwarnJarern_bot&start=packages"
import os
PROMO_IMAGE_PATH = os.environ.get("ENDMONTH_PROMO_IMAGE_PATH", "/app/assets/vip-charoenpon-18plus-apr30.jpg")
PROMO_2499_IMAGE_PATH = os.environ.get("ENDMONTH_2499_PROMO_IMAGE_PATH", "/app/assets/godmode-2499-to-2000-apr30.jpg")


def now_th() -> datetime:
    return datetime.now(TH_TZ)


def is_endmonth_vip_promo_active(at: datetime | None = None) -> bool:
    """Return True while the G300 end-month promo should be shown/used."""
    current = at or now_th()
    if current.tzinfo is None:
        current = current.replace(tzinfo=TH_TZ)
    return current.astimezone(TH_TZ) < PROMO_END_TH


def get_effective_price_for_tier(tier: str, base_price: Decimal) -> Decimal:
    """Apply active end-month promos."""
    if tier == PROMO_TIER and is_endmonth_vip_promo_active():
        return PROMO_PRICE
    if tier == PROMO_2499_TIER and is_endmonth_vip_promo_active():
        return PROMO_2499_PRICE
    return base_price


def get_promo_badge_for_tier(tier: str) -> str:
    if tier == PROMO_TIER and is_endmonth_vip_promo_active():
        return f"🔥 โปร 300 เหลือ 200 บาท — {PROMO_DATE_TEXT}"
    if tier == PROMO_2499_TIER and is_endmonth_vip_promo_active():
        return f"🔥 โปร 2,499 เหลือ 2,000 บาท — {PROMO_DATE_TEXT}"
    return ""


def get_group_promo_caption(group_index: int | None = None) -> str:
    """Caption for posting promo image into public/free groups.

    Uses embedded HTML link per boss request: สมัครสมาชิกกดที่นี่.
    """
    start = "packages" if group_index is None else f"apr30_g{group_index}"
    link = f"tg://resolve?domain=NamwarnJarern_bot&start={start}"
    return (
        "🔥 <b>VIP เจริญพร 18+</b>\n\n"
        "โปรสิ้นเดือน กลุ่ม 300\n"
        "จาก <s>300 บาท</s> เหลือ <b>200 บาท</b>\n"
        f"⏰ {PROMO_DATE_TEXT}\n\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f'👉 <a href="{link}">สมัครสมาชิกกดที่นี่</a>\n'
        "━━━━━━━━━━━━━━━━━━"
    )


def get_user_broadcast_caption() -> str:
    return (
        "🔥 <b>โปรสิ้นเดือน VIP เจริญพร 18+</b>\n\n"
        "กลุ่ม 300 ลดเหลือ <b>200 บาท</b>\n"
        f"⏰ {PROMO_DATE_TEXT}\n\n"
        "กดปุ่มด้านล่างเพื่อเลือกแพ็กเกจได้เลยค่ะ"
    )


def get_group_2499_promo_caption(group_index: int | None = None) -> str:
    """Caption for GOD MODE 2499 -> 2000 group promotion via Content Bot."""
    start = "packages" if group_index is None else f"apr30_god_g{group_index}"
    link = f"tg://resolve?domain=NamwarnJarern_bot&start={start}"
    return (
        "💎 <b>โปรสิ้นเดือน GOD MODE ถาวร</b>\n\n"
        "แพ็ก 2,499 ลดเหลือ <b>2,000 บาท</b>\n"
        "เข้าครบทุกห้อง + Lifetime ไม่ต้องต่ออายุ\n"
        f"⏰ {PROMO_DATE_TEXT} เวลา 23:59\n\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f'👉 <a href="{link}">สมัครสมาชิกกดที่นี่</a>\n'
        "━━━━━━━━━━━━━━━━━━"
    )

# >>> MAY26_COMBO_PROMO <<<
# May 2026 OnlyFans Combo + GOD 3M promo (independent of Apr endmonth)
PROMO_500_TIER = "500"
PROMO_500_NORMAL_PRICE = Decimal("500")
PROMO_500_PRICE = Decimal("349")
PROMO_1299_TIER = "1299"
PROMO_1299_NORMAL_PRICE = Decimal("1299")
PROMO_1299_PRICE = Decimal("999")
PROMO_MAY_END_TH = datetime(2026, 6, 1, 0, 0, 0, tzinfo=TH_TZ)
PROMO_MAY_TITLE = "OnlyFans Combo + GOD 90 วัน"
PROMO_MAY_DATE_TEXT = "ถึงวันที่ 31 พฤษภาคมนี้เท่านั้น"

def is_may_combo_promo_active(at=None) -> bool:
    """True while May-end combo promo (TIER_500/1299) is active."""
    current = at or now_th()
    if current.tzinfo is None:
        current = current.replace(tzinfo=TH_TZ)
    return current.astimezone(TH_TZ) < PROMO_MAY_END_TH

def get_may_effective_price(tier: str, base_price):
    if tier == PROMO_500_TIER and is_may_combo_promo_active():
        return PROMO_500_PRICE
    if tier == PROMO_1299_TIER and is_may_combo_promo_active():
        return PROMO_1299_PRICE
    return base_price

def get_may_promo_badge(tier: str) -> str:
    if tier == PROMO_500_TIER and is_may_combo_promo_active():
        return f"🔥 โปร OF Combo 500 เหลือ 349 บาท — {PROMO_MAY_DATE_TEXT}"
    if tier == PROMO_1299_TIER and is_may_combo_promo_active():
        return f"🔥 โปร GOD 3M 1,299 เหลือ 999 บาท — {PROMO_MAY_DATE_TEXT}"
    return ""
# <<< MAY26_COMBO_PROMO >>>


# MID_MONTH_FLASH — 15-17 มิ.ย. 2026 (BKK)
def is_mid_month_flash_active() -> bool:
    """Mid-Month Flash Sale window: 15-17 มิ.ย. 2026 BKK."""
    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone(timedelta(hours=7)))
    return (now.year == 2026 and now.month == 6 and 15 <= now.day <= 17)

MID_FLASH_VIP_PRICE = 199
MID_FLASH_OF_PRICE = 349
MID_FLASH_GOD3M_PRICE = 999
