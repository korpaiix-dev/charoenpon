"""AI / OCR helpers extracted from handlers/payment.py (Round 2 retry).
Calls OpenRouter for slip screening + reading, plus OCR fallback.
"""
from __future__ import annotations

import asyncio
import base64
import io
import json
import pytesseract
from PIL import Image
import logging
import os
import re
from typing import Optional

import httpx
from shared.api_cost_tracker import call_openrouter, OpenRouterCircuitOpen
from bots.sales_bot.payment_util.utils import _looks_like_non_slip_ad

# Alias matching legacy code
CircuitOpen = OpenRouterCircuitOpen

logger = logging.getLogger(__name__)

async def _ai_screen_image(b64_image: str) -> str | None:
    """AI screen: classify image as slip, spam, inappropriate, or customer question.
    
    Returns one of: SLIP, NOT_SLIP_QUESTION, NOT_SLIP_SUPPORT, SPAM, GAMBLING, PORN, INAPPROPRIATE
    """
    from shared.api_cost_tracker import call_openrouter

    prompt = (
        "ดูรูปนี้แล้วตอบสั้นๆ 1 คำ:\n"
        "- SLIP เฉพาะรูปสลิปโอนเงิน/หลักฐานการจ่ายเงินจากธนาคารหรือวอลเล็ทจริงเท่านั้น\n"
        "- NOT_SLIP_QUESTION ถ้าเป็นรูปทั่วไปหรือคำถาม (screenshot แชท, รูปแพ็กเกจ)\n"
        "- NOT_SLIP_SUPPORT ถ้าเป็น screenshot ปัญหา (เข้ากลุ่มไม่ได้, error)\n"
        "- GAMBLING ถ้าเป็นภาพโฆษณาพนัน/คาสิโน/สล็อต/บาคาร่า/เครดิตฟรี/UFABET/UFA แม้มีตัวเลขหรือคำว่าเงิน\n"
        "- SPAM ถ้าเป็นโฆษณาหรือโปรโมทเว็บอื่นที่ไม่ใช่สลิป\n"
        "- INAPPROPRIATE ถ้าเป็นรูปอนาจาร/ไม่เหมาะสม\n\n"
        "ถ้าไม่แน่ใจว่าเป็นสลิปจริง ให้ตอบ NOT_SLIP_QUESTION ห้ามเดาเป็น SLIP\n"
        "ตอบแค่คำเดียว ไม่ต้องอธิบาย"
    )

    try:
        data = await call_openrouter(
            model="google/gemini-2.5-flash",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{b64_image}"},
                        },
                    ],
                }
            ],
            caller="sales_bot/ai_screen_image",
            max_tokens=20,
            temperature=0.0,
        )
        result = data["choices"][0]["message"]["content"].strip()
        logger.info("AI screen result: %s", result)
        return result
    except Exception as exc:
        # FIX 2025-05-21 (Phase 2d caller): re-raise circuit-open so caller can defer
        from shared.api_cost_tracker import OpenRouterCircuitOpen as _CircuitOpen
        if isinstance(exc, _CircuitOpen):
            raise
        logger.error("AI screen API error: %s", exc)

    return None

# Daily cap for Layer 2 (Gemini Vision OCR fallback) — กัน burn cost เกิน
_LAYER2_DAILY_CAP = int(os.environ.get("LAYER2_DAILY_CAP", "100"))


async def _check_layer2_daily_cap() -> bool:
    """Return True ถ้ายัง under daily cap; False = escalate admin manual."""
    try:
        from shared.database import get_session
        from sqlalchemy import text as _t
        async with get_session() as s:
            # FIX 2026-06-29 (Bug 3): นับทั้ง Layer 1 OCR (ai_read_slip) และ Layer 2 vision (slip_layer2)
            # ก่อนหน้านี้ LIKE '%ai_read_slip%' จับเฉพาะ caller=ai_read_slip → Layer 2 calls (slip_layer2)
            # หลุด cap → burn cost ทะลุ
            r = await s.execute(_t("""
                SELECT COUNT(*) FROM api_cost_log
                WHERE endpoint IN ('sales_bot/ai_read_slip', 'sales_bot/slip_layer2')
                  AND created_at > NOW() - INTERVAL '24 hours'
            """))
            count = int(r.scalar() or 0)
        if count >= _LAYER2_DAILY_CAP:
            logger.warning(
                "Layer 2 daily cap reached: %d/%d calls — skip Gemini Vision, escalate admin",
                count, _LAYER2_DAILY_CAP,
            )
            # Alert ห้อง Report (once per hour ก็พอ — admin_alert ของผมไม่มี throttle)
            try:
                from shared.admin_alert import notify_admin_report
                await notify_admin_report(
                    f"⚠️ <b>Layer 2 cost cap reached</b>\n"
                    f"━━━━━━━━━━━━\n"
                    f"📊 calls today: <b>{count} / {_LAYER2_DAILY_CAP}</b>\n"
                    f"⏸ skip Gemini Vision → escalate admin manual\n\n"
                    f"<i>ปรับ cap ที่ LAYER2_DAILY_CAP env variable</i>",
                    parse_mode="HTML",
                )
            except Exception:
                pass
            return False
    except Exception as exc:
        logger.warning("Layer 2 cap check failed: %s — allowing call", exc)
    return True


