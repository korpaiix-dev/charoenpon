"""Gacha admin endpoints — overview / prizes / winners / recent activity.

Read-heavy. Toggle/edit kept minimal in v1 (only enable/disable + probability).
"""
from __future__ import annotations

import logging
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..auth.dependencies import require_role
from ..database import pool

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/gacha-admin", tags=["gacha-admin"])


async def _log(admin_id: int, action: str, target_id: int, details: str) -> None:
    try:
        await pool.execute(
            "INSERT INTO admin_logs (admin_id, action, target_type, target_id, details) "
            "VALUES ($1, $2, $3, $4, $5)",
            admin_id, action, "gacha", target_id, details,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("audit log failed: %s", exc)


@router.get("/overview")
async def gacha_overview(admin=Depends(require_role("admin"))):
    """Top-level KPI: pulls today/7d/30d, revenue, RTP actual, free vs paid."""

    # Pulls today (BKK)
    today = await pool.fetchrow("""
        SELECT
            COUNT(*) AS pulls,
            COUNT(DISTINCT user_id) AS users,
            COALESCE(SUM(prize_value_thb), 0) AS prize_value,
            COUNT(*) FILTER (WHERE payment_id IS NOT NULL) AS paid_pulls,
            COUNT(*) FILTER (WHERE payment_id IS NULL)    AS free_pulls
        FROM gachapon_pulls
        WHERE ((pulled_at AT TIME ZONE 'UTC') AT TIME ZONE 'Asia/Bangkok')::date
            = (NOW() AT TIME ZONE 'Asia/Bangkok')::date
    """)

    # 7-day window
    last7 = await pool.fetchrow("""
        SELECT
            COUNT(*) AS pulls,
            COUNT(DISTINCT user_id) AS users,
            COALESCE(SUM(prize_value_thb), 0) AS prize_value,
            COUNT(*) FILTER (WHERE payment_id IS NOT NULL) AS paid_pulls
        FROM gachapon_pulls
        WHERE pulled_at >= NOW() - INTERVAL '7 days'
    """)

    # 30-day window
    last30 = await pool.fetchrow("""
        SELECT
            COUNT(*) AS pulls,
            COUNT(DISTINCT gp.user_id) AS users,
            COALESCE(SUM(prize_value_thb), 0) AS prize_value,
            COUNT(*) FILTER (WHERE payment_id IS NOT NULL) AS paid_pulls,
            COALESCE(SUM(p.amount), 0) AS revenue
        FROM gachapon_pulls gp
        LEFT JOIN payments p ON p.id = gp.payment_id AND p.status = 'CONFIRMED'
        WHERE gp.pulled_at >= NOW() - INTERVAL '30 days'
    """)

    # RTP actual (paid pulls only) — 30d
    rtp_row = await pool.fetchrow("""
        SELECT
            COALESCE(SUM(p.amount), 0) AS revenue,
            COALESCE(SUM(gp.prize_value_thb), 0) AS paid_outs
        FROM gachapon_pulls gp
        JOIN payments p ON p.id = gp.payment_id AND p.status = 'CONFIRMED'
        WHERE gp.pulled_at >= NOW() - INTERVAL '30 days'
    """)
    revenue30 = float(rtp_row["revenue"] or 0)
    paid_outs30 = float(rtp_row["paid_outs"] or 0)
    rtp_actual_pct = (paid_outs30 / revenue30 * 100) if revenue30 > 0 else 0

    return {
        "today": {
            "pulls": int(today["pulls"] or 0),
            "users": int(today["users"] or 0),
            "prize_value": float(today["prize_value"] or 0),
            "paid_pulls": int(today["paid_pulls"] or 0),
            "free_pulls": int(today["free_pulls"] or 0),
        },
        "last_7d": {
            "pulls": int(last7["pulls"] or 0),
            "users": int(last7["users"] or 0),
            "prize_value": float(last7["prize_value"] or 0),
            "paid_pulls": int(last7["paid_pulls"] or 0),
        },
        "last_30d": {
            "pulls": int(last30["pulls"] or 0),
            "users": int(last30["users"] or 0),
            "prize_value": float(last30["prize_value"] or 0),
            "paid_pulls": int(last30["paid_pulls"] or 0),
            "revenue": float(last30["revenue"] or 0),
            "rtp_pct": round(rtp_actual_pct, 2),
        },
    }


@router.get("/prizes")
async def list_prizes(admin=Depends(require_role("admin"))):
    """Both legacy gachapon_prizes + new gacha_prize_pool."""
    # Legacy table — probability is fraction (0-1)
    legacy = await pool.fetch("""
        SELECT code, label, probability, type, value_thb, tier, is_active, image_url
        FROM gachapon_prizes
        ORDER BY probability DESC
    """)
    # New pool — probability_pct is percent (0-100)
    pool_rows = await pool.fetch("""
        SELECT id, code, name, tier, prize_type, value_thb, probability_pct, enabled, sort_order
        FROM gacha_prize_pool
        ORDER BY probability_pct ASC
    """)
    return {
        "legacy_prizes": [dict(r) for r in legacy],
        "prize_pool": [dict(r) for r in pool_rows],
    }


class LegacyPrizeUpdate(BaseModel):
    is_active: Optional[bool] = None
    probability: Optional[float] = None  # fraction 0-1


@router.patch("/prizes/legacy/{code}")
async def update_legacy_prize(code: str, req: LegacyPrizeUpdate,
                              admin=Depends(require_role("admin"))):
    sets = []
    params: list = []
    idx = 1
    if req.is_active is not None:
        sets.append(f"is_active = ${idx}"); params.append(req.is_active); idx += 1
    if req.probability is not None:
        if req.probability < 0 or req.probability > 1:
            raise HTTPException(400, "probability must be 0-1 (fraction)")
        sets.append(f"probability = ${idx}"); params.append(Decimal(str(req.probability))); idx += 1
    if not sets:
        raise HTTPException(400, "no fields to update")
    params.append(code)
    sql = f"UPDATE gachapon_prizes SET {', '.join(sets)} WHERE code = ${idx} RETURNING code, label"
    row = await pool.fetchrow(sql, *params)
    if not row:
        raise HTTPException(404, "prize not found")
    details = f"code={code} " + " ".join(
        [f"active={req.is_active}" if req.is_active is not None else "",
         f"prob={req.probability}" if req.probability is not None else ""]
    ).strip()
    await _log(admin["telegram_id"], "gacha_prize_legacy_update", 0, details)
    return {"ok": True, "code": code, "label": row["label"]}


class PoolPrizeUpdate(BaseModel):
    enabled: Optional[bool] = None
    probability_pct: Optional[float] = None  # percent 0-100


@router.patch("/prize-pool/{pid}")
async def update_pool_prize(pid: int, req: PoolPrizeUpdate,
                            admin=Depends(require_role("admin"))):
    sets = []
    params: list = []
    idx = 1
    if req.enabled is not None:
        sets.append(f"enabled = ${idx}"); params.append(req.enabled); idx += 1
    if req.probability_pct is not None:
        if req.probability_pct < 0 or req.probability_pct > 100:
            raise HTTPException(400, "probability_pct must be 0-100")
        sets.append(f"probability_pct = ${idx}"); params.append(Decimal(str(req.probability_pct))); idx += 1
    if not sets:
        raise HTTPException(400, "no fields to update")
    sets.append("updated_at = NOW()")
    params.append(pid)
    sql = f"UPDATE gacha_prize_pool SET {', '.join(sets)} WHERE id = ${idx} RETURNING id, code, name"
    row = await pool.fetchrow(sql, *params)
    if not row:
        raise HTTPException(404, "prize not found")
    details = f"id={pid} code={row['code']} " + " ".join(
        [f"enabled={req.enabled}" if req.enabled is not None else "",
         f"pct={req.probability_pct}" if req.probability_pct is not None else ""]
    ).strip()
    await _log(admin["telegram_id"], "gacha_prize_pool_update", pid, details)
    return {"ok": True, "id": pid, "name": row["name"]}


@router.get("/top-winners")
async def top_winners(days: int = 30, limit: int = 20,
                      admin=Depends(require_role("admin"))):
    """Top jackpot winners by prize value within window."""
    days = max(1, min(days, 365))
    limit = max(1, min(limit, 100))
    rows = await pool.fetch("""
        SELECT
            gp.id, gp.telegram_id, gp.prize_label, gp.prize_value_thb, gp.pulled_at,
            gp.payment_id, u.first_name, u.username, u.loyalty_rank
        FROM gachapon_pulls gp
        LEFT JOIN users u ON u.id = gp.user_id
        WHERE gp.pulled_at >= NOW() - ($1::int * INTERVAL '1 day')
          AND gp.prize_value_thb > 0
        ORDER BY gp.prize_value_thb DESC, gp.pulled_at DESC
        LIMIT $2
    """, days, limit)
    return {"items": [dict(r) for r in rows], "days": days}


@router.get("/recent-pulls")
async def recent_pulls(limit: int = 50, admin=Depends(require_role("admin"))):
    """Recent pulls feed (newest first)."""
    limit = max(1, min(limit, 200))
    rows = await pool.fetch("""
        SELECT
            gp.id, gp.telegram_id, gp.prize_code, gp.prize_label, gp.prize_value_thb,
            gp.payment_id, gp.pulled_at, gp.outcome,
            u.first_name, u.username, u.loyalty_rank
        FROM gachapon_pulls gp
        LEFT JOIN users u ON u.id = gp.user_id
        ORDER BY gp.pulled_at DESC
        LIMIT $1
    """, limit)
    return {"items": [dict(r) for r in rows]}


# ====== Audit 2026-06-26: prize CRUD + spin price control ======
from pydantic import BaseModel as _BM_g, Field as _F_g


class _PrizePoolCreate(_BM_g):
    code: str = _F_g(..., max_length=32)
    tier: str = _F_g(..., max_length=20)  # COMMON / RARE / EPIC / LEGENDARY
    name: str
    prize_type: str = _F_g(..., max_length=20)  # discount / clip / sub / cash / item
    value_thb: float = 0
    probability_pct: float = 0.01
    enabled: bool = True
    sort_order: int = 0


@router.post("/prize-pool")
async def create_prize_pool(req: _PrizePoolCreate, admin=Depends(require_role("admin"))):
    """Add new prize to gacha_prize_pool."""
    try:
        row = await pool.fetchrow("""
            INSERT INTO gacha_prize_pool (code, tier, name, prize_type, value_thb, probability_pct, enabled, sort_order)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            RETURNING id, code, tier, name
        """,
            req.code, req.tier, req.name, req.prize_type,
            req.value_thb, req.probability_pct, req.enabled, req.sort_order,
        )
    except Exception as exc:
        from fastapi import HTTPException
        raise HTTPException(400, f"create failed (code already exists?): {exc}")

    try:
        await pool.execute(
            "INSERT INTO admin_logs (admin_id, action, target_type, target_id, details) "
            "VALUES ($1, 'gacha_prize_create', 'gacha_prize_pool', $2, $3)",
            admin["telegram_id"], row["id"],
            f"code={req.code} name={req.name} value={req.value_thb} prob={req.probability_pct}"
        )
    except Exception:
        pass

    return {"ok": True, **dict(row)}


@router.delete("/prize-pool/{pid}")
async def delete_prize_pool(pid: int, admin=Depends(require_role("admin"))):
    """Delete a prize from gacha_prize_pool.

    Safety: blocks deletion if prize has been won (referenced by gachapon_pulls.prize_pool_id).
    Soft option: just disable it (set enabled=FALSE) — preserves history.
    """
    row = await pool.fetchrow(
        "SELECT id, code, name FROM gacha_prize_pool WHERE id = $1", pid
    )
    if not row:
        from fastapi import HTTPException
        raise HTTPException(404, "prize not found")

    # Check if prize has been won historically
    try:
        winners = await pool.fetchval(
            "SELECT COUNT(*) FROM gachapon_pulls WHERE prize_pool_id = $1", pid
        )
        if winners and winners > 0:
            # Soft delete (disable)
            await pool.execute("UPDATE gacha_prize_pool SET enabled = FALSE WHERE id = $1", pid)
            try:
                await pool.execute(
                    "INSERT INTO admin_logs (admin_id, action, target_type, target_id, details) "
                    "VALUES ($1, 'gacha_prize_disable', 'gacha_prize_pool', $2, $3)",
                    admin["telegram_id"], pid,
                    f"code={row['code']} name={row['name']} — soft-disabled (had {winners} winners, kept for history)"
                )
            except Exception:
                pass
            return {"ok": True, "soft_deleted": True, "winners": winners}
    except Exception:
        pass

    # Hard delete (no winners)
    await pool.execute("DELETE FROM gacha_prize_pool WHERE id = $1", pid)
    try:
        await pool.execute(
            "INSERT INTO admin_logs (admin_id, action, target_type, target_id, details) "
            "VALUES ($1, 'gacha_prize_delete', 'gacha_prize_pool', $2, $3)",
            admin["telegram_id"], pid,
            f"code={row['code']} name={row['name']} — hard deleted"
        )
    except Exception:
        pass
    return {"ok": True, "hard_deleted": True}


# ====== Spin price override (DB-backed pricing) ======
async def _ensure_gacha_pricing_table():
    """Optional override of GACHA prices stored in DB (vs hardcoded pricing.py)."""
    await pool.execute("""
        CREATE TABLE IF NOT EXISTS gacha_spin_pricing (
            tier VARCHAR(20) PRIMARY KEY,
            price_thb INTEGER NOT NULL,
            updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
            updated_by BIGINT
        )
    """)
    # Seed defaults
    await pool.execute("""
        INSERT INTO gacha_spin_pricing (tier, price_thb) VALUES
        ('GACHA_1', 99), ('GACHA_3', 270), ('GACHA_10', 890)
        ON CONFLICT (tier) DO NOTHING
    """)


@router.get("/spin-pricing")
async def get_spin_pricing(admin=Depends(require_role("admin"))):
    """Get current per-spin pricing (from DB override + hardcoded fallback)."""
    await _ensure_gacha_pricing_table()
    rows = await pool.fetch(
        "SELECT tier, price_thb, updated_at FROM gacha_spin_pricing ORDER BY price_thb"
    )
    return [dict(r) for r in rows]


class _SpinPriceUpdate(_BM_g):
    price_thb: int = _F_g(..., ge=1, le=99999)


@router.patch("/spin-pricing/{tier}")
async def update_spin_pricing(tier: str, req: _SpinPriceUpdate, admin=Depends(require_role("admin"))):
    """Update per-spin price for one tier (GACHA_1 / GACHA_3 / GACHA_10)."""
    if tier not in ("GACHA_1", "GACHA_3", "GACHA_10"):
        from fastapi import HTTPException
        raise HTTPException(400, "tier must be GACHA_1, GACHA_3, or GACHA_10")
    await _ensure_gacha_pricing_table()
    await pool.execute("""
        UPDATE gacha_spin_pricing SET price_thb = $1, updated_at = NOW(), updated_by = $2
        WHERE tier = $3
    """, req.price_thb, admin["telegram_id"], tier)
    try:
        await pool.execute(
            "INSERT INTO admin_logs (admin_id, action, target_type, target_id, details) "
            "VALUES ($1, 'gacha_spin_price_update', 'gacha_spin_pricing', 0, $2)",
            admin["telegram_id"],
            f"tier={tier} new_price={req.price_thb}"
        )
    except Exception:
        pass
    return {"ok": True, "tier": tier, "price_thb": req.price_thb}

