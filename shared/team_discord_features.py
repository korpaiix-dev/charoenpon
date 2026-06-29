"""Discord team gimmicks — Morning Briefing, Weekly MVP, Login Streak, /task, Decision Helper.

Used by guardian-bot scheduler + discord-bot listener.
"""
from __future__ import annotations
import datetime as dt
import logging
from typing import Optional

from sqlalchemy import text as sql_text

from shared.database import get_session

logger = logging.getLogger(__name__)


# =========================================================
# 1. Morning Briefing — runs every day 09:00 BKK in #รายงานประจำวัน
# =========================================================
async def morning_briefing() -> str:
    """Generate morning briefing text."""
    bkk_today = "(now() AT TIME ZONE 'Asia/Bangkok')::date"
    bkk_yest = f"{bkk_today} - 1"
    pay_bkk = "(p.created_at AT TIME ZONE 'UTC') AT TIME ZONE 'Asia/Bangkok'"

    async with get_session() as s:
        # Yesterday revenue
        r = await s.execute(sql_text(f"""
            SELECT COUNT(*) AS cnt, COALESCE(SUM(p.amount),0)::int AS rev
            FROM payments p JOIN users u ON u.id = p.user_id
            WHERE p.status='CONFIRMED' AND p.amount > 0
              AND u.telegram_id < 9000000000
              AND ({pay_bkk})::date = {bkk_yest}
        """))
        y_row = r.fetchone()
        # 7-day avg for comparison
        r2 = await s.execute(sql_text(f"""
            SELECT COALESCE(AVG(daily), 0)::int AS avg7
            FROM (
                SELECT ({pay_bkk})::date AS d, SUM(p.amount) AS daily
                FROM payments p JOIN users u ON u.id = p.user_id
                WHERE p.status='CONFIRMED' AND p.amount > 0
                  AND u.telegram_id < 9000000000
                  AND ({pay_bkk})::date BETWEEN {bkk_today} - 8 AND {bkk_today} - 2
                GROUP BY d
            ) sub
        """))
        avg7 = int((r2.fetchone() or [0])[0] or 0)

        # Active subs
        r3 = await s.execute(sql_text("""
            SELECT COUNT(*) FROM subscriptions s WHERE s.status='ACTIVE'
              AND s.end_date > NOW()
        """))
        active_subs = int(r3.scalar() or 0)

        # Pending slips
        r4 = await s.execute(sql_text("SELECT COUNT(*) FROM payments WHERE status='PENDING'"))
        pending = int(r4.scalar() or 0)

    rev = int(y_row.rev or 0)
    cnt = int(y_row.cnt or 0)
    delta = ""
    if avg7 > 0:
        pct = (rev - avg7) / avg7 * 100
        if pct >= 5:
            delta = f" 📈 (▲ {pct:.0f}% vs avg)"
        elif pct <= -5:
            delta = f" 📉 (▼ {abs(pct):.0f}% vs avg)"

    # Greeting variants based on weekday (BKK)
    now_bkk = dt.datetime.utcnow() + dt.timedelta(hours=7)
    weekday = now_bkk.weekday()  # 0=Mon
    greetings = [
        "☀️ อรุณสวัสดิ์ทีมเจริญพร! เริ่มสัปดาห์ใหม่ลุยกันเลย",
        "☀️ สวัสดีตอนเช้า อังคารวันแห่งการเริ่ม",
        "☀️ พุธกลางสัปดาห์ ทุกคนสู้ๆ นะคะ",
        "☀️ พฤหัสใกล้จะถึงวันหยุดแล้วววว",
        "☀️ ศุกร์ละ! อีกแค่วันเดียว 🎉",
        "☀️ เสาร์สบายๆ เจริญพรไม่หลับ",
        "☀️ อาทิตย์เริ่มต้นใหม่ ใจเย็นๆ",
    ]
    greet = greetings[weekday]

    lines = [
        f"{greet}",
        "",
        f"💰 **เมื่อวาน:** ฿{rev:,} จาก {cnt} order{delta}",
        f"🔥 **Active subs:** {active_subs} คน",
    ]
    if pending > 0:
        lines.append(f"⚠️  **Pending slips:** {pending} ใบ — ตรวจหน่อยนะ")
    
    return "\n".join(lines)


