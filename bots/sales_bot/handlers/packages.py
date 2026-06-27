# >>> MAY26_COMBO_PROMO <<<  # patched packages.py
"""Package display handler - Sales Bot แพร.

แสดง 4 แพ็กเกจ: 300 / 500 / 1299 / 2499 พร้อมรายละเอียดกลุ่ม.
"""

from __future__ import annotations

import logging
from shared.discount_helper import reserve_in_context as _disc_reserve

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes


# === SAFE_EDIT — handles photo-original messages ===
async def _safe_edit(query, text: str, reply_markup=None, parse_mode="HTML",
                      disable_web_page_preview=True) -> None:
    """Edit message text safely. Falls back to delete+send when original is a photo."""
    try:
        await query.edit_message_text(
            text, parse_mode=parse_mode, reply_markup=reply_markup,
            disable_web_page_preview=disable_web_page_preview,
        )
    except Exception:
        # Original is photo → can't edit text. Delete + send new.
        try:
            await query.message.delete()
        except Exception:
            pass
        try:
            await query.message.chat.send_message(
                text, parse_mode=parse_mode, reply_markup=reply_markup,
                disable_web_page_preview=disable_web_page_preview,
            )
        except Exception:
            try:
                await query.message.reply_text(
                    text, parse_mode=parse_mode, reply_markup=reply_markup,
                )
            except Exception:
                pass





# FIX 2026-06-26 (audit): show boss-created promotion_campaigns from dashboard in menu
async def _active_campaign_banner() -> str:
    """Return banner text for any active promotion_campaigns from dashboard.

    Boss creates campaigns in dashboard → this auto-shows in bot menu.
    Returns empty string if none active."""
    try:
        from shared.database import get_session
        from sqlalchemy import text as _t
        async with get_session() as s:
            rows = (await s.execute(_t("""
                SELECT pc.name, pc.normal_price, pc.promo_price, pc.bot_badge, pc.bot_sales_text,
                       pk.name AS pkg_name
                FROM promotion_campaigns pc
                LEFT JOIN packages pk ON pk.id = pc.package_id
                WHERE pc.is_active = TRUE
                  AND pc.starts_at IS NOT NULL AND pc.ends_at IS NOT NULL
                  AND pc.starts_at <= NOW() AND pc.ends_at >= NOW()
                  AND pc.promo_price IS NOT NULL
                ORDER BY pc.ends_at
                LIMIT 3
            """))).fetchall()
        if not rows:
            return ""
        lines = []
        for r in rows:
            badge = r.bot_badge or "🎁"
            if r.normal_price and r.promo_price and r.pkg_name:
                lines.append(f"{badge} <b>{r.name}</b>: <s>{int(r.normal_price)}</s> {int(r.promo_price)} บาท ({r.pkg_name})")
            else:
                lines.append(f"{badge} <b>{r.name}</b>")
        return "🎁 <b>โปรพิเศษวันนี้!</b>\n" + "\n".join(lines) + "\n━━━━━━━━━━━━━━━━━━━━━\n\n"
    except Exception:
        return ""

from shared.endmonth_vip_promo import (
    is_mid_month_flash_active,
    is_lucky_6_active,
    is_birthday_promo_active,
    PROMO_2499_PRICE,
    PROMO_DATE_TEXT,
    PROMO_PRICE,
    PROMO_500_PRICE,
    PROMO_1299_PRICE,
    PROMO_MAY_DATE_TEXT,
    get_promo_badge_for_tier,
    is_endmonth_vip_promo_active,
    is_may_combo_promo_active,
)
from shared.songkran_promo import is_songkran_promo_window

logger = logging.getLogger(__name__)



