"""Ad Copywriter (เจมส์) - เขียน Facebook ad copy เซ็กซี่แต่ safe.

Model: anthropic/claude-haiku-4-5 ผ่าน OpenRouter
กฎเหล็ก: เซ็กซี่-safe, ห้าม 18+, ห้าม repost จาก Telegram
"""

from __future__ import annotations

import logging
from typing import Any

from shared.api_cost_tracker import call_openrouter

logger = logging.getLogger(__name__)

MODEL = "anthropic/claude-haiku-4-5"
CALLER = "marketing_agent/copywriter"

FB_ALLOWED_CTAS = ["Send Message", "Learn More"]

FB_BANNED_WORDS = [
    "xxx", "porn", "nude", "naked", "sex", "18+",
    "โป๊", "เปลือย", "โป้", "หนังx", "คลิปx", "หนังโป๊",
    "เย็ด", "หี", "ควย", "สาวนม", "onlyfans.com",
    "เสียว", "ลับเฉพาะ", "ส่วนลับ",
]

AD_TONES = {
    "sexy_safe": (
        "เซ็กซี่แต่ปลอดภัย ใช้คำพูดที่ชวนจินตนาการ "
        "ไม่พูดตรงๆ ไม่มี 18+ content ผ่าน FB policy ได้"
    ),
    "curiosity": (
        "สร้างความอยากรู้ ลึกลับ ชวนให้คลิก "
        "ไม่เปิดเผยว่าข้างในมีอะไร"
    ),
    "lifestyle": (
        "สไตล์ lifestyle หรูหรา น่าดึงดูด "
        "เหมือนโปรโมท exclusive club"
    ),
    "social_proof": (
        "ใช้ social proof สมาชิกเยอะ คนติดใจ "
        "ไม่ต้องบอกว่ามีอะไรข้างใน"
    ),
}


def _build_ad_copy_prompt(
    campaign_name: str,
    target_audience: str,
    content_hint: str | None = None,
    tone: str = "sexy_safe",
    cta: str = "Send Message",
) -> list[dict[str, str]]:
    """สร้าง prompt สำหรับเขียน Facebook ad copy."""
    tone_desc = AD_TONES.get(tone, AD_TONES["sexy_safe"])

    banned_list = ", ".join(FB_BANNED_WORDS[:10])

    system_msg = (
        "คุณคือ 'เจมส์' Marketing Copywriter ของบริษัทเจริญพร\n"
        "เขียน Facebook ad copy สำหรับโปรโมท Telegram VIP group\n\n"
        "กฎเหล็ก Facebook Ads:\n"
        "1. ห้ามมีเนื้อหา 18+ หรือ sexual content โดยเด็ดขาด\n"
        "2. ห้าม repost หรืออ้างอิง content จาก Telegram\n"
        "3. ห้ามใช้คำต้องห้าม FB: " + banned_list + " ฯลฯ\n"
        "4. ห้ามใส่ URL หรือลิงก์ Telegram\n"
        "5. CTA ต้องเป็น 'Send Message' หรือ 'Learn More' เท่านั้น\n"
        "6. เนื้อหนังในรูปต้องไม่เกิน 20%\n"
        "7. ใช้ภาษาที่สื่อถึง exclusive community / premium content\n"
        "8. ห้ามสัญญาสิ่งที่ทำไม่ได้\n\n"
        f"โทน: {tone_desc}\n\n"
        "รูปแบบ output:\n"
        "- Headline (สูงสุด 40 ตัวอักษร)\n"
        "- Primary Text (สูงสุด 125 ตัวอักษร)\n"
        "- Description (สูงสุด 30 ตัวอักษร)\n"
        f"- CTA: {cta}\n"
    )

    user_msg = (
        f"Campaign: {campaign_name}\n"
        f"Target Audience: {target_audience}\n"
        f"CTA Button: {cta}\n"
    )
    if content_hint:
        user_msg += f"Content Hint (ห้ามใช้ตรงๆ แค่เป็น inspiration): {content_hint}\n"

    return [
        {"role": "system", "content": system_msg},
        {"role": "user", "content": user_msg},
    ]


def _validate_banned_words(text: str) -> list[str]:
    """ตรวจสอบคำต้องห้ามใน text."""
    text_lower = text.lower()
    found = []
    for word in FB_BANNED_WORDS:
        if word.lower() in text_lower:
            found.append(word)
    return found