# =========================================================
# 2. Weekly MVP — runs Friday 18:00 BKK
# =========================================================
async def weekly_mvp() -> str:
    """Compute weekly MVP from marketing performance.

    Uses ``shared.marketing_stats.stats_weekly_mvp`` so the leaderboard
    numbers stay consistent with Discord notify and Dashboard ROI.
    """
    from shared.marketing_stats import stats_weekly_mvp, list_active_marketers

    marketers = await list_active_marketers()
    rows = []
    for m in marketers:
        try:
            s = await stats_weekly_mvp(m)
        except Exception:
            continue
        rows.append(s)

    # Sort: revenue DESC, conversions DESC, joins DESC
    rows.sort(key=lambda s: (s.revenue_thb, s.conversions, s.joins), reverse=True)

    if not rows or all(s.revenue_thb == 0 and s.joins == 0 for s in rows):
        return ("🎉 **WEEKLY WRAP-UP** 🎉\n\nสัปดาห์นี้ทีมยังไม่มีการ track เลย — สู้ๆ สัปดาห์หน้านะคะ! 💕")

    winner = rows[0]
    medals = ["🥇", "🥈", "🥉"]
    lines = [
        "🎉 **WEEKLY MVP** — รายงานสัปดาห์",
        "─" * 25,
        "",
    ]
    for i, s in enumerate(rows):
        medal = medals[i] if i < 3 else f"{i+1}."
        rate = (s.conversions / s.joins * 100) if s.joins else 0
        lines.append(
            f"{medal} **{s.marketer}** — ฿{s.revenue_thb:,.0f} "
            f"({s.conversions}/{s.joins} → {rate:.0f}%)"
        )
    lines.append("")
    if winner.revenue_thb > 0 or winner.joins > 0:
        lines.append(f"🏆 MVP สัปดาห์: **{winner.marketer}** เก่งมาก! ทีมปรบมือเลย 👏✨")
    lines.append("")
    lines.append("สู้สัปดาห์หน้านะทุกคน 💪💕")
    return "\n".join(lines)


# =========================================================
# 3. Login Streak — ping each Monday 09:30 BKK in #รายงานประจำวัน
# =========================================================
async def record_user_seen(discord_user_id: int, discord_user_name: str) -> None:
    """Called whenever a user sends a message in any channel — updates streak."""
    today = dt.date.today()
    try:
        async with get_session() as s:
            row = (await s.execute(sql_text(
                "SELECT current_streak, longest_streak, last_seen_date FROM discord_login_streak WHERE discord_user_id = :u"
            ), {"u": discord_user_id})).first()
            if row is None:
                await s.execute(sql_text(
                    "INSERT INTO discord_login_streak (discord_user_id, discord_user_name, current_streak, longest_streak, last_seen_date) "
                    "VALUES (:u, :n, 1, 1, :d)"
                ), {"u": discord_user_id, "n": discord_user_name, "d": today})
            else:
                if row.last_seen_date == today:
                    return  # already counted today
                delta = (today - row.last_seen_date).days
                if delta == 1:
                    new_streak = row.current_streak + 1
                elif delta > 1:
                    new_streak = 1  # broke streak
                else:
                    return
                new_longest = max(row.longest_streak, new_streak)
                await s.execute(sql_text(
                    "UPDATE discord_login_streak SET current_streak = :c, longest_streak = :lg, "
                    "last_seen_date = :d, discord_user_name = :n, updated_at = now() "
                    "WHERE discord_user_id = :u"
                ), {"c": new_streak, "lg": new_longest, "d": today, "n": discord_user_name, "u": discord_user_id})
            await s.commit()
    except Exception as exc:
        logger.warning("record_user_seen failed: %s", exc)