# DAY 0 (2026-06-28): Auto-apply active promotions to sales menu labels
async def _get_active_promo_discounts() -> dict:
    """Returns {tier_str: {promo_code, discounted_price, savings, promo_name}} for all active promos.
    Empty dict if no active promo / on error (fail-open = show regular prices).
    """
    try:
        from shared.promotion_service import list_active_promotions, calculate_price
        promos = await list_active_promotions()
        result = {}
        TIER_PRICE_MAP = {
            "TIER_300": 300, "TIER_500": 500, "TIER_1299": 1299, "TIER_2499": 2499,
            "TIER_100": 100,
        }
        for promo in promos:
            pkg_codes = promo.get("package_codes") or []
            if isinstance(pkg_codes, str):
                import json as _j
                try: pkg_codes = _j.loads(pkg_codes)
                except: pkg_codes = []
            for tier_str in pkg_codes:
                base = TIER_PRICE_MAP.get(tier_str)
                if not base:
                    continue
                calc = calculate_price(promo, tier_str, base)
                if not calc.get("applied") or calc["savings"] <= 0:
                    continue
                # Keep the best discount per tier (highest savings)
                existing = result.get(tier_str)
                if existing is None or calc["savings"] > existing["savings"]:
                    result[tier_str] = {
                        "promo_code": promo.get("code"),
                        "promo_name": promo.get("name"),
                        "promo_id": promo.get("id"),
                        "original": calc["original"],
                        "discounted": calc["discounted"],
                        "savings": calc["savings"],
                    }
        return result
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning("get_active_promo_discounts failed: %s", exc)
        return {}


# ---- Package definitions ----

SONGKRAN_PACKAGE_BONUS_LINE = "🎁 ช่วงโปร 7 วันนี้ ซื้อแพ็กนี้แถมกลุ่ม โปรโมชั่นสงกรานต์"

PACKAGES = [
    {
        "tier": "2499",
        "name": "💎 2,499.- | GOD MODE (ถาวร)",
        "price": "2,499",
        "duration": "ถาวร",
        "groups": ["VIP", "SSS", "OnlyFans", "นานาชาติ", "V GOD", "หนังซีรีส์", "สายซุ่ม", "Summer Fest 🌊"],
        "details": (
            "💎 <b>2,499.- | GOD MODE (ถาวร)</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "ตัวจบของจริง จ่ายครั้งเดียว ดูได้ตลอดชีพ!\n\n"
            "✅ เข้าครบทุกกลุ่ม (7 ห้อง + หนัง):\n"
            "• VIP (งานทางบ้าน/แอบถ่าย/นักเรียน)\n"
            "• SSS (งานแรร์กว่า หายากกว่า VIP ทีเด็ด)\n"
            "• OnlyFans (รวมงานแรร์ 50 คน++)\n"
            "• นานาชาติ VIP (คลิปต่างชาติ ยุโรป เอเชีย)\n"
            "• V GOD (งานหลุดทางบ้าน เซฟได้) ✨\n"
            "• สายซุ่ม (llอU ถ่าe) 🎲\n"
            "• 🌊 Summer Fest (งานแรร์90/สาวอ้วน/เลสเบี้ยน/สาวน้อยตกน้ำ) 🔥 NEW!\n"
            "• หนังซีรีส์ ไทย ฝรั่ง จีน เกาหลี\n\n"
            "✅ สถานะ Lifetime ไม่ต้องต่ออายุ\n"
            "✅ คุ้มที่สุดในระยะยาว\n\n"
            '📋 <a href="https://t.me/+hv7uXYj4bxFhODZl">ดูรีวิวจากลูกค้าจริง</a>\n'
            '👀 <a href="https://t.me/+Q0Qf-4t8TQo3YTBl">ดูตัวอย่างงาน</a>'
        ),
    },
    {
        "tier": "1299",
        "name": "🥈 1,299.- | GOD MODE (3 เดือน)",
        "price": "1,299",
        "duration": "90 วัน",
        "groups": ["VIP", "SSS", "OnlyFans", "นานาชาติ", "V GOD", "หนังซีรีส์", "สายซุ่ม"],
        "details": (
            "🥈 <b>1,299.- | GOD MODE (3 เดือน)</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "สายเหมา งบประหยัด จัดเต็มครบทุกห้อง!\n\n"
            "✅ เข้าครบทุกกลุ่ม (6 ห้อง + หนัง):\n"
            "• VIP (งานทางบ้าน/แอบถ่าย/นักเรียน)\n"
            "• SSS (งานแรร์กว่า หายากกว่า VIP ทีเด็ด)\n"
            "• V GOD (งานหลุดทางบ้าน เซฟได้) ✨\n"
            "• OnlyFans (รวมงานแรร์ 50 คน++)\n"
            "• นานาชาติ VIP (คลิปต่างชาติ ยุโรป เอเชีย)\n"
            "• สายซุ่ม (llอU ถ่าe) 🎲\n"
            "• หนังซีรีส์ ไทย ฝรั่ง จีน เกาหลี\n\n"
            "✅ 90 วัน (เฉลี่ยวันละ 14 บาท)\n\n"
            "💡 <b>อยากได้ครบกว่านี้?</b> GOD MODE ถาวร 2,499.- ได้เพิ่ม Summer Fest + ไม่มีหมดอายุ!"
        ),
    },
    {
        "tier": "500",
        "name": "👙 500.- | OnlyFans + VIP (30 วัน)",
        "price": "500",
        "duration": "30 วัน",
        "groups": ["VIP", "OnlyFans"],
        "details": (
            "👙 <b>500.- | OnlyFans + VIP (30 วัน)</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "คอมโบยอดฮิต! ได้ทั้งงานแรร์และงานทางบ้าน\n\n"
            "✅ เข้าได้ 2 ห้อง:\n"
            "• OnlyFans (รวมงานแรร์ 50 คน++)\n"
            "• VIP (งานทางบ้าน/นักเรียน)\n\n"
            "✅ 30 วัน\n"
            "✅ เพิ่มนิดเดียวจากตัวเริ่มต้น ได้ OF ตัวเด็ดเพิ่ม"
        ),
    },
    {
        "tier": "300",
        "name": "🥉 VIP เจริญพร 18+ | VIP (30 วัน)",
        "price": "300",
        "duration": "30 วัน",
        "groups": ["VIP"],
        "details": (
            "🥉 <b>VIP เจริญพร 18+ | VIP (30 วัน)</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "แพ็กเกจเริ่มต้น สำหรับสายทดลอง\n\n"
            "✅ เข้าได้ 1 ห้อง:\n"
            "• VIP (งานทางบ้าน/แอบถ่าย/นักเรียน)\n\n"
            "✅ 30 วัน\n"
            "✅ อัปเดตงานใหม่ทุกวัน"
        ),
    },
]


