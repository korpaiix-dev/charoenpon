"""Payments / Finance router."""
from fastapi import APIRouter, Depends, Request, HTTPException
from ..auth.dependencies import get_current_admin, require_role
from ..database import pool
from ..models.schemas import PaymentReject
from ..config import SALES_BOT_TOKEN, GUARDIAN_BOT_TOKEN, ADMIN_BOT_TOKEN
import json
import os
import logging
import httpx
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

PROMO_SONGKRAN_SLUG = "PROMO_SONGKRAN_2026"
PROMO_SONGKRAN_TITLE = "โปรโมชั่นสงกรานต์"
PROMO_SONGKRAN_CHAT_ID = -1003970513277
PROMO_SONGKRAN_START_UTC = datetime(2026, 4, 13, 20, 0, 0)
PROMO_SONGKRAN_END_UTC = datetime(2026, 4, 20, 20, 0, 0)

logger = logging.getLogger(__name__)

ADMIN_GROUP_CHAT_ID = os.getenv("ADMIN_GROUP_CHAT_ID", "-1003830920430")

router = APIRouter(prefix="/api/payments", tags=["payments"])


def get_group_display_title(slug: str) -> str:
    return PROMO_SONGKRAN_TITLE if slug == PROMO_SONGKRAN_SLUG else slug


def get_songkran_special_group() -> SimpleNamespace:
    return SimpleNamespace(chat_id=PROMO_SONGKRAN_CHAT_ID, title=PROMO_SONGKRAN_TITLE)


async def should_include_songkran_bonus_group(user_telegram_id: int, package_id: int | None = None) -> bool:
    if not user_telegram_id:
        return False

    has_existing = await pool.fetchrow(
        """
        SELECT 1
        FROM subscriptions s
        JOIN users u ON u.id = s.user_id
        JOIN packages p ON p.id = s.package_id
        WHERE u.telegram_id = $1
          AND s.status = 'ACTIVE'
          AND s.end_date > NOW()
          AND s.start_date >= $2
          AND s.start_date < $3
          AND p.tier = 'TIER_1299'
        LIMIT 1
        """,
        user_telegram_id,
        PROMO_SONGKRAN_START_UTC,
        PROMO_SONGKRAN_END_UTC,
    )
    if has_existing:
        return True

    if package_id is None:
        return False

    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    if not (PROMO_SONGKRAN_START_UTC <= now_utc < PROMO_SONGKRAN_END_UTC):
        return False

    pkg = await pool.fetchrow("SELECT tier FROM packages WHERE id = $1", package_id)
    return bool(pkg and pkg["tier"] == "TIER_1299")

async def _log(admin_id, action, entity_type, entity_id, details, ip):
    await pool.execute(
        "INSERT INTO dashboard_activity_log (admin_id, action, entity_type, entity_id, details, ip_address) VALUES ($1,$2,$3,$4,$5::jsonb,$6)",
        admin_id, action, entity_type, entity_id, json.dumps(details) if details else None, ip
    )

@router.get("")
async def list_payments(
    page: int = 1, per_page: int = 25, status: str = "all", method: str = "all",
    date_from: str = "", date_to: str = "",
    admin=Depends(get_current_admin)
):
    offset = (page - 1) * per_page
    conditions = []
    params = []
    idx = 1
    
    if status != "all":
        conditions.append(f"p.status = ${idx}::paymentstatus")
        params.append(status)
        idx += 1
    if method != "all":
        conditions.append(f"p.method = ${idx}::paymentmethod")
        params.append(method)
        idx += 1
    if date_from:
        conditions.append(f"p.created_at >= ${idx}::timestamp")
        params.append(date_from)
        idx += 1
    if date_to:
        conditions.append(f"p.created_at <= ${idx}::timestamp")
        params.append(date_to)
        idx += 1
    
    where = "WHERE " + " AND ".join(conditions) if conditions else ""
    
    total = await pool.fetchval(f"SELECT COUNT(*) FROM payments p {where}", *params)
    rows = await pool.fetch(f"""
        SELECT p.*, u.username, u.first_name, u.telegram_id, pk.name as package_name
        FROM payments p
        JOIN users u ON p.user_id = u.id
        JOIN packages pk ON p.package_id = pk.id
        {where}
        ORDER BY p.created_at DESC
        LIMIT {per_page} OFFSET {offset}
    """, *params)
    
    return {
        "items": [dict(r) for r in rows],
        "total": total,
        "page": page,
        "per_page": per_page,
        "pages": (total + per_page - 1) // per_page,
    }

