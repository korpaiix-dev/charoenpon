"""Admin tools: /find (customer search), /auditlog (action history), /topspenders (raffle).

All commands are admin-only. Uses inline buttons so admin doesn't have to remember syntax.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CommandHandler, CallbackQueryHandler, ContextTypes
from shared.admin_perms import is_admin_for_bot

logger = logging.getLogger(__name__)


def _is_admin(user_id: int) -> bool:
    """Migrated to shared.admin_perms (DB-first with env fallback)."""
    return is_admin_for_bot(user_id, "admin_bot")


def _fmt_dt(dt) -> str:
    if not dt: return "-"
    if isinstance(dt, str): return dt[:16]
    return dt.strftime("%d/%m %H:%M")


def _fmt_thb(amt) -> str:
    try: return f"฿{float(amt):,.0f}"
    except Exception: return str(amt)


# ─── /find — customer search ──────────────────────────────────────
async def cmd_find(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update.effective_user.id):
        return
    args = " ".join(context.args or []).strip()
    if not args:
        await update.message.reply_text(
            "<b>🔎 ค้นหาลูกค้า</b>\n\n"
            "ใช้: <code>/find &lt;ชื่อ/username/เบอร์/telegram_id&gt;</code>\n\n"
            "ตัวอย่าง:\n"
            "• <code>/find 0812345678</code>\n"
            "• <code>/find @username</code>\n"
            "• <code>/find สมชาย</code>\n"
            "• <code>/find 123456789</code>",
            parse_mode="HTML"
        )
        return

    results = await _search_customers(args)
    if not results:
        await update.message.reply_text(f"❌ ไม่พบลูกค้าที่ตรงกับ \"{args}\"")
        return

    text = f"<b>🔎 พบ {len(results)} คน</b> (ค้นด้วย \"{args}\")\n\n"
    buttons = []
    for u in results[:8]:
        text += _format_user_brief(u) + "\n\n"
        buttons.append([
            InlineKeyboardButton(
                f"👤 {u.get('username') or u.get('first_name') or u['telegram_id']}",
                callback_data=f"find_view:{u['telegram_id']}"
            )
        ])
    if len(results) > 8:
        text += f"<i>...อีก {len(results) - 8} คน (refine query)</i>\n"

    kb = InlineKeyboardMarkup(buttons) if buttons else None
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=kb,
                                     disable_web_page_preview=True)

    # Audit
    try:
        from shared.utils import log_admin_action
        await log_admin_action(
            admin_id=update.effective_user.id,
            action="customer_search",
            details=f"query={args[:80]} hits={len(results)}"
        )
    except Exception as e:
        logger.debug("audit log skip: %s", e)


async def _search_customers(q: str) -> list[dict]:
    """Search by phone, username, first_name, last_name, telegram_id."""
    from shared.database import get_session
    from sqlalchemy import text as _t

    q = q.strip().lstrip("@")
    digits = re.sub(r"\D", "", q)

    where_clauses = []
    params = {}
    # By telegram_id (large number)
    if digits and len(digits) >= 7:
        where_clauses.append("u.telegram_id = :tid")
        params["tid"] = int(digits)
    # By phone
    if digits and 9 <= len(digits) <= 12:
        where_clauses.append("REGEXP_REPLACE(COALESCE(u.phone,''), '[^0-9]', '', 'g') = :phn")
        params["phn"] = digits
    # By username / name (case-insensitive LIKE)
    if q:
        where_clauses.append(
            "(LOWER(u.username) LIKE :like_q OR LOWER(u.first_name) LIKE :like_q "
            "OR LOWER(u.last_name) LIKE :like_q)"
        )
        params["like_q"] = f"%{q.lower()}%"

    if not where_clauses:
        return []

    sql = f"""
        SELECT u.id, u.telegram_id, u.username, u.first_name, u.last_name, u.phone,
               u.is_banned, u.is_blocked_bot, u.created_at,
               s.package_id AS tier, s.end_date AS expiry, s.status AS sub_status,
               (SELECT COUNT(*) FROM payments p WHERE p.user_id=u.id AND p.status='APPROVED') AS paid_count,
               (SELECT COALESCE(SUM(amount),0) FROM payments p WHERE p.user_id=u.id AND p.status='APPROVED') AS total_paid
        FROM users u
        LEFT JOIN LATERAL (
            SELECT package_id, end_date, status FROM subscriptions
            WHERE user_id=u.id ORDER BY end_date DESC NULLS LAST LIMIT 1
        ) s ON true
        WHERE {' OR '.join(where_clauses)}
        ORDER BY u.created_at DESC LIMIT 20
    """
    async with get_session() as s:
        r = await s.execute(_t(sql), params)
        return [dict(row._mapping) for row in r.fetchall()]


def _format_user_brief(u: dict) -> str:
    name = u.get("first_name") or ""
    if u.get("last_name"): name += f" {u['last_name']}"
    name = name.strip() or "(no name)"
    uname = f"@{u['username']}" if u.get("username") else ""
    phone = u.get("phone") or "-"
    tier = u.get("tier") or "—"
    expiry = u.get("expiry")
    if expiry:
        if hasattr(expiry, 'strftime'):
            try:
                days_left = (expiry - datetime.utcnow()).days
                expiry_str = f"{expiry.strftime('%d/%m/%Y')} ({days_left:+d}d)"
            except Exception:
                expiry_str = str(expiry)[:10]
        else:
            expiry_str = str(expiry)[:10]
    else:
        expiry_str = "ไม่มี sub"
    flags = ""
    if u.get("is_banned"): flags += " 🚫BANNED"
    if u.get("is_blocked_bot"): flags += " 🛑BLOCKED-BOT"
    return (
        f"<b>{name}</b> {uname}{flags}\n"
        f"  🆔 <code>{u['telegram_id']}</code> | 📱 <code>{phone}</code>\n"
        f"  🎟️ tier {tier} | ⏰ {expiry_str}\n"
        f"  💸 จ่ายไป {u['paid_count']} ครั้ง / {_fmt_thb(u['total_paid'])}"
    )


async def cb_find_view(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show detail of a found user + admin actions."""
    q = update.callback_query
    if not q or not _is_admin(q.from_user.id):
        try: await q.answer("⛔", show_alert=True)
        except Exception: pass
        return
    try: await q.answer()
    except Exception: pass

    try:
        telegram_id = int((q.data or "").split(":")[1])
    except Exception: return

    from shared.database import get_session
    from sqlalchemy import text as _t
    async with get_session() as s:
        u = await s.execute(_t("""
            SELECT u.id, u.telegram_id, u.username, u.first_name, u.last_name,
                   u.phone, u.is_banned, u.is_blocked_bot, u.created_at, u.updated_at,
                   u.real_name, u.total_spent, u.loyalty_rank, u.loyalty_rank_at
            FROM users u WHERE u.telegram_id = :tid LIMIT 1
        """), {"tid": telegram_id})
        u = u.fetchone()
        if not u:
            try: await q.edit_message_text("❌ ไม่พบ user")
            except Exception: pass
            return
        user = dict(u._mapping)

        subs = await s.execute(_t("""
            SELECT package_id, status, start_date, end_date FROM subscriptions
            WHERE user_id=:uid ORDER BY end_date DESC NULLS LAST LIMIT 3
        """), {"uid": user["id"]})
        subs = [dict(r._mapping) for r in subs.fetchall()]

        pays = await s.execute(_t("""
            SELECT id, amount, status, package_id, created_at FROM payments
            WHERE user_id=:uid ORDER BY created_at DESC LIMIT 5
        """), {"uid": user["id"]})
        pays = [dict(r._mapping) for r in pays.fetchall()]

    name = (user.get("first_name") or "") + " " + (user.get("last_name") or "")
    name = name.strip() or "(no name)"
    uname = f"@{user['username']}" if user.get("username") else ""

    text = f"<b>👤 {name}</b> {uname}\n\n"
    text += f"🆔 <code>{user['telegram_id']}</code>\n"
    text += f"📱 {user.get('phone') or '-'}\n"
    if user.get("real_name"):
        text += f"🪪 ชื่อจริง: {user['real_name']}\n"
    text += f"📅 สมัครเมื่อ {_fmt_dt(user.get('created_at'))} | ⏱️ updated {_fmt_dt(user.get('updated_at'))}\n"
    if user.get("total_spent"):
        text += f"💰 รวมจ่าย: {_fmt_thb(user['total_spent'])}\n"
    # Loyalty rank
    _RANK_DISPLAY = {
        "BRONZE":  "🥉 <b>ขาประจำ</b>",
        "SILVER":  "🥈 <b>เซเลบเจริญพร</b>",
        "DIAMOND": "💎 <b>เจ้าพ่อเจริญพร</b>",
    }
    if user.get("loyalty_rank") and user["loyalty_rank"] != "NONE":
        text += f"🎖️ ยศ: {_RANK_DISPLAY.get(user['loyalty_rank'], user['loyalty_rank'])}"
        if user.get("loyalty_rank_at"):
            text += f" <i>(ตั้งแต่ {_fmt_dt(user['loyalty_rank_at'])})</i>"
        text += "\n"
    if user.get("is_banned"):
        text += "🚫 <b>BANNED</b>\n"
    if user.get("is_blocked_bot"):
        text += "🛑 <b>BLOCKED BOT</b> (ลูกค้า block บอท)\n"

    text += "\n<b>📋 Subscriptions (3 ล่าสุด)</b>\n"
    if subs:
        for s_ in subs:
            text += f"  • tier {s_['package_id']} {s_['status']} {_fmt_dt(s_['end_date'])}\n"
    else:
        text += "  ไม่มี\n"

    text += "\n<b>💸 Payments (5 ล่าสุด)</b>\n"
    if pays:
        for p in pays:
            text += f"  • {_fmt_thb(p['amount'])} {p['status']} {_fmt_dt(p['created_at'])}\n"
    else:
        text += "  ไม่มี\n"

    buttons = [
        [InlineKeyboardButton("💬 Chat", callback_data=f"chat_user_{user['telegram_id']}")],
        [InlineKeyboardButton("🔙 กลับไปหน้าค้นหา", callback_data=f"find_back")],
    ]
    try:
        await q.edit_message_text(text, parse_mode="HTML",
                                   reply_markup=InlineKeyboardMarkup(buttons))
    except Exception as e:
        logger.warning("find_view edit failed: %s", e)