async def _build_package_list_text() -> str:
    """Build the text for the package overview."""
    # FIX 2026-06-26 (audit): fetch dashboard-managed promotion_campaigns
    # and show them in menu (override hardcoded prices when applicable)
    dash_campaigns = {}  # package_id → {name, normal_price, promo_price, bot_badge}
    dash_banner_lines = []
    try:
        from shared.database import get_session
        from sqlalchemy import text as _t_dc
        async with get_session() as _s_dc:
            _rows = (await _s_dc.execute(_t_dc("""
                SELECT pc.package_id, pc.name, pc.normal_price, pc.promo_price,
                       pc.bot_badge, pk.tier::text AS pkg_tier
                FROM promotion_campaigns pc
                LEFT JOIN packages pk ON pk.id = pc.package_id
                WHERE pc.is_active = TRUE
                  AND pc.starts_at IS NOT NULL AND pc.ends_at IS NOT NULL
                  AND pc.starts_at <= NOW() AND pc.ends_at >= NOW()
                  AND pc.promo_price IS NOT NULL AND pc.promo_price > 0
                ORDER BY pc.ends_at
                LIMIT 5
            """))).fetchall()
        for r in _rows:
            if r.package_id:
                dash_campaigns[r.package_id] = {
                    "name": r.name,
                    "normal_price": float(r.normal_price or 0),
                    "promo_price": float(r.promo_price or 0),
                    "badge": r.bot_badge or "🎁",
                    "tier": r.pkg_tier or "",
                }
                badge = r.bot_badge or "🎁"
                if r.normal_price and r.promo_price:
                    dash_banner_lines.append(
                        f"{badge} <b>{r.name}</b>: <s>{int(r.normal_price)}</s> {int(r.promo_price)} บาท"
                    )
                else:
                    dash_banner_lines.append(f"{badge} <b>{r.name}</b>")
    except Exception as _exc_dc:
        import logging
        logging.getLogger(__name__).warning("dashboard campaign fetch failed: %s", _exc_dc)

    dash_banner = ""
    if dash_banner_lines:
        dash_banner = "🎁 <b>โปรพิเศษวันนี้!</b>\n" + "\n".join(dash_banner_lines) + "\n━━━━━━━━━━━━━━━━━━━━━\n\n"

    lucky6 = is_lucky_6_active()
    birthday = is_birthday_promo_active() and not lucky6
    flash = is_mid_month_flash_active() and not lucky6 and not birthday
    if lucky6:
        flash_header = "🍀 <b>LUCKY 6.6 SALE — วันเดียวเท่านั้น!</b> 🍀\n🔥 ลด -45% to -49% + ฟรี 6 วัน\n━━━━━━━━━━━━━━━━━━━━━\n\n"
    elif birthday:
        flash_header = "🎂 <b>เดือนเกิดเฮียตั๋ง — แจกใหญ่!</b> 🎉\n🎁 ซื้อ OF+VIP ฿500 = เข้าจับฉลาก GOD ถาวร 1 รางวัล\n📅 ประกาศผล 10 มิ.ย. 18:00 น.\n━━━━━━━━━━━━━━━━━━━━━\n\n"
    elif flash:
        flash_header = "⚡ <b>FLASH SALE 48 ชม. — ลดทุก tier!</b> ⚡\n🔥 หมดเขต 17 มิ.ย. เท่านั้น!\n━━━━━━━━━━━━━━━━━━━━━\n\n"
    else:
        flash_header = ""
    songkran_bonus = "- โปรโมชั่นสงกรานต์ (โบนัสเฉพาะคนซื้อช่วงโปร)\n" if is_songkran_promo_window() else ""
    songkran_note = "🎁 ซื้อช่วงโปร 7 วันนี้ แถมกลุ่ม โปรโมชั่นสงกรานต์\n\n" if is_songkran_promo_window() else ""
    # LUCKY 6.6 > FLASH > end-month VIP promo (priority)
    if lucky6:
        vip_price_line = "💰 ราคา: <s>300</s> 166 บาท / 30 วัน 🍀 LUCKY 6.6! +ฟรี 6 วัน\n"
        god_price_line = "💰 ราคา: <s>2,499</s> 2,266 บาท / ถาวร 🍀 LUCKY 6.6! +ฟรี 6 วัน\n"
    elif flash:
        vip_price_line = "💰 ราคา: <s>300</s> 199 บาท / 30 วัน ⚡ FLASH!\n"
        god_price_line = "💰 ราคา: 2,499 บาท / ถาวร (ถาวรห้ามลด)\n"
    elif is_endmonth_vip_promo_active():
        vip_price_line = "💰 ราคา: <s>300</s> 200 บาท / 30 วัน 🔥 โปรถึง 30 เม.ย.\n"
        god_price_line = "💰 ราคา: <s>2,499</s> 2,000 บาท / ถาวร 🔥 โปรถึง 30 เม.ย.\n"
    else:
        vip_price_line = "💰 ราคา: 300 บาท / 30 วัน\n"
        god_price_line = "💰 ราคา: 2,499 บาท / ถาวร\n"
    vip_promo_note = f"🔥 <b>โปรสิ้นเดือน:</b> VIP เจริญพร 18+ จาก 300 เหลือ 200 บาท — {PROMO_DATE_TEXT}\n\n" if (is_endmonth_vip_promo_active() and not flash) else ""
    god_promo_note = f"💎 <b>โปรสิ้นเดือน:</b> GOD MODE ถาวร จาก 2,499 เหลือ 2,000 บาท — {PROMO_DATE_TEXT}\n\n" if (is_endmonth_vip_promo_active() and not flash) else ""
    return (
        dash_banner +
        flash_header +
        (
        "<b>📦 แพ็กเกจ VIP ทั้งหมด</b>\n\n"
        "เลือกแพ็กเกจที่สนใจได้เลยค่ะ 👇\n\n"
        "────────────────────\n"
        "💎 2,499.- | GOD MODE (ถาวร)\n"
        f"{god_price_line}"
        "🏠 ห้อง\n"
        "- VIP ( ถาวร )\n"
        "- SSS ( ถาวร )\n"
        "- OnlyFans ( ถาวร )\n"
        "- นานาชาติ ( ถาวร )\n"
        "- V GOD ( เซฟได้/ถาวร )\n"
        "- หนังซีรีส์ ( ถาวร )\n"
        "- สายซุ่ม ( ถาวร )\n"
        "- Summer Fest ( ถาวร )\n\n"
        f"{god_promo_note}"
        "────────────────────\n"
        "🥈 1,299.- | GOD MODE (3 เดือน)\n"
        f"💰 ราคา: {('<s>1,299</s> 666 บาท / 90 วัน 🍀 LUCKY 6.6!' if lucky6 else ('<s>1,299</s> 999 บาท / 90 วัน ⚡ FLASH!' if flash else ('<s>1,299</s> 999 บาท / 90 วัน 🔥 โปรถึง 31 พ.ค.' if is_may_combo_promo_active() else '1,299 บาท / 90 วัน')))}\n"
        "🏠 ห้อง\n"
        "- VIP ( 90 วัน )\n"
        "- SSS ( 90 วัน )\n"
        "- OnlyFans ( 90 วัน )\n"
        "- นานาชาติ ( 90 วัน )\n"
        "- V GOD ( เซฟได้/90 วัน )\n"
        "- หนังซีรีส์ ( 90 วัน )\n"
        "- สายซุ่ม ( 90 วัน )\n"
        f"{songkran_bonus}\n"
        f"{songkran_note}"
        "────────────────────\n"
        "👙 500.- | OnlyFans + VIP (30 วัน)\n"
        f"💰 ราคา: {('<s>500</s> 266 บาท / 30 วัน 🍀 LUCKY 6.6!' if lucky6 else ('<s>500</s> 349 บาท / 30 วัน ⚡ FLASH!' if flash else ('<s>500</s> 349 บาท / 30 วัน 🔥 โปรถึง 31 พ.ค.' if is_may_combo_promo_active() else '500 บาท / 30 วัน')))}\n"
        "🏠 ห้อง\n"
        "- VIP ( 30 วัน )\n"
        "- OnlyFans ( 30 วัน )\n\n"
        "────────────────────\n"
        "🥉 VIP เจริญพร 18+ | VIP (30 วัน)\n"
        f"{vip_price_line}"
        "🏠 ห้อง\n"
        "- VIP ( 30 วัน )\n\n"
        f"{vip_promo_note}"
        "────────────────────\n"
        "กดเลือกแพ็กเกจเพื่อดูรายละเอียดเพิ่มเติมค่ะ"
    ))


