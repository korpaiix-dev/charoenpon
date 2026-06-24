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


SYSTEM_PROMPT = """คุณคือ "แพร" — สาวน้อย AI ในออฟฟิศของบอสเจริญพร (18+ Telegram VIP)
คุยกับ **ทีมในออฟฟิศ** ใน Discord เท่านั้น ไม่ใช่ลูกค้า

**บุคลิกคุณ:**
- ผู้หญิงน่ารัก ขี้เล่น กวนๆ นิดๆ แต่เก่งงาน
- เหมือนเพื่อนผู้หญิงในทีมที่รู้ข้อมูลทุกอย่าง — แซวบอสได้ ไม่ใส่หน้ากาก
- ใช้ "ค่ะ/นะคะ/หนู/แพร" — เป็นกันเอง ไม่ทางการเกิน
- อาจ "555", "อะ", "เอ๊ะ", "งืออ", "เฮ้!", "อืม", "โห่ๆ", "อ๋อค่ะ" บ้างนิดหน่อย
- มี emoji บ้างประปราย (✨💕🥺😅🙄☕) แต่อย่ายัดทุกประโยค

**สิ่งสำคัญที่ห้ามลืม:**
- ตัวเลข/ข้อมูลต้อง **ถูกต้อง 100%** — เรียก tool ก่อนตอบเสมอ ไม่เดา
- บุคลิกน่ารัก = **เรื่อง flavor ของคำพูด** ไม่ใช่ความถูกต้อง

**ตัวอย่างวิธีตอบ:**

❌ ทางการเกิน: "วันนี้ลูกค้าจ่ายไป ฿2,499 ครับ"
✅ แพร: "อิหม่ะ วันนี้ ฿2,499 อยู่นะคะ ไม่ได้แย่งานน 5 รายการ ❤️"

❌ แห้งๆ: "Top spender: A (฿2,499)..."
✅ แพร: "Top 3 ของเดือนนี้นะ:
1. **A** ฿2,499 (เจ้าแม่ของเดือน 👑)
2. **Mad** ฿2,499 (สูสีมาก)
3. **Reko** ฿2,499
รวยกันทั้งนั้นเลย 😅"

❌ ห่างเหิน: "ไม่พบข้อมูล"
✅ แพร: "เอ๊ะ หนูหาไม่เจอเลย บอสจำชื่อถูกมั้ย 🤔"

❌ ขายของ: "อยากให้บอสซื้อ VIP 300..."
✅ ไม่ขายเลย ทีมงานไม่ใช่ลูกค้า — ห้ามขาย!

**ภาษา:** ไทยลำลอง 80% + emoji 5-10% + ตัวเลข/ชื่อ business term ที่ต้องเป๊ะ

**ห้าม:**
- ❌ ขายแพ็กเกจให้ทีม
- ❌ ตอบ "คุณยังไม่มี subscription" (เพราะเป็นทีม)
- ❌ HTML tags `<b>` — ใช้ markdown ของ Discord (`**bold**`, `[link](url)`)
- ❌ ตอบยาวเป็นจดหมาย — ตอบกระชับ มีอารมณ์
- ❌ ใส่ emoji เยอะเกิน 5 ตัวต่อข้อความ

**ทำได้:**
- ✅ ใช้ tool: get_revenue_summary, find_customer, top_spenders, expiring_soon, pending_slips
- ✅ แซวบอสเล็กน้อย (แต่สุภาพ — เพราะเป็นเจ้านาย)
- ✅ บอกความหมายของตัวเลข ไม่ใช่แค่ตัวเลข ("ยอดดีกว่าเมื่อวานนิด ๆ ")
- ✅ ใช้ Discord markdown: `**ตัวหนา**`, `*เอียง*`, `\`code\``

จำไว้: **ข้อมูลถูกต้อง** + **บุคลิกน่ารักกวนๆ** = แพร Discord version 💕
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
    {
        "type": "function",
        "function": {
            "name": "create_marketing_link",
            "description": (
                "สร้างลิงก์เชิญกลุ่มฟรีใหม่สำหรับทีมการตลาด (Ivy/Wasu/Pai) "
                "ลิงก์จะ track ว่าใครเข้ามาเพื่อใช้คำนวณ conversion. "
                "ใช้เมื่อมีคนพิมพ์ 'ขอลิ้ง', 'สร้างลิ้ง', 'ขอลิ้งใหม่'"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "marketer": {"type": "string", "enum": ["Ivy", "Wasu", "Pai"]},
                    "platform": {"type": "string", "description": "facebook / tiktok / youtube / etc."},
                    "group": {"type": "string", "description": "ชื่อกลุ่ม: รวมกลุ่ม หรือ แจ้งข่าวสาร"},
                },
                "required": ["marketer", "platform", "group"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "marketing_stats",
            "description": (
                "ดู stats ของทีมการตลาด — joins, paid users, revenue, ARPU, conversion %, avg days to pay. "
                "Default window = 30d (มาตรฐาน). "
                "ใช้เมื่อมีคนถาม 'stat ของฉัน', 'รายได้จาก facebook', 'เปรียบเทียบ ivy vs wasu'"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "marketer": {"type": "string", "description": "Ivy / Wasu / Pai (ไม่ใส่ = ดูทุกคน)"},
                    "platform": {"type": "string", "description": "facebook / tiktok / ... (ไม่ใส่ = ดูทุก platform)"},
                    "window": {"type": "string", "enum": ["7d", "30d", "lifetime"], "default": "30d"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "marketing_links_list",
            "description": "ดูรายการลิงก์ active ของทีมการตลาด (ลิงก์อะไรบ้าง คนเข้ามาเท่าไหร่)",
            "parameters": {
                "type": "object",
                "properties": {
                    "marketer": {"type": "string", "description": "Ivy / Wasu / Pai (ไม่ใส่ = ดูทุกคน)"},
                    "limit": {"type": "integer", "default": 20},
                },
            },
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


# Marketing tools — import lazily to avoid load order issues
from shared.marketing_tools import (
    create_marketing_link as _tool_create_marketing_link,
    marketing_stats as _tool_marketing_stats,
    marketing_links_list as _tool_marketing_links_list,
)

TOOL_HANDLERS = {
    "get_revenue_summary": _tool_get_revenue_summary,
    "find_customer": _tool_find_customer,
    "top_spenders": _tool_top_spenders,
    "expiring_soon": _tool_expiring_soon,
    "pending_slips": _tool_pending_slips,
    "create_marketing_link": _tool_create_marketing_link,
    "marketing_stats": _tool_marketing_stats,
    "marketing_links_list": _tool_marketing_links_list,
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


async def team_reply(
    user_text: str,
    user_name: str = "ทีม",
    marketer_context: str | None = None,
    channel_context: str | None = None,
) -> str:
    """Main entry — team member asks question, returns answer.

    Args:
        marketer_context: if set ('Ivy'/'Wasu'/'Pai'), AI knows this is a
            marketing person's personal channel — use that marketer by default.
        channel_context: name of the Discord channel (for AI context).
    """
    sys_extra = ""
    if marketer_context:
        sys_extra = (
            f"\n\n**Context พิเศษ:** ข้อความนี้มาจากห้อง #{channel_context or marketer_context.lower()} "
            f"ซึ่งเป็นห้องของคุณ {marketer_context} (ทีมการตลาด).\n"
            f"ถ้าเค้าขอลิ้ง/ดู stat ของตัวเอง — ให้ใช้ marketer='{marketer_context}' โดยอัตโนมัติ ไม่ต้องถามชื่อ"
        )
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT + sys_extra},
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
