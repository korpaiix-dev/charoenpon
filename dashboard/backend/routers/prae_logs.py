"""Prae conversation log viewer — read-only."""
from __future__ import annotations

from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query

from ..auth.dependencies import require_role
from ..database import pool

router = APIRouter(prefix='/prae-logs', tags=['prae-logs'])


@router.get('/summary')
async def prae_summary(days: int = Query(7, ge=1, le=90),
                       admin=Depends(require_role('admin'))):
    """Overview: total msgs / users / cost in last N days."""
    row = await pool.fetchrow("""
        SELECT
            COUNT(*) AS total_msgs,
            COUNT(DISTINCT telegram_id) AS unique_users,
            COALESCE(SUM(cost_usd), 0)::float AS total_cost_usd,
            COUNT(*) FILTER (WHERE role = 'user') AS user_msgs,
            COUNT(*) FILTER (WHERE role = 'assistant') AS prae_msgs
        FROM prae_conversations
        WHERE created_at >= NOW() - ($1::int * INTERVAL '1 day')
    """, days)
    return dict(row) if row else {}


@router.get('/top-users')
async def prae_top_users(days: int = Query(7, ge=1, le=90),
                         limit: int = 20,
                         admin=Depends(require_role('admin'))):
    """Most active users in Prae chat with cost summary."""
    rows = await pool.fetch("""
        SELECT
            pc.telegram_id,
            COUNT(*) AS msgs,
            COALESCE(SUM(pc.cost_usd), 0)::float AS total_cost_usd,
            MAX(pc.created_at) AS last_msg_at,
            u.username, u.first_name, u.last_name
        FROM prae_conversations pc
        LEFT JOIN users u ON u.telegram_id = pc.telegram_id
        WHERE pc.created_at >= NOW() - ($1::int * INTERVAL '1 day')
        GROUP BY pc.telegram_id, u.username, u.first_name, u.last_name
        ORDER BY msgs DESC
        LIMIT $2
    """, days, limit)
    return [dict(r) for r in rows]


@router.get('/conversation/{telegram_id}')
async def prae_conversation(telegram_id: int,
                            limit: int = Query(100, ge=1, le=500),
                            admin=Depends(require_role('admin'))):
    """Full chronological convo with one user."""
    user = await pool.fetchrow(
        'SELECT id, telegram_id, username, first_name, last_name FROM users WHERE telegram_id = $1',
        telegram_id,
    )
    rows = await pool.fetch("""
        SELECT id, role, content, tools_used, cost_usd::float AS cost_usd, created_at
        FROM prae_conversations
        WHERE telegram_id = $1
        ORDER BY created_at DESC
        LIMIT $2
    """, telegram_id, limit)
    msgs = [dict(r) for r in rows]
    msgs.reverse()
    return {
        'user': dict(user) if user else None,
        'telegram_id': telegram_id,
        'messages': msgs,
    }


@router.get('/recent')
async def prae_recent(limit: int = Query(50, ge=1, le=200),
                      admin=Depends(require_role('admin'))):
    """Most recent messages across all users (for inspection)."""
    rows = await pool.fetch("""
        SELECT pc.id, pc.telegram_id, pc.role, pc.content, pc.cost_usd::float AS cost_usd,
               pc.created_at, u.username, u.first_name
        FROM prae_conversations pc
        LEFT JOIN users u ON u.telegram_id = pc.telegram_id
        ORDER BY pc.created_at DESC
        LIMIT $1
    """, limit)
    return [dict(r) for r in rows]
