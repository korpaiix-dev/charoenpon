"""Loyalty rank V2 — Bronze / Silver / Diamond

Rank computation (run every 6 hours by scheduler):
  - BRONZE  : 30+ days since loyalty_first_paid_at
  - SILVER  : 90+ days OR total_spent >= 1000
  - DIAMOND : total_spent >= 4000

Rewards on rank-up:
  - BRONZE  : +3 gacha credits
  - SILVER  : +5 gacha credits + TIER_1299 sub free 14 days
  - DIAMOND : tag only (boss hands out physical gifts manually)

Side effects on rank-up:
  1. UPDATE users.loyalty_rank + loyalty_rank_at
  2. mint rewards (gacha credits / free sub) into DB
  3. DM customer with celebration + reward summary
  4. INSERT admin_logs (audit trail)
  5. Notify admin group (boss can see real-time)

NOTE: No Telegram group tagging — Bot API does not support member tags.
Display rank in Customer Dashboard + admin /find instead.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime
from html import escape as _h

from sqlalchemy import text as _t

logger = logging.getLogger(__name__)

# ─── Public titles (ชุด C สายเซเลบ) ───
RANK_TITLE = {
    "BRONZE":  "🥉 ขาประจำ",
    "SILVER":  "🥈 เซเลบเจริญพร",
    "DIAMOND": "💎 เจ้าพ่อเจริญพร",
}

RANK_ORDER = {"NONE": 0, "BRONZE": 1, "SILVER": 2, "DIAMOND": 3}


async def compute_rank_for_user(user_id: int) -> str:
    """Return target rank based on spend + tenure."""
    from shared.database import get_session
    async with get_session() as s:
        r = await s.execute(_t(
            "SELECT total_spent::int AS spent, loyalty_first_paid_at "
            "FROM users WHERE id = :uid"
        ), {"uid": user_id})
        row = r.fetchone()
        if not row:
            return "NONE"
        spent = int(row.spent or 0)
        first_paid = row.loyalty_first_paid_at
        days_paid = (datetime.utcnow() - first_paid).days if first_paid else 0
        # FIX 2026-06-22: ลบ tenure-only path สำหรับ Silver — ต้องจ่ายเกิน ฿1,000 เท่านั้น
        # ก่อนหน้านี้ลูกค้าจ่าย ฿300 ครั้งเดียว + อยู่ครบ 90 วัน → ได้ Silver + ของฟรี ฿1,549
        # ใหม่: rank-up เฉพาะจากเงินจริง (monotonic up, ไม่มี downgrade)
        if spent >= 4000:
            return "DIAMOND"
        if spent >= 1000:
            return "SILVER"
        if days_paid >= 30:  # Bronze: เคยจ่าย (loyalty_first_paid_at มีค่า) + 30 วัน
            return "BRONZE"
        return "NONE"


def rank_higher(a: str, b: str) -> bool:
    return RANK_ORDER.get(a, 0) > RANK_ORDER.get(b, 0)


# ─── Award rewards (BRONZE/SILVER only — Diamond gets nothing per boss spec) ─

async def _award_bronze(user_id: int, telegram_id: int) -> dict:
    """+3 gacha credits."""
    from shared.database import get_session
    async with get_session() as s:
        await s.execute(_t(
            "INSERT INTO gachapon_credits (user_id, telegram_id, credits, total_purchased) "
            "VALUES (:uid, :tg, 3, 0) "
            "ON CONFLICT (user_id) DO UPDATE SET "
            "  credits = gachapon_credits.credits + 3, updated_at = NOW()"
        ), {"uid": user_id, "tg": telegram_id})
        await s.commit()
    return {"gacha": 3}


async def _award_silver(user_id: int, telegram_id: int) -> dict:
    """FIX 2026-06-22: ลดของแจก 90% — ของเดิม ฿1,549 → ใหม่ ~฿150
    +2 gacha credits + TIER_300 free 5 days (extend OR create)."""
    from shared.database import get_session
    async with get_session() as s:
        # 1. Gacha credits (ลด 5 → 2)
        await s.execute(_t(
            "INSERT INTO gachapon_credits (user_id, telegram_id, credits, total_purchased) "
            "VALUES (:uid, :tg, 2, 0) "
            "ON CONFLICT (user_id) DO UPDATE SET "
            "  credits = gachapon_credits.credits + 2, updated_at = NOW()"
        ), {"uid": user_id, "tg": telegram_id})

        # 2. TIER_300 — find package (ลด tier 1299 → 300)
        pkg_row = (await s.execute(_t(
            "SELECT id FROM packages WHERE tier = 'TIER_300' AND is_active = TRUE ORDER BY id LIMIT 1"
        ))).fetchone()
        if not pkg_row:
            await s.commit()
            return {"gacha": 2, "sub_days": 0}
        pkg_id = pkg_row.id

        # 3. Check existing ACTIVE sub of TIER_300 OR higher
        # ถ้าลูกค้ามี active sub สูงกว่าอยู่แล้ว → ไม่ต้องสร้าง TIER_300 (ลดลำดับ)
        # ถ้าเท่ากันหรือต่ำกว่า → extend / create
        existing = (await s.execute(_t(
            "SELECT s.id, pk.tier FROM subscriptions s "
            "JOIN packages pk ON pk.id = s.package_id "
            "WHERE s.user_id = :uid AND s.status = 'ACTIVE' AND s.end_date > NOW() "
            "ORDER BY pk.id DESC LIMIT 1"
        ), {"uid": user_id})).fetchone()

        if existing and str(existing.tier) in ("TIER_500", "TIER_1299", "TIER_2499"):
            # ลูกค้ามี tier สูงกว่าอยู่แล้ว → ไม่ extend (ไม่ลดเขา)
            await s.commit()
            return {"gacha": 2, "sub_days": 0, "package_id": pkg_id, "skipped_higher_tier": True}

        # มี TIER_300 อยู่ → extend; ไม่มี → สร้างใหม่
        existing_300 = (await s.execute(_t(
            "SELECT id FROM subscriptions "
            "WHERE user_id = :uid AND package_id = :pid AND status = 'ACTIVE' AND end_date > NOW() "
            "ORDER BY end_date DESC LIMIT 1"
        ), {"uid": user_id, "pid": pkg_id})).fetchone()

        if existing_300:
            await s.execute(_t(
                "UPDATE subscriptions SET end_date = end_date + INTERVAL '5 days', "
                "updated_at = NOW() WHERE id = :sid"
            ), {"sid": existing_300.id})
        else:
            await s.execute(_t(
                "INSERT INTO subscriptions "
                "(user_id, package_id, start_date, end_date, status, created_at, updated_at) "
                "VALUES (:uid, :pid, NOW(), NOW() + INTERVAL '5 days', "
                "'ACTIVE'::subscriptionstatus, NOW(), NOW())"
            ), {"uid": user_id, "pid": pkg_id})
        await s.commit()
    return {"gacha": 2, "sub_days": 5, "package_id": pkg_id}


async def _award_diamond(user_id: int, telegram_id: int) -> dict:
    """No automatic reward — boss hands out physical gifts."""
    return {"manual_gift": True}


# ─── DM customer about rank-up ────────────────────────────────────────────

async def _dm_customer_rank_up(telegram_id: int, new_rank: str, rewards: dict) -> bool:
    import telegram as tg
    from telegram.error import Forbidden

    title = RANK_TITLE.get(new_rank, new_rank)
    if new_rank == "BRONZE":
        msg = (
            f"🎊 <b>ยินดีด้วย! คุณได้รับยศ {title}</b>\n\n"
            "ขอบคุณที่อยู่กับเจริญพรครบ 30 วัน 🥂\n\n"
            f"🎁 <b>ของขวัญ:</b>\n"
            f"   🎰 หมุนกาชาฟรี <b>{rewards.get('gacha', 0)} หมุน</b>\n\n"
            "✨ ดูยศของคุณได้ในแดชบอร์ดส่วนตัว"
        )
    elif new_rank == "SILVER":
        # Generate invite links via Guardian bot (so customer can enter rooms)
        invite_lines = []
        try:
            g_tok = os.environ.get("GUARDIAN_BOT_TOKEN", "")
            pkg_id = rewards.get("package_id")
            if g_tok and pkg_id:
                import telegram as _tg
                from bots.guardian_bot.group_monitor import generate_invite_links_for_user
                gbot = _tg.Bot(token=g_tok)
                await gbot.initialize()
                try:
                    links = await generate_invite_links_for_user(gbot, telegram_id, pkg_id)
                    titles = {
                        "G300": "VIP (งานทางบ้าน)",
                        "G500": "OnlyFans + งานแรร์",
                        "SSS": "SSS (งานแรร์ทีเด็ด)",
                        "VGOD": "V GOD (งานหลุด)",
                        "INTER": "นานาชาติ VIP",
                        "SERIES": "หนังซีรีส์",
                        "RANDOM": "สายซุ่ม",
                    }
                    for slug, link in (links or {}).items():
                        t = titles.get(slug, slug)
                        invite_lines.append(f'   • <a href="{link}">{t}</a>')
                finally:
                    try: await gbot.shutdown()
                    except: pass
        except Exception as _e:
            logger.warning("Silver invite link gen failed tg=%s: %s", telegram_id, _e)

        links_block = ("\n🚪 <b>ลิงก์เข้าห้อง</b>:\n" + "\n".join(invite_lines) + "\n") if invite_lines else ""
        msg = (
            f"🎊 <b>ยินดีด้วย! คุณได้รับยศ {title}</b>\n\n"
            "ขอบคุณที่สนับสนุนเจริญพรอย่างต่อเนื่อง 🥂\n"
            "(อยู่ครบ 90 วัน หรือจ่ายรวมเกิน ฿1,000)\n\n"
            f"🎁 <b>ของขวัญ:</b>\n"
            f"   🎰 หมุนกาชาฟรี <b>{rewards.get('gacha', 0)} หมุน</b>\n"
            f"   🌟 GOD MODE 1299 <b>ฟรี {rewards.get('sub_days', 14)} วัน</b>\n"
            f"{links_block}\n"
            "✨ ดูยศและสิทธิประโยชน์ในแดชบอร์ดส่วนตัว"
        )
    elif new_rank == "DIAMOND":
        msg = (
            f"🎊 <b>ยินดีด้วย! คุณได้รับยศ {title}</b>\n\n"
            "คุณคือสมาชิกระดับท็อปของเจริญพร 🏆\n"
            "(จ่ายสะสมเกิน ฿4,000)\n\n"
            "🎁 บอสจะมอบของขวัญพิเศษให้คุณเอง คอยติดตามนะคะ ✨"
        )
    else:
        return False

    token = os.environ.get("SALES_BOT_TOKEN", "")
    if not token:
        return False
    bot = tg.Bot(token=token)
    await bot.initialize()
    try:
        await bot.send_message(chat_id=telegram_id, text=msg, parse_mode="HTML")
        return True
    except Forbidden:
        return False
    except Exception as e:
        logger.warning("DM rank-up failed tg=%s: %s", telegram_id, e)
        return False
    finally:
        try: await bot.shutdown()
        except: pass


# ─── Main: promote a user (idempotent) ────────────────────────────────────

async def promote_user_to_rank(user_id: int, new_rank: str, silent: bool = False) -> dict:
    """Promote user. silent=True skips per-user admin notify (use for batch).

    FIX 2026-06-21: เพิ่ม per-user advisory lock + atomic rank update
    ป้องกัน race condition (2 scheduler รันซ้อน → award rewards ซ้ำ).
    """
    from shared.database import get_session

    if new_rank not in RANK_TITLE:
        return {"skip": True, "reason": f"unknown rank {new_rank}"}

    # Lock key — unique per user + namespace สำหรับ loyalty promotion
    # ใช้ 2-arg version เพื่อ namespace แยกจาก lock อื่น (8888 = silver backfill)
    lock_ns = 7777
    lock_key = int(user_id)

    # Lock + atomic check-and-set ใน same transaction
    async with get_session() as s:
        # Acquire advisory lock — auto-release on tx end
        await s.execute(_t("SELECT pg_advisory_xact_lock(:ns, :k)"),
                        {"ns": lock_ns, "k": lock_key})

        r = await s.execute(_t(
            "SELECT id, telegram_id, first_name, loyalty_rank, total_spent::int "
            "FROM users WHERE id = :uid FOR UPDATE"
        ), {"uid": user_id})
        u = r.fetchone()
        if not u:
            await s.commit()
            return {"skip": True, "reason": "user_not_found"}

        current = u.loyalty_rank or "NONE"
        if not rank_higher(new_rank, current):
            await s.commit()
            return {"skip": True, "reason": f"already_at_{current}"}

        # Atomic update — ภายใน lock, รับประกัน exclusive rights to promote
        upd = await s.execute(_t(
            "UPDATE users SET loyalty_rank = :r, loyalty_rank_at = NOW() "
            "WHERE id = :uid AND COALESCE(loyalty_rank, 'NONE') = :prev"
        ), {"r": new_rank, "uid": user_id, "prev": current})
        if upd.rowcount == 0:
            await s.commit()
            return {"skip": True, "reason": "concurrent_update_skipped"}
        await s.commit()

    # ตรงนี้: ได้ exclusive rights, rank ใน DB เป็น new_rank แล้ว → ค่อย award + DM
    # (Award functions ใช้ session ของตัวเอง)
    if new_rank == "BRONZE":
        rewards = await _award_bronze(user_id, u.telegram_id)
    elif new_rank == "SILVER":
        rewards = await _award_silver(user_id, u.telegram_id)
    elif new_rank == "DIAMOND":
        rewards = await _award_diamond(user_id, u.telegram_id)
    else:
        rewards = {}

    # DM customer
    dm_sent = await _dm_customer_rank_up(u.telegram_id, new_rank, rewards)

    # audit log (separate session to isolate failures)
    try:
        async with get_session() as s:
            await s.execute(_t(
                "INSERT INTO admin_logs (admin_id, action, target_type, target_id, details, created_at) "
                "VALUES (0, 'loyalty_rank_up_v2', 'user', :uid, :det, NOW())"
            ), {"uid": user_id,
                "det": f"tg={u.telegram_id} {current}→{new_rank} dm={dm_sent} rewards={rewards}"})
            await s.commit()
    except Exception as e:
        logger.warning("audit log failed: %s", e)

    # Admin group notify (skip in batch mode)
    if not silent:
        try:
            from shared.admin_alert import notify_admin_report
            title = RANK_TITLE[new_rank]
            _spent = int(u.total_spent or 0)
            reward_text = "—"
            if new_rank == "BRONZE":
                reward_text = f"🎰 กาชา {rewards.get('gacha',0)} หมุน"
            elif new_rank == "SILVER":
                reward_text = f"🎰 กาชา {rewards.get('gacha',0)} + 🌟 1299 ฟรี {rewards.get('sub_days',0)} วัน"
            elif new_rank == "DIAMOND":
                reward_text = "🏆 รอบอสมอบของพิเศษ"
            await notify_admin_report(
                f"🎖️ <b>เลื่อนยศ Loyalty</b>\n"
                f"👤 {_h(u.first_name or '-')} <code>{u.telegram_id}</code>\n"
                f"🏆 {current} → <b>{title}</b>\n"
                f"💰 จ่ายรวม: ฿{_spent:,}\n"
                f"🎁 ของแถม: {reward_text}\n"
                f"💬 DM ลูกค้า: {'✅' if dm_sent else '❌'}",
                parse_mode="HTML",
            )
        except Exception as e:
            logger.warning("admin notify failed: %s", e)

    return {
        "user_id": user_id, "telegram_id": u.telegram_id,
        "from": current, "to": new_rank,
        "rewards": rewards, "dm_sent": dm_sent,
    }


# ─── Scheduled job: scan + promote ────────────────────────────────────────

async def run_loyalty_check_job(context=None) -> dict:
    """Run every 6 hours via APScheduler."""
    from shared.database import get_session
    import asyncio as _a

    async with get_session() as s:
        r = await s.execute(_t(
            "SELECT id FROM users "
            "WHERE loyalty_first_paid_at IS NOT NULL AND is_banned = FALSE"
        ))
        ids = [row.id for row in r.fetchall()]

    promoted = 0
    skipped = 0
    errors = 0
    for uid in ids:
        try:
            target = await compute_rank_for_user(uid)
            if target == "NONE":
                skipped += 1
                continue
            res = await promote_user_to_rank(uid, target, silent=False)
            if res.get("skip"):
                skipped += 1
            else:
                promoted += 1
                await _a.sleep(0.5)
        except Exception as e:
            errors += 1
            logger.warning("loyalty check uid=%s failed: %s", uid, e)

    logger.info("loyalty_check: scanned=%d promoted=%d skipped=%d errors=%d",
                len(ids), promoted, skipped, errors)
    return {"scanned": len(ids), "promoted": promoted,
            "skipped": skipped, "errors": errors}


__all__ = ["RANK_TITLE", "compute_rank_for_user",
           "promote_user_to_rank", "run_loyalty_check_job"]
