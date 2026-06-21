"""Unified Ban Service — 1 call = ban everywhere.

WHY THIS EXISTS:
  Banning a customer used to mean ONLY users.is_banned = TRUE, which left
  open: active subscriptions, gacha credits, discount balance, group access,
  DM queue, scam slip name/hash recognition. Dam (tg=8755901950) showcased
  this gap — he was "banned" but still had VIP 30d, ฿650 discount credits,
  and (until boss revoked) gacha spins.

  This module makes ban() = the single trusted command. Calling it cascades
  across the whole system in one go.

USAGE:
    from shared.ban_service import ban_user, unban_user

    result = await ban_user(
        telegram_id=8755901950,
        reason="scam_dam_ring",
        admin_id=8502597269,
    )
    # result.report → ready-to-send admin message

WHAT IT DOES (sequence inside one DB transaction + best-effort post-commit):
    1.  Look up the user (or create stub row if unknown)
    2.  Set users.is_banned + banned_at + banned_reason + banned_by
    3.  Expire ALL active subscriptions of this user
    4.  Zero user_discount_credits.balance (record old value)
    5.  Zero gachapon_credits.credits
    6.  Add this user's distinct sender_names to banned_senders
    7.  Add this user's distinct slip_trans_refs + slip_hashes to banned_slips
    8.  Stop pending DM jobs (comeback, retention, expiry)
    9.  Mark is_blocked_bot=TRUE so DM workers skip
    10. Audit log entry
    COMMIT
    11. Guardian Bot: kick + ban_chat_member across all active groups
    12. Build report string

`unban_user` does the inverse for users.is_banned + clears banned_at, restores
WebApp access, and unbans Guardian. It does NOT restore subscriptions, credits,
or DM jobs (those are intentionally destroyed).
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select, text as sql_text

logger = logging.getLogger(__name__)


@dataclass
class BanResult:
    success: bool
    telegram_id: int
    user_id: int | None = None
    first_name: str | None = None
    reason: str = ""
    banned_subs_count: int = 0
    revoked_discount: Decimal = field(default_factory=lambda: Decimal("0"))
    revoked_gacha_credits: int = 0
    senders_added: list[str] = field(default_factory=list)
    slips_added_count: int = 0
    groups_kicked: list[str] = field(default_factory=list)
    groups_failed: list[tuple[str, str]] = field(default_factory=list)  # (slug, err)
    dm_jobs_stopped: int = 0
    elapsed_sec: float = 0.0
    error: str | None = None

    def report_html(self) -> str:
        """Pretty admin-group message."""
        from html import escape
        name = escape(self.first_name or "ลูกค้า")
        lines = []
        if not self.success:
            lines.append(f"❌ <b>แบนไม่สำเร็จ</b>")
            lines.append(f"🆔 tg=<code>{self.telegram_id}</code>")
            lines.append(f"📋 {escape(self.error or '')}")
            return "\n".join(lines)

        lines.append(f"🔨 <b>แบนเรียบร้อย</b>")
        lines.append(f"👤 {name} (tg=<code>{self.telegram_id}</code>)")
        if self.reason:
            lines.append(f"📋 เหตุผล: <i>{escape(self.reason)}</i>")
        lines.append("")
        lines.append(f"✅ ห้ามส่งสลิปจ่ายเงิน")
        if self.banned_subs_count:
            lines.append(f"✅ ยกเลิกสมาชิก <b>{self.banned_subs_count}</b> รายการ")
        if self.revoked_discount > 0:
            lines.append(f"✅ ส่วนลด = 0 (เก็บคืน ฿{self.revoked_discount:.0f})")
        if self.revoked_gacha_credits > 0:
            lines.append(f"✅ สิทธิ์หมุนกาชา = 0 (เก็บคืน {self.revoked_gacha_credits} ครั้ง)")
        if self.senders_added:
            sample = ", ".join(escape(s)[:30] for s in self.senders_added[:3])
            lines.append(f"✅ บันทึกชื่อสลิป {len(self.senders_added)} ชื่อ: {sample}")
        if self.slips_added_count:
            lines.append(f"✅ บันทึกเลขสลิป <b>{self.slips_added_count}</b> ใบ")
        if self.dm_jobs_stopped:
            lines.append(f"✅ หยุดงาน DM ค้าง <b>{self.dm_jobs_stopped}</b> งาน")
        lines.append(f"✅ ปิดหน้าข้อมูล + หยุดส่งข้อความ")
        ok = len(self.groups_kicked)
        fail = len(self.groups_failed)
        if ok or fail:
            lines.append(f"✅ เตะออกจากกลุ่ม: <b>{ok}</b> สำเร็จ" +
                         (f", {fail} ล้มเหลว" if fail else ""))
            if self.groups_failed:
                for slug, err in self.groups_failed[:3]:
                    lines.append(f"   ⚠️ {escape(slug)}: {escape(err)[:40]}")
        lines.append("")
        lines.append(f"🛡️ เสร็จใน {self.elapsed_sec:.1f} วินาที")
        return "\n".join(lines)


async def ban_user(
    telegram_id: int,
    reason: str = "",
    admin_id: int | None = None,
    *,
    add_to_blacklist: bool = True,
    kick_from_groups: bool = True,
) -> BanResult:
    """Ban user across every system. Returns BanResult with report."""
    from shared.database import get_session
    from shared.models import User, Subscription, SubscriptionStatus

    started = datetime.utcnow()
    result = BanResult(success=False, telegram_id=telegram_id, reason=reason)

    try:
        async with get_session() as session:
            # 1. resolve user
            u = (await session.execute(
                select(User).where(User.telegram_id == telegram_id)
            )).scalar_one_or_none()
            if not u:
                # create stub so we have a user_id to reference in blacklist FK
                u = User(telegram_id=telegram_id, first_name="(banned-no-history)")
                session.add(u)
                await session.flush()
                logger.info("ban_user: created stub user for unknown tg=%s", telegram_id)

            result.user_id = u.id
            result.first_name = u.first_name

            # 2. mark banned + metadata
            u.is_banned = True
            u.banned_at = started
            u.banned_reason = (reason or "")[:255]
            u.banned_by = admin_id
            u.is_blocked_bot = True  # skip DM workers
            u.blocked_bot_at = started

            # 3. expire active subscriptions
            r = await session.execute(sql_text(
                "UPDATE subscriptions SET status='EXPIRED', end_date=NOW() "
                "WHERE user_id = :uid AND status = 'ACTIVE'"
            ), {"uid": u.id})
            result.banned_subs_count = r.rowcount or 0

            # 4. zero discount credits (capture old balance for the report)
            r = await session.execute(sql_text(
                "SELECT balance FROM user_discount_credits WHERE telegram_id = :tg"
            ), {"tg": telegram_id})
            row = r.fetchone()
            if row and row[0]:
                result.revoked_discount = Decimal(str(row[0]))
                await session.execute(sql_text(
                    "UPDATE user_discount_credits SET balance = 0, updated_at = NOW() "
                    "WHERE telegram_id = :tg"
                ), {"tg": telegram_id})

            # 5. zero gacha credits
            r = await session.execute(sql_text(
                "SELECT credits FROM gachapon_credits WHERE user_id = :uid"
            ), {"uid": u.id})
            row = r.fetchone()
            if row and row[0]:
                result.revoked_gacha_credits = int(row[0])
                await session.execute(sql_text(
                    "UPDATE gachapon_credits SET credits = 0, updated_at = NOW() "
                    "WHERE user_id = :uid"
                ), {"uid": u.id})

            if add_to_blacklist:
                # 6. blacklist sender_names this user has ever used
                r = await session.execute(sql_text("""
                    SELECT DISTINCT TRIM(sender_name)
                    FROM payments
                    WHERE user_id = :uid AND sender_name IS NOT NULL
                      AND TRIM(sender_name) != ''
                """), {"uid": u.id})
                senders = [row[0] for row in r.fetchall() if row[0]]
                for sn in senders:
                    try:
                        await session.execute(sql_text("""
                            INSERT INTO banned_senders
                              (sender_name, source_user_id, source_telegram_id,
                               reason, banned_by)
                            VALUES (:sn, :uid, :tg, :rsn, :adm)
                            ON CONFLICT (sender_name) DO NOTHING
                        """), {
                            "sn": sn, "uid": u.id, "tg": telegram_id,
                            "rsn": reason[:255], "adm": admin_id,
                        })
                        result.senders_added.append(sn)
                    except Exception as exc:
                        logger.warning("ban_user: blacklist sender %r fail: %s", sn, exc)

                # 7. blacklist slip refs + hashes
                r = await session.execute(sql_text("""
                    SELECT DISTINCT slip_trans_ref, slip_hash
                    FROM payments
                    WHERE user_id = :uid AND (slip_trans_ref IS NOT NULL OR slip_hash IS NOT NULL)
                """), {"uid": u.id})
                slips_count = 0
                for tref, shash in r.fetchall():
                    try:
                        await session.execute(sql_text("""
                            INSERT INTO banned_slips
                              (slip_trans_ref, slip_hash, source_user_id,
                               source_telegram_id, reason, banned_by)
                            VALUES (:tref, :sh, :uid, :tg, :rsn, :adm)
                            ON CONFLICT DO NOTHING
                        """), {
                            "tref": tref, "sh": shash, "uid": u.id, "tg": telegram_id,
                            "rsn": reason[:255], "adm": admin_id,
                        })
                        slips_count += 1
                    except Exception as exc:
                        logger.warning("ban_user: blacklist slip fail: %s", exc)
                result.slips_added_count = slips_count

            # 8. stop pending DM jobs (best-effort — tables may differ across deployments)
            dm_stopped = 0
            for sql in (
                "UPDATE comeback_dm_log SET responded = TRUE "
                "WHERE user_id = :uid AND responded = FALSE",
                "UPDATE expiry_notifications SET acknowledged = TRUE "
                "WHERE user_id = :uid AND acknowledged = FALSE",
                "UPDATE trial_dm_log SET responded = TRUE "
                "WHERE user_id = :uid AND responded = FALSE",
            ):
                try:
                    r = await session.execute(sql_text(sql), {"uid": u.id})
                    dm_stopped += r.rowcount or 0
                except Exception:
                    pass
            result.dm_jobs_stopped = dm_stopped

        # ───── 10. Audit log (own session — isolated from main txn) ─────
        try:
            async with get_session() as _alog:
                await _alog.execute(sql_text("""
                    INSERT INTO admin_logs
                      (admin_id, action, target_type, target_id, details, created_at)
                    VALUES (:adm, 'user_banned', 'user', :uid, :det, NOW())
                """), {
                    "adm": admin_id or 0,
                    "uid": result.user_id,
                    "det": (
                        f"tg={telegram_id} reason={reason} "
                        f"subs={result.banned_subs_count} "
                        f"disc=฿{result.revoked_discount} "
                        f"gacha={result.revoked_gacha_credits}"
                    )[:500],
                })
        except Exception as exc:
            logger.warning("ban_user: audit log fail: %s", exc)

        # ───── 11. Kick from groups (post-commit, best-effort) ─────
        if kick_from_groups:
            try:
                import telegram as tg_lib
                from shared.database import get_session as _gs
                async with _gs() as s2:
                    r = await s2.execute(sql_text(
                        "SELECT slug, chat_id, title FROM group_registry "
                        "WHERE is_active = TRUE"
                    ))
                    groups = list(r.fetchall())

                guardian_token = os.environ.get("GUARDIAN_BOT_TOKEN", "")
                if guardian_token and groups:
                    gb = tg_lib.Bot(token=guardian_token)
                    await gb.initialize()
                    try:
                        for slug, chat_id, title in groups:
                            try:
                                await gb.ban_chat_member(
                                    chat_id=chat_id,
                                    user_id=telegram_id,
                                    revoke_messages=False,
                                )
                                result.groups_kicked.append(slug)
                            except Exception as exc:
                                msg = str(exc)
                                # Bot not in group / user not in group → not really a failure
                                if any(s in msg.lower() for s in
                                       ("user not found", "chat not found",
                                        "participant_id_invalid", "user_not_participant")):
                                    result.groups_kicked.append(f"{slug}(not-in)")
                                else:
                                    result.groups_failed.append((slug, msg[:80]))
                    finally:
                        try: await gb.shutdown()
                        except Exception: pass
                elif not guardian_token:
                    result.groups_failed.append(("__all__", "GUARDIAN_BOT_TOKEN not set"))
            except Exception as exc:
                logger.exception("ban_user: kick loop crashed: %s", exc)
                result.groups_failed.append(("__crash__", str(exc)[:80]))

        result.success = True
        result.elapsed_sec = (datetime.utcnow() - started).total_seconds()
        logger.info("[BAN] tg=%s done in %.1fs (subs=%s, disc=฿%s, gacha=%s, groups=%s)",
                    telegram_id, result.elapsed_sec, result.banned_subs_count,
                    result.revoked_discount, result.revoked_gacha_credits,
                    len(result.groups_kicked))
        return result

    except Exception as exc:
        logger.exception("ban_user: crashed tg=%s: %s", telegram_id, exc)
        result.error = str(exc)[:200]
        return result


async def unban_user(
    telegram_id: int,
    admin_id: int | None = None,
    *,
    remove_from_blacklist: bool = False,
    unkick_from_groups: bool = False,
) -> BanResult:
    """Reverse the ban. By default leaves blacklist + group bans alone
    (admin must opt in to clear those if it was a mistake).

    Does NOT restore subscriptions / credits / DM jobs — those were
    intentionally destroyed.
    """
    from shared.database import get_session
    from shared.models import User

    started = datetime.utcnow()
    result = BanResult(success=False, telegram_id=telegram_id, reason="UNBAN")

    try:
        async with get_session() as session:
            u = (await session.execute(
                select(User).where(User.telegram_id == telegram_id)
            )).scalar_one_or_none()
            if not u:
                result.error = "user_not_found"
                return result

            result.user_id = u.id
            result.first_name = u.first_name

            u.is_banned = False
            u.banned_at = None
            u.banned_reason = None
            u.is_blocked_bot = False
            u.blocked_bot_at = None

            if remove_from_blacklist:
                r = await session.execute(sql_text(
                    "DELETE FROM banned_senders WHERE source_user_id = :uid RETURNING sender_name"
                ), {"uid": u.id})
                result.senders_added = [row[0] for row in r.fetchall()]
                r = await session.execute(sql_text(
                    "DELETE FROM banned_slips WHERE source_user_id = :uid"
                ), {"uid": u.id})
                result.slips_added_count = r.rowcount or 0

            try:
                await session.execute(sql_text("""
                    INSERT INTO admin_logs
                      (admin_id, action, target_type, target_id, details, created_at)
                    VALUES (:adm, 'user_unbanned', 'user', :uid, :det, NOW())
                """), {
                    "adm": admin_id or 0, "uid": u.id,
                    "det": f"tg={telegram_id} remove_blacklist={remove_from_blacklist}",
                })
            except Exception:
                pass

        # Unkick from groups (optional)
        if unkick_from_groups:
            try:
                import telegram as tg_lib
                from shared.database import get_session as _gs
                async with _gs() as s2:
                    r = await s2.execute(sql_text(
                        "SELECT slug, chat_id FROM group_registry WHERE is_active = TRUE"
                    ))
                    groups = list(r.fetchall())
                guardian_token = os.environ.get("GUARDIAN_BOT_TOKEN", "")
                if guardian_token:
                    gb = tg_lib.Bot(token=guardian_token)
                    await gb.initialize()
                    try:
                        for slug, chat_id in groups:
                            try:
                                await gb.unban_chat_member(
                                    chat_id=chat_id, user_id=telegram_id,
                                    only_if_banned=True,
                                )
                                result.groups_kicked.append(slug)
                            except Exception as exc:
                                result.groups_failed.append((slug, str(exc)[:60]))
                    finally:
                        try: await gb.shutdown()
                        except Exception: pass
            except Exception as exc:
                result.groups_failed.append(("__crash__", str(exc)[:80]))

        result.success = True
        result.elapsed_sec = (datetime.utcnow() - started).total_seconds()
        return result

    except Exception as exc:
        logger.exception("unban_user: crashed tg=%s: %s", telegram_id, exc)
        result.error = str(exc)[:200]
        return result


# ─── Blacklist lookup helpers (used by apply_payment_approval) ────────────


async def is_sender_blacklisted(sender_name: str | None) -> tuple[bool, str | None]:
    """Return (True, reason) if this exact sender_name is blacklisted."""
    if not sender_name or not sender_name.strip():
        return False, None
    from shared.database import get_session
    async with get_session() as s:
        r = await s.execute(sql_text(
            "SELECT reason FROM banned_senders WHERE sender_name = :sn LIMIT 1"
        ), {"sn": sender_name.strip()})
        row = r.fetchone()
        if row:
            return True, (row[0] or "blacklisted_sender")
    return False, None


async def is_slip_blacklisted(
    slip_trans_ref: str | None,
    slip_hash: str | None,
) -> tuple[bool, str | None]:
    """Return (True, reason) if either transRef or hash is blacklisted."""
    from shared.database import get_session
    if not slip_trans_ref and not slip_hash:
        return False, None
    async with get_session() as s:
        if slip_trans_ref:
            r = await s.execute(sql_text(
                "SELECT reason FROM banned_slips WHERE slip_trans_ref = :tref LIMIT 1"
            ), {"tref": slip_trans_ref})
            row = r.fetchone()
            if row:
                return True, (row[0] or "blacklisted_slip_transref")
        if slip_hash:
            r = await s.execute(sql_text(
                "SELECT reason FROM banned_slips WHERE slip_hash = :sh LIMIT 1"
            ), {"sh": slip_hash})
            row = r.fetchone()
            if row:
                return True, (row[0] or "blacklisted_slip_hash")
    return False, None


__all__ = [
    "ban_user",
    "unban_user",
    "BanResult",
    "is_sender_blacklisted",
    "is_slip_blacklisted",
]