@router.get("/pending")
async def pending_payments(admin=Depends(get_current_admin)):
    """Get pending payments from last 24 hours only.
    Excludes payments where user already has active subscription (approved via Bot)."""
    rows = await pool.fetch("""
        SELECT p.*, u.username, u.first_name, u.telegram_id, pk.name as package_name
        FROM payments p
        JOIN users u ON p.user_id = u.id
        JOIN packages pk ON p.package_id = pk.id
        WHERE p.status = 'PENDING' AND p.created_at >= NOW() - interval '24 hours'
          AND NOT EXISTS (
            SELECT 1 FROM subscriptions s
            WHERE s.user_id = p.user_id AND s.status = 'ACTIVE'
              AND s.start_date > p.created_at - interval '1 hour'
          )
        ORDER BY p.created_at ASC
    """)
    # Auto-confirm stale PENDING payments where user already got subscription via Bot
    stale = await pool.fetch("""
        SELECT p.id FROM payments p
        WHERE p.status = 'PENDING' AND p.created_at >= NOW() - interval '24 hours'
          AND EXISTS (
            SELECT 1 FROM subscriptions s
            WHERE s.user_id = p.user_id AND s.status = 'ACTIVE'
              AND s.start_date > p.created_at - interval '1 hour'
          )
    """)
    if stale:
        stale_ids = [r["id"] for r in stale]
        await pool.execute(
            "UPDATE payments SET status = 'CONFIRMED', verified_by = 0, verified_at = NOW() WHERE id = ANY($1::int[])",
            stale_ids
        )
        logger.info("Auto-confirmed %d stale PENDING payments (already have active sub): %s", len(stale_ids), stale_ids)
    return [dict(r) for r in rows]

@router.get("/pending-expired")
async def pending_expired_payments(admin=Depends(get_current_admin)):
    """Get PENDING payments older than 24 hours (expired/stale).
    Excludes payments where user already has active subscription (approved via Bot)."""
    # Auto-confirm stale ones first
    stale = await pool.fetch("""
        SELECT p.id FROM payments p
        WHERE p.status = 'PENDING' AND p.created_at < NOW() - interval '24 hours'
          AND EXISTS (
            SELECT 1 FROM subscriptions s
            WHERE s.user_id = p.user_id AND s.status = 'ACTIVE'
              AND s.start_date > p.created_at - interval '1 hour'
          )
    """)
    if stale:
        stale_ids = [r["id"] for r in stale]
        await pool.execute(
            "UPDATE payments SET status = 'CONFIRMED', verified_by = 0, verified_at = NOW() WHERE id = ANY($1::int[])",
            stale_ids
        )
        logger.info("Auto-confirmed %d expired PENDING payments: %s", len(stale_ids), stale_ids)
    
    rows = await pool.fetch("""
        SELECT p.*, u.username, u.first_name, u.telegram_id, pk.name as package_name
        FROM payments p
        JOIN users u ON p.user_id = u.id
        JOIN packages pk ON p.package_id = pk.id
        WHERE p.status = 'PENDING' AND p.created_at < NOW() - interval '24 hours'
        ORDER BY p.created_at DESC
        LIMIT 100
    """)
    return [dict(r) for r in rows]

async def _telegram_api(token: str, method: str, payload: dict) -> dict:
    """Call Telegram Bot API. Returns response JSON or raises."""
    url = f"https://api.telegram.org/bot{token}/{method}"
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(url, json=payload)
        data = resp.json()
        if not data.get("ok"):
            logger.warning("Telegram API %s failed: %s", method, data)
        return data


