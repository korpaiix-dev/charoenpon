"""/getlink handler — Sales Bot.

FIX 2025-05-21 (Phase 2a): ลูกค้า VIP ที่จ่ายเงินแล้วสามารถพิมพ์ /getlink เพื่อขอ
ลิงก์ one-time entry ของกลุ่ม VIP ใหม่ได้เอง (กรณีลิงก์เดิมหมดอายุ / กดเข้าไม่ทัน).

Flow:
  1. เช็ค subscription ของ user (ต้อง ACTIVE และยังไม่หมดอายุ)
  2. ถ้าไม่มี → แจ้งให้สมัครก่อน + ปุ่มไป /packages
  3. ถ้ามี → loop ทุก group ใน packages.groups_access → สร้าง one-time
     invite ผ่าน guardian-bot (อายุ 2 วัน, member_limit=1) → ส่งปุ่มกลับ
  4. Rate limit: 1 ครั้ง / 5 นาที / user (in-memory dict — ถ้าจะใส่ Redis ทีหลังได้)
  5. Log ทุก invite ที่สร้างใน admin_logs
"""

from __future__ import annotations
from shared.contact_admin import contact_admin_kb

import json
import logging
import os
from datetime import datetime, timedelta
from typing import Any

import telegram as tg
from sqlalchemy import select
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CommandHandler, ContextTypes

from shared.database import get_session
from shared.models import (
    GroupRegistry,
    Package,
    Subscription,
    SubscriptionStatus,
    User,
)
from shared.utils import log_admin_action
from shared.bot_messages import render_or_fallback

logger = logging.getLogger(__name__)

# Rate limit: 1 request / user / 5 minutes
_LAST_REQUEST: dict[int, datetime] = {}
_RATE_LIMIT_SEC = 300




async def getlink_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/getlink — ขอ one-time invite สำหรับกลุ่ม VIP ที่ลูกค้ามีสิทธิ์เข้า."""
    if not update.effective_user or not update.message:
        return

    tg_id = update.effective_user.id
    now = datetime.utcnow()

    # --- Rate limit ----------------------------------------------------------
    last = _LAST_REQUEST.get(tg_id)
    if last and (now - last).total_seconds() < _RATE_LIMIT_SEC:
        wait = int(_RATE_LIMIT_SEC - (now - last).total_seconds())
        await update.message.reply_text(
            f"ขอลิงก์ใหม่ได้อีก {wait} วินาที กรุณารอสักครู่นะคะ"
        )
        return

    # --- Lookup user + active subscription -----------------------------------
    async with get_session() as session:
        user_q = await session.execute(
            select(User).where(User.telegram_id == tg_id)
        )
        user = user_q.scalar_one_or_none()
        if not user:
            await update.message.reply_text(
                "ยังไม่พบบัญชีคุณในระบบ — กรุณาสมัครก่อนค่ะ\n"
                "พิมพ์ /packages เพื่อดูแพ็กเกจ",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("ดูแพ็กเกจ", callback_data="view_packages")],
                ]),
            )
            return

        # P1-6: give links for ALL groups the user is entitled to via ANY active sub (union) —
        # a stacked customer (e.g. GOD ถาวร + OF add-on) must get every room, not one sub's subset.
        from shared.subscription_access import user_active_group_slugs
        slugs = list(await user_active_group_slugs(user.id))
        if not slugs:
            await update.message.reply_text(
                "คุณยังไม่มีสมาชิก VIP ที่ active ค่ะ — กรุณาสมัครก่อน\n"
                "พิมพ์ /packages เพื่อดูแพ็กเกจ",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("ดูแพ็กเกจ", callback_data="view_packages")],
                ]),
            )
            return

        grp_q = await session.execute(
            select(GroupRegistry).where(
                GroupRegistry.slug.in_(slugs),
                GroupRegistry.is_active.is_(True),
            )
        )
        groups = list(grp_q.scalars().all())

    if not groups:
        await update.message.reply_text(
            "ไม่พบกลุ่มที่เปิดใช้งานสำหรับแพ็กเกจของคุณ — กรุณาทักแอดมินค่ะ"
        )
        return

    # --- Create one-time invites via guardian-bot ----------------------------
    guardian_token = os.environ.get("GUARDIAN_BOT_TOKEN", "")
    if not guardian_token:
        logger.error("getlink: GUARDIAN_BOT_TOKEN not set")
        await update.message.reply_text(await render_or_fallback("system_error_temp", "⚠️ ระบบขัดข้องชั่วคราว กดปุ่มด้านล่างทักแอดมินได้เลยค่ะ"), reply_markup=contact_admin_kb())
        return

    guardian = tg.Bot(token=guardian_token)
    try:
        await guardian.initialize()
    except Exception as exc:
        logger.error("getlink: guardian.initialize() failed: %s", exc)
        await update.message.reply_text(await render_or_fallback("system_error_temp", "⚠️ ระบบขัดข้องชั่วคราว กดปุ่มด้านล่างทักแอดมินได้เลยค่ะ"), reply_markup=contact_admin_kb())
        return

    buttons: list[list[InlineKeyboardButton]] = []
    expire = now + timedelta(days=2)
    for g in groups:
        try:
            link_obj = await guardian.create_chat_invite_link(
                chat_id=g.chat_id,
                expire_date=expire,
                member_limit=1,
                name=f"getlink_{tg_id}_{g.slug.value if hasattr(g.slug, 'value') else g.slug}",
            )
            buttons.append([
                InlineKeyboardButton(f"เข้ากลุ่ม {g.title}", url=link_obj.invite_link)
            ])
            try:
                await log_admin_action(
                    admin_id=0,  # system action (not a human admin)
                    action="create_one_time_invite",
                    target_type="user",
                    target_id=tg_id,
                    details=(
                        f"chat_id={g.chat_id} slug={g.slug} "
                        f"link={link_obj.invite_link} expire={expire.isoformat()} "
                        f"reason=getlink_command"
                    ),
                )
            except Exception as log_exc:
                logger.warning("getlink: log_admin_action failed: %s", log_exc)
        except Exception as exc:
            logger.error(
                "getlink: create_chat_invite_link failed user=%d chat=%d (%s): %s",
                tg_id, g.chat_id, g.slug, exc,
            )

    if not buttons:
        await update.message.reply_text(
            "สร้างลิงก์ไม่สำเร็จ กรุณาทักแอดมินค่ะ"
        )
        return

    # --- Commit rate limit + send response -----------------------------------
    _LAST_REQUEST[tg_id] = now

    msg = (
        "📥 *ลิงก์เข้ากลุ่มใหม่ของคุณ*\n\n"
        f"แพ็กเกจ: *{package.name}*\n"
        "ลิงก์อายุ 2 วัน, ใช้ได้ครั้งเดียวเท่านั้น\n\n"
        "กดปุ่มด้านล่างเพื่อเข้ากลุ่มค่ะ"
    )
    await update.message.reply_text(
        msg,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


def get_getlink_handler() -> CommandHandler:
    """Return CommandHandler for /getlink — public, available to all users."""
    return CommandHandler("getlink", getlink_command)