async def _build_package_keyboard() -> InlineKeyboardMarkup:
    """Build inline keyboard. Day-0 promo > LUCKY_6.6 > Flash > end-month > combo > normal."""
    # DAY 0 (2026-06-28): check active Day-0 promotions FIRST — overrides legacy promos
    _dayzero_promos = await _get_active_promo_discounts()
    
    lucky6 = is_lucky_6_active()
    flash = is_mid_month_flash_active() and not lucky6
    if lucky6:
        vip_label = "🍀 VIP 300→166 LUCKY"
        of_label = "🍀 OF 500→266 LUCKY"
        god3m_label = "🍀 GOD3M 1,299→666 LUCKY"
        god_label = "🍀 GOD ถาวร 2,499→2,266 LUCKY"
    elif flash:
        vip_label = "⚡ VIP 300→199 FLASH"
        of_label = "⚡ OF 500→349 FLASH"
        god3m_label = "⚡ GOD3M 1,299→999 FLASH"
        god_label = "💎 GOD ถาวร 2,499"
    else:
        vip_label = "🔥 VIP 300 เหลือ 200" if is_endmonth_vip_promo_active() else "🥉 300 บาท"
        god_label = "💎 GOD 2,499 เหลือ 2,000" if is_endmonth_vip_promo_active() else "💎 2,499 บาท"
        of_label = "🔥 OF 500 เหลือ 349" if is_may_combo_promo_active() else "🥈 500 บาท"
        god3m_label = "🔥 GOD3M 1,299 เหลือ 999" if is_may_combo_promo_active() else "🥇 1,299 บาท"
    # DAY 0: override with Day-0 promo if present (highest priority)
    if _dayzero_promos.get("TIER_300"):
        d = _dayzero_promos["TIER_300"]
        vip_label = f"🎁 VIP 300 → {int(d['discounted'])} ลด ฿{int(d['savings'])}"
    if _dayzero_promos.get("TIER_500"):
        d = _dayzero_promos["TIER_500"]
        of_label = f"🎁 OF 500 → {int(d['discounted'])} ลด ฿{int(d['savings'])}"
    if _dayzero_promos.get("TIER_1299"):
        d = _dayzero_promos["TIER_1299"]
        god3m_label = f"🎁 GOD3M 1,299 → {int(d['discounted'])} ลด ฿{int(d['savings'])}"
    if _dayzero_promos.get("TIER_2499"):
        d = _dayzero_promos["TIER_2499"]
        god_label = f"🎁 GOD ถาวร 2,499 → {int(d['discounted'])} ลด ฿{int(d['savings'])}"

    buttons = [
        [InlineKeyboardButton(vip_label, callback_data="pkg_300")],
        [InlineKeyboardButton(of_label, callback_data="pkg_500")],
        [InlineKeyboardButton(god3m_label, callback_data="pkg_1299")],
        [InlineKeyboardButton(god_label, callback_data="pkg_2499")],
        [InlineKeyboardButton("🔙 กลับเมนูหลัก", callback_data="back_main")],
    ]
    return InlineKeyboardMarkup(buttons)


