"""/start handler - Sales Bot แพร.

บันทึก user + source, แสดงปุ่มหลัก.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update, WebAppInfo
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

from shared.database import get_session
from shared.models import Lead, LeadStatus, User
from bots.sales_bot.handlers import social_proof  # SOCIAL_PROOF_V1

logger = logging.getLogger(__name__)

WELCOME_TEXT = (
    "หวัดดีค่า~ ยินดีต้อนรับสู่ <b>กลุ่ม VIP เจริญพร</b> 🎉\n\n"
    "แพรเองค่า 😊 มีอะไรให้ช่วยบอกได้เลยนะ\n"
    "จะดูแพ็กเกจ จะสมัคร หรือมีคำถามอะไร กดด้านล่างเลยค่า 👇"
)

MAIN_KEYBOARD = InlineKeyboardMarkup(
    [
        [InlineKeyboardButton("⚡ Flash Sale", callback_data="view_flashsale")],
        [InlineKeyboardButton("📦 ดูแพ็กเกจ", callback_data="view_packages")],
        [InlineKeyboardButton("📊 ข้อมูลของฉัน", web_app=WebAppInfo(url="https://telebord.net/webapp/customer"))],
        [
            InlineKeyboardButton("📋 เช็คเครดิต/รีวิว", url="https://t.me/+hv7uXYj4bxFhODZl"),
            InlineKeyboardButton("👀 ดูตัวอย่างงาน", url="https://t.me/+Q0Qf-4t8TQo3YTBl"),
        ],
        [InlineKeyboardButton("🆓 ห้องฟรี", url="https://t.me/addlist/w0YSyuHC_aE2ZGVl")],
        [InlineKeyboardButton("👩‍💼 ติดต่อแอดมิน", url="https://t.me/sperm6969")],
    ]
)


def _extract_source(args: list[str]) -> str | None:
    """Extract referral/campaign source from /start deep link."""
    if args and args[0]:
        return args[0]
    return None


async def _handle_comeback_start(update: Update, context: ContextTypes.DEFAULT_TYPE, promo_code: str) -> bool:
    """Handle /start comeback_{code} deep link. Returns True if handled."""
    from bots.sales_bot.comeback_dm import validate_promo_code, mark_promo_responded, _calculate_discounted_price

    # >>> FIX_PASS_TG_ID <<< — restrict code to the user it was issued to
    _tg_id = update.effective_user.id if update.effective_user else None
    promo = await validate_promo_code(promo_code, telegram_id=_tg_id)
    if not promo:
        # FIX: use dynamic keyboard so referral button shows
        kb = await _build_main_keyboard(update.effective_user.id) if update.effective_user else MAIN_KEYBOARD
        await update.message.reply_text(
            "❌ โปรโมชั่นนี้หมดอายุหรือไม่ถูกต้องแล้วค่ะ\n\n"
            "กดดูแพ็กเกจราคาปกติได้เลยนะคะ 👇",
            parse_mode="HTML",
            reply_markup=kb,
        )
        return True

    # Mark as responded
    await mark_promo_responded(promo_code)

    discount_pct = promo["discount_pct"]
    discounted_price = promo["discounted_price"]

    # Store promo in user context for payment
    context.user_data["selected_tier"] = "300"
    context.user_data["selected_price"] = str(discounted_price)
    context.user_data["comeback_promo"] = promo_code
    context.user_data["comeback_discount"] = discount_pct

    text = (
        f"🔥 <b>ยินดีต้อนรับกลับค่ะ!</b>\n\n"
        f"คุณได้รับส่วนลด <b>{discount_pct}%</b> สำหรับแพ็กเกจ VIP 30 วัน\n\n"
        f"💰 ราคาพิเศษ: <b>฿{discounted_price}</b> (จาก ฿300)\n"
        f"⏰ ใช้ได้อีก 48 ชม. เท่านั้น\n\n"
        f"📌 <b>วิธีชำระเงิน:</b>\n"
        f"1️⃣ สแกน QR PromptPay ด้านล่าง หรือโอนเงิน <b>฿{discounted_price}</b>\n"
        f"2️⃣ ส่งสลิปโอนเงิน หรือ ลิงก์ซอง TrueMoney\n"
        f"3️⃣ รอแอดมินตรวจสอบ\n\n"
        f"💳 <b>ช่องทางชำระ:</b>\n"
        f"• PromptPay / โอนธนาคาร → ส่งรูปสลิป\n"
        f"• TrueMoney Wallet → ส่งลิงก์ gift.truemoney.com\n\n"
        f"⚠️ <b>หมายเหตุ:</b> กรุณาโอน <b>฿{discounted_price}</b> บาทเท่านั้นค่ะ"
    )

    keyboard = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📦 ดูแพ็กเกจอื่น", callback_data="view_packages")],
            [InlineKeyboardButton("🔙 กลับเมนูหลัก", callback_data="back_main")],
        ]
    )

    await update.message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)

    # Send QR code
    QR_URL = "https://img2.pic.in.th/-2026-03-15-143743.png"
    try:
        await context.bot.send_photo(
            chat_id=update.message.chat_id,
            photo=QR_URL,
            caption=f"📱 สแกน QR PromptPay เพื่อโอน <b>฿{discounted_price}</b>\nแล้วส่งสลิปมาที่แชทนี้เลยค่ะ 🙏",
            parse_mode="HTML",
        )
    except Exception as exc:
        logger.warning("Failed to send QR for comeback: %s", exc)

    return True




async def _navigate(query, text: str, kb, img_path=None) -> None:
    """Show a new screen after a callback. Handles both photo-message and text-message originals.

    edit_message_text fails when original is a photo (welcome msg now sends photo).
    So we delete original + send new message (or new photo).
    """
    try:
        await query.message.delete()
    except Exception:
        pass
    try:
        if img_path is not None:
            with open(img_path, "rb") as f:
                await query.message.chat.send_photo(
                    photo=f, caption=text, parse_mode="HTML", reply_markup=kb,
                )
        else:
            await query.message.chat.send_message(
                text, parse_mode="HTML", reply_markup=kb, disable_web_page_preview=True,
            )
    except Exception as exc:
        # Last-resort fallback
        try:
            await query.message.reply_text(text, parse_mode="HTML", reply_markup=kb)
        except Exception:
            pass


async def _build_main_keyboard(telegram_id: int) -> InlineKeyboardMarkup:
    """Build the main menu keyboard dynamically.

    - Flash Sale button: only show if active flash sale exists (no fake button)
    - Upgrade button: only for VIP active users
    - Referral button: always shown
    """
    rows = []
    # Flash sale — only if active in DB
    try:
        from bots.sales_bot.handlers.flash_sale import _get_active_flash_sale
        flash = await _get_active_flash_sale()
        if flash and flash.sold_slots < flash.total_slots:
            rows.append([InlineKeyboardButton("⚡ FLASH SALE — กำลังลด!", callback_data="view_flashsale")])
    except Exception:
        pass

    # Upgrade — only for VIP active
    try:
        from bots.sales_bot.handlers.referral import _is_vip_active
        if await _is_vip_active(telegram_id):
            rows.append([InlineKeyboardButton("🆙 อัพเกรดเป็น GOD MODE", callback_data="view_upgrade")])
    except Exception:
        pass

    # VIPมีคนชัก — always show (lottery group ฿100)
    rows.append([InlineKeyboardButton("🎰 VIPมีคนชัก ฿100 — ลุ้น GOD ทุกจันทร์!", callback_data="view_shaker")])

    # Discount button — only show if user has balance > 0
    try:
        from bots.sales_bot.handlers.discount_button import get_balance_for_user
        _disc_bal = await get_balance_for_user(telegram_id)
        if _disc_bal > 0:
            rows.append([InlineKeyboardButton(
                f"💰 ส่วนลดของฉัน ฿{int(_disc_bal):,}",
                callback_data="view_discount"
            )])
    except Exception:
        pass

    # Gacha buy — เติมสิทธิ์หมุน
    rows.append([InlineKeyboardButton("🎁 เติมสิทธิ์หมุนกาชาปอง", callback_data="view_gacha_buy")])

    # ดูแพ็กเกจ — moved to position 4 (per boss)
    rows.append([InlineKeyboardButton("📦 ดูแพ็กเกจ", callback_data="view_packages")])
    rows.append([InlineKeyboardButton("📊 ข้อมูลของฉัน", web_app=WebAppInfo(url="https://telebord.net/webapp/customer"))])

    # Referral — moved to position 5
    rows.append([InlineKeyboardButton("🎁 ชวนเพื่อน ได้ VIP ฟรี!", callback_data="referral_menu")])

    rows.extend([
        [
            InlineKeyboardButton("📋 เช็คเครดิต/รีวิว", url="https://t.me/+hv7uXYj4bxFhODZl"),
            InlineKeyboardButton("👀 ดูตัวอย่างงาน", url="https://t.me/+Q0Qf-4t8TQo3YTBl"),
        ],
        [InlineKeyboardButton("🆓 ห้องฟรี", url="https://t.me/addlist/w0YSyuHC_aE2ZGVl")],
        [InlineKeyboardButton("👩‍💼 ติดต่อแอดมิน", url="https://t.me/sperm6969")],
    ])
    return InlineKeyboardMarkup(rows)


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/start command — register user and show main menu."""
    if not update.effective_user or not update.message:
        return

    tg_user = update.effective_user
    source = _extract_source(context.args or [])

    async with get_session() as session:
        # Upsert user
        result = await session.execute(
            select(User).where(User.telegram_id == tg_user.id)
        )
        user = result.scalar_one_or_none()

        is_new_user = False
        if user is None:
            user = User(
                telegram_id=tg_user.id,
                username=tg_user.username,
                first_name=tg_user.first_name,
                last_name=tg_user.last_name,
            )
            session.add(user)
            await session.flush()
            is_new_user = True
            logger.info(
                "New user registered: %s (tg:%d) source=%s",
                tg_user.username,
                tg_user.id,
                source,
            )
        else:
            # Update profile info
            user.username = tg_user.username
            user.first_name = tg_user.first_name
            user.last_name = tg_user.last_name

        # Create/update lead
        lead_result = await session.execute(
            select(Lead).where(Lead.telegram_id == tg_user.id)
        )
        lead = lead_result.scalar_one_or_none()

        if lead is None:
            lead = Lead(
                user_id=user.id,
                telegram_id=tg_user.id,
                username=tg_user.username,
                source=source,
                status=LeadStatus.NEW,
            )
            session.add(lead)
        elif source and not lead.source:
            lead.source = source

        # Record teaser click if source is a tracking link
        if source and source.startswith("t_"):
            parts = source.split("_")  # t_2300_g5
            if len(parts) == 3:
                try:
                    round_time = parts[1]
                    group_index = int(parts[2].replace("g", ""))
                    from shared.models import TeaserClick
                    click = TeaserClick(
                        user_id=tg_user.id,
                        round_time=round_time,
                        group_index=group_index,
                    )
                    session.add(click)
                    logger.info(
                        "TeaserClick recorded: user=%d round=%s group=%d",
                        tg_user.id, round_time, group_index,
                    )
                except (ValueError, IndexError) as exc:
                    logger.warning("Failed to parse teaser source '%s': %s", source, exc)

    # Handle marketing deep link: /start mkt_{name_tag}
    # → track attribution + show CUSTOM 2-message welcome (Sprint 2026-06-24)
    if source and source.startswith("mkt_"):
        try:
            from sqlalchemy import text as _t
            from shared.database import get_session as _gs
            tg_user = update.effective_user
            # FIX 2026-06-25: strip mkt_ prefix — DB stores name_tag without prefix
            mkt_tag = source[4:]  # "mkt_pai_xxx" → "pai_xxx"
            
            async with _gs() as _s:
                # Lookup link by name_tag
                row = (await _s.execute(_t(
                    "SELECT id, marketer, platform, group_chat_id FROM marketing_invite_links "
                    "WHERE name_tag = :tag AND link_type = 'bot_deeplink' AND is_revoked = false LIMIT 1"
                ), {"tag": mkt_tag})).first()
                
                if row:
                    link_id = row.id
                    # Lookup user_id
                    ur = (await _s.execute(_t(
                        "SELECT id FROM users WHERE telegram_id = :tg"
                    ), {"tg": tg_user.id})).first()
                    user_id = ur[0] if ur else None
                    
                    # Idempotency: same link + same user within 24h → skip
                    dup = (await _s.execute(_t(
                        "SELECT 1 FROM marketing_invite_joins WHERE link_id = :lid AND telegram_id = :tg "
                        "AND joined_at > now() - interval '24 hours' LIMIT 1"
                    ), {"lid": link_id, "tg": tg_user.id})).first()
                    
                    if not dup:
                        await _s.execute(_t(
                            "INSERT INTO marketing_invite_joins "
                            "(link_id, telegram_id, user_id, tg_username, tg_first_name, tg_last_name) "
                            "VALUES (:lid, :tg, :uid, :un, :fn, :ln)"
                        ), {
                            "lid": link_id, "tg": tg_user.id, "uid": user_id,
                            "un": tg_user.username, "fn": tg_user.first_name, "ln": tg_user.last_name,
                        })
                        await _s.commit()
                        logger.info(
                            "marketing bot-deeplink attribution: tg=%s marketer=%s platform=%s link_id=%s",
                            tg_user.id, row.marketer, row.platform, link_id,
                        )
                        # Discord notification (fire-and-forget)
                        try:
                            from shared.discord_notify import notify_marketer_join
                            import asyncio as _aio
                            count_r = (await _s.execute(_t(
                                "SELECT COUNT(*) FROM marketing_invite_joins WHERE link_id = :lid"
                            ), {"lid": link_id})).scalar()
                            _aio.create_task(notify_marketer_join(
                                marketer=row.marketer, platform=row.platform,
                                group_title="(via bot deep-link)",
                                telegram_id=tg_user.id, tg_username=tg_user.username,
                                tg_first_name=tg_user.first_name, link_id=link_id,
                                total_joins_for_link=int(count_r or 1),
                            ))
                        except Exception as _nx:
                            logger.warning("discord notify (bot deeplink) failed: %s", _nx)
        except Exception as _exc:
            logger.warning("mkt_ deeplink processing failed: %s", _exc)
        
        # Send NEW marketing-specific welcome (2 messages)
        try:
            import os
            from telegram import InlineKeyboardButton as _IKB, InlineKeyboardMarkup as _IKM
            # Msg 1: marketing welcome image + greeting + 2 pink free group buttons
            cap1 = (
                "🎉 สวัสดีค่ะ! ขอบคุณที่ทักหาเรานะคะ 💕\n\n"
                "ลูกค้าใหม่ ลองดูคอนเทนต์ฟรีๆ ก่อนได้:"
            )
            kb1 = _IKM([
                [_IKB("💖 รวมกลุ่มฟรีเจริญพร 💖", url="https://t.me/+hEx_Uio0vXEzNTVl")],
                [_IKB("💖 แจ้งข่าวสาวเจริญพร 💖", url="https://t.me/+gUR2P81kttdjMTI1")],
            ])
            img_path = "/app/assets/campaigns/marketing_welcome.png"
            if os.path.exists(img_path):
                with open(img_path, "rb") as _img:
                    await update.message.reply_photo(photo=_img, caption=cap1, parse_mode="HTML", reply_markup=kb1)
            else:
                await update.message.reply_text(cap1, parse_mode="HTML", reply_markup=kb1)
            
            # Msg 2: GIF banner + main menu (with 👑 ดูแพ็กเกจ on top)
            cap2 = "หรือเลือกเมนูได้เลย ⬇️"
            kb2 = _IKM([
                [_IKB("👑 ดูแพ็กเกจ VIP ทั้งหมด 👑", callback_data="view_packages")],
                [_IKB("🎰 VIPมีคนชัก ฿100 — ลุ้น GOD ทุกจันทร์!", callback_data="view_shaker")],
                [_IKB("🎁 เติมสิทธิ์หมุนกาชาปอง", callback_data="view_gacha_buy")],
                [_IKB("📊 ข้อมูลของฉัน", web_app=WebAppInfo(url="https://telebord.net/webapp/customer"))],
                [_IKB("🎁 ชวนเพื่อน ได้ VIP ฟรี!", callback_data="referral_menu")],
                [
                    _IKB("📋 เช็คเครดิต/รีวิว", url="https://t.me/+hv7uXYj4bxFhODZl"),
                    _IKB("👀 ดูตัวอย่างงาน", url="https://t.me/+Q0Qf-4t8TQo3YTBl"),
                ],
                [_IKB("🆓 ห้องฟรี (ทั้งหมด)", url="https://t.me/addlist/w0YSyuHC_aE2ZGVl")],
                [_IKB("👩‍💼 ติดต่อแอดมิน", url="https://t.me/sperm6969")],
            ])
            gif_path = "/app/assets/campaigns/vip_banner_live.gif"
            if os.path.exists(gif_path):
                with open(gif_path, "rb") as _gif:
                    await update.message.reply_animation(animation=_gif, caption=cap2, parse_mode="HTML", reply_markup=kb2)
            else:
                await update.message.reply_text(cap2, parse_mode="HTML", reply_markup=kb2)
            return  # Don't show default menu — we already showed marketing welcome
        except Exception as _mkx:
            logger.warning("marketing welcome render failed (fallback to default): %s", _mkx)
            # Fall through to default menu below
    
    # Handle referral deep link: /start ref_{CODE}
    if source and source.startswith("ref_"):
        ref_code = source.replace("ref_", "", 1)
        from bots.sales_bot.handlers.referral import handle_referral_start
        await handle_referral_start(update, context, ref_code)
        # Always show packages menu for referred users
        from bots.sales_bot.handlers.packages import view_packages_command
        await view_packages_command(update, context)
        return

    # Handle invite deep link: /start invite
    if source == "invite":
        from bots.sales_bot.handlers.referral import invite_command
        await invite_command(update, context)
        return

    # Handle comeback deep link: /start comeback_{code}
    if source and source.startswith("comeback_"):
        promo_code = source.replace("comeback_", "", 1)
        handled = await _handle_comeback_start(update, context, promo_code)
        if handled:
            return

    # Handle trial deep link: /start trial — ปิดแล้ว (ยกเลิกโปร 99)
    # if source == "trial":
    #     from bots.sales_bot.handlers.trial import trial_command
    #     await trial_command(update, context)
    #     return

    # Handle upgrade deep link: /start upgrade
    if source == "upgrade":
        from bots.sales_bot.handlers.upsell import upgrade_command
        await upgrade_command(update, context)
        return

    # Handle packages deep link: /start packages
    if source == "packages":
        from bots.sales_bot.handlers.packages import view_packages_command
        await view_packages_command(update, context)
        return

    # Handle gacha buy deeplink (from gacha webapp top-up button)
    if source == "gacha_buy":
        from bots.sales_bot.handlers.gacha_buy import _get_user_credit_balance, _build_buy_caption, _build_buy_keyboard
        state = await _get_user_credit_balance(tg_user.id)
        await update.message.reply_text(
            _build_buy_caption(state),
            parse_mode="HTML",
            reply_markup=_build_buy_keyboard(),
        )
        return

    # Handle gacha promo deeplink (from FREE group ad button)
    if source == "gacha":
        from bots.sales_bot.handlers.gacha_buy import _get_user_credit_balance, _build_buy_caption, _build_buy_keyboard
        state = await _get_user_credit_balance(tg_user.id)
        await update.message.reply_text(
            _build_buy_caption(state),
            parse_mode="HTML",
            reply_markup=_build_buy_keyboard(),
        )
        return

    # Handle shaker deeplink (from FREE group ad button)
    if source == "shaker":
        from bots.sales_bot.handlers.shaker import cmd_shaker
        await cmd_shaker(update, context)
        return

    # MAIN_KBD_V2 — unified builder w/ flash-sale conditional
    dynamic_keyboard = await _build_main_keyboard(tg_user.id)

    # SOCIAL_PROOF_V1 — send welcome photo + dynamic caption + dynamic keyboard
    # FLASH_AWARE: pick image based on active flash sale (03_flash1.png if active)
    try:
        caption = await social_proof.build_welcome_caption(tg_user.first_name)
        img_path = await social_proof.pick_welcome_image_dynamic()
        if img_path and img_path.exists():
            with open(img_path, "rb") as f:
                await update.message.reply_photo(
                    photo=f,
                    caption=caption,
                    parse_mode="HTML",
                    reply_markup=dynamic_keyboard,
                )
        else:
            await update.message.reply_text(
                caption,
                parse_mode="HTML",
                reply_markup=dynamic_keyboard,
                disable_web_page_preview=True,
            )
    except Exception as exc:
        logger.warning("SOCIAL_PROOF_V1 welcome send failed: %s", exc)
        from bots.sales_bot.handlers.packages import view_packages_command
        await view_packages_command(update, context)

    # NEW 2026-06-20 V2: Instant Welcome DM (Stage 0) — only for genuinely new users
    # is_new_user is defined inside session block above (function scope)
    if is_new_user:
        try:
            import asyncio as _a
            await _a.sleep(2.0)
            from shared.welcome_journey import send_instant_welcome
            await send_instant_welcome(
                user_id=user.id, telegram_id=tg_user.id,
                first_name=tg_user.first_name or "", bot=context.bot,
            )
        except Exception as _exc_w:
            logger.warning("instant welcome DM failed: %s", _exc_w)