async def streak_ranking_text() -> str:
    """Generate streak ranking for #รายงานประจำวัน (Monday morning)."""
    async with get_session() as s:
        rows = (await s.execute(sql_text("""
            SELECT discord_user_name, current_streak, longest_streak, last_seen_date
            FROM discord_login_streak
            WHERE last_seen_date >= CURRENT_DATE - 7
            ORDER BY current_streak DESC, longest_streak DESC
            LIMIT 10
        """))).fetchall()

    if not rows:
        return None

    def streak_emoji(n):
        if n >= 100: return "👑"
        if n >= 30: return "💎"
        if n >= 7: return "🔥🔥🔥"
        if n >= 3: return "🔥"
        return "✨"

    medals = ["🥇", "🥈", "🥉"]
    lines = ["🔥 **STREAK RANKING** — ใครเข้าทีมทุกวันสุดๆ", "─" * 25, ""]
    for i, r in enumerate(rows[:10]):
        medal = medals[i] if i < 3 else f"{i+1}."
        em = streak_emoji(r.current_streak)
        lines.append(f"{medal} **{r.discord_user_name}** — {r.current_streak} วัน {em}")
    lines.append("")
    lines.append("ทีมเจริญพร — ลุย! 💪")
    return "\n".join(lines)


# =========================================================
# 4. /task — handled in discord_bot directly  (see discord_bot/main.py)
# =========================================================
async def task_add(discord_user_id: int, discord_user_name: str, channel_id: int, text_body: str) -> dict:
    """Add a task for a user."""
    text_body = (text_body or "").strip()
    if not text_body:
        return {"error": "task text empty"}
    try:
        async with get_session() as s:
            r = await s.execute(sql_text(
                "INSERT INTO team_tasks (discord_user_id, discord_user_name, text, channel_id) "
                "VALUES (:u, :n, :t, :c) RETURNING id, created_at"
            ), {"u": discord_user_id, "n": discord_user_name, "t": text_body, "c": channel_id})
            row = r.first()
            await s.commit()
        return {"ok": True, "id": row.id, "text": text_body}
    except Exception as exc:
        return {"error": str(exc)[:200]}


async def task_list(discord_user_id: int, include_done: bool = False) -> list[dict]:
    """List tasks for a user."""
    cond = "" if include_done else "AND is_done = false"
    async with get_session() as s:
        rows = (await s.execute(sql_text(f"""
            SELECT id, text, is_done, created_at, due_at, completed_at
            FROM team_tasks
            WHERE discord_user_id = :u {cond}
            ORDER BY is_done, COALESCE(due_at, created_at)
            LIMIT 30
        """), {"u": discord_user_id})).fetchall()
    return [
        {"id": r.id, "text": r.text, "done": r.is_done,
         "created_at": r.created_at.isoformat() if r.created_at else None,
         "due_at": r.due_at.isoformat() if r.due_at else None}
        for r in rows
    ]


async def task_done(discord_user_id: int, task_id: int) -> dict:
    async with get_session() as s:
        r = await s.execute(sql_text(
            "UPDATE team_tasks SET is_done = true, completed_at = now() "
            "WHERE id = :i AND discord_user_id = :u AND is_done = false RETURNING id, text"
        ), {"i": int(task_id), "u": discord_user_id})
        row = r.first()
        await s.commit()
    if not row:
        return {"error": f"task {task_id} not found or not yours"}
    return {"ok": True, "id": row.id, "text": row.text}


async def task_delete(discord_user_id: int, task_id: int) -> dict:
    async with get_session() as s:
        r = await s.execute(sql_text(
            "DELETE FROM team_tasks WHERE id = :i AND discord_user_id = :u RETURNING id"
        ), {"i": int(task_id), "u": discord_user_id})
        row = r.first()
        await s.commit()
    if not row:
        return {"error": f"task {task_id} not found"}
    return {"ok": True, "id": row.id}
