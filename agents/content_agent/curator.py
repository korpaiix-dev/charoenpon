"""Content Curator (มิน) - จัดการ content ทุกกลุ่ม VIP ตามมาตรฐานแต่ละ tier.

Model: deepseek/deepseek-chat ผ่าน OpenRouter
กฎเหล็ก: 18+ เต็มที่ใน VIP, ห้ามส่งไฟล์ให้เจมส์ ส่งแค่ hint
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from shared.api_cost_tracker import call_openrouter
from shared.database import get_session
from shared.models import ContentSchedule, GroupSlug

logger = logging.getLogger(__name__)

MODEL = "deepseek/deepseek-chat"
CALLER = "content_agent/curator"


class ContentTone(str, Enum):
    G300_STANDARD = "g300"
    G500_PREMIUM = "g500"
    SSS_EXCLUSIVE = "sss"
    VGOD_EXCLUSIVE = "vgod"
    OF_STYLE = "of"
    INTER_GLOBAL = "inter"
    SERIES_CINEMA = "series"


GROUP_CONTENT_RULES: dict[str, dict[str, Any]] = {
    GroupSlug.G300.value: {
        "tone": ContentTone.G300_STANDARD,
        "description": "กลุ่มเริ่มต้น ราคา 300 บาท",
        "content_level": "เซ็กซี่ระดับกลาง เน้นยั่วเบาๆ ไม่เปิดเผยมาก",
        "post_style": "รูป/คลิปเซ็กซี่ แคปชั่นเร้าใจ ชวนอัปเกรด",
        "upgrade_hint": True,
        "max_explicit": 0.4,
    },
    GroupSlug.G500.value: {
        "tone": ContentTone.G500_PREMIUM,
        "description": "กลุ่มระดับสูงกว่า ราคา 500 บาท",
        "content_level": "เซ็กซี่ระดับสูง เปิดเผยมากขึ้น",
        "post_style": "รูป/คลิปเซ็กซี่จัดเต็ม แคปชั่นเผ็ดร้อน",
        "upgrade_hint": True,
        "max_explicit": 0.6,
    },
    GroupSlug.SSS.value: {
        "tone": ContentTone.SSS_EXCLUSIVE,
        "description": "กลุ่ม Premium SSS",
        "content_level": "18+ เต็มที่ exclusive content",
        "post_style": "คอนเทนต์พรีเมียมเต็มรูปแบบ ไม่มีเซ็นเซอร์",
        "upgrade_hint": False,
        "max_explicit": 1.0,
    },
    GroupSlug.VGOD.value: {
        "tone": ContentTone.VGOD_EXCLUSIVE,
        "description": "กลุ่ม VGOD Premium สุดพิเศษ",
        "content_level": "18+ เต็มที่ VGOD exclusive ไม่มีที่ไหน",
        "post_style": "คอนเทนต์ระดับสูงสุด exclusive เฉพาะ VGOD",
        "upgrade_hint": False,
        "max_explicit": 1.0,
    },
    GroupSlug.OF.value: {
        "tone": ContentTone.OF_STYLE,
        "description": "กลุ่ม OnlyFans style",
        "content_level": "18+ สไตล์ OnlyFans creator content",
        "post_style": "OF-style photoshoot, personal & intimate feel",
        "upgrade_hint": False,
        "max_explicit": 1.0,
    },
    GroupSlug.INTER.value: {
        "tone": ContentTone.INTER_GLOBAL,
        "description": "กลุ่ม International เอเชีย/ยุโรป",
        "content_level": "18+ นานาชาติ สาวเอเชีย สาวยุโรป",
        "post_style": "international models, mixed Asian/European content",
        "upgrade_hint": False,
        "max_explicit": 1.0,
    },
    GroupSlug.SERIES.value: {
        "tone": ContentTone.SERIES_CINEMA,
        "description": "กลุ่มหนัง/ซีรี่ส์ 18+",
        "content_level": "18+ หนัง ซีรี่ส์ คลิปยาว",
        "post_style": "movie/series clips, scene highlights, reviews",
        "upgrade_hint": False,
        "max_explicit": 1.0,
    },
}


def _build_caption_prompt(
    group_slug: str,
    content_type: str,
    context: str | None = None,
) -> list[dict[str, str]]:
    """สร้าง prompt สำหรับเขียนแคปชั่น content ตาม group rules."""
    rules = GROUP_CONTENT_RULES.get(group_slug, GROUP_CONTENT_RULES[GroupSlug.G300.value])

    system_msg = (
        "คุณคือ 'มิน' Content Curator ของบริษัทเจริญพร "
        "เขียนแคปชั่นสำหรับโพสต์ใน Telegram VIP group\n\n"
        f"กลุ่ม: {rules['description']}\n"
        f"ระดับคอนเทนต์: {rules['content_level']}\n"
        f"สไตล์โพสต์: {rules['post_style']}\n\n"
        "กฎสำคัญ:\n"
        "- เขียนเป็นภาษาไทย ใช้อิโมจิพอเหมาะ\n"
        "- ห้ามใส่ URL หรือลิงก์ใดๆ\n"
        "- ห้ามส่งไฟล์หรือคอนเทนต์จริงให้เจมส์(Marketing) ส่งได้แค่ hint เท่านั้น\n"
        "- ห้ามเอ่ยชื่อจริงของนางแบบ\n"
    )

    if rules.get("upgrade_hint"):
        system_msg += (
            "- ปิดท้ายด้วยข้อความชวนอัปเกรดแพ็กเกจเพื่อดูคอนเทนต์เต็มๆ\n"
        )

    user_msg = f"เขียนแคปชั่นสำหรับ {content_type}"
    if context:
        user_msg += f"\nบริบทเพิ่มเติม: {context}"

    return [
        {"role": "system", "content": system_msg},
        {"role": "user", "content": user_msg},
    ]


async def generate_caption(
    group_slug: str,
    content_type: str,
    context: str | None = None,
) -> str:
    """สร้างแคปชั่นสำหรับ content โดยใช้ DeepSeek ผ่าน OpenRouter."""
    messages = _build_caption_prompt(group_slug, content_type, context)

    response = await call_openrouter(
        model=MODEL,
        messages=messages,
        caller=CALLER,
        temperature=0.8,
        max_tokens=512,
        metadata={"group": group_slug, "content_type": content_type},
    )

    caption = response["choices"][0]["message"]["content"].strip()
    logger.info("Generated caption for %s (%s): %d chars", group_slug, content_type, len(caption))
    return caption


async def curate_content(
    group_slug: str,
    content_type: str,
    media_file_id: str | None = None,
    media_url: str | None = None,
    context: str | None = None,
    scheduled_at: datetime | None = None,
    created_by: int | None = None,
) -> ContentSchedule:
    """สร้าง content พร้อมแคปชั่นและบันทึกลง schedule."""
    caption = await generate_caption(group_slug, content_type, context)

    if scheduled_at is None:
        scheduled_at = datetime.now(timezone.utc)

    schedule_entry = ContentSchedule(
        group_slug=GroupSlug(group_slug),
        scheduled_at=scheduled_at,
        content_type=content_type,
        caption=caption,
        media_file_id=media_file_id,
        media_url=media_url,
        is_sent=False,
        created_by=created_by,
    )

    async with get_session() as session:
        session.add(schedule_entry)
        await session.flush()
        await session.refresh(schedule_entry)

    logger.info(
        "Curated content #%d for group %s scheduled at %s",
        schedule_entry.id, group_slug, scheduled_at,
    )
    return schedule_entry


async def curate_for_all_groups(
    content_type: str,
    media_file_id: str | None = None,
    media_url: str | None = None,
    context: str | None = None,
    scheduled_at: datetime | None = None,
    created_by: int | None = None,
) -> list[ContentSchedule]:
    """สร้าง content สำหรับทุกกลุ่ม VIP พร้อมแคปชั่นที่ customize ตาม group."""
    results = []
    for slug in GroupSlug:
        entry = await curate_content(
            group_slug=slug.value,
            content_type=content_type,
            media_file_id=media_file_id,
            media_url=media_url,
            context=context,
            scheduled_at=scheduled_at,
            created_by=created_by,
        )
        results.append(entry)
    return results


def get_hint_for_marketing(group_slug: str, content_type: str) -> str:
    """สร้าง hint สำหรับส่งให้เจมส์ (Marketing Agent) ห้ามส่งไฟล์จริง."""
    rules = GROUP_CONTENT_RULES.get(group_slug, GROUP_CONTENT_RULES[GroupSlug.G300.value])
    return (
        f"📌 Content Hint จากมิน:\n"
        f"กลุ่ม: {rules['description']}\n"
        f"ประเภท: {content_type}\n"
        f"โทน: {rules['post_style']}\n"
        f"⚠️ ห้ามใช้คอนเทนต์จริงในโฆษณา FB — ใช้ hint นี้เป็นแรงบันดาลใจเท่านั้น"
    )


async def get_group_content_stats(group_slug: str) -> dict[str, Any]:
    """ดึงสถิติ content ของกลุ่ม."""
    from sqlalchemy import func, select

    async with get_session() as session:
        result = await session.execute(
            select(
                func.count(ContentSchedule.id).label("total"),
                func.count(ContentSchedule.id).filter(ContentSchedule.is_sent.is_(True)).label("sent"),
                func.count(ContentSchedule.id).filter(ContentSchedule.is_sent.is_(False)).label("pending"),
            ).where(ContentSchedule.group_slug == GroupSlug(group_slug))
        )
        row = result.one()

    return {
        "group": group_slug,
        "total_content": row.total,
        "sent": row.sent,
        "pending": row.pending,
        "rules": GROUP_CONTENT_RULES.get(group_slug, {}),
    }