async def cb_find_back(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if q:
        try: await q.answer("ใช้ /find ใหม่ได้เลย")
        except Exception: pass


# ─── /auditlog — recent admin actions ─────────────────────────────
async def cmd_auditlog(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update.effective_user.id):
        return

    args = context.args or []
    limit = 20
    filter_action = None
    filter_admin = None
    for a in args:
        if a.isdigit(): limit = min(int(a), 100)
        elif ":" in a:
            k, v = a.split(":", 1)
            if k == "action": filter_action = v
            elif k == "admin": filter_admin = v.lstrip("@")

    from shared.database import get_session
    from sqlalchemy import text as _t

    where = ["1=1"]
    params = {"lim": limit}
    if filter_action:
        where.append("action ILIKE :act")
        params["act"] = f"%{filter_action}%"
    sql = f"""
        SELECT id, admin_id, action, target_type, target_id, details, created_at
        FROM admin_logs WHERE {' AND '.join(where)}
        ORDER BY id DESC LIMIT :lim
    """
    async with get_session() as s:
        r = await s.execute(_t(sql), params)
        rows = [dict(x._mapping) for x in r.fetchall()]

    if not rows:
        await update.message.reply_text("ไม่มี audit log ที่ตรง filter")
        return

    text = f"<b>📋 Audit Log</b> ({len(rows)} entries)\n"
    if filter_action: text += f"<i>filter action={filter_action}</i>\n"
    text += "\n"
    for row in rows:
        ts = _fmt_dt(row["created_at"])
        details = (row.get("details") or "")[:60]
        text += (
            f"<code>{ts}</code> <b>{row['action']}</b>\n"
            f"  admin=<code>{row['admin_id']}</code>"
        )
        if row.get("target_type"):
            text += f" → {row['target_type']} <code>{row['target_id']}</code>"
        if details:
            text += f"\n  <i>{details}</i>"
        text += "\n"

    text += "\n<i>filter: /auditlog 50 action:approve</i>"
    # Telegram 4096 limit
    if len(text) > 4000:
        text = text[:3950] + "\n<i>... (truncated)</i>"
    await update.message.reply_text(text, parse_mode="HTML")


# ─── /topspenders — for monthly raffle ────────────────────────────
async def cmd_topspenders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update.effective_user.id):
        return

    args = context.args or []
    days = 30
    for a in args:
        if a.isdigit(): days = min(int(a), 365)

    from shared.database import get_session
    from sqlalchemy import text as _t
    async with get_session() as s:
        r = await s.execute(_t("""
            SELECT u.telegram_id, u.username, u.first_name, u.last_name,
                   COUNT(p.id) AS pay_count,
                   COALESCE(SUM(p.amount), 0) AS total
            FROM payments p
            JOIN users u ON u.id = p.user_id
            WHERE p.status = 'APPROVED'
              AND p.created_at >= NOW() - (:d || ' days')::interval
            GROUP BY u.id, u.telegram_id, u.username, u.first_name, u.last_name
            ORDER BY total DESC LIMIT 15
        """), {"d": days})
        rows = [dict(x._mapping) for x in r.fetchall()]

    if not rows:
        await update.message.reply_text(f"ไม่มี payment ใน {days} วัน")
        return

    text = f"<b>🏆 Top Spenders ({days} วัน)</b>\n\n"
    medals = ["🥇", "🥈", "🥉"] + ["🏅"] * 12
    total_all = sum(float(r["total"]) for r in rows)
    for i, row in enumerate(rows):
        name = (row.get("first_name") or "") + " " + (row.get("last_name") or "")
        name = name.strip() or f"id={row['telegram_id']}"
        uname = f" @{row['username']}" if row.get("username") else ""
        text += (
            f"{medals[i]} <b>{name}</b>{uname}\n"
            f"   💸 {_fmt_thb(row['total'])} ({row['pay_count']} ครั้ง)\n"
        )
    text += f"\n<i>รวม top {len(rows)} = {_fmt_thb(total_all)}</i>\n"
    text += "<i>/raffle &lt;จำนวน&gt; — สุ่มผู้โชคดี (weight by spend)</i>"

    await update.message.reply_text(text, parse_mode="HTML")

    try:
        from shared.utils import log_admin_action
        await log_admin_action(
            admin_id=update.effective_user.id,
            action="topspenders_view",
            details=f"days={days} top={len(rows)}"
        )
    except Exception: pass


