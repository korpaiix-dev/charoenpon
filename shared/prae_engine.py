"""แพร v2 engine — orchestrates LLM + tools + memory + safety net.

Public API:
    await reply_to_user(telegram_id, user_text, context) -> {"reply": str, "should_handoff": bool, ...}

Internal flow:
1. Save user message to memory
2. Run safety net (handoff keyword filter)
3. Call LLM with system prompt + memory + tool schemas
4. If LLM wants tool → execute → feed back → loop (max 3 iterations)
5. Parse final response → save assistant message
6. Return structured result
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

import httpx

from sqlalchemy import text as _t
from shared.database import get_session

from shared.prae_tools import TOOLS, TOOL_SCHEMAS

logger = logging.getLogger(__name__)


# ============================================================
# Config
# ============================================================
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
MODEL = "anthropic/claude-haiku-4-5"

MAX_TOOL_ITERATIONS = 3
MAX_MEMORY_TURNS = 8  # last 8 user+assistant pairs
MAX_REPLY_TOKENS = 400
DAILY_COST_CAP_THB = 50.0  # fallback when exceeded


# Trigger keywords → instant handoff (no LLM call)
HANDOFF_KEYWORDS_INSTANT = [
    "ขอเงินคืน", "เงินคืน", "refund", "ขอ refund",
    "ฟ้อง", "รีพอร์ต", "report", "โกง",
    "ยกเลิก subscription", "ยกเลิกสมาชิก", "cancel",
    "ขายของ", "โปรโมท", "ติดต่อขาย",
]


# ============================================================
# System Prompt v3 (improvements from Phase 4)
# ============================================================
SYSTEM_PROMPT_V3 = """คุณคือ "แพร" ผู้ช่วยฝ่ายขายของ เจริญพร VIP — ระบบสมาชิกคอนเทนต์ 18+ บน Telegram

ตัวตน: ผู้หญิง สุภาพ อบอุ่น เป็นมิตร มืออาชีพ ขายเก่ง ไม่ตื๊อ
- สรรพนาม: "หนู / แพร" / ลงท้าย "ค่ะ / นะคะ / ค่า" เสมอ
- ⛔ ห้ามพูด "ครับ" เด็ดขาด — แพรเป็นผู้หญิง ทุกประโยคลงท้าย ค่ะ/นะคะ/ค่า เท่านั้น (ผิดข้อนี้ = ผิดร้ายแรงสุด)
- ตอบสั้น **ไม่เกิน 5 บรรทัด** ใช้ emoji **1-2 ตัวเท่านั้น**
- ใช้ \\n สำหรับขึ้นบรรทัดใหม่ ห้ามใช้ <br>

═══════════════════════════════════════════════
🚨 OUTPUT PROTOCOL (สำคัญสุด — ทำผิดไม่ได้)
═══════════════════════════════════════════════

คุณต้องตอบเป็น **JSON object เท่านั้น** ใน 1 ใน 2 รูปแบบ:

**Mode A — ตอบทันที (ไม่ใช้ tool):**
```
{
  "reply": "คำตอบครบถ้วน ลูกค้าอ่านจบเข้าใจทันที ลงท้าย ค่ะ",
  "confidence": 0.0-1.0,
  "intent": "ask_price | ask_compare | greeting | objection | other",
  "should_handoff": false,
  "suggested_tier": "TIER_300 | TIER_500 | TIER_1299 | TIER_2499 | TIER_4999 | null"
}
```

**Mode B — ใช้ tool ก่อนตอบ:**
```
{
  "tools_to_call": [{"name": "check_my_status", "args": {}}],
  "reply": "ขอเช็คให้สักครู่นะคะ 🔍",
  "intent": "status_check",
  "confidence": 0.9
}
```
ระบบจะเรียก tool + ส่ง result กลับมา — คุณจะตอบรอบ 2 ด้วย Mode A

**กฎสำคัญ:**
- ❌ ห้ามตอบข้อความเปล่า ไม่มี JSON
- ❌ ห้ามตอบ Mode A แต่บอก "ขอเช็คให้สักครู่" (ลูกค้าจะรอเก้อ)
- ❌ ห้ามมั่วข้อมูลส่วนตัวลูกค้า — **ต้องเรียก tool ทุกครั้ง**
- ✅ ถ้าตอบ knowledge ทั่วไป (ราคา/แพ็คเกจ/slang) → Mode A ตอบครบ
- ✅ ถ้าต้องดูข้อมูลส่วนตัว (สถานะ/สลิป/balance) → Mode B
- ✅ ถ้าโปรเฉพาะวันนี้ → Mode B เรียก check_active_promo

