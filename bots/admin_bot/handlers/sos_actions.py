"""sos_actions.py — SOS alert callbacks extracted from approval.py.

Manual invite-link recovery and ban-on-fraud flow:
- sos_resend_callback — re-send VIP invites manually after a failure
- copy_invites_callback — copy invites to clipboard for admin to forward
- sos_deny_callback — admin marks the SOS as resolved (no action)
- sos_ban_callback — ban a user flagged by the SOS

Strangler Fig Round 9 extraction. Logic UNCHANGED.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, func
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from shared.database import get_session
from shared.models import (
    Package,
    Payment,
    PaymentStatus,
    Subscription,
    SubscriptionStatus,
    User,
)
from shared.utils import format_datetime_thai, format_thb, log_admin_action
from shared.admin_alert import _admin_group_id
from shared.songkran_promo import get_group_display_title, is_songkran_bonus_slug
from shared.admin_perms import is_admin_for_bot

logger = logging.getLogger(__name__)


# Inlined to avoid circular import with approval.py
def _admin_ids() -> list[int]:
    raw = os.environ.get("ADMIN_TELEGRAM_IDS", "")
    ids: list[int] = []
    for tok in raw.split(","):
        tok = tok.strip()
        if tok:
            try:
                ids.append(int(tok))
            except ValueError:
                pass
    return ids

def _is_admin(user_id: int) -> bool:
    """Migrated to shared.admin_perms (DB-first with env fallback)."""
    return is_admin_for_bot(user_id, "admin_bot")


async def sos_resend_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """SOS: ส่งลิงก์เข้ากลุ่มใหม่ให้ลูกค้า."""
    query = update.callback_query
    await query.answer()

    if not query.from_user or not _is_admin(query.from_user.id):
        await query.answer("⛔ คุณไม่มีสิทธิ์", show_alert=True)
        return

    target_user_id = int(query.data.split("_")[2])

    import os
    import telegram as tg
    from bots.guardian_bot.group_monitor import generate_invite_links_for_user

    try:
        # Find user's active subscription to determine package
        # ⚠️ เช็คทั้ง status=ACTIVE และ end_date ยังไม่หมดอายุ
        from datetime import datetime as _dt
        _now = _dt.utcnow()
        async with get_session() as session:
            sub_result = await session.execute(
                select(Subscription).where(
                    Subscription.user_id == (
                        select(User.id).where(User.telegram_id == target_user_id).scalar_subquery()
                    ),
                    Subscription.status == SubscriptionStatus.ACTIVE,
                    Subscription.end_date > _now,
                ).order_by(Subscription.end_date.desc())
            )
            sub = sub_result.scalars().first()

        if not sub:
            # เช็ค CSV whitelist — ลูกค้าเก่าอาจไม่มี subscription ในระบบใหม่
            import csv
            csv_path = "/app/data/members2_latest.csv"
            csv_found = False
            csv_status = None
            try:
                with open(csv_path) as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        if row.get("User ID", "").strip() == str(target_user_id):
                            csv_found = True
                            csv_status = (row.get("Status") or "").strip()
                            break
            except Exception:
                pass

            if not csv_found:
                # แจ้งเตือนในข้อความแทน popup — ให้แอดมินเห็นชัด
                import html as _html
                safe_admin = _html.escape(str(query.from_user.first_name or "Admin"))
                old_text = query.message.text or ""
                new_text = (
                    f"{old_text}\n\n"
                    f"⚠️ <b>แจ้งเตือน:</b> ลูกค้า TG ID <code>{target_user_id}</code> "
                    f"ไม่มีในระบบ (ไม่มี subscription, ไม่มี payment, ไม่อยู่ในฐานข้อมูลลูกค้าเก่า)\n"
                    f"👮 กดโดย: {safe_admin}\n\n"
                    f"💡 <b>ทางแก้:</b> ต้อง approve แพ็กเกจให้ลูกค้าก่อน แล้วค่อยกดส่งลิงก์ใหม่"
                )
                try:
                    approve_keyboard = tg.InlineKeyboardMarkup([
                        [
                            tg.InlineKeyboardButton("✅ 300 (VIP)", callback_data=f"approve_300_{target_user_id}", api_kwargs={"style": "success"}),
                            tg.InlineKeyboardButton("✅ 500 (OF)", callback_data=f"approve_500_{target_user_id}", api_kwargs={"style": "success"}),
                        ],
                        [
                            tg.InlineKeyboardButton("✅ 1299 (3M)", callback_data=f"approve_1299_{target_user_id}", api_kwargs={"style": "success"}),
                            tg.InlineKeyboardButton("✅ 2499 (GOD)", callback_data=f"approve_2499_{target_user_id}", api_kwargs={"style": "success"}),
                        ],
                        [
                            tg.InlineKeyboardButton("🌊 500 (Summer)", callback_data=f"approve_ADD500_{target_user_id}", api_kwargs={"style": "success"}),
                        ],
                        [tg.InlineKeyboardButton("🔄 ส่งลิงก์ใหม่", callback_data=f"sos_resend_{target_user_id}", api_kwargs={"style": "primary"})],
                    ])
                    await query.edit_message_text(text=new_text[:4096], parse_mode="HTML", reply_markup=approve_keyboard)
                except Exception as e:
                    logger.error("Failed to edit SOS no-sub message: %s", e)
                    await query.answer("❌ ลูกค้าไม่มีในระบบ — ต้อง approve แพ็กเกจก่อน", show_alert=True)
                return

            if csv_status == "Expired":
                old_text = query.message.text or ""
                new_text = (
                    f"{old_text}\n\n"
                    f"⚠️ <b>แจ้งเตือน:</b> ลูกค้า TG ID <code>{target_user_id}</code> หมดอายุแล้ว\n"
                    f"💡 ต้องต่ออายุ/ซื้อแพ็กเกจใหม่ก่อนถึงจะส่งลิงก์ได้"
                )
                try:
                    await query.edit_message_text(text=new_text[:4096], parse_mode="HTML")
                except Exception:
                    await query.answer("❌ ลูกค้าหมดอายุแล้ว ไม่สามารถส่งลิ้งค์ได้", show_alert=True)
                return

            # ลูกค้าเก่า — ตรวจสอบว่าเป็นสมาชิกกลุ่มไหนอยู่แล้ว แล้วส่งเฉพาะกลุ่มนั้น
            sub_package_id = "__csv_member__"
        else:
            sub_package_id = sub.package_id

        # Generate invite links using Guardian Bot
        guardian_bot = tg.Bot(token=os.environ.get("GUARDIAN_BOT_TOKEN", ""))
        await guardian_bot.initialize()
        sales_bot = tg.Bot(token=os.environ.get("SALES_BOT_TOKEN", ""))
        await sales_bot.initialize()

        if sub_package_id == "__csv_member__":
            # CSV user: ตรวจสอบ membership จริงในแต่ละกลุ่ม แล้วสร้าง invite เฉพาะกลุ่มที่เป็นสมาชิก
            from bots.guardian_bot.group_monitor import generate_invite_links_for_csv_user
            invite_links = await generate_invite_links_for_csv_user(guardian_bot, target_user_id)
        else:
            invite_links = await generate_invite_links_for_user(guardian_bot, target_user_id, sub_package_id)

        if not invite_links:
            await query.answer("❌ สร้างลิงก์ไม่สำเร็จ", show_alert=True)
            return

        # Build link buttons
        links_list = []
        async with get_session() as session:
            from shared.models import GroupRegistry
            for slug, link in invite_links.items():
                if is_songkran_bonus_slug(slug):
                    title = get_group_display_title(slug)
                else:
                    grp_result = await session.execute(
                        select(GroupRegistry).where(GroupRegistry.slug == slug)
                    )
                    group = grp_result.scalar_one_or_none()
                    title = group.title if group else get_group_display_title(slug)
                links_list.append(tg.InlineKeyboardButton(f"🚀 {title}", url=link))

        link_buttons = [links_list[i:i+2] for i in range(0, len(links_list), 2)]
        keyboard = tg.InlineKeyboardMarkup(link_buttons)

        # Send to customer via Sales Bot
        is_sent = True
        try:
            await sales_bot.send_message(
                chat_id=target_user_id,
                text="🔄 <b>ส่งลิงก์เข้ากลุ่มให้ใหม่แล้วค่า</b>\nกดเข้าได้เลยนะ 👇",
                parse_mode="HTML",
                reply_markup=keyboard,
            )
        except Exception:
            is_sent = False

        # ดึงข้อมูล user สำหรับปุ่มแชท
        db_user = None
        try:
            async with get_session() as session:
                user_result = await session.execute(
                    select(User).where(User.telegram_id == target_user_id)
                )
                db_user = user_result.scalar_one_or_none()
        except Exception:
            pass

        # Update admin message
        safe_admin = query.from_user.first_name or "Admin"
        old_text = query.message.text or ""
        
        # สร้างปุ่มแชทลูกค้าเฉพาะกรณีมี username
        chat_row = []
        if db_user and db_user.username:
            chat_row = [[
                tg.InlineKeyboardButton(
                    f"💬 @{db_user.username}",
                    url=f"https://t.me/{db_user.username}",
                    api_kwargs={"style": "primary"},
                )
            ]]

        if is_sent:
            new_text = f"{old_text}\n\n✅ <b>ส่งลิงก์สำเร็จแล้ว ✓ โดย {safe_admin}</b>"
            new_keyboard = tg.InlineKeyboardMarkup(chat_row) if chat_row else None
        else:
            new_text = f"{old_text}\n\n❌ <b>ส่งไม่สำเร็จ (ลูกค้าบล็อกบอท)</b>"
            rows = [
                [tg.InlineKeyboardButton("🔄 ลองส่งอีกครั้ง", callback_data=f"sos_resend_{target_user_id}", api_kwargs={"style": "primary"})],
            ]
            rows.extend(chat_row)
            new_keyboard = tg.InlineKeyboardMarkup(rows)

        try:
            await query.edit_message_text(text=new_text[:4096], parse_mode="HTML", reply_markup=new_keyboard)
        except Exception as e:
            logger.error("Failed to edit SOS resend message: %s", e)

    except Exception as exc:
        logger.error("SOS resend error: %s", exc)
        await query.answer(f"❌ Error: {str(exc)[:100]}", show_alert=True)


async def copy_invites_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Generate fresh invite links and send plain text for manual copy."""
    query = update.callback_query
    await query.answer()

    if not query.from_user or not _is_admin(query.from_user.id):
        await query.answer("⛔ คุณไม่มีสิทธิ์", show_alert=True)
        return

    target_user_id = int(query.data.split("_")[2])

    import os
    import telegram as tg
    from bots.guardian_bot.group_monitor import generate_invite_links_for_user, generate_invite_links_for_csv_user

    try:
        from datetime import datetime as _dt
        _now = _dt.utcnow()
        async with get_session() as session:
            sub_result = await session.execute(
                select(Subscription).where(
                    Subscription.user_id == (
                        select(User.id).where(User.telegram_id == target_user_id).scalar_subquery()
                    ),
                    Subscription.status == SubscriptionStatus.ACTIVE,
                    Subscription.end_date > _now,
                ).order_by(Subscription.end_date.desc())
            )
            sub = sub_result.scalars().first()

        guardian_bot = tg.Bot(token=os.environ.get("GUARDIAN_BOT_TOKEN", ""))
        await guardian_bot.initialize()

        if not sub:
            invite_links = await generate_invite_links_for_csv_user(guardian_bot, target_user_id)
        else:
            invite_links = await generate_invite_links_for_user(guardian_bot, target_user_id, sub.package_id)

        if not invite_links:
            await query.answer("❌ สร้างลิงก์ไม่สำเร็จ", show_alert=True)
            return

        lines = []
        async with get_session() as session:
            for slug, link in invite_links.items():
                if is_songkran_bonus_slug(slug):
                    title = get_group_display_title(slug)
                else:
                    grp_result = await session.execute(select(GroupRegistry).where(GroupRegistry.slug == slug))
                    group = grp_result.scalar_one_or_none()
                    title = group.title if group else get_group_display_title(slug)
                lines.append(f"• {title}: {link}")

        text = (
            f"📋 <b>ลิงก์สำหรับคัดลอกส่งลูกค้า</b>\n"
            f"🆔 TG ID: <code>{target_user_id}</code>\n\n" + "\n".join(lines)
        )
        await query.message.reply_text(text, parse_mode="HTML")
    except Exception as exc:
        logger.error("copy_invites error: %s", exc)
        await query.answer(f"❌ Error: {str(exc)[:100]}", show_alert=True)


