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

ADMIN_GROUP_CHAT_ID = os.getenv("ADMIN_GROUP_CHAT_ID", "")

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
    # FIX 2025-05-21 (Phase D-6-business): clamp pagination to prevent abusive page sizes
    per_page = max(1, min(per_page, 100))
    page = max(1, page)
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
    # FIX 2025-05-21 (Phase D-2-business): หยุด auto-CONFIRM payment ของ user ที่มี active sub แล้ว
    # (ลูกค้าจ่ายซ้ำ → revenue บวกเก๊, refund ยาก) — เปลี่ยนเป็น flag REJECTED + reject_reason ชัดเจน
    # ให้ admin ตัดสินใจ refund หรือ extend เอง
    if stale:
        stale_ids = [r["id"] for r in stale]
        await pool.execute(
            """
            UPDATE payments
               SET status = 'REJECTED',
                   verified_by = 0, verified_at = NOW(),
                   reject_reason = COALESCE(reject_reason, '') || ' [auto-DUPLICATE] ลูกค้ามี active sub อยู่แล้ว — รอ admin ตัดสินใจ refund/extend'
             WHERE id = ANY($1::int[]) AND status = 'PENDING'
            """,
            stale_ids
        )
        logger.warning("Auto-flagged %d stale payments as REJECTED/DUPLICATE (was: auto-CONFIRM, dangerous): %s", len(stale_ids), stale_ids)
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
    # FIX 2025-05-21 (Phase D-2-business): หยุด auto-CONFIRM — ใช้ REJECTED + reject_reason แทน
    if stale:
        stale_ids = [r["id"] for r in stale]
        await pool.execute(
            """
            UPDATE payments
               SET status = 'REJECTED',
                   verified_by = 0, verified_at = NOW(),
                   reject_reason = COALESCE(reject_reason, '') || ' [auto-DUPLICATE expired] ลูกค้ามี active sub อยู่แล้ว — รอ admin ตัดสินใจ refund/extend'
             WHERE id = ANY($1::int[]) AND status = 'PENDING'
            """,
            stale_ids
        )
        logger.warning("Auto-flagged %d expired stale payments as REJECTED/DUPLICATE (was: auto-CONFIRM, dangerous): %s", len(stale_ids), stale_ids)
    
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