async def _generate_invite_links(package_id: int, user_telegram_id: int | None = None) -> dict[str, str]:
    """Create one-time invite links for all groups the package grants access to.
    
    Uses Guardian Bot via Telegram API (createChatInviteLink).
    Returns {group_slug: invite_link_url}.
    """
    # Get package's groups_access
    pkg = await pool.fetchrow("SELECT groups_access FROM packages WHERE id = $1", package_id)
    if not pkg or not pkg["groups_access"]:
        return {}

    # Parse groups_access (comma-separated or JSON array)
    import json as _json
    raw = pkg["groups_access"].strip()
    if raw.startswith("["):
        try:
            group_slugs = _json.loads(raw)
        except Exception:
            group_slugs = [g.strip().strip('"') for g in raw.split(",") if g.strip()]
    else:
        group_slugs = [g.strip().strip('"') for g in raw.split(",") if g.strip()]

    if user_telegram_id and await should_include_songkran_bonus_group(user_telegram_id, package_id):
        if PROMO_SONGKRAN_SLUG not in group_slugs:
            group_slugs.append(PROMO_SONGKRAN_SLUG)

    invite_links = {}
    for slug in group_slugs:
        if slug == PROMO_SONGKRAN_SLUG:
            special = get_songkran_special_group()
            grp = {"chat_id": special.chat_id, "title": special.title}
        else:
            # Get chat_id from group_registry
            grp = await pool.fetchrow(
                "SELECT chat_id, title FROM group_registry WHERE slug = $1", slug
            )
        if not grp or not grp["chat_id"]:
            logger.warning("Group %s not found in registry, skipping invite link", slug)
            continue

        try:
            result = await _telegram_api(GUARDIAN_BOT_TOKEN, "createChatInviteLink", {
                "chat_id": grp["chat_id"],
                "member_limit": 1,
                "name": f"Dashboard approve",
            })
            if result.get("ok"):
                invite_links[slug] = result["result"]["invite_link"]
            else:
                logger.warning("Failed to create invite link for %s: %s", slug, result)
        except Exception as exc:
            logger.warning("Invite link creation error for %s: %s", slug, exc)

    return invite_links