def _build_package_detail_text(tier: str) -> str | None:
    """Build detail text for a specific package tier."""
    for pkg in PACKAGES:
        if pkg["tier"] == tier:
            groups = list(pkg["groups"])
            promo_block = ""
            if tier == "1299" and is_songkran_promo_window():
                groups.append("โปรโมชั่นสงกรานต์ (โบนัสช่วงโปร 7 วัน)")
                promo_block = (
                    "\n🎁 <b>โบนัสช่วงโปร:</b> ถ้าซื้อภายใน 7 วันนี้ จะได้สิทธิ์เข้ากลุ่ม <b>โปรโมชั่นสงกรานต์</b> เพิ่มทันที\n"
                    "สิทธิ์กลุ่มโบนัสจะอยู่ได้ตราบใดที่แพ็ก 1,299 ของรอบที่ซื้อช่วงโปรยัง active\n"
                )
            groups_str = "\n  ".join(f"• {g}" for g in groups)
            price_line = f"💰 <b>ราคา: {pkg['price']} บาท / {pkg['duration']}</b>"
            tier_promo_badge = get_promo_badge_for_tier(tier)
            if tier_promo_badge:
                if tier == "2499":
                    price_line = f"💰 <b>ราคาโปร: <s>2,499</s> {int(PROMO_2499_PRICE):,} บาท / {pkg['duration']}</b>\n{tier_promo_badge}"
                else:
                    price_line = f"💰 <b>ราคาโปร: <s>300</s> {int(PROMO_PRICE)} บาท / {pkg['duration']}</b>\n{tier_promo_badge}"
            return (
                f"{pkg['name']}\n\n"
                f"{price_line}\n\n"
                f"📋 <b>รายละเอียด:</b>\n{pkg['details']}{promo_block}\n"
                f"🏠 <b>ห้องที่เข้าได้:</b>\n  {groups_str}\n\n"
                f"สนใจสมัครกดปุ่มด้านล่างได้เลยค่ะ 😊"
            )
    return None


