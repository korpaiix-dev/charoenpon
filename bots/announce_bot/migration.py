"""Migration handler for Announce Bot - บริษัทเจริญพร.

จัดการ /newgroup command:
- โพสต์ประกาศในกลุ่มกลาง
- แจ้งเตือน Admin
- บันทึก DB
- ส่ง Discord alerts
- บันทึก Google Sheets (optional)
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone

import httpx

from shared.database import get_session
from shared.models import GroupMigration

logger = logging.getLogger(__name__)

TH_TZ = timezone(timedelta(hours=7))

ANNOUNCE_TEXT_TEMPLATE = (
    "📢 แจ้งย้ายกลุ่ม\n\n"
    "🔴 กลุ่มเดิมใช้งานไม่ได้แล้ว\n\n"
    "✅ กลุ่มใหม่พร้อมแล้ว!\n"
    "👇 กดลิ้งด้านล่างเพื่อเข้ากลุ่มได้เลย\n\n"
    "🔗 {link}\n\n"
    "━━━━━━━━━━━━━━━\n"
    "📌 เซฟลิ้งนี้ไว้ก่อนนะครับ\n"
    "━━━━━━━━━━━━━━━\n"
    "🏢 เจริญพร Official"
)


async def post_new_group_announcement(
    bot,
    new_link: str,
    created_by: int,
    old_group_id: int | None = None,
) -> dict:
    """โพสต์ประกาศย้ายกลุ่มและบันทึกทุกที่.

    Returns:
        dict with keys: success, announce_message_id, db_id, errors
    """
    result = {"success": False, "announce_message_id": None, "db_id": None, "errors": []}

    tg_announce_ids: list[int] = [
        int(os.environ[k])
        for k in ("TG_GROUP_ANNOUNCE_1", "TG_GROUP_ANNOUNCE_2")
        if os.environ.get(k, "").lstrip("-").isdigit()
    ]
    tg_admin = int(os.environ.get("TG_GROUP_ADMIN", "0"))

    announce_text = ANNOUNCE_TEXT_TEMPLATE.format(link=new_link)

    # 1. โพสต์ในกลุ่มกลางทั้ง 2 กลุ่ม
    if not tg_announce_ids:
        err = "No TG_GROUP_ANNOUNCE_1/2 configured"
        logger.error(err)
        result["errors"].append(err)
        return result

    for group_id in tg_announce_ids:
        try:
            msg = await bot.send_message(chat_id=group_id, text=announce_text)
            if result["announce_message_id"] is None:
                result["announce_message_id"] = msg.message_id
            logger.info("Posted migration announcement in group %s, msg_id=%s", group_id, msg.message_id)
        except Exception as exc:
            err = f"Failed to post in group {group_id}: {exc}"
            logger.error(err)
            result["errors"].append(err)

    # 2. แจ้งเตือน Admin
    if tg_admin:
        try:
            now_th = datetime.now(TH_TZ).strftime("%d/%m/%Y %H:%M น.")
            admin_msg = (
                f"✅ โพสต์ประกาศย้ายกลุ่มแล้ว\n\n"
                f"🔗 ลิ้งใหม่: {new_link}\n"
                f"⏰ เวลา: {now_th}\n"
                f"👤 สั่งโดย: {created_by}"
            )
            await bot.send_message(chat_id=tg_admin, text=admin_msg)
        except Exception as exc:
            logger.warning("Could not notify admin group: %s", exc)
            result["errors"].append(f"Admin notify failed: {exc}")

    # 3. บันทึก DB
    try:
        migration = GroupMigration(
            old_group_id=old_group_id,
            new_group_link=new_link,
            created_by=created_by,
        )
        async with get_session() as session:
            session.add(migration)
            await session.flush()
            await session.refresh(migration)
            result["db_id"] = migration.id
        logger.info("Saved migration to DB, id=%s", result["db_id"])
    except Exception as exc:
        err = f"DB save failed: {exc}"
        logger.error(err)
        result["errors"].append(err)

    # 4. Discord alerts
    await _send_discord_migration_alert(new_link, created_by)

    # 5. Google Sheets (optional)
    await _log_to_sheets(new_link, created_by)

    result["success"] = True
    return result


async def _send_discord_migration_alert(new_link: str, created_by: int) -> None:
    """ส่ง Discord alert ไปที่ DISCORD_CH_ALERTS และ DISCORD_CH_GROWTH_INSIGHTS."""
    token = os.environ.get("DISCORD_BOT_TOKEN", "")
    if not token:
        return

    channels = []
    ch_alerts = os.environ.get("DISCORD_CH_ALERTS", "")
    ch_growth = os.environ.get("DISCORD_CH_GROWTH_INSIGHTS", "")
    if ch_alerts:
        channels.append(ch_alerts)
    if ch_growth and ch_growth != ch_alerts:
        channels.append(ch_growth)

    now_th = datetime.now(TH_TZ).strftime("%d/%m/%Y %H:%M น.")
    content = (
        f"📢 **แจ้งย้ายกลุ่ม — เจริญพร**\n"
        f"🔗 ลิ้งใหม่: {new_link}\n"
        f"⏰ {now_th}\n"
        f"👤 สั่งโดย: {created_by}"
    )

    now_th = datetime.now(TH_TZ)
    embed = {
        "title": "📢 ย้ายกลุ่ม — Announce Bot",
        "description": content,
        "color": 0x2ECC71,
        "footer": {"text": f"⊙ เจริญพร | {now_th.strftime('%d/%m/%Y %H:%M')}"},
    }
    async with httpx.AsyncClient(timeout=10) as client:
        for ch in channels:
            try:
                await client.post(
                    f"https://discord.com/api/v10/channels/{ch}/messages",
                    headers={
                        "Authorization": f"Bot {token}",
                        "Content-Type": "application/json",
                    },
                    json={"embeds": [embed]},
                )
            except Exception as exc:
                logger.warning("Discord alert to %s failed: %s", ch, exc)


async def _log_to_sheets(new_link: str, created_by: int) -> None:
    """บันทึกลง Google Sheets sheet 'Group Migrations' ถ้ามี credentials."""
    creds_json = os.environ.get("GOOGLE_SHEETS_CREDENTIALS", "")
    sheets_id = os.environ.get("GOOGLE_SHEETS_ID", "")
    if not creds_json or not sheets_id:
        logger.debug("Google Sheets credentials not configured, skipping")
        return

    try:
        import gspread
        from google.oauth2.service_account import Credentials

        if creds_json.strip().startswith("{"):
            creds_info = json.loads(creds_json)
        else:
            import base64
            creds_info = json.loads(base64.b64decode(creds_json).decode())

        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ]
        creds = Credentials.from_service_account_info(creds_info, scopes=scopes)
        gc = gspread.authorize(creds)
        sh = gc.open_by_key(sheets_id)

        try:
            worksheet = sh.worksheet("Group Migrations")
        except gspread.WorksheetNotFound:
            worksheet = sh.add_worksheet(title="Group Migrations", rows=1000, cols=6)
            worksheet.append_row(["ID", "New Link", "Created By", "Timestamp", "Old Group ID", "Note"])

        now_th = datetime.now(TH_TZ).strftime("%d/%m/%Y %H:%M:%S")
        worksheet.append_row(["-", new_link, str(created_by), now_th, "", ""])
        logger.info("Logged migration to Google Sheets")
    except ImportError:
        logger.debug("gspread not installed, skipping Sheets logging")
    except Exception as exc:
        logger.warning("Google Sheets logging failed: %s", exc)