# ─── /raffle — weighted random pick ───────────────────────────────
async def cmd_raffle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update.effective_user.id):
        return
    args = context.args or []
    pick_n = 1
    days = 30
    for a in args:
        if a.isdigit():
            v = int(a)
            if v <= 10: pick_n = v
            else: days = min(v, 365)

    from shared.database import get_session
    from sqlalchemy import text as _t
    async with get_session() as s:
        r = await s.execute(_t("""
            SELECT u.telegram_id, u.username, u.first_name,
                   COALESCE(SUM(p.amount), 0) AS total
            FROM payments p
            JOIN users u ON u.id = p.user_id
            WHERE p.status='APPROVED'
              AND p.created_at >= NOW() - (:d || ' days')::interval
            GROUP BY u.id, u.telegram_id, u.username, u.first_name
            HAVING SUM(p.amount) > 0
        """), {"d": days})
        candidates = [dict(x._mapping) for x in r.fetchall()]

    if not candidates:
        await update.message.reply_text("ไม่มี candidate")
        return

    # Weighted random by total spend
    import random
    weights = [float(c["total"]) for c in candidates]
    picks = []
    pool = list(zip(candidates, weights))
    for _ in range(min(pick_n, len(pool))):
        cands, w = zip(*pool)
        chosen = random.choices(cands, weights=w, k=1)[0]
        picks.append(chosen)
        pool = [(c, ww) for c, ww in pool if c["telegram_id"] != chosen["telegram_id"]]
        if not pool: break

    text = f"<b>🎉 Raffle Winners ({days} วัน, n={pick_n})</b>\n\n"
    for i, w in enumerate(picks, 1):
        name = w.get("first_name") or f"id={w['telegram_id']}"
        uname = f" @{w['username']}" if w.get("username") else ""
        text += (
            f"<b>{i}.</b> {name}{uname}\n"
            f"   🆔 <code>{w['telegram_id']}</code> | spend {_fmt_thb(w['total'])}\n"
        )
    text += f"\n<i>เลือกแบบถ่วงน้ำหนัก: ใครจ่ายมากกว่า โอกาสมากกว่า</i>"

    await update.message.reply_text(text, parse_mode="HTML")

    try:
        from shared.utils import log_admin_action
        await log_admin_action(
            admin_id=update.effective_user.id,
            action="raffle_draw",
            details=f"days={days} winners={[w['telegram_id'] for w in picks]}"
        )
    except Exception: pass