@router.post("/{payment_id}/approve")
async def approve_payment(payment_id: int, request: Request, admin=Depends(get_current_admin)):
    pay = await pool.fetchrow("""
        SELECT p.*, u.telegram_id as customer_telegram_id, u.first_name as customer_name
        FROM payments p
        JOIN users u ON p.user_id = u.id
        WHERE p.id = $1
    """, payment_id)
    if not pay:
        raise HTTPException(404, "Payment not found")
    if pay["status"] != "PENDING":
        raise HTTPException(400, f"Payment is already {pay['status']}")

    telegram_errors = []

    # ── 1. Update payment status = CONFIRMED ──
    await pool.execute(
        "UPDATE payments SET status = 'CONFIRMED', verified_by = $1, verified_at = NOW() WHERE id = $2",
        admin["telegram_id"], payment_id
    )

    # ── 2. Create subscription (expire old active ones first) ──
    pkg = await pool.fetchrow("SELECT * FROM packages WHERE id = $1", pay["package_id"])
    if pkg:
        # Expire existing active subscriptions
        await pool.execute("""
            UPDATE subscriptions SET status = 'EXPIRED'
            WHERE user_id = $1 AND status = 'ACTIVE'
        """, pay["user_id"])

        # Trial (tier 99) = 24 hours, others use duration_days
        if pkg["tier"] == "99":
            duration_expr = "NOW() + interval '24 hours'"
        else:
            duration_expr = f"NOW() + interval '{pkg['duration_days']} days'"

        await pool.execute(f"""
            INSERT INTO subscriptions (user_id, package_id, status, start_date, end_date, auto_renew, payment_id)
            VALUES ($1, $2, 'ACTIVE', NOW(), {duration_expr}, FALSE, $3)
        """, pay["user_id"], pay["package_id"], payment_id)

        # Update user total_spent
        await pool.execute("""
            UPDATE users SET total_spent = COALESCE(total_spent, 0) + $1 WHERE id = $2
        """, pay["amount"], pay["user_id"])

    # ── 3. Generate invite links via Guardian Bot ──
    invite_links = {}
    links_list = []
    try:
        if GUARDIAN_BOT_TOKEN and pkg:
            invite_links = await _generate_invite_links(pay["package_id"], pay["customer_telegram_id"])
            for slug, link in invite_links.items():
                grp = await pool.fetchrow(
                    "SELECT title FROM group_registry WHERE slug = $1", slug
                ) if slug != PROMO_SONGKRAN_SLUG else None
                title = grp["title"] if grp else get_group_display_title(slug)
                links_list.append({"text": f"🚀 {title}", "url": link})
    except Exception as exc:
        logger.error("Failed to generate invite links: %s", exc)
        telegram_errors.append(f"สร้าง invite link ไม่ได้: {exc}")

    # ── 4. Send DM to customer via Sales Bot ──
    try:
        if SALES_BOT_TOKEN and pay["customer_telegram_id"] and pkg:
            duration_days = pkg["duration_days"]
            expire_date = (datetime.utcnow() + timedelta(
                hours=24 if pkg["tier"] == "99" else duration_days * 24
            )).strftime("%d/%m/%Y")

            msg = (
                f"✅ <b>อนุมัติยอด {int(pay['amount'])} บาท เรียบร้อยค่ะ</b>\n"
                f"📦 แพ็กเกจ: {pkg['name']}\n"
                f"📅 หมดอายุ: {expire_date}\n\n"
                f"👇 <b>กดเข้ากลุ่มที่ปุ่มด้านล่างได้เลย</b>\n\n"
                f"🆓 <b>ห้องฟรี:</b> https://t.me/addlist/2xN-ag15W4U2MTNl"
            )

            # Build inline keyboard (2 buttons per row)
            keyboard_rows = []
            for i in range(0, len(links_list), 2):
                row = [{"text": b["text"], "url": b["url"]} for b in links_list[i:i+2]]
                keyboard_rows.append(row)

            payload = {
                "chat_id": pay["customer_telegram_id"],
                "text": msg,
                "parse_mode": "HTML",
            }
            if keyboard_rows:
                payload["reply_markup"] = {"inline_keyboard": keyboard_rows}

            result = await _telegram_api(SALES_BOT_TOKEN, "sendMessage", payload)
            if not result.get("ok"):
                telegram_errors.append(f"ส่ง DM ลูกค้าไม่ได้: {result.get('description', 'unknown')}")
    except Exception as exc:
        logger.error("Failed to send DM to customer: %s", exc)
        telegram_errors.append(f"ส่ง DM ลูกค้าไม่ได้: {exc}")

    # ── 5. Edit admin group message ──
    # Note: Payment model doesn't store admin_message_id, so we send a new
    # confirmation message to admin group instead of editing the original
    try:
        if ADMIN_BOT_TOKEN and ADMIN_GROUP_CHAT_ID:
            admin_name = admin.get("display_name") or admin.get("username") or "Dashboard Admin"
            notify_msg = (
                f"✅ <b>อนุมัติจาก Dashboard</b>\n"
                f"💰 ยอด: {int(pay['amount'])} บาท\n"
                f"📦 แพ็กเกจ: {pkg['name'] if pkg else 'N/A'}\n"
                f"👤 ลูกค้า: {pay['customer_name'] or 'N/A'} (TG: {pay['customer_telegram_id']})\n"
                f"👮 อนุมัติโดย: {admin_name}"
            )
            await _telegram_api(ADMIN_BOT_TOKEN, "sendMessage", {
                "chat_id": ADMIN_GROUP_CHAT_ID,
                "text": notify_msg,
                "parse_mode": "HTML",
            })
    except Exception as exc:
        logger.error("Failed to notify admin group: %s", exc)
        telegram_errors.append(f"แจ้ง admin group ไม่ได้: {exc}")

    # ── 6. Sync Google Sheets ──
    try:
        import sys
        if "/root/charoenpon" not in sys.path:
            sys.path.insert(0, "/root/charoenpon")
        from sheets.income_log import IncomeLogSheet
        from sheets.daily_revenue import DailyRevenueSheet
        from sheets.daily_summary import DailySummarySheet
        from sheets.members import MembersSheet
        await DailyRevenueSheet.update()
        await DailySummarySheet.update()
        await IncomeLogSheet.log_payment(payment_id, approved_by=admin.get("display_name", "Dashboard"))
        await MembersSheet.update_member(pay["user_id"])
        logger.info("Sheets synced for dashboard approval payment %d", payment_id)
    except Exception as exc:
        logger.warning("Sheets sync failed (non-critical): %s", exc)
        telegram_errors.append(f"Sync Sheets ไม่ได้: {exc}")

    # ── 7. Check referral reward ──
    try:
        import sys
        if "/root/charoenpon" not in sys.path:
            sys.path.insert(0, "/root/charoenpon")
        from bots.sales_bot.handlers.referral import process_referral_reward
        import telegram as tg
        sales_bot = tg.Bot(token=SALES_BOT_TOKEN)
        await process_referral_reward(pay["customer_telegram_id"], sales_bot)
    except Exception as exc:
        logger.warning("Referral reward failed (non-critical): %s", exc)

    # ── 8. Flash sale slot increment ──
    try:
        if pkg and pkg["tier"] == "300":
            import sys
            if "/root/charoenpon" not in sys.path:
                sys.path.insert(0, "/root/charoenpon")
            from bots.sales_bot.handlers.flash_sale import increment_sold_slot
            success, sold, total = await increment_sold_slot(pay["package_id"])
            if success:
                logger.info("Flash sale slot incremented: %d/%d", sold, total)
    except Exception as exc:
        logger.warning("Flash sale slot increment failed (non-critical): %s", exc)

    # ── Mark teaser clicks as converted ──
    try:
        await pool.execute("""
            UPDATE teaser_clicks SET converted = TRUE
            WHERE user_id = $1 AND converted = FALSE
        """, pay["customer_telegram_id"])
    except Exception:
        pass

    # ── Activity log ──
    ip = request.client.host if request.client else None
    await _log(admin["id"], "approve_payment", "payment", payment_id,
               {"amount": float(pay["amount"]), "user_id": pay["user_id"],
                "telegram_sent": len(telegram_errors) == 0}, ip)

    # Return result — DB is always approved, report telegram errors separately
    if telegram_errors:
        return {
            "ok": True,
            "warning": "อนุมัติแล้วแต่มีข้อผิดพลาดบางส่วน",
            "errors": telegram_errors,
        }
    return {"ok": True}

