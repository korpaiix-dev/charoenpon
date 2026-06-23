"""Prae Team Engine — AI assistant for internal team chat in Discord.

DIFFERENT from prae_engine (which is customer-facing Sales bot):
- prae_engine: persuasive seller, talks to customers, pushes packages
- prae_team_engine: business analyst for boss/staff, queries DB, gives data

Same LLM, totally different system prompt + tools.
"""
from __future__ import annotations
import asyncio
import json
import logging
import os
from datetime import datetime, timedelta
from typing import Any

import httpx

from shared.database import get_session
from sqlalchemy import text as sql_text

logger = logging.getLogger(__name__)

OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
MODEL = "anthropic/claude-haiku-4-5"


SYSTEM_PROMPT = """คุณคือ "แพร" — AI ผู้ช่วยภายในของทีมเจริญพร (Charoenpon 18+ Telegram VIP business)
คุณคุยกับ **ทีมงาน** (บอส + หุ้นส่วน + พนักงาน) ใน Discord ไม่ใช่ลูกค้า

**บทบาทคุณ:**
- ตอบคำถามทาง business — ยอดขาย ลูกค้า สถิติ ปัญหา
- ให้ข้อมูลตรงไปตรงมา ไม่ขายของ ไม่พูดเหมือนพนักงานเซล
- ใช้ TOOLS ที่มีเพื่อ query DB ก่อนตอบ — ไม่เดา ไม่มั่ว
- ตอบสั้น กระชับ มืออาชีพ (ทีมงานไม่ต้องการ emoji กระจาย)

**ภาษา:** ไทย เป็นกันเอง ใช้ "ครับ/ค่ะ" สลับได้
**โทน:** เหมือนนักวิเคราะห์ Business — มีข้อมูลรองรับทุกคำตอบ

**ห้ามทำ:**
- ❌ ห้ามแนะนำให้คนในทีมซื้อแพ็กเกจ
- ❌ ห้ามตอบ "คุณยังไม่มี subscription"
- ❌ ห้ามใช้ HTML tags <b> <a> — ใช้ markdown **bold** [link](url) ไปเลย
- ❌ ห้ามตอบยาวเกินจำเป็น

**ทำได้:**
- ✅ ใช้ tool query revenue/customer/sub
- ✅ บอกตัวเลข + ความหมาย ("วันนี้ ฿2,499 ลด 12% จากเมื่อวาน")
- ✅ แนะนำ action ถ้ามี ("ลูกค้า X กำลังหมดอายุพรุ่งนี้ ส่ง DM ดีไหม")
- ✅ ถ้าไม่รู้ ตอบ "ไม่มีข้อมูลในระบบ" — อย่ามั่ว

**Response format:** plain text + markdown ของ Discord
"""


