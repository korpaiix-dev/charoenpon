"""Payments / Finance router."""
from fastapi import APIRouter, Depends, Request, HTTPException
from ..auth.dependencies import get_current_admin, require_role
from ..database import pool
from ..models.schemas import PaymentReject
import json

router = APIRouter(prefix="/api/payments", tags=["payments"])

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
    """Get pending payments from last 24 hours only."""
    rows = await pool.fetch("""
        SELECT p.*, u.username, u.first_name, u.telegram_id, pk.name as package_name
        FROM payments p
        JOIN users u ON p.user_id = u.id
        JOIN packages pk ON p.package_id = pk.id
        WHERE p.status = 'PENDING' AND p.created_at >= NOW() - interval '24 hours'
        ORDER BY p.created_at ASC
    """)
    return [dict(r) for r in rows]

@router.get("/pending-expired")
async def pending_expired_payments(admin=Depends(get_current_admin)):
    """Get PENDING payments older than 24 hours (expired/stale)."""
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

@router.post("/{payment_id}/approve")
async def approve_payment(payment_id: int, request: Request, admin=Depends(get_current_admin)):
    pay = await pool.fetchrow("SELECT * FROM payments WHERE id = $1", payment_id)
    if not pay:
        raise HTTPException(404, "Payment not found")
    if pay["status"] != "PENDING":
        raise HTTPException(400, f"Payment is already {pay['status']}")
    
    await pool.execute(
        "UPDATE payments SET status = 'CONFIRMED', verified_by = $1, verified_at = NOW() WHERE id = $2",
        admin["telegram_id"], payment_id
    )
    
    # Create subscription
    pkg = await pool.fetchrow("SELECT * FROM packages WHERE id = $1", pay["package_id"])
    if pkg:
        await pool.execute("""
            INSERT INTO subscriptions (user_id, package_id, status, start_date, end_date, auto_renew, payment_id)
            VALUES ($1, $2, 'ACTIVE', NOW(), NOW() + ($3 || ' days')::interval, FALSE, $4)
        """, pay["user_id"], pay["package_id"], str(pkg["duration_days"]), payment_id)
    
    ip = request.client.host if request.client else None
    await _log(admin["id"], "approve_payment", "payment", payment_id, 
               {"amount": float(pay["amount"]), "user_id": pay["user_id"]}, ip)
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
            COALESCE(SUM(CASE WHEN created_at::date = CURRENT_DATE THEN amount END), 0) as today,
            COALESCE(SUM(CASE WHEN created_at >= date_trunc('week', CURRENT_DATE) THEN amount END), 0) as week,
            COALESCE(SUM(CASE WHEN created_at >= date_trunc('month', CURRENT_DATE) THEN amount END), 0) as month,
            COALESCE(SUM(CASE WHEN created_at >= date_trunc('year', CURRENT_DATE) THEN amount END), 0) as year
        FROM payments WHERE status = 'CONFIRMED'
    """)
    return {k: float(v) for k, v in dict(row).items()}

@router.get("/chart/by-package")
async def chart_by_package(days: int = 30, admin=Depends(require_role("admin"))):
    rows = await pool.fetch("""
        SELECT pk.name, pk.tier, COALESCE(SUM(p.amount), 0) as total
        FROM payments p JOIN packages pk ON p.package_id = pk.id
        WHERE p.status = 'CONFIRMED' AND p.created_at >= CURRENT_DATE - $1
        GROUP BY pk.name, pk.tier ORDER BY total DESC
    """, days)
    return [dict(r) for r in rows]

@router.get("/chart/by-method")
async def chart_by_method(days: int = 30, admin=Depends(require_role("admin"))):
    rows = await pool.fetch("""
        SELECT method::text, COALESCE(SUM(amount), 0) as total, COUNT(*) as count
        FROM payments
        WHERE status = 'CONFIRMED' AND created_at >= CURRENT_DATE - $1
        GROUP BY method ORDER BY total DESC
    """, days)
    return [dict(r) for r in rows]

@router.get("/{payment_id}/slip")
async def get_slip(payment_id: int, admin=Depends(get_current_admin)):
    row = await pool.fetchrow("SELECT slip_url, slip_file_id FROM payments WHERE id = $1", payment_id)
    if not row:
        raise HTTPException(404, "Payment not found")
    return {"slip_url": row["slip_url"], "slip_file_id": row["slip_file_id"]}
