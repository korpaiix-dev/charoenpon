"""AI / OCR helpers extracted from handlers/payment.py (Round 2 retry).
Calls OpenRouter for slip screening + reading, plus OCR fallback.
"""
from __future__ import annotations

import asyncio
import base64
import json
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

async def _ai_read_slip(b64_image: str) -> str | None:
    """Use AI vision (Gemini Flash Lite via OpenRouter) to read payment slip.

    Returns extracted text with amount, date, bank, ref number.
    Also checks for signs of forgery.
    """
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
        "แล้ววิเคราะห์ว่าสลิปนี้มีสัญญาณปลอมไหม เช่น:\n"
        "- font ไม่ตรงกับธนาคาร\n"
        "- วันที่อนาคต\n"
        "- layout ผิดปกติ\n"
        "- ภาพเบลอเฉพาะจุดตัวเลข\n"
        "ถ้าสงสัยปลอม ให้เขียน SUSPICIOUS: ตามด้วยเหตุผล\n"
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
        return content
    except Exception as exc:
        # FIX 2025-05-21 (Phase 2d caller): re-raise circuit-open so caller can defer slip
        from shared.api_cost_tracker import OpenRouterCircuitOpen as _CircuitOpen
        if isinstance(exc, _CircuitOpen):
            raise
        logger.error("AI slip reader API error: %s", exc)

    return None

async def _ocr_slip_image(bot, file_id: str) -> str:
    """Download image from Telegram and use AI to read slip."""
    import base64

    file = await bot.get_file(file_id)
    buf = io.BytesIO()
    await file.download_to_memory(buf)
    buf.seek(0)
    image_bytes = buf.read()
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