async def cmd_welcome_stats(update, context):
    """แสดงสถิติ welcome journey stages — เปรียบเทียบ stage 0/1/2/3 ตัวไหนทำให้ลูกค้าซื้อมากสุด."""
    if not _is_admin(update.effective_user.id):
        return
    args = context.args or []
    days = 30
    for a in args:
        if a.isdigit(): days = min(int(a), 365)

    from shared.database import get_session
    from sqlalchemy import text as _t
    async with get_session() as s:
        r = await s.execute(_t(
            "SELECT round, "
            "       COUNT(*) AS sent, "
            "       SUM(CASE WHEN responded THEN 1 ELSE 0 END) AS responded, "
            "       SUM(CASE WHEN purchased THEN 1 ELSE 0 END) AS purchased "
            "FROM comeback_dm_log "
            "WHERE sent_at >= NOW() - (:d || \' days\')::interval "
            "  AND round BETWEEN 301 AND 304 "
            "GROUP BY round ORDER BY round"
        ), {"d": str(days)})
        rows = [dict(x._mapping) for x in r.fetchall()]

    if not rows:
        await update.message.reply_text(
            f"⚠️ ยังไม่มีข้อมูล welcome journey ใน {days} วันล่าสุด\n"
            f"<i>ระบบเริ่มทำงานเมื่อ 19:00 วันนี้ — รอเก็บข้อมูล 24-48 ชม.</i>",
            parse_mode="HTML",
        )
        return

    STAGE_NAMES = {
        301: ("👋 Stage 0", "ทักทันที (เมื่อ /start)"),
        302: ("⏰ Stage 1", "3 ชม. หลัง /start"),
        303: ("📊 Stage 2", "12 ชม. หลัง /start"),
        304: ("🚨 Stage 3", "23 ชม. (ชั่วโมงสุดท้าย)"),
    }

    total_sent = sum(r["sent"] for r in rows)
    total_bought = sum(r["purchased"] for r in rows)
    overall_cr = (total_bought / total_sent * 100) if total_sent else 0
    best_cr = max((r["purchased"] / max(r["sent"], 1) for r in rows), default=0)

    text = f"<b>📊 Welcome Journey — {days} วัน</b>\n"
    text += f"━━━━━━━━━━\n"
    text += f"<i>เปรียบเทียบ stage ไหนทำให้ลูกค้าซื้อมากสุด</i>\n\n"

    for row in rows:
        rn = row["round"]
        sent = row["sent"]; bought = row["purchased"]
        cr = (bought / sent * 100) if sent else 0
        is_winner = (bought / max(sent, 1) == best_cr) and bought > 0
        crown = " 🥇 BEST" if is_winner else ""

        name, when = STAGE_NAMES.get(rn, (f"Stage {rn}", ""))
        text += f"<b>{name}</b>{crown}\n"
        text += f"   <i>{when}</i>\n"
        text += f"   📨 ส่งไป: <b>{sent}</b> คน\n"
        text += f"   💰 ซื้อ: <b>{bought}</b> คน (<b>{cr:.1f}%</b>)\n\n"

    text += f"━━━━━━━━━━\n"
    text += f"📊 <b>รวม:</b> ส่ง {total_sent} ซื้อ {total_bought} = <b>{overall_cr:.1f}%</b>\n\n"
    text += f"<i>💡 ส่ง /welcome_stats 7 เพื่อดู 7 วันล่าสุด</i>"

    await update.message.reply_text(text, parse_mode="HTML")