async def sos_deny_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """SOS: ไม่อนุมัติ — แจ้งลูกค้าว่าไม่พบสิทธิ์."""
    query = update.callback_query
    await query.answer()

    if not query.from_user or not _is_admin(query.from_user.id):
        await query.answer("⛔ คุณไม่มีสิทธิ์", show_alert=True)
        return

    target_user_id = int(query.data.split("_")[2])
    admin_name = query.from_user.first_name or "Admin"

    import os, html as _html
    import telegram as tg

    try:
        # Send denial to user via sales bot
        sales_token = os.environ.get("SALES_BOT_TOKEN", "")
        if sales_token:
            sales_bot = tg.Bot(token=sales_token)
            await sales_bot.initialize()
            await sales_bot.send_message(
                chat_id=target_user_id,
                text=(
                    "❌ ขออภัยค่ะ ตรวจสอบแล้วไม่พบสิทธิ์การเข้ากลุ่ม\n\n"
                    "หากคิดว่ามีข้อผิดพลาด กรุณาติดต่อแอดมิน:\n"
                    "→ https://t.me/sperm6969"
                ),
            )

        # Update admin message
        old_text = query.message.text_html or query.message.text or ""
        safe_admin = _html.escape(str(admin_name))
        new_text = f"{old_text}\n\n❌ <b>ไม่อนุมัติ โดย {safe_admin}</b>"
        await query.edit_message_text(text=new_text[:4096], parse_mode="HTML", reply_markup=None)

    except Exception as exc:
        logger.error("SOS deny error: %s", exc)
        await query.answer(f"❌ Error: {str(exc)[:100]}", show_alert=True)