═══════════════════════════════════════════════

PACKAGES (รายละเอียดห้องแต่ละแพ็ก — ⚠️ ราคายึดตามบล็อก "เมนู + ราคาปัจจุบัน" ด้านล่างเท่านั้น ห้ามใช้ราคาที่จำมา):
- VIP (30 วัน) → ห้อง G300 (1 ห้อง — งานทางบ้าน/นักเรียน/แอบถ่าย)
- OF+VIP (30 วัน) → G300 + G500 (2 ห้อง — เพิ่ม OnlyFans แรร์ 50+ คน)
- GOD MODE (90 วัน) → 6 ห้อง (G300, G500, SSS, VGOD, INTER, SERIES + สายซุ่ม)
- GOD MODE ถาวร → ครบทุกห้อง 7 ห้อง + หนัง + Summer Fest 🔥 ⭐แนะนำ (จ่ายครั้งเดียว ดูตลอดชีพ)
- Super VIP ถาวร → แพ็กพรีเมียมสูงสุด ถาวร (ราคา/โปรดูในบล็อกเมนูสดด้านล่าง)

กฎแนะนำ:
- ลูกค้าใหม่/ทักครั้งแรก → ทักทาย + แนะนำ VIP 300 (low barrier)
- ถาม "คุ้มสุด" → GOD ถาวร 2,499
- อยาก OnlyFans → OF+VIP 500

SLANG DICTIONARY:
- "งานทางบ้าน" / "นักเรียน" / "นักศึกษา" / "แอบถ่าย" → G300 (VIP 300)
- "งานส่วนตัว" → G300 + G500
- "งานแร่" / "แรร์" → G500 + SSS
- "โอนลี่แฟน" / "OnlyFans" / "OF" → G500 ขึ้นไป
- "ต่างชาติ" / "นานาชาติ" → TIER_1299 (INTER)
- "หนัง" / "ซีรีส์" → TIER_1299 (SERIES)
- "สายซุ่ม" → TIER_1299 + TIER_2499

LINK:
- รีวิวลูกค้า: https://t.me/+hv7uXYj4bxFhODZl
- ตัวอย่างงาน: https://t.me/+Q0Qf-4t8TQo3YTBl
- ทักแอดมิน (handoff): <a href="https://t.me/sperm6969">@sperm6969</a>

🛒 PURCHASE INTENT (สำคัญสูงสุด — ปิดการขายให้สำเร็จ!):
ถ้าลูกค้าพิมพ์อะไรเกี่ยวกับการซื้อ — ใช้ tool send_payment_info ทันที (อย่าตอบลูกค้าว่าให้ติดต่อ @sperm6969):
- ลูกค้าพิมพ์เลขราคา: "300", "500", "1299", "2499", "100", "2999", "4999", "300 บาท", "เอา 2499"
- ลูกค้าพิมพ์ชื่อแพ็กเกจ: "VIP", "GOD", "OF+VIP", "ห้องชัก", "Super VIP", "ซุปเปอร์วีไอพี"
- ลูกค้าบอกอยากซื้อ: "อยากได้", "สนใจ", "เอา", "สั่งซื้อ", "ขอ VIP", "ซื้อ GOD"
- ลูกค้ายืนยันราคา: "300 บาท ใช่ไหม", "เท่าไหร่นะ", "2499 อันนี้"

→ ตอบ Mode B เรียก send_payment_info พร้อม tier_or_amount = สิ่งที่ลูกค้าพิมพ์
{
  "tools_to_call": [{"name": "send_payment_info", "args": {"tier_or_amount": "2499"}}],
  "reply": "ขอเตรียมเลขบัญชีให้สักครู่นะคะ 💰",
  "intent": "purchase_intent",
  "confidence": 0.95
}

หลังได้ผล tool → ตอบลูกค้าด้วย instructions_html (เลขบัญชี + QR code + วิธีโอน + วิธีส่งสลิป)
ตัวอย่าง:
"ได้เลยค่ะ 💰 {package_name} {price} บาท
🏦 {bank_name}
{receiver_name}
{account_number}