def _build_detail_keyboard(tier: str) -> InlineKeyboardMarkup:
    """Build keyboard for package detail view."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "✅ สมัครแพ็กเกจนี้",
                    callback_data=f"buy_{tier}",
                )
            ],
            [InlineKeyboardButton("📦 ดูแพ็กเกจอื่น", callback_data="view_packages")],
            [InlineKeyboardButton("🔙 กลับเมนูหลัก", callback_data="back_main")],
        ]
    )



async def _maint_guard(update, context) -> bool:
    """Returns True if blocked (maintenance ON). Caller should return early."""
    try:
        if await is_maintenance_mode():
            txt, kb = build_maintenance_reply()
            if update.callback_query:
                await update.callback_query.answer()
                await update.callback_query.message.reply_text(txt, parse_mode="HTML", reply_markup=kb)
            elif update.message:
                await update.message.reply_text(txt, parse_mode="HTML", reply_markup=kb)
            return True
    except Exception:
        pass
    return False


async def view_packages_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """/packages command — show all packages."""

    if await _maint_guard(update, context):
        return
    if not update.message:
        return
    await update.message.reply_text(
        await _build_package_list_text(),
        parse_mode="HTML",
        reply_markup=await _build_package_keyboard(),
    )


async def view_packages_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Callback: show all packages."""

    if await _maint_guard(update, context):
        return
    query = update.callback_query
    if not query:
        return
    try:
        await query.answer()
    except Exception:
        pass  # callback may be too old / already answered
    await _safe_edit(query, await _build_package_list_text(),
        parse_mode="HTML",
        reply_markup=await _build_package_keyboard(),)