async def _generate_invite_links(package_id: int, user_telegram_id: int | None = None, return_titles: bool = False):
    """Create one-time invite links for all groups the package grants access to.
    
    Uses Guardian Bot via Telegram API (createChatInviteLink).
    Returns {group_slug: invite_link_url} by default.
    FIX 2025-05-21 (Phase D-9-business): when return_titles=True, returns
    {group_slug: {"url": link, "title": title}} so callers don't need an N+1 SELECT.
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
    # FIX 2025-05-21 (Phase D-9-business): also collect titles to avoid N+1 lookup in caller
    titles: dict[str, str] = {}
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
                titles[slug] = grp["title"] or get_group_display_title(slug)
            else:
                logger.warning("Failed to create invite link for %s: %s", slug, result)
        except Exception as exc:
            logger.warning("Invite link creation error for %s: %s", slug, exc)

    if return_titles:
        return {slug: {"url": url, "title": titles.get(slug, get_group_display_title(slug))}
                for slug, url in invite_links.items()}
    return invite_links


@router.post("/{payment_id}/approve")
async def approve_payment(payment_id: int, request: Request, admin=Depends(get_current_admin)):
    """Dashboard approve payment.

    REFACTOR 2026-06-29 (P1 audit #447):
      ก่อนหน้านี้ endpoint นี้ re-implement subscription create + invite links + DM
      ทับ apply_payment_approval (ของ shared) → ข้าม 16/22 step (sender_ring, blacklist,
      birthday bonus, onboarding rewards, gachapon credits, shaker, lifetime guard,
      record_payment_received, discount apply, comeback mark, log_admin_action,
      marketing attribution).
      
      ตอนนี้ route ผ่าน apply_payment_approval(source=ADMIN_BY_PID) → side-effect ครบ.
      เก็บ dashboard-specific extras: double-approve guard, admin-group notify,
      teaser_clicks mark, dashboard activity log.
    """
    telegram_errors: list[str] = []

    # ── 1. Pre-check (guard before unified service does the heavy lift) ──
    async with pool.acquire() as conn:
        pay = await conn.fetchrow("""
            SELECT p.*, u.telegram_id AS customer_telegram_id, u.first_name AS customer_name,
                   u.is_banned AS user_is_banned
            FROM payments p
            JOIN users u ON u.id = p.user_id
            WHERE p.id = $1
        """, payment_id)
        if not pay:
            raise HTTPException(404, "Payment not found")
        if pay["status"] != "PENDING":
            raise HTTPException(409, f"Payment already {pay['status']}")
        if pay["user_is_banned"]:
            raise HTTPException(403, f"ลูกค้า (TG: {pay['customer_telegram_id']}) ถูกแบน — /unban ก่อน")

        # Double-approve guard: same package within 15 min (preserve from old logic)
        recent = await conn.fetchrow("""
            SELECT id, amount, created_at FROM payments
            WHERE user_id = $1 AND status = 'CONFIRMED' AND id <> $2
              AND package_id = $3
              AND created_at >= NOW() - INTERVAL '15 minutes'
            ORDER BY created_at DESC LIMIT 1
        """, pay["user_id"], payment_id, pay["package_id"])
        if recent:
            raise HTTPException(
                409,
                f"ลูกค้านี้เพิ่งถูกอนุมัติไปแล้ว (payment #{recent['id']}, ฿{recent['amount']}, {recent['created_at'].strftime('%H:%M:%S')}). กดอนุมัติซ้ำไม่ได้"
            )

        pkg = await conn.fetchrow("SELECT * FROM packages WHERE id = $1", pay["package_id"])
        if not pkg:
            raise HTTPException(404, "Package not found")

    # ── 2. Route through unified service ──
    try:
        from shared.payment_approval import (
            apply_payment_approval, ApprovalInput, ApprovalSource,
        )
        from decimal import Decimal as _Dec
        result = await apply_payment_approval(ApprovalInput(
            user_id=pay["user_id"],
            telegram_id=pay["customer_telegram_id"],
            source=ApprovalSource.ADMIN_BY_PID,
            amount_paid=_Dec(str(pay["amount"])),
            explicit_package_id=pay["package_id"],
            admin_id=admin.get("telegram_id"),
            payment_id=payment_id,
            slip_trans_ref=pay.get("slip_trans_ref"),
            slip_hash=pay.get("slip_hash"),
            sender_name=pay.get("sender_name"),
            sender_bank_name=pay.get("sender_bank_name"),
            sender_bank_account=pay.get("sender_bank_account"),
            slip_file_id=pay.get("slip_file_id"),
            method=str(pay.get("method") or "SLIP"),
            matched_receiver_account_id=pay.get("matched_receiver_account_id"),
            skip_sender_ring=True,   # admin override — they vetted it
        ))
    except Exception as exc:
        logger.exception("[dashboard approve] apply_payment_approval crashed pid=%s: %s", payment_id, exc)
        raise HTTPException(500, f"Approval service crashed: {exc}")

    if not result.success:
        # Service refused (banned, dup, sender_ring (shouldn't with override), etc.)
        err_map = {
            "user_banned": 403,
            "dup_transref": 409,
            "dup_hash": 409,
            "blacklisted_sender": 403,
            "blacklisted_slip": 403,
            "sender_ring": 403,
            "payment_not_found": 404,
            "package_not_found": 404,
        }
        # dup_transref / dup_hash come like "dup_transref:123"
        err_key = (result.error or "").split(":")[0]
        status = err_map.get(err_key, 400)
        raise HTTPException(status, result.error_details or result.error or "approval failed")

    # ── 3. Admin-group notify (dashboard-specific) ──
    try:
        if ADMIN_BOT_TOKEN and ADMIN_GROUP_CHAT_ID:
            admin_name = admin.get("display_name") or admin.get("username") or "Dashboard Admin"
            notify_msg = (
                f"✅ <b>อนุมัติจาก Dashboard</b>\n"
                f"💰 ยอด: {int(pay['amount'])} บาท\n"
                f"📦 แพ็กเกจ: {pkg['name']}\n"
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

    # ── 4. Mark teaser clicks converted ──
    try:
        await pool.execute("""
            UPDATE teaser_clicks SET converted = TRUE
            WHERE user_id = $1 AND converted = FALSE
        """, pay["customer_telegram_id"])
    except Exception:
        pass

    # ── 5. Activity log ──
    ip = request.client.host if request.client else None
    await _log(admin["id"], "approve_payment", "payment", payment_id,
               {"amount": float(pay["amount"]), "user_id": pay["user_id"],
                "subscription_id": result.subscription_id,
                "via": "apply_payment_approval"}, ip)

    if not result.customer_dm_sent and not result.is_lifetime:
        telegram_errors.append("ส่ง DM ลูกค้าไม่สำเร็จ (ระบบจะ alert ห้องแอดมินแล้ว)")
    if telegram_errors:
        return {"ok": True, "warning": "อนุมัติแล้วแต่มีข้อผิดพลาดบางส่วน", "errors": telegram_errors}
    return {"ok": True, "subscription_id": result.subscription_id}


@router.post("/{payment_id}/reject")
async def reject_payment(payment_id: int, req: PaymentReject, request: Request, admin=Depends(get_current_admin)):
    pay = await pool.fetchrow("""
        SELECT p.*, (SELECT telegram_id FROM users WHERE id = p.user_id) AS customer_telegram_id,
               (SELECT first_name FROM users WHERE id = p.user_id) AS customer_name
        FROM payments p WHERE p.id = $1
    """, payment_id)
    if not pay:
        raise HTTPException(404, "Payment not found")
    if pay["status"] != "PENDING":
        raise HTTPException(400, f"Payment is already {pay['status']}")
    
    await pool.execute(
        "UPDATE payments SET status = 'REJECTED', reject_reason = $1, verified_by = $2, verified_at = NOW() WHERE id = $3",
        req.reason, admin["telegram_id"], payment_id
    )
    
    # FIX 2026-06-25 (audit): DM customer about rejection (was missing — silent reject)
    dm_sent = False
    try:
        if SALES_BOT_TOKEN and pay["customer_telegram_id"]:
            # Phase A.2 (2026-06-27): if admin chose a custom message → use it.
            # Otherwise fallback to default templated message.
            custom = (req.customer_message or "").strip()
            if custom:
                msg = custom
            else:
                msg = (
                    f"❌ <b>สลิปไม่ผ่านการตรวจสอบ</b>\n"
                    f"💰 ยอด: {int(pay['amount'])} บาท\n"
                    f"📝 เหตุผล: {req.reason}\n\n"
                    f"กรุณาตรวจสอบและส่งสลิปใหม่ หรือทักแอดมินถ้าต้องการสอบถาม"
                )
            result = await _telegram_api(SALES_BOT_TOKEN, "sendMessage", {
                "chat_id": pay["customer_telegram_id"],
                "text": msg, "parse_mode": "HTML",
            })
            dm_sent = bool(result.get("ok"))
    except Exception as exc:
        logger.warning("[dashboard reject] DM customer failed: %s", exc)
    
    ip = request.client.host if request.client else None
    await _log(admin["id"], "reject_payment", "payment", payment_id,
               {"amount": float(pay["amount"]), "reason": req.reason, "dm_sent": dm_sent}, ip)
    return {"ok": True, "dm_sent": dm_sent}

@router.post("/{payment_id}/cancel")
async def cancel_payment(payment_id: int, req: PaymentReject, request: Request, admin=Depends(require_role("owner"))):
    """Reverse a CONFIRMED payment (refund/chargeback/fraud): expire subscription + undo receiver-pool
    credit + refund discount credit + re-sync total_spent. Owner-only. Uses shared.reverse_payment_approval.
    """
    from shared.payment_approval import reverse_payment_approval
    result = await reverse_payment_approval(payment_id, admin_id=admin.get("telegram_id"), reason=(req.reason or "")[:200])
    if not result.get("ok"):
        raise HTTPException(400, result.get("error") or "cancel_failed")
    ip = request.client.host if request.client else None
    try:
        await _log(admin["id"], "cancel_payment", "payment", payment_id, {"reason": req.reason}, ip)
    except Exception:
        pass
    # notify admin group (best-effort)
    try:
        if ADMIN_BOT_TOKEN and ADMIN_GROUP_CHAT_ID:
            _rcv = result.get("receiver_undone") or {}
            _cr = result.get("credit_refunded") or {}
            await _telegram_api(ADMIN_BOT_TOKEN, "sendMessage", {
                "chat_id": ADMIN_GROUP_CHAT_ID,
                "text": (f"↩️ <b>ยกเลิก/คืนเงิน Payment #{payment_id}</b>\n"
                         f"💰 ยอด: {result.get('amount')}\n"
                         f"📦 sub ปิด: {result.get('subs_expired')}\n"
                         f"🏦 บัญชีลดยอด: {_rcv.get('amount', '-')}\n"
                         f"💳 คืนเครดิต: {_cr.get('amount', '-')}\n"
                         f"👤 โดย: {admin.get('display_name') or admin.get('id')}\n"
                         f"📝 {req.reason or '-'}"),
                "parse_mode": "HTML",
            })
    except Exception:
        pass
    return result


@router.get("/summary")
async def payment_summary(admin=Depends(require_role("admin"))):
    # FIX 2026-06-27 (Phase A.2): align timezone with dashboard/summary
    # created_at stored as UTC; dashboard.py uses (... AT TIME ZONE UTC AT TIME ZONE Asia/Bangkok)
    # payments was using only ::date AT TIME ZONE Asia/Bangkok which gave inconsistent results
    row = await pool.fetchrow("""
        SELECT
            COALESCE(SUM(CASE WHEN (created_at AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Bangkok')::date = (NOW() AT TIME ZONE 'Asia/Bangkok')::date THEN amount END), 0) as today,
            COALESCE(SUM(CASE WHEN (created_at AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Bangkok')::date >= date_trunc('week', (NOW() AT TIME ZONE 'Asia/Bangkok')::date)::date THEN amount END), 0) as week,
            COALESCE(SUM(CASE WHEN (created_at AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Bangkok')::date >= date_trunc('month', (NOW() AT TIME ZONE 'Asia/Bangkok')::date)::date THEN amount END), 0) as month,
            COALESCE(SUM(CASE WHEN (created_at AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Bangkok')::date >= date_trunc('year', (NOW() AT TIME ZONE 'Asia/Bangkok')::date)::date THEN amount END), 0) as year
        FROM payments WHERE status = 'CONFIRMED' AND amount > 0
    """)
    return {k: float(v) for k, v in dict(row).items()}

@router.get("/chart/by-package")
async def chart_by_package(days: int = 30, admin=Depends(require_role("admin"))):
    rows = await pool.fetch("""
        SELECT pk.name, pk.tier, COALESCE(SUM(p.amount), 0) as total
        FROM payments p JOIN packages pk ON p.package_id = pk.id
        WHERE p.status = 'CONFIRMED' AND p.amount > 0 AND pk.is_active = TRUE AND p.created_at >= (NOW() AT TIME ZONE 'Asia/Bangkok')::date - $1 * interval '1 day'
        GROUP BY pk.name, pk.tier ORDER BY total DESC
    """, days)
    return [dict(r) for r in rows]

@router.get("/chart/by-method")
async def chart_by_method(days: int = 30, admin=Depends(require_role("admin"))):
    rows = await pool.fetch("""
        SELECT method::text, COALESCE(SUM(amount), 0) as total, COUNT(*) as count
        FROM payments
        WHERE status = 'CONFIRMED' AND amount > 0 AND created_at >= (NOW() AT TIME ZONE 'Asia/Bangkok')::date - $1 * interval '1 day'
        GROUP BY method ORDER BY total DESC
    """, days)
    return [dict(r) for r in rows]

@router.get("/{payment_id}/slip")
async def get_slip(payment_id: int, admin=Depends(get_current_admin)):
    row = await pool.fetchrow("SELECT slip_url, slip_file_id FROM payments WHERE id = $1", payment_id)
    if not row:
        raise HTTPException(404, "Payment not found")
    return {"slip_url": row["slip_url"], "slip_file_id": row["slip_file_id"]}



@router.get("/{payment_id}/detail")
async def payment_detail(payment_id: int, admin=Depends(get_current_admin)):
    """Full payment detail for popup modal — purchase + customer + slip2go retry + verification."""
    pay = await pool.fetchrow("""
        SELECT p.*,
               u.telegram_id, u.username, u.first_name, u.last_name, u.phone,
               u.total_spent, u.loyalty_rank, u.is_banned, u.is_blocked_bot,
               u.created_at AS user_created_at,
               pk.name AS package_name, pk.tier::text AS package_tier, pk.price AS package_price,
               pk.duration_days
        FROM payments p
        LEFT JOIN users u ON u.id = p.user_id
        LEFT JOIN packages pk ON pk.id = p.package_id
        WHERE p.id = $1
    """, payment_id)
    if not pay:
        raise HTTPException(404, "Payment not found")

    # Promo campaign info if any
    promo = None
    if pay["promotion_campaign_id"]:
        promo = await pool.fetchrow(
            "SELECT name, normal_price, promo_price FROM promotion_campaigns WHERE id = $1",
            pay["promotion_campaign_id"],
        )

    # Slip2Go retry queue info
    retry = await pool.fetchrow("""
        SELECT id, attempt, max_attempts, status, last_error, next_retry_at, enqueued_at
        FROM slip2go_retry_queue WHERE payment_id = $1 ORDER BY id DESC LIMIT 1
    """, payment_id)

    # Customer past payment count
    past_count = await pool.fetchval(
        "SELECT COUNT(*) FROM payments WHERE user_id = $1 AND status = 'CONFIRMED' AND id != $2",
        pay["user_id"], payment_id,
    )

    # Calculate discount
    package_price = float(pay["package_price"] or 0)
    amount_paid = float(pay["amount"] or 0)
    discount = max(0, package_price - amount_paid) if package_price > 0 else 0

    # Verifier display name
    verifier_name = None
    if pay["verified_by"]:
        verifier = await pool.fetchrow(
            "SELECT display_name FROM dashboard_admins WHERE telegram_id = $1",
            pay["verified_by"],
        )
        if verifier:
            verifier_name = verifier["display_name"]

    return {
        "id": pay["id"],
        "status": pay["status"],
        "method": pay["method"],
        "amount": amount_paid,
        "auto_approved": pay["auto_approved"],
        "created_at": pay["created_at"].isoformat() if pay["created_at"] else None,
        "verified_at": pay["verified_at"].isoformat() if pay["verified_at"] else None,
        "verified_by": pay["verified_by"],
        "verifier_name": verifier_name,
        "reject_reason": pay["reject_reason"],

        "package": {
            "id": pay["package_id"],
            "name": pay["package_name"],
            "tier": pay["package_tier"],
            "price": package_price,
            "duration_days": pay["duration_days"],
        },
        "discount": discount,
        "promo": dict(promo) if promo else None,

        "customer": {
            "id": pay["user_id"],
            "telegram_id": pay["telegram_id"],
            "username": pay["username"],
            "first_name": pay["first_name"],
            "last_name": pay["last_name"],
            "phone": pay["phone"],
            "total_spent": float(pay["total_spent"] or 0),
            "loyalty_rank": pay["loyalty_rank"],
            "is_banned": pay["is_banned"],
            "is_blocked_bot": pay["is_blocked_bot"],
            "past_confirmed_count": int(past_count or 0),
            "is_returning": (past_count or 0) > 0,
            "registered_at": pay["user_created_at"].isoformat() if pay["user_created_at"] else None,
        },

        "slip": {
            "trans_ref": pay["slip_trans_ref"],
            "hash": pay["slip_hash"][:16] + "..." if pay["slip_hash"] else None,
            "sender_name": pay["sender_name"],
            "sender_bank_name": pay["sender_bank_name"],
            "sender_bank_account": pay["sender_bank_account"],
            "has_image": bool(pay["slip_file_id"]),
        },

        "retry": dict(retry) if retry else None,
    }

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


# ====== Sprint 3.4: Bulk actions ======
from pydantic import BaseModel as _BM_bulk
from typing import List as _List

class _BulkApproveReq(_BM_bulk):
    payment_ids: _List[int]

@router.post('/bulk-approve')
async def bulk_approve(req: _BulkApproveReq, request: Request, admin=Depends(get_current_admin)):
    """Approve multiple payments — call existing approve flow per id."""
    results = {'approved': [], 'failed': []}
    for pid in req.payment_ids[:50]:  # safety cap
        try:
            # FIX 2026-06-29 (P0#2): class name + field shape mismatch — bulk-approve
            # was always throwing ImportError/TypeError → every payment marked 'failed'
            # while admin assumed it worked.
            from shared.payment_approval import (
                apply_payment_approval, ApprovalInput, ApprovalSource,
            )
            from shared.database import get_session
            from sqlalchemy import text as _t
            from decimal import Decimal as _D
            async with get_session() as s:
                row = (await s.execute(_t(
                    'SELECT p.user_id, p.amount, p.package_id, p.status::text AS status, '
                    'p.slip_trans_ref, p.slip_hash, p.method::text AS method, '
                    'u.telegram_id '
                    'FROM payments p JOIN users u ON u.id = p.user_id '
                    'WHERE p.id=:id'
                ), {'id': pid})).first()
                if not row:
                    results['failed'].append({'id': pid, 'reason': 'not found'})
                    continue
                if row.status == 'CONFIRMED':
                    results['failed'].append({'id': pid, 'reason': 'already confirmed'})
                    continue
                inp = ApprovalInput(
                    user_id=row.user_id,
                    telegram_id=row.telegram_id,
                    source=ApprovalSource.ADMIN_BY_PID,
                    payment_id=pid,
                    amount_paid=_D(str(row.amount or 0)),
                    explicit_package_id=row.package_id,
                    method=(row.method or 'SLIP'),
                    slip_trans_ref=row.slip_trans_ref,
                    slip_hash=row.slip_hash,
                    admin_id=admin['telegram_id'],
                )
                result = await apply_payment_approval(inp)
                if result.success:
                    results['approved'].append(pid)
                else:
                    results['failed'].append({'id': pid, 'reason': result.error or 'unknown'})
        except Exception as exc:
            results['failed'].append({'id': pid, 'reason': str(exc)[:100]})

    try:
        await pool.execute(
            "INSERT INTO admin_logs (admin_id, action, target_type, target_id, details) "
            "VALUES ($1, 'bulk_approve', 'payment', 0, $2)",
            admin['telegram_id'], f"approved={len(results['approved'])} failed={len(results['failed'])}"
        )
    except Exception:
        pass
    return results


class _BulkRejectReq(_BM_bulk):
    payment_ids: _List[int]
    reason: str = ''

@router.post('/bulk-reject')
async def bulk_reject(req: _BulkRejectReq, admin=Depends(get_current_admin)):
    """Reject multiple payments. Logs per-payment audit row for full trail."""
    results = {'rejected': [], 'failed': []}
    reason_clipped = (req.reason or 'bulk reject')[:200]
    for pid in req.payment_ids[:50]:
        try:
            row = await pool.fetchrow(
                "UPDATE payments SET status='REJECTED', reject_reason=$2, verified_at=NOW(), verified_by=$3 "
                "WHERE id=$1 AND status::text NOT IN ('CONFIRMED','REJECTED') RETURNING id",
                pid, reason_clipped, admin['telegram_id']
            )
            if row:
                results['rejected'].append(pid)
                # Per-payment audit row (full trail vs target_id=0 aggregate)
                try:
                    await pool.execute(
                        "INSERT INTO admin_logs (admin_id, action, target_type, target_id, details) "
                        "VALUES ($1, 'payment_rejected_bulk', 'payment', $2, $3)",
                        admin['telegram_id'], pid, f"reason={reason_clipped[:150]}"
                    )
                except Exception:
                    pass
            else:
                results['failed'].append({'id': pid, 'reason': 'not in pending state'})
        except Exception as exc:
            results['failed'].append({'id': pid, 'reason': str(exc)[:100]})
    # Aggregate summary row (kept for backwards-compat dashboards/reports)
    try:
        await pool.execute(
            "INSERT INTO admin_logs (admin_id, action, target_type, target_id, details) "
            "VALUES ($1, 'bulk_reject', 'payment', 0, $2)",
            admin['telegram_id'], f"rejected={len(results['rejected'])} failed={len(results['failed'])} reason={reason_clipped[:100]}"
        )
    except Exception:
        pass
    return results