async def sos_ban_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """SOS: แบนลูกค้า — เตะออกจากทุกกลุ่ม + ยกเลิก subscription."""
    query = update.callback_query

    if not query.from_user or not _is_admin(query.from_user.id):
        await query.answer("⛔ คุณไม่มีสิทธิ์", show_alert=True)
        return

    # Confirm
    await query.answer("⚠️ กำลังแบนลูกค้า...", show_alert=False)

    target_user_id = int(query.data.split("_")[2])
    admin_name = query.from_user.first_name or "Admin"

    import os, html as _html
    import telegram as tg

    try:
        # 1. Cancel subscription in DB
        async with get_session() as session:
            user_result = await session.execute(
                select(User).where(User.telegram_id == target_user_id)
            )
            db_user = user_result.scalar_one_or_none()

            if db_user:
                sub_result = await session.execute(
                    select(Subscription).where(
                        Subscription.user_id == db_user.id,
                        Subscription.status == SubscriptionStatus.ACTIVE,
                    )
                )
                for sub in sub_result.scalars().all():
                    sub.status = SubscriptionStatus.EXPIRED
                await session.commit()

        # 2. Kick from all VIP groups
        guardian_token = os.environ.get("GUARDIAN_BOT_TOKEN", "")
        if guardian_token:
            guardian_bot = tg.Bot(token=guardian_token)
            await guardian_bot.initialize()
            async with get_session() as session:
                from shared.models import GroupRegistry
                groups_result = await session.execute(select(GroupRegistry))
                groups = groups_result.scalars().all()
            
            kicked_count = 0
            for g in groups:
                try:
                    await guardian_bot.ban_chat_member(chat_id=g.group_id, user_id=target_user_id)
                    await guardian_bot.unban_chat_member(chat_id=g.group_id, user_id=target_user_id)  # unban so they're just kicked, not permanently banned
                    kicked_count += 1
                except Exception:
                    pass

        # 3. Notify user
        sales_token = os.environ.get("SALES_BOT_TOKEN", "")
        if sales_token:
            sales_bot = tg.Bot(token=sales_token)
            await sales_bot.initialize()
            try:
                await sales_bot.send_message(
                    chat_id=target_user_id,
                    text="🚫 บัญชีของคุณถูกระงับการใช้งาน\nหากมีข้อสงสัย ติดต่อแอดมิน → https://t.me/sperm6969",
                )
            except Exception:
                pass

        # 4. Update admin message
        old_text = query.message.text_html or query.message.text or ""
        safe_admin = _html.escape(str(admin_name))
        new_text = f"{old_text}\n\n🚫 <b>แบนแล้ว โดย {safe_admin}</b> (เตะ {kicked_count} กลุ่ม, ยกเลิก subscription)"
        await query.edit_message_text(text=new_text[:4096], parse_mode="HTML", reply_markup=None)

        logger.info("SOS ban: user %s banned by %s, kicked from %d groups", target_user_id, admin_name, kicked_count)

    except Exception as exc:
        logger.error("SOS ban error: %s", exc)
        await query.answer(f"❌ Error: {str(exc)[:100]}", show_alert=True)