@router.post("/{payment_id}/reject")
async def reject_payment(payment_id: int, req: PaymentReject, request: Request, admin=Depends(get_current_admin)):
    pay = await pool.fetchrow("SELECT * FROM payments WHERE id = $1", payment_id)
    if not pay:
        raise HTTPException(404, "Payment not found")
    if pay["status"] != "PENDING":
        raise HTTPException(400, f"Payment is already {pay['status']}")
    
    await pool.execute(
        "UPDATE payments SET status = 'REJECTED', reject_reason = $1, verified_by = $2, verified_at = NOW() WHERE id = $3",
        req.reason, admin["telegram_id"], payment_id
    )
    
    ip = request.client.host if request.client else None
    await _log(admin["id"], "reject_payment", "payment", payment_id,
               {"amount": float(pay["amount"]), "reason": req.reason}, ip)
    return {"ok": True}

@router.get("/summary")
async def payment_summary(admin=Depends(require_role("admin"))):
    row = await pool.fetchrow("""
        SELECT
            COALESCE(SUM(CASE WHEN (created_at AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Bangkok')::date = (NOW() AT TIME ZONE 'Asia/Bangkok')::date THEN amount END), 0) as today,
            COALESCE(SUM(CASE WHEN created_at >= date_trunc('week', (NOW() AT TIME ZONE 'Asia/Bangkok')::date) THEN amount END), 0) as week,
            COALESCE(SUM(CASE WHEN created_at >= date_trunc('month', (NOW() AT TIME ZONE 'Asia/Bangkok')::date) THEN amount END), 0) as month,
            COALESCE(SUM(CASE WHEN created_at >= date_trunc('year', (NOW() AT TIME ZONE 'Asia/Bangkok')::date) THEN amount END), 0) as year
        FROM payments WHERE status = 'CONFIRMED'
    """)
    return {k: float(v) for k, v in dict(row).items()}