📸 โอนแล้วส่งสลิปกลับมาในแชทนี้ ระบบจะตรวจสอบและเปิดสิทธิให้อัตโนมัติค่ะ"

❌ ห้ามตอบ "ทักแอดมิน @sperm6969" สำหรับการซื้อ — บอตปิดการขายเองได้!
❌ ห้ามอธิบายแพ็กเกจซ้ำเมื่อลูกค้ายืนยันราคาแล้ว — ส่งเลขบัญชีเลย!

🚨 GROUP ACCESS PROBLEM (สำคัญสูงสุด — ใช้ tool เสมอ!):
ถ้าลูกค้าพิมพ์อะไรก็ตามที่เกี่ยวกับ "เข้ากลุ่ม" หรือ "ลิงก์":
- "เข้ากลุ่มไม่ได้", "เข้าไม่ได้", "กดไม่ได้", "เข้าไม่ทัน", "เข้าไม่เป็น"
- "ลิงก์หมด", "ลิ้งหมดอายุ", "ลิงก์ไม่ได้", "ลิงก์ไม่ทัน", "ลิงก์ใช้ไม่ได้"
- "ขอลิงก์", "ขอลิงค์", "ขอลิ้ง", "ขอลิงก์ใหม่", "ขอลิงก์เข้ากลุ่ม", "ขอลิงก์อีก", "ส่งลิงก์ให้หน่อย"
- "กลุ่มหาย", "กลุ่มหายไป", "หาคลิปไม่เจอ", "หากลุ่มไม่เจอ"
- "ออกจากกลุ่ม", "โดนเตะ", "เผลอออก"
→ **ต้องเรียก tool handle_group_access_issue ก่อน** (Mode B):
{
  "tools_to_call": [{"name": "handle_group_access_issue", "args": {}}],
  "reply": "ขอเช็คสถานะให้ก่อนนะคะ 🔍",
  "intent": "technical_issue",
  "confidence": 0.9
}

หลังได้ผล tool — ตอบลูกค้าตาม status:

ถ้า status = "active":
  → แจ้งว่าเป็นสมาชิกอยู่ ส่งลิงก์เข้ากลุ่มให้ครบทุกกลุ่ม (จาก invite_links)
  → ใช้ HTML <a href="..."> รายการแต่ละกลุ่ม
  → บอกว่า "ลิงก์ใช้ครั้งเดียว หมดอายุ 24 ชม."
  → ตัวอย่าง reply:
    "เห็นแล้วค่ะ คุณเป็นสมาชิก [tier_name] อยู่ค่ะ\n\n
     ส่งลิงก์ใหม่ให้แล้ว 👇\n
     🚀 <a href='{url1}'>{title1}</a>\n
     🚀 <a href='{url2}'>{title2}</a>\n\n
     ⏰ ใช้ครั้งเดียว หมดอายุใน 24 ชม. ค่ะ"

ถ้า status = "expired":
  → เห็นใจ + ชวนต่ออายุพร้อม discount จาก tool
  → ใช้ renewal_url (deep link พร้อม promo ติดมา)
  → ตัวอย่าง:
    "ของคุณ {expired_tier_name} หมดอายุ {days_since_expiry} วันแล้วค่ะ\n\n
     ขอชวนต่ออายุพร้อมโปรพิเศษ:\n
     🎁 ลด {renewal_discount_pct}% เฉพาะคุณค่ะ\n\n
     👉 <a href='{renewal_url}'>กดต่ออายุพร้อมส่วนลด</a>"

ถ้า status = "never_paid":
  → แนะนำ 3 ทาง (VIP 300 / Shaker 100 / Gacha) + ลิงก์ /start

❌ ห้ามตอบ Mode A สำหรับเรื่องนี้ — ลูกค้าต้องได้คำตอบที่ tool ตรวจสอบแล้วเท่านั้น
❌ ห้ามตอบว่า "ติดต่อแอดมิน" / "ทักแอดมินโดยตรง" สำหรับเรื่องลิงก์/เข้ากลุ่ม — handle_group_access_issue ทำได้ทุกเคส:
   • ลูกค้ามีสิทธิ์ → ส่งลิงก์ใหม่ทันที (Branch A)
   • ลูกค้าหมดอายุ → เสนอ renewal_url พร้อมส่วนลด (Branch B)
   • ลูกค้าไม่เคยซื้อ → แนะนำ 3 ทาง (Branch C)
   ทุก branch ตอบเสร็จในตัว ไม่ต้อง escalate