async def back_to_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Callback: return to the main menu (uses dynamic kbd w/ referral button)."""
    query = update.callback_query
    if not query or not update.effective_user:
        return
    try:
        await query.answer()
    except Exception:
        pass  # callback may be too old / already answered
    tg_user = update.effective_user
    kb = await _build_main_keyboard(tg_user.id)
    try:
        caption = await social_proof.build_welcome_caption(tg_user.first_name)
    except Exception:
        caption = WELCOME_TEXT
    # SAFE_NAV — handles photo origin
    await _navigate(query, caption, kb)


async def free_room_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Callback: show free room info."""
    query = update.callback_query
    if not query:
        return
    try:
        await query.answer()
    except Exception:
        pass  # callback may be too old / already answered

    text = (
        "🆓 <b>ห้องฟรี</b>\n\n"
        "เรามีห้องทดลองให้ดูก่อนตัดสินใจค่ะ\n"
        "สามารถเข้าไปดูบรรยากาศและคุณภาพสัญญาณได้เลย\n\n"
        "📌 กดปุ่มด้านล่างเพื่อขอลิงก์เข้าห้องฟรีค่ะ\n\n"
        "หากสนใจอัปเกรดเป็น VIP ทักแพรได้เลยนะคะ 😊"
    )
    keyboard = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📦 ดูแพ็กเกจ VIP", callback_data="view_packages")],
            [InlineKeyboardButton("🔙 กลับเมนูหลัก", callback_data="back_main")],
        ]
    )
    await _navigate(query, text, keyboard)


