"""แพร tools — DB query functions for AI Concierge.

Each tool:
- Async function
- Takes simple args (mostly telegram_id)
- Returns dict (will be JSON-serialized for LLM)
- Never raises — return {"error": ...} instead
- Read-only — never mutates state
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from sqlalchemy import text as _t
from shared.database import get_session

logger = logging.getLogger(__name__)


# ============================================================
# Tool 1: check_my_status
# ============================================================
async def check_my_status(telegram_id: int) -> dict:
    """Return user's active subscriptions + summary.

    Returns:
        {
          "tier": "TIER_2499" | "TIER_1299" | ... | null,
          "tier_name": "GOD MODE ถาวร" | ...,
          "expires": "2026-12-31" | "ตลอดชีพ" | null,
          "days_left": int | null,
          "is_lifetime": bool,
          "active_subs_count": int,
        }
    """
    try:
        async with get_session() as s:
            r = await s.execute(_t("""
                SELECT pk.tier::text AS tier, pk.name, s.end_date,
                       pk.duration_days,
                       EXTRACT(DAY FROM (s.end_date - NOW()))::int AS days_left
                FROM subscriptions s
                JOIN users u ON u.id = s.user_id
                JOIN packages pk ON pk.id = s.package_id
                WHERE u.telegram_id = :tg AND s.status = 'ACTIVE'
                  AND s.end_date > NOW()
                ORDER BY s.end_date DESC LIMIT 1
            """), {"tg": telegram_id})
            row = r.fetchone()
            if not row:
                return {
                    "tier": None,
                    "tier_name": None,
                    "expires": None,
                    "days_left": None,
                    "is_lifetime": False,
                    "active_subs_count": 0,
                    "message": "ยังไม่มี subscription active",
                }
            # Count active subs
            c = await s.execute(_t("""
                SELECT count(*) FROM subscriptions s
                JOIN users u ON u.id = s.user_id
                WHERE u.telegram_id = :tg AND s.status = 'ACTIVE' AND s.end_date > NOW()
            """), {"tg": telegram_id})
            count = c.scalar() or 0

        is_lifetime = (row.duration_days or 0) > 1000
        return {
            "tier": row.tier,
            "tier_name": row.name,
            "expires": "ตลอดชีพ" if is_lifetime else row.end_date.strftime("%Y-%m-%d"),
            "days_left": None if is_lifetime else row.days_left,
            "is_lifetime": is_lifetime,
            "active_subs_count": int(count),
        }
    except Exception as e:
        logger.exception("check_my_status failed")
        return {"error": f"DB error: {e}"}


# ============================================================
# Tool 2: check_recent_payment
# ============================================================
async def check_recent_payment(telegram_id: int, limit: int = 3) -> dict:
    """Return last N payments + their status.

    Returns:
        {
          "payments": [
            {"id": 123, "amount": 1299, "status": "CONFIRMED", "created_at": "...", "tier": "..."},
            ...
          ],
          "has_pending": bool,
        }
    """
    try:
        async with get_session() as s:
            r = await s.execute(_t("""
                SELECT p.id, p.amount::float AS amount, p.status::text AS status,
                       p.created_at, p.verified_at, p.reject_reason,
                       pk.tier::text AS tier, pk.name AS package_name
                FROM payments p
                JOIN users u ON u.id = p.user_id
                JOIN packages pk ON pk.id = p.package_id
                WHERE u.telegram_id = :tg
                ORDER BY p.id DESC LIMIT :lim
            """), {"tg": telegram_id, "lim": limit})
            rows = r.fetchall()

        payments = []
        for row in rows:
            payments.append({
                "id": row.id,
                "amount": row.amount,
                "status": row.status,
                "tier": row.tier,
                "package_name": row.package_name,
                "created_at": row.created_at.strftime("%Y-%m-%d %H:%M"),
                "verified_at": row.verified_at.strftime("%Y-%m-%d %H:%M") if row.verified_at else None,
                "reject_reason": row.reject_reason,
            })
        has_pending = any(p["status"] == "PENDING" for p in payments)
        return {"payments": payments, "has_pending": has_pending}
    except Exception as e:
        logger.exception("check_recent_payment failed")
        return {"error": f"DB error: {e}"}


# ============================================================
# Tool 3: check_balance (gacha credit + discount credit + shaker ticket)
# ============================================================
async def check_balance(telegram_id: int) -> dict:
    """Return user's gacha spin credits + discount balance + shaker ticket.

    Returns:
        {
          "gacha_credits": int,
          "gacha_total_spun": int,
          "discount_balance": float,
          "discount_total_earned": float,
          "shaker_tickets": [{"number": "47", "expires": "2026-07-11"}],
        }
    """
    try:
        async with get_session() as s:
            # Gacha credits
            g = await s.execute(_t("""
                SELECT credits, total_spun FROM gachapon_credits
                WHERE telegram_id = :tg
            """), {"tg": telegram_id})
            grow = g.fetchone()
            # Discount balance
            d = await s.execute(_t("""
                SELECT balance, total_earned FROM user_discount_credits
                WHERE telegram_id = :tg
            """), {"tg": telegram_id})
            drow = d.fetchone()
            # Shaker active tickets
            t = await s.execute(_t("""
                SELECT number, expires_at FROM shaker_tickets
                WHERE telegram_id = :tg AND status = 'ACTIVE' AND expires_at > NOW()
                ORDER BY id DESC LIMIT 10
            """), {"tg": telegram_id})
            tickets = [
                {"number": row.number, "expires": row.expires_at.strftime("%Y-%m-%d")}
                for row in t.fetchall()
            ]
        return {
            "gacha_credits": int(grow.credits) if grow else 0,
            "gacha_total_spun": int(grow.total_spun) if grow else 0,
            "discount_balance": float(drow.balance) if drow else 0.0,
            "discount_total_earned": float(drow.total_earned) if drow else 0.0,
            "shaker_tickets": tickets,
        }
    except Exception as e:
        logger.exception("check_balance failed")
        return {"error": f"DB error: {e}"}


# ============================================================
# Tool 4: check_active_promo
# ============================================================
async def check_active_promo() -> dict:
    """Return currently active promotions / flash sales.

    Returns:
        {
          "promos": [
            {"name": "...", "starts_at": "...", "ends_at": "...", "details": "..."}
          ],
          "active_flash_sale": {...} | null,
        }
    """
    try:
        async with get_session() as s:
            # Active promotion campaigns
            r = await s.execute(_t("""
                SELECT name, normal_price, promo_price, starts_at, ends_at
                FROM promotion_campaigns
                WHERE is_active = true
                  AND (starts_at IS NULL OR starts_at <= NOW())
                  AND (ends_at IS NULL OR ends_at > NOW())
                ORDER BY id DESC LIMIT 5
            """))
            promos = [
                {
                    "name": row.name,
                    "normal_price": float(row.normal_price) if row.normal_price else None,
                    "promo_price": float(row.promo_price) if row.promo_price else None,
                    "starts_at": row.starts_at.strftime("%Y-%m-%d") if row.starts_at else None,
                    "ends_at": row.ends_at.strftime("%Y-%m-%d %H:%M") if row.ends_at else None,
                }
                for row in r.fetchall()
            ]
            # Flash sale check
            f = await s.execute(_t("""
                SELECT name, flash_price, original_price, total_slots, sold_slots, ends_at
                FROM flash_sales
                WHERE is_active = true AND ends_at > NOW() AND starts_at <= NOW()
                LIMIT 1
            """))
            frow = f.fetchone()
            flash = None
            if frow:
                flash = {
                    "name": frow.name,
                    "flash_price": float(frow.flash_price),
                    "original_price": float(frow.original_price),
                    "slots_left": int(frow.total_slots - frow.sold_slots),
                    "ends_at": frow.ends_at.strftime("%Y-%m-%d %H:%M"),
                }
        return {"promos": promos, "active_flash_sale": flash}
    except Exception as e:
        logger.exception("check_active_promo failed")
        return {"error": f"DB error: {e}"}


# ============================================================
# Tool 5: get_my_invite_links — get links for user to re-enter groups
# ============================================================
async def get_my_invite_links(telegram_id: int) -> dict:
    """Return guidance for user to get fresh invite links.

    Note: We don't auto-generate links here (that creates DB rows + admin trail).
    Instead, instruct user to use /getlink command which is the safer flow.
    """
    try:
        async with get_session() as s:
            # Check what groups they should have
            r = await s.execute(_t("""
                SELECT DISTINCT gr.title, gr.slug
                FROM subscriptions s
                JOIN users u ON u.id = s.user_id
                JOIN packages pk ON pk.id = s.package_id
                LEFT JOIN package_group_access pga ON pga.package_id = pk.id
                LEFT JOIN group_registry gr ON gr.chat_id = pga.group_chat_id
                WHERE u.telegram_id = :tg
                  AND s.status = 'ACTIVE' AND s.end_date > NOW()
                  AND gr.is_active = true
            """), {"tg": telegram_id})
            groups = [
                {"title": row.title, "slug": row.slug}
                for row in r.fetchall() if row.title
            ]
        return {
            "groups_should_have_access": groups,
            "instruction": "ใช้คำสั่ง /getlink เพื่อขอลิงก์เข้ากลุ่มใหม่ค่ะ",
        }
    except Exception:
        # Schema may differ — fallback to instruction only
        return {
            "groups_should_have_access": [],
            "instruction": "ใช้คำสั่ง /getlink เพื่อขอลิงก์เข้ากลุ่มใหม่ค่ะ",
        }



# ============================================================
# Tool 6: handle_group_access_issue (mega-tool)
# Called when customer reports: "can't enter group / link expired / group gone"
# ============================================================
async def handle_group_access_issue(telegram_id: int) -> dict:
    """Smart triage + action for group-access problems.

    Returns dict with status + invite_links (if active) + recommendation.
    Side effect: notifies admin group with audit trail.
    """
    import os as _os
    import secrets as _secrets

    try:
        async with get_session() as s:
            # 1. Look up user record
            r_user = await s.execute(_t("""
                SELECT id, first_name, username FROM users WHERE telegram_id = :tg
            """), {"tg": telegram_id})
            user_row = r_user.fetchone()
            user_id = user_row.id if user_row else None
            customer_name = (user_row.first_name or user_row.username or "ลูกค้า") if user_row else "ลูกค้า"
            customer_username = (user_row.username if user_row else None) or None  # FIX 2026-06-22: capture for admin alert button

            # 2. Check active subscription
            r_active = await s.execute(_t("""
                SELECT pk.tier::text AS tier, pk.name, pk.id AS pkg_id,
                       pk.duration_days, s.end_date,
                       EXTRACT(DAY FROM (s.end_date - NOW()))::int AS days_left
                FROM subscriptions s
                JOIN users u ON u.id = s.user_id
                JOIN packages pk ON pk.id = s.package_id
                WHERE u.telegram_id = :tg AND s.status = 'ACTIVE' AND s.end_date > NOW()
                ORDER BY s.end_date DESC LIMIT 1
            """), {"tg": telegram_id})
            active = r_active.fetchone()

        # ── Branch A: ACTIVE — generate invite links ──
        if active:
            is_lifetime = (active.duration_days or 0) > 1000
            invite_links: list[dict] = []

            try:
                from telegram import Bot as _Bot
                gtok = _os.environ.get("GUARDIAN_BOT_TOKEN", "")
                if gtok:
                    g = _Bot(token=gtok)
                    await g.initialize()
                    try:
                        from bots.guardian_bot.group_monitor import generate_invite_links_for_user as _gen
                        links_dict = await _gen(g, telegram_id, active.pkg_id) or {}
                        # Map slug -> title
                        for slug, url in links_dict.items():
                            title = str(slug)
                            try:
                                async with get_session() as s2:
                                    gr = await s2.execute(_t(
                                        "SELECT title FROM group_registry WHERE slug = :sl LIMIT 1"
                                    ), {"sl": slug})
                                    row = gr.fetchone()
                                    if row and row.title:
                                        title = row.title
                            except Exception:
                                pass
                            invite_links.append({"slug": str(slug), "title": title, "url": url})
                    finally:
                        try: await g.shutdown()
                        except Exception: pass
            except Exception as e:
                logger.exception("invite link generation failed: %s", e)

            # Admin audit
            await _notify_admin_group_access(
                telegram_id=telegram_id,
                customer_name=customer_name,
                action="link_resent",
                status="active",
                tier=active.tier,
                tier_name=active.name,
                link_count=len(invite_links),
                username=customer_username,
            )

            return {
                "status": "active",
                "active_tier": active.tier,
                "active_tier_name": active.name,
                "days_left": None if is_lifetime else int(active.days_left or 0),
                "is_lifetime": is_lifetime,
                "invite_links": invite_links,
                "expired_tier": None,
                "renewal_url": None,
                "recommendation": (
                    f"ลูกค้าเป็นสมาชิก {active.name} อยู่ — "
                    f"ส่งลิงก์เข้ากลุ่ม {len(invite_links)} กลุ่มให้แล้ว "
                    "ใช้ครั้งเดียว หมดอายุ 24 ชม. แจ้งลูกค้าให้กดเข้าได้เลย"
                ),
            }

        # ── Branch B: EXPIRED — offer retention discount ──
        async with get_session() as s:
            r_exp = await s.execute(_t("""
                SELECT pk.tier::text AS tier, pk.name, s.end_date,
                       EXTRACT(DAY FROM (NOW() - s.end_date))::int AS days_since
                FROM subscriptions s
                JOIN users u ON u.id = s.user_id
                JOIN packages pk ON pk.id = s.package_id
                WHERE u.telegram_id = :tg
                ORDER BY s.end_date DESC LIMIT 1
            """), {"tg": telegram_id})
            expired = r_exp.fetchone()

        if expired:
            days_since = int(expired.days_since or 0)
            # Sliding discount
            if days_since <= 7:
                discount_pct, round_num = 20, 202
            elif days_since <= 30:
                discount_pct, round_num = 30, 211  # comeback round 1
            else:
                discount_pct, round_num = 40, 212  # comeback round 2

            # Generate + save promo code (for deep link)
            promo_code = "WB" + _secrets.token_hex(4).upper()
            try:
                async with get_session() as s:
                    await s.execute(_t("""
                        INSERT INTO comeback_dm_log
                            (user_id, telegram_id, discount_pct, promo_code, round, variant)
                        VALUES (:uid, :tg, :pct, :code, :rnd, :var)
                    """), {
                        "uid": user_id, "tg": telegram_id, "pct": discount_pct,
                        "code": promo_code, "rnd": round_num, "var": "AI_HANDOFF",
                    })
                    await s.commit()
            except Exception as e:
                logger.warning("promo code save failed: %s", e)

            await _notify_admin_group_access(
                telegram_id=telegram_id,
                customer_name=customer_name,
                action="renewal_offered",
                status="expired",
                tier=expired.tier,
                tier_name=expired.name,
                discount_pct=discount_pct,
                promo_code=promo_code,
                username=customer_username,
            )

            return {
                "status": "expired",
                "active_tier": None,
                "active_tier_name": None,
                "expired_tier": expired.tier,
                "expired_tier_name": expired.name,
                "days_since_expiry": days_since,
                "renewal_discount_pct": discount_pct,
                "renewal_promo_code": promo_code,
                "renewal_url": f"https://t.me/NamwarnJarern_bot?start=comeback_{promo_code}",
                "invite_links": [],
                "recommendation": (
                    f"ลูกค้าหมดอายุ {expired.name} {days_since} วันแล้ว — "
                    f"เสนอต่ออายุ ลด {discount_pct}% (กดลิงก์ deep link เพื่อใช้ promo "
                    f"{promo_code} ทันที). บอกความน่าสนใจ + กดปุ่มสมัครได้เลย"
                ),
            }

        # ── Branch C: NEVER PAID ──
        await _notify_admin_group_access(
            telegram_id=telegram_id,
            customer_name=customer_name,
            action="recommend_new",
            status="never_paid",
            username=customer_username,
        )

        return {
            "status": "never_paid",
            "active_tier": None,
            "active_tier_name": None,
            "expired_tier": None,
            "renewal_url": None,
            "invite_links": [],
            "recommendation": (
                "ลูกค้ายังไม่เคยสมัคร — แนะนำ 3 ทาง: "
                "(1) VIP 300/30วัน เริ่มต้นง่าย "
                "(2) ห้องมีคนชัก 100 ลุ้น GOD ถาวร "
                "(3) กาชาปอง 99 หมุนลุ้นรางวัล"
            ),
        }

    except Exception as e:
        logger.exception("handle_group_access_issue failed")
        return {"status": "error", "error": str(e)[:200]}


async def _notify_admin_group_access(
    telegram_id: int,
    customer_name: str,
    action: str,  # "link_resent" | "renewal_offered" | "recommend_new"
    status: str,
    tier: str | None = None,
    tier_name: str | None = None,
    discount_pct: int | None = None,
    promo_code: str | None = None,
    link_count: int = 0,
    username: str | None = None,
) -> None:
    """Notify admin group when AI triggers group-access flow."""
    import os as _os
    try:
        admin_chat_id = int(_os.environ.get("ADMIN_GROUP_CHAT_ID", "0") or 0)
        if not admin_chat_id:
            return

        from telegram import Bot as _Bot, InlineKeyboardButton, InlineKeyboardMarkup
        admin_tok = _os.environ.get("ADMIN_BOT_TOKEN") or _os.environ.get("SALES_BOT_TOKEN", "")
        b = _Bot(token=admin_tok)
        await b.initialize()
        try:
            if action == "link_resent":
                msg = (
                    f"🔔 <b>แพรส่งลิงก์เข้ากลุ่มใหม่</b>\n\n"
                    f"👤 <a href=\"tg://user?id={telegram_id}\">{customer_name}</a>\n"
                    f"🆔 <code>{telegram_id}</code>\n"
                    f"📦 {tier_name or tier}\n"
                    f"🔗 ส่งให้ {link_count} กลุ่ม (one-time, 24h)"
                )
            elif action == "renewal_offered":
                msg = (
                    f"🔔 <b>แพรเสนอต่ออายุ (ลูกค้าหมดอายุ)</b>\n\n"
                    f"👤 <a href=\"tg://user?id={telegram_id}\">{customer_name}</a>\n"
                    f"🆔 <code>{telegram_id}</code>\n"
                    f"📦 หมด: {tier_name or tier}\n"
                    f"🎁 Promo: <b>ลด {discount_pct}%</b> code <code>{promo_code}</code>"
                )
            else:  # recommend_new
                msg = (
                    f"🔔 <b>แพรแนะนำสมัครใหม่ (never paid)</b>\n\n"
                    f"👤 <a href=\"tg://user?id={telegram_id}\">{customer_name}</a>\n"
                    f"🆔 <code>{telegram_id}</code>"
                )

            # FIX 2026-06-22: ปุ่มแยกตาม branch (ไม่ใช่ "เปิดแชท" ซ้ำๆ)
            # Branch A (link_resent): ไม่ต้องมีปุ่ม — Prae จัดการแล้ว audit อย่างเดียว
            # Branch B (renewal_offered): "💬 ทักตามต่ออายุ" — follow up หลัง promo
            # Branch C (recommend_new): "🛒 ทักปิดการขาย" — admin ลองปิดดีล
            kb = None
            if action == "renewal_offered" and username:
                kb = InlineKeyboardMarkup([[
                    InlineKeyboardButton("💬 ทักตามต่ออายุ", url=f"https://t.me/{username}"),
                ]])
            elif action == "recommend_new" and username:
                kb = InlineKeyboardMarkup([[
                    InlineKeyboardButton("🛒 ทักปิดการขาย", url=f"https://t.me/{username}"),
                ]])
            # ถ้าไม่มี username → กดที่ชื่อ (mention link ในข้อความ) แทน
            send_kwargs = {
                "chat_id": admin_chat_id, "text": msg, "parse_mode": "HTML",
                "disable_web_page_preview": True,
            }
            if kb is not None:
                send_kwargs["reply_markup"] = kb
            await b.send_message(**send_kwargs)
        finally:
            try: await b.shutdown()
            except Exception: pass
    except Exception as e:
        logger.warning("admin notify (group_access) failed: %s", e)



# ============================================================
# Tool registry — for engine to dispatch
# ============================================================


async def _handle_group_access_issue_with_sos(telegram_id: int) -> dict:
    """Wraps handle_group_access_issue + updates SOS alert with the outcome."""
    result = await handle_group_access_issue(telegram_id)
    try:
        from shared.sos_smart import update_sos_with_ai_result
        status = str(result.get("status", "unknown"))
        if status == "active":
            n = len(result.get("invite_links") or [])
            tier_name = result.get("active_tier_name") or ""
            detail = f"ส่งลิงก์ {n} กลุ่ม ({tier_name})" if tier_name else f"ส่งลิงก์ {n} กลุ่มให้ลูกค้าแล้ว"
        elif status == "expired":
            tier_name = result.get("expired_tier_name") or result.get("expired_tier") or ""
            detail = f"ลูกค้าหมดอายุ {tier_name} — เสนอโปรต่ออายุแล้ว"
        elif status == "never_paid":
            detail = "ลูกค้ายังไม่เคยสมัคร — แนะนำแพ็กเกจ"
        elif status == "error":
            detail = f"AI ขัดข้อง: {result.get('error', '')[:80]}"
        else:
            detail = (result.get("recommendation") or "")[:160]
        await update_sos_with_ai_result(telegram_id=telegram_id, status=status, detail=detail)
    except Exception as _exc:
        import logging
        logging.getLogger(__name__).warning("SOS update from tool failed: %s", _exc)
    return result


# Tool: send_payment_info
# Used when customer expresses purchase intent (types amount, "อยากซื้อ", "เอา X")
# Returns: package info + bank account + QR code URL → Prae includes in reply
async def send_payment_info(telegram_id: int, tier_or_amount: str) -> dict:
    """Get payment instructions for a customer's selected tier.

    Args:
        telegram_id: customer telegram ID
        tier_or_amount: '300' / 'VIP' / 'TIER_300' / 'GOD' / '1299' / '2499' / '100' etc.

    Returns: dict with package_name, price, account info, QR url, message_template
    """
    from shared.database import get_session
    from sqlalchemy import text as _t

    # Normalize input to find tier
    s_raw = (tier_or_amount or "").strip().upper()
    tier_map = {
        "100": ("ห้องมีคนชัก 30 วัน", 100, "TIER_100"),
        "ชัก": ("ห้องมีคนชัก 30 วัน", 100, "TIER_100"),
        "SHAKER": ("ห้องมีคนชัก 30 วัน", 100, "TIER_100"),
        "300": ("VIP 30 วัน", 300, "TIER_300"),
        "VIP": ("VIP 30 วัน", 300, "TIER_300"),
        "TIER_300": ("VIP 30 วัน", 300, "TIER_300"),
        "500": ("OnlyFans + VIP 30 วัน", 500, "TIER_500"),
        "OF": ("OnlyFans + VIP 30 วัน", 500, "TIER_500"),
        "ONLYFANS": ("OnlyFans + VIP 30 วัน", 500, "TIER_500"),
        "TIER_500": ("OnlyFans + VIP 30 วัน", 500, "TIER_500"),
        "1299": ("GOD MODE 90 วัน", 1299, "TIER_1299"),
        "GOD90": ("GOD MODE 90 วัน", 1299, "TIER_1299"),
        "TIER_1299": ("GOD MODE 90 วัน", 1299, "TIER_1299"),
        "2499": ("GOD MODE ถาวร", 2499, "TIER_2499"),
        "GOD": ("GOD MODE ถาวร", 2499, "TIER_2499"),
        "GODMODE": ("GOD MODE ถาวร", 2499, "TIER_2499"),
        "TIER_2499": ("GOD MODE ถาวร", 2499, "TIER_2499"),
    }

    matched = None
    for key, val in tier_map.items():
        if key in s_raw:
            matched = val
            break

    if not matched:
        return {
            "error": "unknown_tier",
            "message": f"Could not identify tier from '{tier_or_amount}'. Customer should specify clearly.",
        }

    pkg_name, price, tier = matched

    # Pick a random enabled receiver account
    try:
        from shared.receiver_pool import pick_random
        account = await pick_random()
    except Exception as exc:
        return {"error": f"receiver lookup failed: {exc}"}

    if not account:
        return {
            "error": "no_receiver",
            "message": "No active receiver account. Customer should contact admin.",
            "admin_url": "https://t.me/sperm6969",
        }

    bank_name = account.get("bank_name_th") or "PromptPay"
    account_no = account.get("account_no") or ""
    promptpay = account.get("promptpay_number") or ""
    receiver_name = account.get("owner_name") or "บัญชีรับเงิน"
    qr_url = account.get("qr_url") or None

    # Display: prefer real bank account, fallback to PromptPay number
    display_number = account_no or promptpay or "-"

    # Construct customer-facing instructions (HTML for sales bot)
    msg = (
        f"💰 <b>{pkg_name}</b>\n"
        f"ราคา {price} บาท\n\n"
        f"🏦 <b>{bank_name}</b>\n"
        f"{receiver_name}\n"
        f"<code>{bank_last5}</code>\n\n"
        f"📸 โอนแล้วส่งสลิปกลับมาในแชทนี้ ระบบจะตรวจสอบและเปิดสิทธิให้อัตโนมัติค่ะ"
    )

    return {
        "ok": True,
        "package_name": pkg_name,
        "price": price,
        "tier": tier,
        "receiver": {
            "bank_name": bank_name,
            "account_number": display_number,
            "name": receiver_name,
            "qr_url": qr_url,
        },
        "instructions_html": msg,
        "next_step": "โอนเงินตามจำนวน แล้วส่งสลิปกลับมาในแชทนี้",
    }

TOOLS = {
    "check_my_status": check_my_status,
    "check_recent_payment": check_recent_payment,
    "check_balance": check_balance,
    "check_active_promo": check_active_promo,
    "get_my_invite_links": get_my_invite_links,
    "handle_group_access_issue": _handle_group_access_issue_with_sos,
    "send_payment_info": send_payment_info,
}


# JSON schema for LLM tool-calling
TOOL_SCHEMAS = [
    {
        "name": "check_my_status",
        "description": "เช็คว่าลูกค้าเป็น VIP tier อะไร เหลือกี่วัน ใช้เมื่อลูกค้าถามเรื่องสถานะสมาชิก",
        "input_schema": {
            "type": "object",
            "properties": {"telegram_id": {"type": "integer"}},
            "required": ["telegram_id"],
        },
    },
    {
        "name": "check_recent_payment",
        "description": "เช็คประวัติการชำระเงินล่าสุด ใช้เมื่อลูกค้าถามว่า slip ที่ส่งไปแล้วเป็นยังไง",
        "input_schema": {
            "type": "object",
            "properties": {
                "telegram_id": {"type": "integer"},
                "limit": {"type": "integer", "default": 3},
            },
            "required": ["telegram_id"],
        },
    },
    {
        "name": "check_balance",
        "description": "เช็คสิทธิ์หมุนกาชาปอง + ส่วนลดสะสม + ตั๋วห้องมีคนชัก ใช้เมื่อลูกค้าถามว่าตัวเองมีอะไรอยู่",
        "input_schema": {
            "type": "object",
            "properties": {"telegram_id": {"type": "integer"}},
            "required": ["telegram_id"],
        },
    },
    {
        "name": "check_active_promo",
        "description": "เช็คโปรโมชั่นที่กำลังจัดอยู่ + flash sale ใช้เมื่อลูกค้าถามว่ามีโปรอะไรไหม",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_my_invite_links",
        "description": "บอกลูกค้าวิธีขอลิงก์เข้ากลุ่มใหม่ ใช้เมื่อลูกค้าบอกว่ากลุ่มหาย/เข้าไม่ได้",
        "input_schema": {
            "type": "object",
            "properties": {"telegram_id": {"type": "integer"}},
            "required": ["telegram_id"],
        },
    },
    {
        "name": "handle_group_access_issue",
        "description": "Use this WHENEVER the customer says they can't enter the group, links are expired, group is missing, can't find content. Returns user's status (active/expired/never_paid), invite_links (if active), renewal_url (if expired), and a recommendation for how to reply.",
        "input_schema": {
            "type": "object",
            "properties": {"telegram_id": {"type": "integer"}},
            "required": ["telegram_id"],
        },
    },
    {
        "name": "send_payment_info",
        "description": "USE THIS when customer expresses purchase intent: types a price (300/500/1299/2499/100), says they want to buy (อยากซื้อ, สนใจ, เอา), confirms a package choice. Returns bank account + QR + instructions. ALWAYS use this instead of telling them to contact admin.",
        "input_schema": {
            "type": "object",
            "properties": {
                "telegram_id": {"type": "integer"},
                "tier_or_amount": {
                    "type": "string",
                    "description": "Tier name or price the customer chose. e.g. '300' / 'VIP' / 'GOD' / 'TIER_2499' / '1299'"
                },
            },
            "required": ["telegram_id", "tier_or_amount"],
        },
    },
]

__all__ = ["TOOLS", "TOOL_SCHEMAS", "check_my_status", "check_recent_payment",
           "check_balance", "check_active_promo", "get_my_invite_links", "handle_group_access_issue"]
