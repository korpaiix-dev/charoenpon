"""Keyword Moderation for Announce Bot - บริษัทเจริญพร.

ดักจับ keyword ต้องห้ามในกลุ่มหลัก → แบนถาวร + ลบข้อความทันที
ส่งแจ้งเตือนกลุ่ม Admin + Discord
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone

import httpx

logger = logging.getLogger(__name__)

TH_TZ = timezone(timedelta(hours=7))

KEYWORD_BLACKLIST = [
    "เด็ก", "ผู้เยาว์", "นักเรียน", "มัธยม", "ประถม", "อายุต่ำกว่า", "ไม่บรรลุนิติภาวะ",
    "minor", "underage", "child", "loli", "shota", "jailbait",
    "ยาบ้า", "ไอซ์", "โคเคน", "เฮโรอีน", "ยาเค", "ยาอี",
    "meth", "cocaine", "heroin", "fentanyl",
    "ปืน", "ระเบิด", "อาวุธสงคราม", "ปืนพก",
    "gun", "bomb", "explosive", "weapon",
    "แอบถ่าย", "ไม่ยินยอม", "ข่มขืน",
    "rape", "non-consent", "hidden cam",
    "ลักพาตัว", "ค้ามนุษย์", "trafficking", "kidnap",
    "หน้าไบโอ", "หน้า bio", "หน้าโปรไฟล์", "หน้าไบโo",
    "สนทัก", "dm ได้เลย", "inbox ได้เลย",
]

# Global ban counter for today
_ban_count_date: str = ""
_ban_count: int = 0


def _today_str() -> str:
    return datetime.now(TH_TZ).strftime("%Y-%m-%d")


def increment_ban_count() -> int:
    global _ban_count_date, _ban_count
    today = _today_str()
    if _ban_count_date != today:
        _ban_count_date = today
        _ban_count = 0
    _ban_count += 1
    return _ban_count


def get_ban_count() -> int:
    global _ban_count_date, _ban_count
    if _ban_count_date != _today_str():
        return 0
    return _ban_count


def find_matched_keyword(text: str) -> str | None:
    """Return the first matched blacklisted keyword (case-insensitive), or None."""
    lower = text.lower()
    for kw in KEYWORD_BLACKLIST:
        if kw.lower() in lower:
            return kw
    return None


async def send_discord_alert(content: str) -> None:
    """Send alert to DISCORD_CH_ALERTS."""
    token = os.environ.get("DISCORD_BOT_TOKEN", "")
    ch = os.environ.get("DISCORD_CH_ALERTS", "")
    if not token or not ch:
        return
    try:
        now_th = datetime.now(TH_TZ)
        embed = {
            "title": "🚨 Moderation — Announce Bot",
            "description": content[:4096],
            "color": 0xE67E22,
            "footer": {"text": f"⊙ เจริญพร | {now_th.strftime('%d/%m/%Y %H:%M')}"},
        }
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(
                f"https://discord.com/api/v10/channels/{ch}/messages",
                headers={
                    "Authorization": f"Bot {token}",
                    "Content-Type": "application/json",
                },
                json={"embeds": [embed]},
            )
    except Exception as exc:
        logger.warning("Discord alert failed: %s", exc)


def build_ban_alert_text(
    username: str | None,
    user_id: int,
    message_text: str,
    matched_keyword: str,
    ban_count: int,
) -> str:
    """สร้างข้อความแจ้งเตือนการแบน."""
    now_th = datetime.now(TH_TZ).strftime("%d/%m/%Y %H:%M น.")
    uname = f"@{username}" if username else "(ไม่มี username)"
    short_msg = message_text[:100] + ("..." if len(message_text) > 100 else "")
    return (
        "🚨 ตรวจพบและแบนผู้ใช้\n\n"
        f"👤 User: {uname} ({user_id})\n"
        f"💬 ข้อความ: {short_msg}\n"
        f"🔑 Keyword: {matched_keyword}\n"
        f"⏰ เวลา: {now_th}\n"
        f"📊 แบนวันนี้: {ban_count} ราย\n\n"
        "━━━━━━━━━━━━━━━\n"
        "🤖 Announce Bot (เจริญพร)"
    )
