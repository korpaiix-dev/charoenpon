#!/usr/bin/env python3
"""Daily expiry report — sends summary to admin group.

Runs daily via cron. Pulls from postgres + Telegram-sends HTML report.

Env required: ADMIN_BOT_TOKEN (or NAMWAN_TOKEN), ADMIN_GROUP_CHAT_ID, DB_*
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from datetime import datetime, timezone, timedelta

import asyncpg
import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("daily_expiry_report")

from shared.tz import TH_TZ
from shared.admin_alert import _admin_group_id

BOT_TOKEN = os.environ.get("ADMIN_BOT_TOKEN") or os.environ.get("NAMWAN_TOKEN") or os.environ.get("GUARDIAN_BOT_TOKEN")
ADMIN_GROUP_ID = _admin_group_id()

DB_HOST = os.environ.get("DB_HOST", "localhost")
DB_PORT = int(os.environ.get("DB_PORT", "5432"))
DB_USER = os.environ.get("DB_USER", "postgres")
DB_PASSWORD = os.environ.get("DB_PASSWORD") or os.environ.get("POSTGRES_PASSWORD", "")
DB_NAME = os.environ.get("DB_NAME", "charoenpon")


async def collect_stats() -> dict:
    conn = await asyncpg.connect(host=DB_HOST, port=DB_PORT, user=DB_USER, password=DB_PASSWORD, database=DB_NAME)
    try:
        expired_today = await conn.fetchval(
            "SELECT COUNT(*) FROM subscriptions WHERE end_date::DATE = (NOW() AT TIME ZONE 'Asia/Bangkok')::DATE"
        )
        active_total = await conn.fetchval("SELECT COUNT(*) FROM subscriptions WHERE status='ACTIVE'")
        expired_total = await conn.fetchval("SELECT COUNT(*) FROM subscriptions WHERE status='EXPIRED'")

        dm_stats = await conn.fetch(
            """SELECT notification_type::text AS t, COUNT(*) AS c,
                      COUNT(*) FILTER (WHERE acknowledged) AS acked
               FROM expiry_notifications
               WHERE sent_at >= NOW() - INTERVAL '24 hours'
               GROUP BY 1"""
        )

        next_7d = await conn.fetch(
            """SELECT TO_CHAR(end_date AT TIME ZONE 'Asia/Bangkok', 'YYYY-MM-DD') AS day,
                      COUNT(*) AS c
               FROM subscriptions
               WHERE status='ACTIVE' AND end_date BETWEEN NOW() AND NOW() + INTERVAL '7 days'
               GROUP BY 1 ORDER BY 1"""
        )

        expired_today_list = await conn.fetch(
            """SELECT COALESCE(u.first_name, '?') AS name,
                      COALESCE(u.username, '') AS username,
                      pk.name AS pkg,
                      TO_CHAR(s.end_date AT TIME ZONE 'Asia/Bangkok', 'HH24:MI') AS at
               FROM subscriptions s
               LEFT JOIN users u ON u.id = s.user_id
               LEFT JOIN packages pk ON pk.id = s.package_id
               WHERE s.end_date::DATE = (NOW() AT TIME ZONE 'Asia/Bangkok')::DATE
               ORDER BY s.end_date"""
        )

        return {
            "expired_today": expired_today,
            "active_total": active_total,
            "expired_total": expired_total,
            "dm_stats": [dict(r) for r in dm_stats],
            "next_7d": [dict(r) for r in next_7d],
            "expired_today_list": [dict(r) for r in expired_today_list],
        }
    finally:
        await conn.close()


def format_report(stats: dict) -> str:
    today = datetime.now(TH_TZ).strftime("%Y-%m-%d")
    lines = [
        f"📅 <b>Daily Expiry Report — {today}</b>",
        "",
        f"❌ <b>หมดอายุวันนี้</b>: {stats['expired_today']} คน",
        f"🟢 Active: {stats['active_total']} | 🔴 Expired total: {stats['expired_total']}",
        "",
    ]

    if stats["expired_today_list"]:
        lines.append("<b>รายชื่อหมดวันนี้:</b>")
        for row in stats["expired_today_list"]:
            name = row["name"]
            user_tag = f"@{row['username']}" if row["username"] else ""
            lines.append(f"  • {name} {user_tag} — {row['pkg']} ({row['at']})")
        lines.append("")

    if stats["dm_stats"]:
        lines.append("📨 <b>DM ส่งใน 24 ชม.</b>")
        for row in stats["dm_stats"]:
            ack_pct = (row["acked"] * 100 / row["c"]) if row["c"] else 0
            lines.append(f"  {row['t']}: sent={row['c']} acked={row['acked']} ({ack_pct:.0f}%)")
        lines.append("")
    else:
        lines.append("📨 ไม่มี DM ส่งใน 24 ชม.ที่ผ่านมา")
        lines.append("")

    if stats["next_7d"]:
        lines.append("📆 <b>จะหมดอายุใน 7 วันข้างหน้า:</b>")
        total_7d = 0
        for row in stats["next_7d"]:
            lines.append(f"  {row['day']}: {row['c']} คน")
            total_7d += row["c"]
        lines.append(f"  <b>รวม: {total_7d} คน</b>")

    return "\n".join(lines)


def send_telegram(text: str) -> bool:
    if not BOT_TOKEN:
        log.error("No bot token in env")
        return False
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    r = requests.post(url, json={
        "chat_id": ADMIN_GROUP_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }, timeout=15)
    payload = r.json()
    if not payload.get("ok"):
        log.error("Telegram send failed: %s", payload)
        return False
    return True


async def main() -> int:
    try:
        stats = await collect_stats()
    except Exception as e:
        log.exception("DB query failed: %s", e)
        return 2
    report = format_report(stats)
    log.info("Report:\n%s", report)
    ok = send_telegram(report)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