def get_admin_tools_handlers():
    return [
        CommandHandler("find", cmd_find),
        CallbackQueryHandler(cb_find_view, pattern=r"^find_view:\d+$"),
        CallbackQueryHandler(cb_find_back, pattern=r"^find_back$"),
        CommandHandler("auditlog", cmd_auditlog),
        CommandHandler("topspenders", cmd_topspenders),
        CommandHandler("raffle", cmd_raffle),
        CommandHandler("abtest", cmd_abtest),
        CommandHandler("welcome_stats", cmd_welcome_stats),
    ]


__all__ = [
    "cmd_find", "cmd_auditlog", "cmd_topspenders", "cmd_raffle",
    "get_admin_tools_handlers",
]


# A/B Testing report ===================================================
async def cmd_abtest(update, context):
    if not _is_admin(update.effective_user.id):
        return
    args = context.args or []
    days = 30
    for a in args:
        if a.isdigit(): days = min(int(a), 365)

    from shared.database import get_session
    from sqlalchemy import text as _t
    async with get_session() as s:
        r = await s.execute(_t(
            "SELECT variant, round, "
            "       COUNT(*) AS sent, "
            "       SUM(CASE WHEN responded THEN 1 ELSE 0 END) AS responded, "
            "       SUM(CASE WHEN purchased THEN 1 ELSE 0 END) AS purchased "
            "FROM comeback_dm_log "
            "WHERE sent_at >= NOW() - (:d || \" days\")::interval "
            "  AND variant IS NOT NULL "
            "GROUP BY variant, round "
            "ORDER BY round, variant"
        ), {"d": days})
        rows = [dict(x._mapping) for x in r.fetchall()]

    if not rows:
        await update.message.reply_text(f"⚠️ No A/B data in last {days} days")
        return

    by_round = {}
    for row in rows:
        by_round.setdefault(row["round"] or 1, []).append(row)

    text = f"<b>\U0001F4CA A/B Test Report ({days} days)</b>\n\n"
    for rn, vs in sorted(by_round.items()):
        text += f"<b>Round {rn}</b>\n"
        total_sent = sum(v["sent"] for v in vs)
        best_cr = max((float(v["purchased"]) / max(v["sent"], 1) for v in vs), default=0)
        for v in sorted(vs, key=lambda x: -float(x["purchased"]) / max(x["sent"], 1)):
            sent = v["sent"]; resp = v["responded"]; bought = v["purchased"]
            cr = (bought / sent * 100) if sent else 0
            rr = (resp / sent * 100) if sent else 0
            share = (sent / total_sent * 100) if total_sent else 0
            is_winner = (bought / max(sent, 1) == best_cr) and bought > 0
            mark = "\U0001F947 " if is_winner else "   "
            vname = v["variant"]
            text += f"{mark}<b>{vname}</b> sent={sent} ({share:.0f}%) | resp {resp} ({rr:.1f}%) | buy {bought} <b>({cr:.2f}%)</b>\n"
        text += "\n"
    text += "<i>winner = highest conversion rate per round</i>"
    await update.message.reply_text(text, parse_mode="HTML")


