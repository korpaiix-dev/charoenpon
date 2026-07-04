# -*- coding: utf-8 -*-
"""Re-invite customers WRONGLY kicked by the guardian bug (kicked in last 3 days but still hold an
active subscription). Sends each an apology + fresh one-time invite links to the groups their
active subs cover. Idempotent-ish: skips users no longer missing (best-effort). DRY-RUN by default.
  python reinvite_wrongly_kicked.py            # dry-run: list only
  python reinvite_wrongly_kicked.py --send      # actually DM apology + links
"""
import asyncio, os, argparse
from telegram import Bot
from sqlalchemy import text as T
from shared.database import get_session
from bots.guardian_bot.group_monitor import generate_invite_links_for_user
from shared.customer_dm import send_invite_links_dm

APOLOGY = (
    "🙏 <b>ขออภัยอย่างสูงครับ</b>\n\n"
    "ระบบของเราตรวจพบข้อผิดพลาด — บัญชีของคุณถูกนำออกจากกลุ่มโดยไม่ได้ตั้งใจ "
    "ทั้งที่สิทธิ์สมาชิกของคุณยัง <b>ใช้งานได้ตามปกติ</b> ครับ\n\n"
    "ทีมงานแก้ไขระบบเรียบร้อยแล้ว และจะไม่เกิดเหตุแบบนี้อีก 🙏\n"
    "ด้านล่างนี้คือลิงก์เข้ากลุ่มทั้งหมดของคุณ — กดเข้าได้ทุกห้องเลยครับ 👇"
)

SQL = """
SELECT DISTINCT al.target_id AS user_id, u.telegram_id, u.first_name,
  (SELECT max(s.package_id) FROM subscriptions s
     WHERE s.user_id=al.target_id AND s.status='ACTIVE' AND s.end_date>now()) AS best_pkg
FROM admin_logs al JOIN users u ON u.id=al.target_id
WHERE al.action='kick_expired' AND al.created_at > now()-interval '3 days'
  AND EXISTS (SELECT 1 FROM subscriptions s WHERE s.user_id=al.target_id AND s.status='ACTIVE' AND s.end_date>now())
ORDER BY u.telegram_id
"""


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--send", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--offset", type=int, default=0)
    a = ap.parse_args()

    async with get_session() as s:
        rows = (await s.execute(T(SQL))).fetchall()
        if a.offset: rows = rows[a.offset:]
        if a.limit: rows = rows[:a.limit]
        # slug -> display title
        grs = (await s.execute(T("SELECT slug::text AS slug, COALESCE(title, slug::text) AS title FROM group_registry WHERE is_active=true"))).fetchall()
        title_map = {r._mapping["slug"]: r._mapping["title"] for r in grs}

    print("wrongly-kicked (kicked <=3d, still active): %d" % len(rows))
    if not a.send:
        for r in rows[:8]:
            m = r._mapping
            print("  tg=%s name=%s best_pkg=%s" % (m["telegram_id"], m["first_name"], m["best_pkg"]))
        print("DRY-RUN — nothing sent. Run with --send to DM apology + links.")
        return

    bot = Bot(os.environ.get("GUARDIAN_BOT_TOKEN", ""))
    await bot.initialize()
    sent = 0
    for r in rows:
        m = r._mapping
        tg = int(m["telegram_id"]); pkg = m["best_pkg"]
        try:
            links = await generate_invite_links_for_user(bot, tg, pkg)  # dict {slug: url}
            pairs = [(title_map.get(slug, slug), url) for slug, url in links.items()]
            if not pairs:
                print("  no links for tg=%s — skip" % tg); continue
            ok = await send_invite_links_dm(tg, m["first_name"], "สมาชิกของคุณ", pairs, extra_top_text=APOLOGY)
            if ok:
                sent += 1
                print("  re-invited tg=%s (%d groups)" % (tg, len(pairs)))
            else:
                print("  DM failed (blocked?) tg=%s" % tg)
        except Exception as exc:
            print("  ERROR tg=%s: %s" % (tg, exc))
        await asyncio.sleep(0.7)  # gentle
    print("DONE re-invited=%d / %d" % (sent, len(rows)))

asyncio.run(main())