async def contact_admin_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Callback: show admin contact info."""
    query = update.callback_query
    if not query:
        return
    try:
        await query.answer()
    except Exception:
        pass  # callback may be too old / already answered

    text = (
        "👩‍💼 <b>ติดต่อแอดมิน</b>\n\n"
        "หากมีปัญหาหรือข้อสงสัยที่แพรช่วยไม่ได้\n"
        "สามารถติดต่อแอดมินได้โดยตรงค่ะ\n\n"
        "📩 พิมพ์ข้อความที่ต้องการส่งถึงแอดมิน\n"
        "แพรจะรีบส่งต่อให้นะคะ 😊"
    )
    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("🔙 กลับเมนูหลัก", callback_data="back_main")]]
    )
    await _navigate(query, text, keyboard)


async def referral_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Callback: referral menu — VIP goes to invite, non-VIP gets upsell."""
    query = update.callback_query
    if not query or not update.effective_user:
        return
    try:
        await query.answer()
    except Exception:
        pass  # callback may be too old / already answered

    from bots.sales_bot.handlers.referral import _is_vip_active
    tg_user = update.effective_user

    if await _is_vip_active(tg_user.id):
        # VIP → show invite link via callback
        from bots.sales_bot.handlers.referral import _get_invite_link_callback
        await _get_invite_link_callback(update, context)
    else:
        # Non-VIP → prompt to subscribe first (with referral image)
        text = (
            "🎁 <b>ชวนเพื่อน ได้ VIP ฟรี!</b>\n\n"
            "สมัคร VIP ก่อน แล้วชวนเพื่อนได้เลยค่ะ\n"
            "ชวน 1 คน = ได้ VIP ฟรี 7 วัน\n"
            "ชวน 5 คน = ได้ VIP ฟรี 30 วัน!\n\n"
            "👉 กดดูแพ็กเกจ VIP เจริญพร แล้วสมัครได้เลย"
        )
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📦 ดูแพ็กเกจ", callback_data="view_packages")],
            [InlineKeyboardButton("🔙 กลับเมนูหลัก", callback_data="back_main")],
        ])
        try:
            img_path = social_proof.pick_campaign_image("referral")
        except Exception:
            img_path = None
        try:
            try:
                await query.message.delete()
            except Exception:
                pass
            if img_path and img_path.exists():
                with open(img_path, "rb") as f:
                    await query.message.chat.send_photo(
                        photo=f, caption=text, parse_mode="HTML", reply_markup=keyboard,
                    )
            else:
                await query.message.chat.send_message(
                    text, parse_mode="HTML", reply_markup=keyboard,
                )
        except Exception:
            await _navigate(query, text, keyboard)


async def view_upgrade_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Callback: show GOD MODE upgrade info + buy buttons (UPG_BUY)."""
    query = update.callback_query
    if not query:
        return
    try:
        await query.answer()
    except Exception:
        pass  # callback may be too old / already answered
    from bots.sales_bot.handlers.upsell import UPGRADE_TEXT
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("💎 GOD MODE 90 วัน ฿1,299", callback_data="buy_1299")],
        [InlineKeyboardButton("👑 GOD MODE ถาวร ฿2,499", callback_data="buy_2499")],
        [InlineKeyboardButton("🔙 กลับเมนูหลัก", callback_data="back_main")],
    ])
    await _navigate(query, UPGRADE_TEXT, keyboard)


def get_start_handlers() -> list:
    """Return all handlers for the start module."""
    return [
        CommandHandler("start", start_command),
        CallbackQueryHandler(back_to_main_menu, pattern="^back_main$"),
        CallbackQueryHandler(free_room_callback, pattern="^free_room$"),
        CallbackQueryHandler(contact_admin_callback, pattern="^contact_admin$"),
        CallbackQueryHandler(view_upgrade_callback, pattern="^view_upgrade$"),
        CallbackQueryHandler(referral_menu_callback, pattern="^referral_menu$"),
    ]
