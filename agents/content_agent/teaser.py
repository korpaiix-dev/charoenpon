"""Teaser Generator (มิน) - สร้าง Teaser สำหรับกลุ่มฟรี.

Model: deepseek/deepseek-chat ผ่าน OpenRouter
สไตล์: เร้าใจ ลึกลับ FOMO ปิดท้าย CTA ห้ามโจ่งแจ้ง วับๆแวมๆ
"""

from __future__ import annotations

import logging
import random
from typing import Any

from shared.api_cost_tracker import call_openrouter
from shared.models import GroupSlug

logger = logging.getLogger(__name__)

MODEL = "deepseek/deepseek-chat"
CALLER = "content_agent/teaser"

TEASER_STYLES = [
    "mystery",
    "fomo",
    "countdown",
    "exclusive_peek",
    "behind_scenes",
]

CTA_TEMPLATES = [
    "🔥 อยากเห็นเต็มๆ? DM มาเลย!",
    "💬 สนใจ? ทักแอดมินได้เลยนะ",
    "🔐 สมาชิก VIP เท่านั้นที่ได้เห็น... ทักมาสิ!",
    "👀 แค่นี้ยังไม่พอใช่มั้ย? มาคุยกัน~",
    "💎 ของดีมีให้เฉพาะคนพิเศษ ทักเลย!",
    "🔥 เปิดล็อกได้ที่ VIP group ทักแอดมินนะ",
    "✨ มีอีกเยอะ... แต่ต้องเป็น VIP ถึงจะได้เห็น 😏",
    "💋 แค่ชิมลาง... ของจริงอยู่ข้างใน ทักมาเลย",
]

FOMO_HOOKS = [
    "วันนี้มีคอนเทนต์พิเศษที่ทำให้สมาชิกร้อง... 🫢",
    "เพิ่งปล่อยไปเมื่อกี้ สมาชิกแชทกันไม่หยุดเลย 🔥",
    "บอกแค่ว่า... คนข้างในเขาเห็นกันหมดแล้ว 👀",
    "Set ใหม่มาแล้ว แต่เฉพาะ VIP เท่านั้น... 🤫",
    "สมาชิกใหม่วันนี้บอกว่า คุ้มมากกกก 💯",
    "ข้างในมีอะไรดีๆ รอคุณอยู่... ลองสิ 😏",
    "คนข้างในเขาดูกันแล้ว คุณเหลือแค่ทักมา... 💬",
]


def _build_teaser_prompt(
    group_slug: str,
    content_hint: str | None = None,
    style: str | None = None,
) -> list[dict[str, str]]:
    """สร้าง prompt สำหรับเขียน teaser ลงกลุ่มฟรี."""
    if style is None:
        style = random.choice(TEASER_STYLES)

    fomo_hook = random.choice(FOMO_HOOKS)
    cta = random.choice(CTA_TEMPLATES)

    system_msg = (
        "คุณคือ 'มิน' ผู้เชี่ยวชาญเขียน Teaser สำหรับ Telegram กลุ่มฟรี "
        "ของบริษัทเจริญพร\n\n"
        "เป้าหมาย: ดึงคนจากกลุ่มฟรีมาสมัคร VIP\n\n"
        "กฎเหล็ก Teaser:\n"
        "1. วับๆแวมๆ เร้าใจ แต่ห้ามโจ่งแจ้ง ห้ามเปิดเผยอะไรชัดเจน\n"
        "2. สร้าง FOMO ให้คนรู้สึกว่าพลาดอะไรดีๆ\n"
        "3. ใช้ความลึกลับ ทำให้อยากรู้อยากเห็น\n"
        "4. ปิดท้ายด้วย CTA ชวนทักแอดมิน/สมัคร VIP\n"
        "5. ห้ามใส่ URL/ลิงก์\n"
        "6. ห้ามใส่ราคาแพ็กเกจ\n"
        "7. ห้ามใช้คำหยาบคาย\n"
        "8. ห้ามพูดถึงเนื้อหา 18+ โดยตรง\n"
        "9. ใช้อิโมจิพอเหมาะ ไม่เยอะเกิน\n"
        "10. ความยาว 3-5 บรรทัด\n\n"
        f"สไตล์ที่ต้องการ: {style}\n"
        f"FOMO hook: {fomo_hook}\n"
        f"CTA ปิดท้าย: {cta}\n\n"
        "เขียนเป็นภาษาไทย น้ำเสียงเป็นกันเอง เซ็กซี่แต่ไม่หยาบ\n"
    )

    user_msg = "เขียน teaser โพสต์ลงกลุ่มฟรี"
    if content_hint:
        user_msg += f"\nHint จาก VIP content: {content_hint}"
    user_msg += f"\nสำหรับโปรโมทกลุ่ม: {group_slug}"

    return [
        {"role": "system", "content": system_msg},
        {"role": "user", "content": user_msg},
    ]


async def generate_teaser(
    group_slug: str = "general",
    content_hint: str | None = None,
    style: str | None = None,
) -> str:
    """สร้าง teaser ข้อความสำหรับโพสต์ในกลุ่มฟรี."""
    messages = _build_teaser_prompt(group_slug, content_hint, style)

    response = await call_openrouter(
        model=MODEL,
        messages=messages,
        caller=CALLER,
        temperature=0.9,
        max_tokens=400,
        metadata={"group_slug": group_slug, "style": style},
    )

    teaser = response["choices"][0]["message"]["content"].strip()
    logger.info("Generated teaser for %s: %d chars", group_slug, len(teaser))
    return teaser


async def generate_batch_teasers(
    count: int = 5,
    group_slug: str = "general",
    content_hint: str | None = None,
) -> list[str]:
    """สร้าง teaser หลายชิ้นพร้อมกัน แต่ละชิ้นใช้สไตล์ต่างกัน."""
    teasers = []
    styles_to_use = TEASER_STYLES[:count] if count <= len(TEASER_STYLES) else (
        TEASER_STYLES * (count // len(TEASER_STYLES) + 1)
    )[:count]

    for style in styles_to_use:
        teaser = await generate_teaser(
            group_slug=group_slug,
            content_hint=content_hint,
            style=style,
        )
        teasers.append(teaser)

    logger.info("Generated batch of %d teasers for %s", len(teasers), group_slug)
    return teasers


async def generate_teaser_for_story(
    story_context: str,
    target_groups: list[str] | None = None,
) -> dict[str, str]:
    """สร้าง teaser สำหรับแต่ละกลุ่มเป้าหมาย จาก story context เดียว."""
    if target_groups is None:
        target_groups = [g.value for g in GroupSlug]

    results: dict[str, str] = {}
    for group in target_groups:
        teaser = await generate_teaser(
            group_slug=group,
            content_hint=story_context,
        )
        results[group] = teaser

    return results


def build_teaser_with_media_hint(teaser_text: str, has_photo: bool = True) -> str:
    """เพิ่ม media hint ให้กับ teaser (บอกว่ามีรูป/คลิปแต่โชว์แค่เบลอ)."""
    if has_photo:
        media_line = "📷 ตัวอย่างอยู่ด้านบน... แต่ของจริงชัดกว่านี้เยอะ 😏"
    else:
        media_line = "🎬 มีคลิปให้ดูข้างใน... แค่ภาพนิ่งยังขนาดนี้ 🔥"

    return f"{teaser_text}\n\n{media_line}"
