"""Event-driven gacha reward delivery.

Sends rewards to customer immediately after gacha claim — no polling.
Uses Telegram Bot HTTP API directly (no python-telegram-bot dep).
"""
from __future__ import annotations
import os
import json
import logging
from datetime import datetime, timedelta
import httpx

logger = logging.getLogger(__name__)

GUARDIAN_TOKEN = os.environ.get("GUARDIAN_BOT_TOKEN", "")
SALES_TOKEN = os.environ.get("SALES_BOT_TOKEN", "")
TG_API_BASE = "https://api.telegram.org"


async def _tg_call(token: str, method: str, json_data: dict, timeout: float = 10.0) -> dict:
    if not token:
        raise RuntimeError("bot token missing")
    url = f"{TG_API_BASE}/bot{token}/{method}"
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.post(url, json=json_data)
        r.raise_for_status()
        data = r.json()
        if not data.get("ok"):
            raise RuntimeError(f"telegram error: {data.get('description')}")
        return data["result"]


async def create_invite_link(chat_id: int, name: str) -> str | None:
    try:
        expire_ts = int((datetime.utcnow() + timedelta(hours=24)).timestamp())
        result = await _tg_call(GUARDIAN_TOKEN, "createChatInviteLink", {
            "chat_id": chat_id, "name": name[:32],
            "expire_date": expire_ts, "member_limit": 1,
            "creates_join_request": False,
        })
        return result.get("invite_link")
    except Exception as exc:
        logger.warning("invite link fail chat=%s: %s", chat_id, exc)
        return None


async def send_dm(tg_id: int, text: str) -> bool:
    try:
        await _tg_call(SALES_TOKEN, "sendMessage", {
            "chat_id": tg_id, "text": text,
            "parse_mode": "HTML", "disable_web_page_preview": True,
        })
        return True
    except Exception as exc:
        msg_lower = str(exc).lower()
        if "blocked" in msg_lower or "forbidden" in msg_lower:
            logger.info("DM blocked tg=%s", tg_id)
        else:
            logger.warning("DM fail tg=%s: %s", tg_id, exc)
        return False


async def already_delivered(conn, pull_id: int) -> bool:
    # Check new marker AND legacy markers so old pulls don't get re-delivered
    row = await conn.fetchrow(
        "SELECT 1 FROM admin_logs "
        "WHERE action IN ('gacha_reward_delivered','gacha_clip_delivered','gacha_sub_link_sent') "
        "AND target_id=$1 LIMIT 1",
        pull_id,
    )
    return row is not None


async def log_delivery(conn, pull_id: int, telegram_id: int, status: str, details: str) -> None:
    try:
        await conn.execute(
            "INSERT INTO admin_logs (admin_id, action, target_type, target_id, details, created_at) "
            "VALUES (0, 'gacha_reward_delivered', 'gacha_pull', $1, $2, NOW())",
            pull_id, f"tg={telegram_id} status={status} {details}"[:500],
        )
    except Exception as e:
        logger.warning("log delivery fail pull=%s: %s", pull_id, e)


def _build_clip_msg(prize_label: str, url: str) -> str:
    parts = [
        f"🎁 คุณได้รับ <b>{prize_label}</b>!",
        "",
        "กดลิงก์ด้านล่างเพื่อเข้าดูคลิปทั้งหมด 👇",
        f"🚀 <a href='{url}'>{prize_label}</a>",
        "",
        "⏰ ลิงก์ใช้ครั้งเดียว หมดอายุใน 24 ชม.",
    ]
    return "\n".join(parts)


def _build_discount_msg(prize_label: str, amount: float) -> str:
    parts = [
        f"🎁 คุณได้รับ <b>{prize_label}</b>!",
        "",
        f"💰 เครดิตส่วนลด <b>฿{amount:.0f}</b> ถูกเพิ่มในบัญชีของคุณแล้ว",
        "จะใช้อัตโนมัติตอนซื้อแพ็กเกจครั้งถัดไปค่ะ ✨",
    ]
    return "\n".join(parts)


def _build_sub_msg(prize_label: str, links: list) -> str:
    parts = [
        f"🎉 ขอแสดงความยินดี! คุณได้รับ <b>{prize_label}</b> จากกาชา 🎰",
        "",
        "📦 ลิงก์เข้ากลุ่มของคุณ:",
        "",
    ]
    for title, url in links:
        parts.append(f"🚀 <a href='{url}'>{title}</a>")
    parts.append("")
    parts.append("⏰ ลิงก์ใช้ครั้งเดียว หมดอายุใน 24 ชม.")
    parts.append("ขอบคุณที่อยู่กับเจริญพรค่ะ 💕")
    return "\n".join(parts)


def _build_extend_msg(prize_label: str, prize_tier: str | None) -> str:
    parts = [
        f"🎁 คุณได้รับ <b>{prize_label}</b>!",
        f"⏰ เพิ่มอายุสมาชิก {prize_tier or ''} ของคุณแล้ว ✨",
    ]
    return "\n".join(parts)




# ─────────────────────────────────────────────────────────────────────────
# DISCORD SHOUT-OUT (added 2026-06-22) — big gacha wins only
# ─────────────────────────────────────────────────────────────────────────
DISCORD_TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "")
DISCORD_SHOUT_CHANNEL = os.environ.get("DISCORD_GACHA_SHOUT_CHANNEL_ID", "")
DISCORD_API = "https://discord.com/api/v10"

# Big-win codes — only shout these in Discord
BIG_WIN_CODES = {"GOD_1299", "GOD_LIFETIME"}