async def sos_resolve_callback(update, context) -> None:
    """SOS: Resolved — mark as RESOLVED + edit admin message. No customer DM
    (Prae AI / boss already handled the customer in chat).
    Pattern: sos_resolve:<telegram_id>
    """
    query = update.callback_query
    if not query:
        return
    try: await query.answer("Resolving...")
    except Exception: pass

    if not query.from_user or not _is_admin(query.from_user.id):
        try: await query.answer("⛔ ไม่มีสิทธิ์", show_alert=True)
        except Exception: pass
        return

    parts = (query.data or "").split(":", 1)
    if len(parts) != 2:
        return
    try:
        target_tg = int(parts[1])
    except ValueError:
        return

    from shared.database import get_session
    from sqlalchemy import text as _t

    # Mark RESOLVED in DB
    try:
        async with get_session() as s:
            await s.execute(_t("""
                UPDATE sos_alerts
                   SET status = 'RESOLVED',
                       resolved_at = NOW(),
                       resolved_by = :adm
                 WHERE telegram_id = :tg AND status = 'PENDING'
            """), {"adm": query.from_user.id, "tg": target_tg})
            await s.commit()
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning("sos_resolve DB update failed: %s", exc)

    # Audit log
    try:
        from shared.utils import log_admin_action
        await log_admin_action(
            admin_id=query.from_user.id,
            action="sos_resolved",
            target_type="sos",
            target_id=target_tg,
            details=f"manual resolve by @{query.from_user.username or query.from_user.first_name}",
        )
    except Exception:
        pass

    # Edit admin message
    actor = query.from_user.username or query.from_user.first_name or "admin"
    import html as _h
    marker = f"\n\n✅ <b>Resolved</b> by @{_h.escape(str(actor))}"
    try:
        if query.message and query.message.caption is not None:
            await query.edit_message_caption(
                caption=(query.message.caption or "") + marker,
                parse_mode="HTML", reply_markup=None,
            )
        elif query.message and query.message.text is not None:
            await query.edit_message_text(
                text=(query.message.text or "") + marker,
                parse_mode="HTML", reply_markup=None,
            )
    except Exception:
        pass