HANDOFF (Mode A เท่านั้น):
ตัวอย่าง: {"reply": "เรื่องนี้แอดมินดูแลเองค่ะ\\n👉 ทักได้ที่ <a href=\"https://t.me/sperm6969\">@sperm6969</a> ค่ะ", "should_handoff": true, "intent": "handoff", "confidence": 0.95}

ก่อนใช้ tool — บอกตัวเองสั้น ๆ ใน reply ว่า "ขอเช็คให้สักครู่นะคะ 🔍"
ถ้าไม่ต้อง tool → ตอบครบเลย ห้ามใส่ "ขอเช็คให้" (ลูกค้าจะรอเก้อ)
"""


# ==================================================================
# Phase A.4 (2026-06-27): Hot-reload Prae prompt from ai_prompts table
# - Cache 60s, fallback to SYSTEM_PROMPT_V3 constant
# - Admin saves in Dashboard → bot uses new prompt within 60s, NO restart
# ==================================================================
import time as _t_prompt
_prompt_cache_v3 = {"val": None, "expires": 0}

async def _build_live_promos_block() -> str:
    """Live CATALOG (packages + active promos) pulled from the dashboard-managed DB tables
    (packages, promotions) and injected into Prae's prompt. Prices are NEVER hardcoded here —
    whatever the boss sets in the dashboard is what Prae quotes (refreshes every 60s). '' on error."""
    try:
        from shared.database import get_session
        from sqlalchemy import text as _t_sql
        import json as _pj
        async with get_session() as sess:
            pkgs = (await sess.execute(_t_sql(
                "SELECT tier::text AS t, name, price, duration_days FROM packages "
                "WHERE is_active=TRUE ORDER BY price"))).fetchall()
            promos = (await sess.execute(_t_sql(
                "SELECT code, name, package_codes, discount_type, discount_value, "
                "CASE WHEN ends_at IS NULL THEN NULL ELSE CEIL(EXTRACT(EPOCH FROM (ends_at - now()))/86400.0) END AS days_left "
                "FROM promotions WHERE is_active=TRUE "
                "AND (starts_at IS NULL OR starts_at <= now()) "
                "AND (ends_at IS NULL OR ends_at > now()) ORDER BY id DESC"))).fetchall()
    except Exception:
        return ""
    pmap = {}
    menu = []
    for r in pkgs:
        m = r._mapping
        t = m["t"]; nm = m["name"]; pr = float(m["price"]); dd = m["duration_days"]
        pmap[t] = (nm, pr)
        if t.startswith("GACHA_"):
            continue
        dur = "ถาวร" if (dd and int(dd) >= 3650) else (("%d วัน" % int(dd)) if dd else "")
        menu.append("- %s: %s บาท%s" % (nm, format(int(pr), ","), (" / " + dur if dur else "")))
    if not menu:
        return ""
    out = ["", "===============================================",
           "\U0001f4cb เมนู + ราคาปัจจุบัน (ดึงสดจากแดชบอร์ด — ใช้ราคานี้เท่านั้น ห้ามจำราคาเอง):"]
    out += menu
    promo_lines = []
    for pr in (promos or []):
        m = pr._mapping
        raw = m["package_codes"]
        try:
            codes = raw if isinstance(raw, list) else (_pj.loads(raw) if raw else [])
        except Exception:
            codes = []
        for c in codes:
            if c in pmap:
                nm, base = pmap[c]
                dt = (m["discount_type"] or "").lower(); dv = float(m["discount_value"] or 0)
                if dt == "percent": fin = base * (100 - dv) / 100
                elif dt == "fixed_off": fin = max(0, base - dv)
                elif dt == "fixed_price": fin = dv
                else: fin = base
                dl = int(m["days_left"]) if m["days_left"] else 0
                _dpart = (" (เหลือ %d วัน)" % dl) if dl and dl > 0 else ""
                promo_lines.append(
                    "- %s: ปกติ %s฿ → ลดเหลือ %s฿%s | ลูกค้าพิมพ์ \"%d\" หรือ \"%d\" = อันนี้ → เรียก send_payment_info ด้วยเลขที่ลูกค้าพิมพ์"
                    % (nm, format(int(base), ","), format(int(fin), ","), _dpart, int(fin), int(base)))
    if promo_lines:
        out.append("")
        out.append("\U0001f381 โปรที่ใช้ได้ตอนนี้ (สำคัญมาก! ถ้าลูกค้าพูดเลขที่ตรงกับราคาโปร/ราคาปกติ = สนใจอันนั้น):")
        out += promo_lines
    out.append("===============================================")
    return "\n".join(out)


async def _async_load_active_prompt() -> str:
    """Return active Prae prompt from DB (60s cache) or SYSTEM_PROMPT_V3 fallback."""
    now = _t_prompt.time()
    if _prompt_cache_v3["val"] and _prompt_cache_v3["expires"] > now:
        return _prompt_cache_v3["val"]
    content = None
    try:
        from shared.database import get_session
        from sqlalchemy import text as _t_sql
        async with get_session() as sess:
            r = (await sess.execute(_t_sql(
                "SELECT content FROM ai_prompts WHERE name='prae' AND is_active=TRUE "
                "ORDER BY version DESC LIMIT 1"
            ))).first()
            if r:
                content = r.content
    except Exception:
        content = None
    if not content:
        content = SYSTEM_PROMPT_V3
    try:
        _pblock = await _build_live_promos_block()
        if _pblock:
            content = content + "\n" + _pblock
    except Exception:
        pass
    _prompt_cache_v3["val"] = content
    _prompt_cache_v3["expires"] = now + 60
    return content



# ============================================================
# Memory (conversation history per user, in DB)
# ============================================================
async def ensure_memory_table():
    async with get_session() as s:
        await s.execute(_t("""
            CREATE TABLE IF NOT EXISTS prae_conversations (
                id SERIAL PRIMARY KEY,
                telegram_id BIGINT NOT NULL,
                role VARCHAR(20) NOT NULL,
                content TEXT NOT NULL,
                tools_used JSONB,
                cost_usd NUMERIC(10,6) DEFAULT 0,
                created_at TIMESTAMP NOT NULL DEFAULT NOW()
            )
        """))
        await s.execute(_t("""
            CREATE INDEX IF NOT EXISTS ix_prae_conv_tg
            ON prae_conversations(telegram_id, created_at DESC)
        """))
        await s.commit()


async def load_history(telegram_id: int, max_turns: int = MAX_MEMORY_TURNS) -> list[dict]:
    """Load last N user+assistant message pairs."""
    async with get_session() as s:
        r = await s.execute(_t("""
            SELECT role, content FROM prae_conversations
            WHERE telegram_id = :tg
            ORDER BY id DESC LIMIT :lim
        """), {"tg": telegram_id, "lim": max_turns * 2})
        rows = list(r.fetchall())
    # reverse to chronological
    rows.reverse()
    return [{"role": row.role, "content": row.content} for row in rows]


async def save_message(telegram_id: int, role: str, content: str,
                       tools_used: list[str] | None = None, cost_usd: float = 0):
    async with get_session() as s:
        await s.execute(_t("""
            INSERT INTO prae_conversations (telegram_id, role, content, tools_used, cost_usd)
            VALUES (:tg, :r, :c, :t, :cost)
        """), {
            "tg": telegram_id, "r": role, "c": content,
            "t": json.dumps(tools_used) if tools_used else None,
            "cost": cost_usd,
        })
        await s.commit()


# ============================================================
# Cost tracking — daily cap
# ============================================================
async def today_cost_thb() -> float:
    async with get_session() as s:
        r = await s.execute(_t("""
            SELECT COALESCE(SUM(cost_usd), 0) FROM prae_conversations
            WHERE (created_at AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Bangkok')::date
                  = (NOW() AT TIME ZONE 'Asia/Bangkok')::date
        """))
        cost_usd = float(r.scalar() or 0)
    return cost_usd * 35.0  # rough USD→THB


# ============================================================
# Tool execution
# ============================================================
async def execute_tool(name: str, args: dict) -> Any:
    if name not in TOOLS:
        return {"error": f"unknown tool: {name}"}
    try:
        return await TOOLS[name](**args)
    except Exception as e:
        logger.exception("tool %s error", name)
        return {"error": f"tool execution failed: {e}"}


# ============================================================
# LLM call (OpenRouter)
# ============================================================
async def _llm_call(messages: list[dict]) -> tuple[str, float]:
    """Returns (raw_text, cost_usd)."""
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
    }
    body = {
        "model": MODEL,
        "messages": messages,
        "temperature": 0.6,
        "max_tokens": MAX_REPLY_TOKENS,
    }
    async with httpx.AsyncClient(timeout=20) as cli:
        r = await cli.post(OPENROUTER_URL, json=body, headers=headers)
        r.raise_for_status()
        data = r.json()
    raw = data["choices"][0]["message"]["content"].strip()
    # rough cost — Haiku 4.5: $0.80/M input, $4.00/M output
    usage = data.get("usage", {})
    in_tok = usage.get("prompt_tokens", 0)
    out_tok = usage.get("completion_tokens", 0)
    cost_usd = (in_tok * 0.80 + out_tok * 4.00) / 1_000_000
    return raw, cost_usd


def _parse_json(raw: str) -> dict:
    """Strip code fence + parse JSON, with fallback.

    Robust against:
    - ```json fence
    - ``` fence
    - leading "json" prefix
    - trailing ``` fence
    - whitespace around fence
    """
    s = (raw or "").strip()
    # Strip leading fence
    if s.startswith("```"):
        # Find first newline after the fence opener
        nl = s.find("\n")
        if nl > 0:
            # Skip the "```json" or "```" line entirely
            s = s[nl + 1:]
        else:
            s = s[3:]
    # Strip trailing fence
    if s.rstrip().endswith("```"):
        s = s.rstrip()[:-3].rstrip()
    s = s.strip()
    # Drop any leading "json\n" left over
    if s.startswith("json\n") or s.startswith("json\r"):
        s = s[5:]
    try:
        return json.loads(s, strict=False)
    except json.JSONDecodeError:
        # Last resort — try regex-extract reply value
        import re
        m = re.search(r'"reply"\s*:\s*"((?:\\.|[^"\\])*)"', s, re.DOTALL)
        if m:
            try:
                reply_text = json.loads('"' + m.group(1) + '"')
            except Exception:
                reply_text = m.group(1)
            return {
                "reply": reply_text,
                "confidence": 0.5,
                "intent": "unknown",
                "should_handoff": False,
                "suggested_tier": None,
                "_parse_recovered": True,
            }
        # absolute fallback — treat whole as reply text
        return {
            "reply": s,
            "confidence": 0.3,
            "intent": "unknown",
            "should_handoff": False,
            "suggested_tier": None,
            "_parse_error": True,
        }


# ============================================================
# Main entry
# ============================================================
async def reply_to_user(telegram_id: int, user_text: str) -> dict:
    """Process user message → return AI reply.

    Returns {
        "reply": str,
        "confidence": float,
        "intent": str,
        "should_handoff": bool,
        "suggested_tier": str | None,
        "tools_used": [str],
        "cost_usd": float,
        "is_fallback": bool,
    }
    """
    # ── Safety: instant handoff keywords ──
    lower = user_text.lower()
    if any(kw.lower() in lower for kw in HANDOFF_KEYWORDS_INSTANT):
        msg = ('ขออาศัยติดต่อแอดมินโดยตรงนะคะ — เรื่องนี้แอดมินดูแลเองค่ะ\n'
               '👉 ทักได้ที่ <a href="https://t.me/sperm6969">@sperm6969</a> ค่ะ')
        await save_message(telegram_id, "user", user_text)
        await save_message(telegram_id, "assistant", msg, tools_used=["handoff_keyword"])
        return {
            "reply": msg, "confidence": 1.0, "intent": "handoff",
            "should_handoff": True, "suggested_tier": None,
            "tools_used": ["handoff_keyword"], "cost_usd": 0,
            "is_fallback": False,
        }

    # ── Cost cap fallback ──
    cost_today = await today_cost_thb()
    if cost_today > DAILY_COST_CAP_THB:
        msg = ("ระบบ AI กำลังพักชั่วครู่ค่ะ — รบกวนทักแอดมินโดยตรงนะคะ\n"
               '👉 <a href="https://t.me/sperm6969">@sperm6969</a>')
        await save_message(telegram_id, "user", user_text)
        await save_message(telegram_id, "assistant", msg, tools_used=["cost_cap_fallback"])
        return {
            "reply": msg, "confidence": 1.0, "intent": "other",
            "should_handoff": True, "suggested_tier": None,
            "tools_used": ["cost_cap_fallback"], "cost_usd": 0,
            "is_fallback": True,
        }

    # ── Build conversation ──
    history = await load_history(telegram_id)
    messages = [{"role": "system", "content": await _async_load_active_prompt()}]
    messages.extend(history)
    # Inject user identity hint so LLM knows whose status to check
    messages.append({
        "role": "user",
        "content": f"[user telegram_id={telegram_id}] {user_text}",
    })

    # ── LLM call (no tool-use loop in v1 — tools called by LLM via explicit JSON action) ──
    # For Phase 5 first cut: we DON'T actually use Claude function-calling.
    # Instead: LLM may include "tools_to_call" in reply → we run them → call LLM again.
    # This keeps tooling explicit + debuggable.
    tools_used: list[str] = []
    total_cost = 0.0
    raw, cost = await _llm_call(messages)
    total_cost += cost
    parsed = _parse_json(raw)

    # If the LLM-returned reply contains tool intent in JSON, dispatch
    if "tools_to_call" in parsed:
        tool_results = {}
        for call in parsed["tools_to_call"][:MAX_TOOL_ITERATIONS]:
            tname = call.get("name")
            targs = call.get("args", {})
            if tname == "check_active_promo":
                targs = {}
            else:
                targs.setdefault("telegram_id", telegram_id)
            tool_results[tname] = await execute_tool(tname, targs)
            tools_used.append(tname)
        # PLAIN_HTML_ROUND2 — Feed tool results back, ask for plain HTML reply
        messages.append({"role": "assistant", "content": raw})
        messages.append({
            "role": "user",
            "content": (
                "[tool_results]\n"
                + json.dumps(tool_results, ensure_ascii=False, default=str)
                + "\n\nตอบลูกค้าโดยใช้ข้อมูลข้างบน:\n"
                + "- ตอบเป็น HTML ล้วน (Telegram HTML) ใช้ <b> <a href=\"...\"> ฯลฯ ได้\n"
                + "- **ห้ามใช้ JSON ในรอบนี้** — ตอบเป็นข้อความที่ลูกค้าจะเห็นเลย\n"
                + "- ลงท้าย ค่ะ/นะคะ เท่านั้น ⛔ ห้ามพูดครับเด็ดขาด (แพรผู้หญิง) ใช้ emoji 1-2 ตัว\n"
                + "- ไม่เกิน 6-8 บรรทัด"
            ),
        })
        raw, cost = await _llm_call(messages)
        total_cost += cost
        # Round 2 is plain HTML — don't parse as JSON
        reply_html = raw.strip()
        # Strip code fence if AI mistakenly wraps it
        if reply_html.startswith("```"):
            nl = reply_html.find("\n")
            if nl > 0:
                reply_html = reply_html[nl + 1:]
            if reply_html.rstrip().endswith("```"):
                reply_html = reply_html.rstrip()[:-3].rstrip()
        # Strip stray JSON wrappers if any
        if reply_html.startswith("{") and '"reply"' in reply_html:
            # Try parsing — extract reply field
            try:
                d = json.loads(reply_html, strict=False)
                reply_html = d.get("reply", reply_html)
            except Exception:
                # Regex extract
                import re as _re
                m = _re.search(r'"reply"\s*:\s*"((?:\\.|[^"\\])*)"', reply_html, _re.DOTALL)
                if m:
                    try:
                        reply_html = json.loads('"' + m.group(1) + '"')
                    except Exception:
                        reply_html = m.group(1)
        parsed = {
            "reply": reply_html,
            "confidence": 0.9,
            "intent": "tool_assisted",
            "should_handoff": False,
            "suggested_tier": None,
        }

    reply = parsed.get("reply", "ขออาศัยทักแอดมินค่ะ")
    # Save to memory
    await save_message(telegram_id, "user", user_text)
    await save_message(telegram_id, "assistant", reply, tools_used=tools_used, cost_usd=total_cost)

    return {
        "reply": reply,
        "confidence": parsed.get("confidence", 0.5),
        "intent": parsed.get("intent", "other"),
        "should_handoff": parsed.get("should_handoff", False),
        "suggested_tier": parsed.get("suggested_tier"),
        "tools_used": tools_used,
        "cost_usd": total_cost,
        "is_fallback": False,
    }


__all__ = ["reply_to_user", "ensure_memory_table", "today_cost_thb"]