async def send_discord_shout(prize_code: str, prize_label: str,
                              user_first_name: str, user_telegram_id: int) -> bool:
    """Post celebration to Discord #ลูกค้า if customer won a big prize."""
    if not DISCORD_TOKEN or not DISCORD_SHOUT_CHANNEL:
        return False
    if prize_code not in BIG_WIN_CODES:
        return False
    
    name = user_first_name or "ลูกค้า"
    safe_name = name.replace("@", "")[:40]
    emoji = "👑" if prize_code == "GOD_LIFETIME" else "💎"
    
    embed = {
        "title": f"{emoji} กาชา BIG WIN!",
        "description": (
            f"**{safe_name}** สุ่มได้รางวัลใหญ่!\n"
            f"🎁 รางวัล: **{prize_label}**\n"
            f"🆔 TG: `{user_telegram_id}`"
        ),
        "color": 0xFFD700 if prize_code == "GOD_LIFETIME" else 0x9333EA,
    }
    payload = {"embeds": [embed]}
    
    try:
        url = f"{DISCORD_API}/channels/{DISCORD_SHOUT_CHANNEL}/messages"
        headers = {"Authorization": f"Bot {DISCORD_TOKEN}"}
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.post(url, json=payload, headers=headers)
            r.raise_for_status()
        logger.info("Discord shout sent for prize=%s tg=%s", prize_code, user_telegram_id)
        return True
    except Exception as exc:
        logger.warning("Discord shout failed: %s", exc)
        return False


async def deliver_prize(
    pool,
    pull_id: int,
    tg_id: int,
    prize_code: str,
    prize_label: str,
    prize_type: str,
    prize_tier: str | None,
    value_thb: float | None,
    outcome: str,
) -> None:
    """Main orchestrator — sends prize to customer after gacha claim.
    
    Safe-by-design: idempotent (admin_log marker), try/except per branch.
    """
    async with pool.acquire() as conn:
        if await already_delivered(conn, pull_id):
            logger.info("SKIP pull=%s already delivered", pull_id)
            return

        try:
            # ── CLIP PACK ──
            if prize_type == "clip_pack":
                src = await conn.fetchval(
                    "SELECT source_chat_id FROM gachapon_prizes WHERE code=$1", prize_code
                )
                if not src:
                    await log_delivery(conn, pull_id, tg_id, "fail", "no source_chat_id")
                    return
                url = await create_invite_link(int(src), f"gacha_clip_{pull_id}")
                if not url:
                    await log_delivery(conn, pull_id, tg_id, "fail", "invite gen failed")
                    return
                msg = _build_clip_msg(prize_label, url)
                ok = await send_dm(tg_id, msg)
                await log_delivery(conn, pull_id, tg_id, "ok" if ok else "blocked",
                                   f"clip {prize_code}")
                return

            # ── DISCOUNT or CREDIT outcome ──
            if prize_type == "discount" or outcome == "credit":
                amt = float(value_thb or 0)
                msg = _build_discount_msg(prize_label, amt)
                ok = await send_dm(tg_id, msg)
                await log_delivery(conn, pull_id, tg_id, "ok" if ok else "blocked",
                                   f"discount baht_{int(amt)}")
                return

            # ── SUBSCRIPTION ──
            if prize_type == "subscription" and prize_tier:
                pkg = await conn.fetchrow(
                    "SELECT id, name, groups_access FROM packages "
                    "WHERE tier::text=$1 AND is_active=true LIMIT 1",
                    prize_tier,
                )
                if not pkg:
                    await log_delivery(conn, pull_id, tg_id, "fail", f"no pkg {prize_tier}")
                    return
                raw_groups = pkg["groups_access"]
                groups_list = json.loads(raw_groups) if isinstance(raw_groups, str) else (raw_groups or [])

                links = []
                for slug in groups_list:
                    grp = await conn.fetchrow(
                        "SELECT chat_id, title FROM group_registry WHERE slug=$1 AND is_active=true",
                        slug,
                    )
                    if not grp:
                        continue
                    url = await create_invite_link(int(grp["chat_id"]), f"gacha_{prize_code}_{pull_id}")
                    if url:
                        links.append((grp["title"] or slug, url))

                if not links:
                    await log_delivery(conn, pull_id, tg_id, "fail", "no links generated")
                    return

                msg = _build_sub_msg(prize_label, links)
                ok = await send_dm(tg_id, msg)
                await log_delivery(conn, pull_id, tg_id, "ok" if ok else "blocked",
                                   f"sub {prize_tier} links={len(links)}")
                # Discord shout-out for big wins (GOD_1299, GOD_LIFETIME)
                try:
                    # Get first_name from DB
                    user_row = await conn.fetchrow(
                        "SELECT first_name FROM users WHERE telegram_id=$1", tg_id
                    )
                    first_name = (user_row["first_name"] if user_row else None) or "ลูกค้า"
                    await send_discord_shout(prize_code, prize_label, first_name, tg_id)
                except Exception as exc:
                    logger.warning("Discord shout-out err (non-fatal): %s", exc)
                return

            # ── EXTEND ──
            if outcome == "extend":
                msg = _build_extend_msg(prize_label, prize_tier)
                ok = await send_dm(tg_id, msg)
                await log_delivery(conn, pull_id, tg_id, "ok" if ok else "blocked",
                                   f"extend {prize_tier}")
                return

            # ── UNKNOWN ──
            await log_delivery(conn, pull_id, tg_id, "skip",
                               f"unknown type={prize_type} outcome={outcome}")

        except Exception as exc:
            logger.exception("deliver_prize exception pull=%s", pull_id)
            try:
                await log_delivery(conn, pull_id, tg_id, "exception", str(exc)[:200])
            except Exception:
                pass