# Available tools — same names as customer-side prae but with team context
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_revenue_summary",
            "description": "ดูสรุปรายได้ — รายวัน รายเดือน หรือช่วงเวลา",
            "parameters": {
                "type": "object",
                "properties": {
                    "period": {"type": "string", "enum": ["today", "yesterday", "week", "month", "all_time"]},
                },
                "required": ["period"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_customer",
            "description": "ค้นหาลูกค้าจาก telegram_id, ชื่อ หรือ username",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "tg_id (number), first_name, username"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "top_spenders",
            "description": "Top spender ของเดือนนี้",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "default": 10},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "expiring_soon",
            "description": "ลูกค้า sub ใกล้หมดอายุใน N วัน",
            "parameters": {
                "type": "object",
                "properties": {
                    "days": {"type": "integer", "default": 3},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "pending_slips",
            "description": "สลิปลูกค้าที่ยังรออนุมัติ",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]


async def _tool_get_revenue_summary(period: str) -> dict:
    # Use created_at directly with BKK timezone conversion
    pay_bkk = "(p.created_at AT TIME ZONE 'UTC') AT TIME ZONE 'Asia/Bangkok'"
    if period == "today":
        cond = f"({pay_bkk})::date = (NOW() AT TIME ZONE 'Asia/Bangkok')::date"
    elif period == "yesterday":
        cond = f"({pay_bkk})::date = (NOW() AT TIME ZONE 'Asia/Bangkok')::date - 1"
    elif period == "week":
        cond = f"{pay_bkk} > NOW() AT TIME ZONE 'Asia/Bangkok' - INTERVAL '7 days'"
    elif period == "month":
        cond = f"date_trunc('month', {pay_bkk}) = date_trunc('month', NOW() AT TIME ZONE 'Asia/Bangkok')"
    else:
        cond = "1=1"
    async with get_session() as s:
        r = await s.execute(sql_text(
            "SELECT COUNT(*) AS n, COALESCE(SUM(p.amount),0)::int AS total "
            "FROM payments p JOIN users u ON u.id = p.user_id "
            "WHERE p.status='CONFIRMED' AND p.amount > 0 "
            "AND u.telegram_id < 9000000000 "
            f"AND {cond}"
        ))
        row = r.fetchone()
        return {"period": period, "count": int(row.n or 0), "total_thb": int(row.total or 0)}



async def _tool_find_customer(query: str) -> dict:
    q = query.strip()
    async with get_session() as s:
        # Try tg_id first
        if q.isdigit():
            r = await s.execute(sql_text(
                "SELECT id, telegram_id, first_name, last_name, username, total_spent, loyalty_rank, "
                "is_banned, is_blocked_bot "
                "FROM users WHERE telegram_id = :tg LIMIT 1"
            ), {"tg": int(q)})
        else:
            r = await s.execute(sql_text(
                "SELECT id, telegram_id, first_name, last_name, username, total_spent, loyalty_rank, "
                "is_banned, is_blocked_bot "
                "FROM users WHERE first_name ILIKE :q OR last_name ILIKE :q OR username ILIKE :q "
                "ORDER BY total_spent DESC LIMIT 5"
            ), {"q": f"%{q}%"})
        rows = r.fetchall()
        if not rows:
            return {"found": False, "query": q}
        return {"found": True, "results": [
            {
                "id": row.id, "tg_id": row.telegram_id,
                "name": f"{row.first_name or ''} {row.last_name or ''}".strip(),
                "username": row.username,
                "total_spent": float(row.total_spent or 0),
                "rank": row.loyalty_rank,
                "is_banned": row.is_banned,
                "is_blocked_bot": row.is_blocked_bot,
            }
            for row in rows
        ]}


async def _tool_top_spenders(limit: int = 10) -> dict:
    async with get_session() as s:
        r = await s.execute(sql_text(
            "SELECT u.first_name, u.username, u.total_spent, u.loyalty_rank, u.telegram_id "
            "FROM users u WHERE u.total_spent > 0 AND u.telegram_id < 9000000000 "
            "ORDER BY u.total_spent DESC LIMIT :n"
        ), {"n": int(limit)})
        rows = r.fetchall()
        return {"top": [
            {"name": row.first_name, "username": row.username, "spent": float(row.total_spent),
             "rank": row.loyalty_rank, "tg_id": row.telegram_id}
            for row in rows
        ]}


async def _tool_expiring_soon(days: int = 3) -> dict:
    async with get_session() as s:
        r = await s.execute(sql_text(
            "SELECT u.first_name, u.telegram_id, pk.tier, "
            "(s.end_date AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Bangkok')::date AS bkk_end "
            "FROM subscriptions s JOIN users u ON u.id = s.user_id "
            "JOIN packages pk ON pk.id = s.package_id "
            "WHERE s.status='ACTIVE' AND s.end_date BETWEEN NOW() AND NOW() + (:d * INTERVAL '1 day') "
            "AND pk.duration_days < 3650 "
            "AND u.telegram_id < 9000000000 "
            "ORDER BY s.end_date LIMIT 30"
        ), {"d": int(days)})
        rows = r.fetchall()
        return {"expiring": [
            {"name": row.first_name, "tg_id": row.telegram_id, "tier": str(row.tier),
             "end_date": row.bkk_end.isoformat() if row.bkk_end else None}
            for row in rows
        ]}


async def _tool_pending_slips() -> dict:
    async with get_session() as s:
        r = await s.execute(sql_text(
            "SELECT p.id, u.first_name, u.telegram_id, p.amount, pk.tier, "
            "(p.created_at AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Bangkok')::timestamp AS bkk_at "
            "FROM payments p JOIN users u ON u.id = p.user_id "
            "LEFT JOIN packages pk ON pk.id = p.package_id "
            "WHERE p.status = 'PENDING' AND u.telegram_id < 9000000000 "
            "ORDER BY p.created_at LIMIT 30"
        ))
        rows = r.fetchall()
        return {"pending": [
            {"payment_id": row.id, "name": row.first_name, "tg_id": row.telegram_id,
             "amount": float(row.amount), "tier": str(row.tier) if row.tier else None,
             "at": row.bkk_at.isoformat() if row.bkk_at else None}
            for row in rows
        ]}


TOOL_HANDLERS = {
    "get_revenue_summary": _tool_get_revenue_summary,
    "find_customer": _tool_find_customer,
    "top_spenders": _tool_top_spenders,
    "expiring_soon": _tool_expiring_soon,
    "pending_slips": _tool_pending_slips,
}


async def _llm_call(messages: list) -> dict:
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
    }
    body = {
        "model": MODEL,
        "messages": messages,
        "tools": TOOLS,
        "tool_choice": "auto",
        "max_tokens": 1500,
    }
    async with httpx.AsyncClient(timeout=60.0) as cli:
        r = await cli.post(OPENROUTER_URL, json=body, headers=headers)
        r.raise_for_status()
        return r.json()


async def team_reply(user_text: str, user_name: str = "ทีม") -> str:
    """Main entry — team member asks question, returns answer."""
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"[ทีม {user_name}] {user_text}"},
    ]

    # Up to 3 tool iterations
    for _ in range(3):
        try:
            resp = await _llm_call(messages)
        except Exception as exc:
            logger.exception("team_reply LLM err: %s", exc)
            return f"ขออภัย ระบบ AI ผิดพลาด: {str(exc)[:100]}"

        msg = resp["choices"][0]["message"]
        tool_calls = msg.get("tool_calls") or []

        if not tool_calls:
            return (msg.get("content") or "ไม่มีคำตอบ").strip()

        # Execute tools
        messages.append(msg)
        for tc in tool_calls:
            fn_name = tc["function"]["name"]
            try:
                args = json.loads(tc["function"]["arguments"] or "{}")
            except Exception:
                args = {}
            handler = TOOL_HANDLERS.get(fn_name)
            try:
                if handler:
                    result = await handler(**args)
                else:
                    result = {"error": f"unknown tool {fn_name}"}
            except Exception as exc:
                result = {"error": str(exc)[:200]}
            messages.append({
                "role": "tool",
                "tool_call_id": tc["id"],
                "content": json.dumps(result, ensure_ascii=False, default=str),
            })

    return "ขออภัย AI ตัดสินใจไม่ได้ ลองพิมพ์ใหม่"