def _validate_cta(cta: str) -> bool:
    """ตรวจสอบว่า CTA อยู่ในรายการที่อนุญาต."""
    return cta in FB_ALLOWED_CTAS


async def generate_ad_copy(
    campaign_name: str,
    target_audience: str,
    content_hint: str | None = None,
    tone: str = "sexy_safe",
    cta: str = "Send Message",
) -> dict[str, Any]:
    """สร้าง Facebook ad copy ด้วย Claude Haiku."""
    if not _validate_cta(cta):
        return {
            "success": False,
            "error": f"CTA '{cta}' ไม่อนุญาต ใช้ได้แค่: {FB_ALLOWED_CTAS}",
        }

    messages = _build_ad_copy_prompt(campaign_name, target_audience, content_hint, tone, cta)

    response = await call_openrouter(
        model=MODEL,
        messages=messages,
        caller=CALLER,
        temperature=0.7,
        max_tokens=512,
        metadata={
            "campaign": campaign_name,
            "tone": tone,
            "cta": cta,
        },
    )

    raw_copy = response["choices"][0]["message"]["content"].strip()

    banned_found = _validate_banned_words(raw_copy)
    if banned_found:
        logger.warning(
            "Ad copy contains banned words: %s — requesting rewrite", banned_found
        )
        rewrite_messages = messages + [
            {"role": "assistant", "content": raw_copy},
            {
                "role": "user",
                "content": (
                    f"ข้อความนี้มีคำต้องห้าม: {', '.join(banned_found)}\n"
                    "กรุณาเขียนใหม่โดยหลีกเลี่ยงคำเหล่านี้ทั้งหมด"
                ),
            },
        ]
        rewrite_resp = await call_openrouter(
            model=MODEL,
            messages=rewrite_messages,
            caller=CALLER,
            temperature=0.6,
            max_tokens=512,
            metadata={"campaign": campaign_name, "rewrite": True},
        )
        raw_copy = rewrite_resp["choices"][0]["message"]["content"].strip()

        banned_found = _validate_banned_words(raw_copy)
        if banned_found:
            return {
                "success": False,
                "error": f"ยังมีคำต้องห้ามหลัง rewrite: {banned_found}",
                "raw_copy": raw_copy,
            }

    parsed = _parse_ad_copy(raw_copy)

    logger.info("Generated ad copy for campaign '%s'", campaign_name)
    return {
        "success": True,
        "campaign_name": campaign_name,
        "cta": cta,
        "tone": tone,
        "raw_copy": raw_copy,
        "parsed": parsed,
    }


def _parse_ad_copy(raw_copy: str) -> dict[str, str]:
    """แยก ad copy ออกเป็น headline, primary_text, description."""
    lines = raw_copy.strip().split("\n")
    parsed = {"headline": "", "primary_text": "", "description": "", "full_text": raw_copy}

    for line in lines:
        line_stripped = line.strip()
        lower = line_stripped.lower()

        if lower.startswith("headline") or lower.startswith("หัวข้อ"):
            parsed["headline"] = line_stripped.split(":", 1)[-1].strip().strip('"\'')
        elif lower.startswith("primary text") or lower.startswith("ข้อความหลัก"):
            parsed["primary_text"] = line_stripped.split(":", 1)[-1].strip().strip('"\'')
        elif lower.startswith("description") or lower.startswith("คำอธิบาย"):
            parsed["description"] = line_stripped.split(":", 1)[-1].strip().strip('"\'')

    if not parsed["headline"] and lines:
        parsed["headline"] = lines[0].strip().strip('"\'')
    if not parsed["primary_text"] and len(lines) > 1:
        parsed["primary_text"] = lines[1].strip().strip('"\'')

    return parsed


async def generate_ad_variations(
    campaign_name: str,
    target_audience: str,
    content_hint: str | None = None,
    count: int = 3,
) -> list[dict[str, Any]]:
    """สร้าง ad copy หลาย variations สำหรับ A/B testing."""
    tones = list(AD_TONES.keys())[:count]
    variations = []

    for i, tone in enumerate(tones):
        result = await generate_ad_copy(
            campaign_name=f"{campaign_name} - Var {i + 1}",
            target_audience=target_audience,
            content_hint=content_hint,
            tone=tone,
        )
        result["variation"] = i + 1
        result["tone_used"] = tone
        variations.append(result)

    logger.info("Generated %d ad variations for '%s'", len(variations), campaign_name)
    return variations