@router.get("/chart/by-package")
async def chart_by_package(days: int = 30, admin=Depends(require_role("admin"))):
    rows = await pool.fetch("""
        SELECT pk.name, pk.tier, COALESCE(SUM(p.amount), 0) as total
        FROM payments p JOIN packages pk ON p.package_id = pk.id
        WHERE p.status = 'CONFIRMED' AND pk.is_active = TRUE AND p.created_at >= (NOW() AT TIME ZONE 'Asia/Bangkok')::date - $1 * interval '1 day'
        GROUP BY pk.name, pk.tier ORDER BY total DESC
    """, days)
    return [dict(r) for r in rows]

@router.get("/chart/by-method")
async def chart_by_method(days: int = 30, admin=Depends(require_role("admin"))):
    rows = await pool.fetch("""
        SELECT method::text, COALESCE(SUM(amount), 0) as total, COUNT(*) as count
        FROM payments
        WHERE status = 'CONFIRMED' AND created_at >= (NOW() AT TIME ZONE 'Asia/Bangkok')::date - $1 * interval '1 day'
        GROUP BY method ORDER BY total DESC
    """, days)
    return [dict(r) for r in rows]

@router.get("/{payment_id}/slip")
async def get_slip(payment_id: int, admin=Depends(get_current_admin)):
    row = await pool.fetchrow("SELECT slip_url, slip_file_id FROM payments WHERE id = $1", payment_id)
    if not row:
        raise HTTPException(404, "Payment not found")
    return {"slip_url": row["slip_url"], "slip_file_id": row["slip_file_id"]}

@router.get("/{payment_id}/slip-image")
async def get_slip_image(payment_id: int, admin=Depends(get_current_admin)):
    """Proxy slip image from Telegram for display in dashboard."""
    from fastapi.responses import StreamingResponse

    row = await pool.fetchrow("SELECT slip_file_id FROM payments WHERE id = $1", payment_id)
    if not row:
        raise HTTPException(404, "Payment not found")
    if not row["slip_file_id"]:
        raise HTTPException(404, "No slip image")

    token = SALES_BOT_TOKEN
    if not token:
        raise HTTPException(500, "SALES_BOT_TOKEN not configured")

    # Get file path from Telegram
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(f"https://api.telegram.org/bot{token}/getFile", params={"file_id": row["slip_file_id"]})
        data = resp.json()
        if not data.get("ok"):
            raise HTTPException(502, f"Telegram getFile failed: {data.get('description', 'unknown')}")
        file_path = data["result"]["file_path"]

        # Download the actual file and stream it back
        file_resp = await client.get(f"https://api.telegram.org/file/bot{token}/{file_path}")
        if file_resp.status_code != 200:
            raise HTTPException(502, "Failed to download slip from Telegram")

        # Telegram often returns application/octet-stream, force image type based on extension
        ext = file_path.rsplit(".", 1)[-1].lower() if "." in file_path else "jpg"
        mime_map = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "gif": "image/gif", "webp": "image/webp"}
        content_type = mime_map.get(ext, "image/jpeg")
        return StreamingResponse(
            iter([file_resp.content]),
            media_type=content_type,
            headers={"Cache-Control": "private, max-age=3600"},
        )