async def _ai_read_slip(b64_image: str) -> str | None:
    """Use AI vision (Gemini Flash Lite via OpenRouter) to read payment slip.

    Returns extracted text with amount, date, bank, ref number.
    Also checks for signs of forgery.
    """
    # FIX 2026-06-21: Daily cost guard
    if not await _check_layer2_daily_cap():
        return None

    from shared.api_cost_tracker import call_openrouter

    prompt = (
        "อ่านสลิปโอนเงินนี้ ตอบเป็น text สั้นๆ ภาษาไทย ข้อมูลต่อไปนี้:\n"
        "- จำนวนเงิน (ตัวเลข เช่น 300.00)\n"
        "- วันที่และเวลา\n"
        "- ธนาคารต้นทาง\n"
        "- ธนาคารปลายทาง\n"
        "- เลขอ้างอิง/Transaction ID\n"
        "- ชื่อผู้ส่ง (จาก)\n"
        "- ชื่อผู้รับ (ไปยัง)\n\n"
        "แล้ววิเคราะห์ว่าสลิปนี้มีสัญญาณปลอมไหม โดยดูจาก:\n"
        "- font ไม่ตรงกับธนาคารจริง\n"
        "- layout ผิดรูปแบบของธนาคาร\n"
        "- ภาพเบลอเฉพาะจุดตัวเลข (น่าจะถูกแก้ไข)\n"
        "- โลโก้ธนาคารผิดเพี้ยน\n"
        "\n"
        "ข้อสำคัญ: ห้ามใช้ \"ปี\" เป็นเหตุผลในการบอกว่าปลอม.\n"
        "  ปี พ.ศ. (เช่น 2569) = ค.ศ. (2026) = ปัจจุบัน — เป็นปกติของสลิปไทย\n"
        "  ห้าม flag \"วันที่อนาคต\" เด็ดขาด\n"
        "\n"
        "ถ้ามีสัญญาณปลอมตามรายการข้างบน ให้เขียน SUSPICIOUS: ตามด้วยเหตุผล\n"
        "ถ้าปกติ ให้เขียน VERIFIED: ตามด้วยข้อมูล"
    )

    try:
        data = await call_openrouter(
            model="google/gemini-2.5-flash",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{b64_image}"
                            },
                        },
                    ],
                }
            ],
            caller="sales_bot/ai_read_slip",
            max_tokens=500,
            temperature=0.7,
        )
        content = data["choices"][0]["message"]["content"]
        logger.info("AI slip reader result: %s", content[:200])

        # FIX 2026-06-29: filter false-positive SUSPICIOUS — Buddhist Era confusion
        # AI vision บางทียัง flag "วันที่อนาคต" แม้บอกใน prompt แล้ว
        # (Thai slips ใช้ พ.ศ. = ค.ศ. + 543 ปกติ)
        # → strip SUSPICIOUS line ถ้าเหตุผลเกี่ยวกับ "อนาคต/ปี/year/future/พ.ศ./BE"
        try:
            filtered_lines = []
            stripped_count = 0
            for line in content.split("\n"):
                ls = line.strip()
                if "SUSPICIOUS" in ls.upper():
                    reason = ls.split("SUSPICIOUS", 1)[-1].strip(": ").lower()
                    false_kw = ["อนาคต", "future", "ปี ", "ปีพ", "พ.ศ.", " be ", "2569", "2570", "2571", "year"]
                    hard_kw = ["จำนวน", "ยอด", "amount", "ปลอม", "fake", "ตัดต่อ", "edited", "แก้ไข", "mismatch", "ไม่ตรง", "ซ้ำ", "duplicate", "บัญชี", "ชื่อ"]
                    if any(kw in reason for kw in false_kw) and not any(hk in reason for hk in hard_kw):
                        stripped_count += 1
                        logger.info("Stripped false-positive SUSPICIOUS (year-related): %s", ls[:120])
                        continue
                filtered_lines.append(line)
            if stripped_count > 0:
                content = "\n".join(filtered_lines)
        except Exception as _e:
            logger.warning("SUSPICIOUS filter skipped: %s", _e)

        return content
    except Exception as exc:
        # FIX 2025-05-21 (Phase 2d caller): re-raise circuit-open so caller can defer slip
        from shared.api_cost_tracker import OpenRouterCircuitOpen as _CircuitOpen
        if isinstance(exc, _CircuitOpen):
            raise
        logger.error("AI slip reader API error: %s", exc)

    return None

async def _ocr_slip_image(bot, file_id: str) -> str:
    """Download image from Telegram and use AI to read slip.

    2026-06-16 FIX: retry 3x with exponential backoff for transient timeouts.
    """
    import asyncio
    import base64

    image_bytes = None
    last_err = None
    for attempt in range(3):
        try:
            file = await bot.get_file(file_id, read_timeout=30, connect_timeout=15)
            buf = io.BytesIO()
            await file.download_to_memory(buf)
            buf.seek(0)
            image_bytes = buf.read()
            if attempt > 0:
                logger.info("OCR download succeeded after %d retries", attempt)
            break
        except Exception as exc:
            last_err = exc
            if attempt < 2:
                delay = 2 ** (attempt + 1)  # 2s, 4s
                logger.warning("OCR download attempt %d failed: %s — retry in %ds",
                                attempt + 1, exc, delay)
                await asyncio.sleep(delay)
            else:
                logger.error("OCR download failed after 3 attempts: %s", exc)
                raise

    if image_bytes is None:
        raise (last_err or RuntimeError("download failed"))
    b64_image = base64.b64encode(image_bytes).decode("utf-8")

    # Try AI vision first, fallback to tesseract
    try:
        ai_text = await _ai_read_slip(b64_image)
        if ai_text:
            return ai_text
    except Exception as exc:
        logger.warning("AI slip reader failed, falling back to OCR: %s", exc)

    # Fallback to tesseract
    buf.seek(0)
    image = Image.open(buf)
    text = pytesseract.image_to_string(image, lang="tha+eng")
    return text