async def package_detail_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Callback: show detail of a specific package (pkg_300, pkg_500, etc.)."""
    query = update.callback_query
    if not query or not query.data:
        return
    try:
        await query.answer()
    except Exception:
        pass  # callback may be too old / already answered

    tier = query.data.replace("pkg_", "")
    text = _build_package_detail_text(tier)
    if not text:
        await _safe_edit(query, "ไม่พบแพ็กเกจที่เลือกค่ะ ลองใหม่นะคะ")
        return

    await _safe_edit(query, text,
        parse_mode="HTML",
        reply_markup=_build_detail_keyboard(tier),)
async def buy_package_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Callback: user wants to buy a package (buy_300, buy_500, etc.)."""
    query = update.callback_query
    if not query or not query.data:
        return
    try:
        await query.answer()
    except Exception:
        pass  # callback may be too old / already answered

    tier = query.data.replace("buy_", "")
    pkg = next((p for p in PACKAGES if p["tier"] == tier), None)
    if not pkg:
        await _safe_edit(query, "ไม่พบแพ็กเกจค่ะ")
        return

    # Store selected package in user context
    promo_active = tier in ("300", "2499") and is_endmonth_vip_promo_active()
    may_promo_active = tier in ("500", "1299") and is_may_combo_promo_active()
    if promo_active and tier == "300":
        display_price = str(int(PROMO_PRICE))
    elif promo_active and tier == "2499":
        display_price = str(int(PROMO_2499_PRICE))
    elif may_promo_active and tier == "500":
        display_price = str(int(PROMO_500_PRICE))
    elif may_promo_active and tier == "1299":
        display_price = str(int(PROMO_1299_PRICE))
    else:
        display_price = pkg["price"]
    promo_active = promo_active or may_promo_active

    context.user_data["selected_tier"] = tier
    context.user_data["selected_price"] = display_price.replace(",", "")

    # ── DISCOUNT AUTO-APPLY ───────────────────────────────────────
    # If user has saved discount credit, bump the displayed price down + reserve in context.
    try:
        from decimal import Decimal as _D
        _base_price = _D(display_price.replace(",", ""))
        _use, _expected, _bal = await _disc_reserve(
            context, query.from_user.id, tier, _base_price
        )
        if _use > 0:
            display_price = f"{int(_expected):,}"
            _discount_line = (
                f"💚 ใช้ส่วนลดสะสม: -฿{int(_use):,} (จากยอด ฿{int(_base_price):,})\n"
                f"💎 คงเหลือยอดส่วนลด: ฿{int(_bal - _use):,}\n\n"
            )
        else:
            _discount_line = ""
    except Exception as _e:
        logger.warning("discount reserve skipped: %s", _e)
        _discount_line = ""
    # ──────────────────────────────────────────────────────────────


    _np_map = {"300": "300", "2499": "2,499", "500": "500", "1299": "1,299"}
    normal_price = _np_map.get(tier, pkg["price"])
    _pdate = PROMO_MAY_DATE_TEXT if may_promo_active else PROMO_DATE_TEXT
    promo_line = f"🔥 โปรสิ้นเดือน: จาก {normal_price} เหลือ <b>{display_price} บาท</b> ({_pdate})\n\n" if promo_active else ""

    text = (
        f"✅ <b>ยืนยันสมัคร {pkg['name']}</b>\n\n"
        f"{promo_line}"
        f"{_discount_line}"
        f"💰 ยอดที่ต้องชำระ: <b>{display_price} บาท</b>\n\n"
        f"📌 <b>วิธีชำระเงิน:</b>\n"
        f"1️⃣ สแกน QR PromptPay ด้านล่าง หรือโอนเงินตามยอด\n"
        f"2️⃣ ส่งสลิปโอนเงิน หรือ ลิงก์ซอง TrueMoney\n"
        f"3️⃣ รอแอดมินตรวจสอบ\n\n"
        f"💳 <b>ช่องทางชำระ:</b>\n"
        f"• PromptPay / โอนธนาคาร → ส่งรูปสลิป\n"
        f"• TrueMoney Wallet → ส่งลิงก์ gift.truemoney.com\n\n"
        f"⚠️ <b>หมายเหตุ:</b> กรุณาโอนตามยอดที่แจ้งเท่านั้นค่ะ"
    )

    keyboard = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📦 เลือกแพ็กเกจอื่น", callback_data="view_packages")],
            [InlineKeyboardButton("🔙 กลับเมนูหลัก", callback_data="back_main")],
        ]
    )

    await _safe_edit(query, text, parse_mode="HTML", reply_markup=keyboard)
    # Send QR code PromptPay
    # # >>> POOL_QR <<< dynamic QR from receiver pool
    _picked = None
    try:
        from shared.receiver_pool import pick_random as _pick
        _picked = await _pick()
    except Exception:
        pass
    QR_URL = (_picked["qr_url"] if _picked and _picked.get("qr_url") else "https://img2.pic.in.th/-2026-03-15-143743.png")
    try:
        await context.bot.send_photo(
            chat_id=query.message.chat_id,
            photo=QR_URL,
            caption=f"📱 สแกน QR PromptPay เพื่อโอน <b>{display_price} บาท</b>\nแล้วส่งสลิปมาที่แชทนี้เลยค่ะ 🙏",
            parse_mode="HTML",
        )
    except Exception as exc:
        logger.warning("Failed to send QR: %s", exc)


async def summer_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """/summer — ซื้อ Summer Fest add-on ฿500 (สำหรับ GOD MODE เก่า)."""
    if not update.message:
        return

    context.user_data["selected_tier"] = "ADD500"
    context.user_data["selected_price"] = "500"

    text = (
        "🌊 <b>Summer Fest — Add-on ฿500</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "สำหรับลูกค้า GOD MODE เก่าที่ต้องการเข้ากลุ่มใหม่\n\n"
        "📋 <b>ห้องที่ได้:</b>\n"
        "• งานแรร์90\n"
        "• สาวอ้วน\n"
        "• เลสเบี้ยน\n"
        "• สาวน้อยตกน้ำ (สงกรานต์) 💦\n\n"
        "💰 <b>จ่ายเพิ่ม ฿500 เข้าถาวร!</b>\n\n"
        "📌 <b>วิธีชำระเงิน:</b>\n"
        "1️⃣ สแกน QR PromptPay ด้านล่าง\n"
        "2️⃣ ส่งสลิปโอนเงิน หรือลิงก์ซอง TrueMoney\n"
        "3️⃣ รอแอดมินตรวจสอบ\n\n"
        "⚠️ กรุณาโอน <b>500 บาท</b> ตามยอดที่แจ้งเท่านั้นค่ะ"
    )

    await update.message.reply_text(text, parse_mode="HTML")

    # Send QR
    # # >>> POOL_QR <<< dynamic QR from receiver pool
    _picked = None
    try:
        from shared.receiver_pool import pick_random as _pick
        _picked = await _pick()
    except Exception:
        pass
    QR_URL = (_picked["qr_url"] if _picked and _picked.get("qr_url") else "https://img2.pic.in.th/-2026-03-15-143743.png")
    try:
        await context.bot.send_photo(
            chat_id=update.message.chat_id,
            photo=QR_URL,
            caption="📱 สแกน QR PromptPay เพื่อโอน <b>500 บาท</b>\nแล้วส่งสลิปมาที่แชทนี้เลยค่ะ 🙏",
            parse_mode="HTML",
        )
    except Exception as exc:
        logger.warning("Failed to send QR: %s", exc)


def get_package_handlers() -> list:
    """Return all handlers for the packages module."""
    return [
        CommandHandler("packages", view_packages_command),
        CommandHandler("summer", summer_command),
        CallbackQueryHandler(view_packages_callback, pattern="^view_packages$"),
        CallbackQueryHandler(package_detail_callback, pattern=r"^pkg_(300|500|1299|2499)$"),
        CallbackQueryHandler(buy_package_callback, pattern=r"^buy_(300|500|1299|2499)$"),
    ]
