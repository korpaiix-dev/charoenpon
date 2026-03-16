"""Safety Check (เจมส์) - ตรวจสอบ ad copy ก่อนยิง Facebook Ads.

Checklist:
1. ไม่มีเนื้อหนังเกิน 20%
2. ไม่มี trigger words FB
3. ไม่มี URL
4. CTA Send Message/Learn More เท่านั้น
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

FB_TRIGGER_WORDS = [
    "xxx", "porn", "nude", "naked", "sex", "18+", "adult",
    "โป๊", "เปลือย", "โป้", "หนังx", "คลิปx", "หนังโป๊",
    "เย็ด", "หี", "ควย", "นม", "เสียว", "ส่วนลับ",
    "onlyfans", "fansly", "manyvids",
    "nsfw", "r-rated", "x-rated",
    "gambling", "casino", "พนัน", "เดิมพัน",
    "drug", "ยาเสพติด", "กัญชา",
    "weapon", "gun", "อาวุธ", "ปืน",
    "crypto", "bitcoin", "รวยเร็ว", "get rich",
    "before and after", "ก่อนและหลัง",
    "guarantee", "รับประกัน",
]

ALLOWED_CTAS = ["Send Message", "Learn More"]

URL_PATTERN = re.compile(
    r"https?://[^\s<>\"']+|"
    r"www\.[^\s<>\"']+|"
    r"t\.me/[^\s<>\"']+|"
    r"telegram\.me/[^\s<>\"']+|"
    r"bit\.ly/[^\s<>\"']+|"
    r"[a-zA-Z0-9.-]+\.(com|net|org|io|co|me|link|xyz|app)/?"
)

SKIN_EXPOSURE_KEYWORDS = [
    "บิกินี่", "bikini", "ชุดว่ายน้ำ", "swimsuit", "lingerie",
    "ชุดชั้นใน", "underwear", "topless", "shirtless",
    "เปลือย", "เปลือยกาย", "naked", "nude",
    "crop top", "ครอปท็อป", "สายเดี่ยว", "เกาะอก",
]


def check_trigger_words(text: str) -> list[str]:
    """ตรวจสอบคำต้องห้ามของ Facebook."""
    text_lower = text.lower()
    found = []
    for word in FB_TRIGGER_WORDS:
        if word.lower() in text_lower:
            found.append(word)
    return found


def check_urls(text: str) -> list[str]:
    """ตรวจสอบ URL ในข้อความ."""
    return URL_PATTERN.findall(text)


def check_cta(cta: str) -> bool:
    """ตรวจสอบว่า CTA อยู่ในรายการที่อนุญาต."""
    return cta in ALLOWED_CTAS


def check_skin_exposure_text(text: str) -> list[str]:
    """ตรวจสอบคำที่สื่อถึงเนื้อหนังมากเกินไป (text-based check)."""
    text_lower = text.lower()
    found = []
    for keyword in SKIN_EXPOSURE_KEYWORDS:
        if keyword.lower() in text_lower:
            found.append(keyword)
    return found


def check_text_length(parsed_copy: dict[str, str]) -> list[str]:
    """ตรวจสอบความยาวข้อความตาม FB ad specs."""
    issues = []
    headline = parsed_copy.get("headline", "")
    primary_text = parsed_copy.get("primary_text", "")
    description = parsed_copy.get("description", "")

    if len(headline) > 40:
        issues.append(f"Headline ยาวเกิน ({len(headline)}/40 ตัวอักษร)")
    if len(primary_text) > 125:
        issues.append(f"Primary Text ยาวเกิน ({len(primary_text)}/125 ตัวอักษร)")
    if len(description) > 30:
        issues.append(f"Description ยาวเกิน ({len(description)}/30 ตัวอักษร)")

    return issues


def estimate_skin_percentage(image_description: str | None = None) -> float:
    """ประมาณสัดส่วนเนื้อหนังจาก image description.

    ค่าจริงต้องใช้ Vision API แต่ตอนนี้ใช้ text-based estimation
    Returns 0.0-1.0 (0% - 100%)
    """
    if not image_description:
        return 0.0

    score = 0.0
    desc_lower = image_description.lower()

    high_skin_words = ["nude", "naked", "topless", "เปลือย", "เปลือยกาย"]
    for word in high_skin_words:
        if word in desc_lower:
            score = max(score, 0.8)

    medium_skin_words = [
        "bikini", "บิกินี่", "lingerie", "ชุดชั้นใน",
        "swimsuit", "ชุดว่ายน้ำ",
    ]
    for word in medium_skin_words:
        if word in desc_lower:
            score = max(score, 0.4)

    low_skin_words = [
        "crop top", "ครอปท็อป", "สายเดี่ยว", "เกาะอก",
        "tank top", "shorts", "กางเกงขาสั้น",
    ]
    for word in low_skin_words:
        if word in desc_lower:
            score = max(score, 0.15)

    return score


def run_safety_check(
    ad_copy: dict[str, Any],
    image_description: str | None = None,
) -> dict[str, Any]:
    """รัน Safety Checklist ครบทุกข้อ.

    Returns:
        dict with:
        - passed: bool - ผ่านทุกข้อหรือไม่
        - issues: list[str] - รายการปัญหาที่พบ
        - warnings: list[str] - คำเตือน (ไม่ถึงขั้น fail)
        - checks: dict - ผลตรวจแต่ละข้อ
    """
    issues: list[str] = []
    warnings: list[str] = []
    checks: dict[str, Any] = {}

    raw_copy = ad_copy.get("raw_copy", "")
    parsed = ad_copy.get("parsed", {})
    cta = ad_copy.get("cta", "")
    full_text = f"{parsed.get('headline', '')} {parsed.get('primary_text', '')} {parsed.get('description', '')} {raw_copy}"

    # 1. Trigger words
    trigger_found = check_trigger_words(full_text)
    checks["trigger_words"] = {
        "passed": len(trigger_found) == 0,
        "found": trigger_found,
    }
    if trigger_found:
        issues.append(f"พบคำต้องห้าม FB: {', '.join(trigger_found)}")

    # 2. URL check
    urls_found = check_urls(full_text)
    checks["urls"] = {
        "passed": len(urls_found) == 0,
        "found": urls_found,
    }
    if urls_found:
        issues.append(f"พบ URL ในข้อความ: {', '.join(str(u) for u in urls_found)}")

    # 3. CTA check
    cta_valid = check_cta(cta) if cta else True
    checks["cta"] = {
        "passed": cta_valid,
        "cta": cta,
        "allowed": ALLOWED_CTAS,
    }
    if not cta_valid:
        issues.append(f"CTA '{cta}' ไม่อนุญาต ใช้ได้แค่: {ALLOWED_CTAS}")

    # 4. Skin exposure
    skin_pct = estimate_skin_percentage(image_description)
    skin_keywords = check_skin_exposure_text(full_text)
    checks["skin_exposure"] = {
        "passed": skin_pct <= 0.20 and len(skin_keywords) == 0,
        "estimated_percentage": skin_pct,
        "keywords_found": skin_keywords,
    }
    if skin_pct > 0.20:
        issues.append(f"เนื้อหนังเกิน 20% (ประมาณ {skin_pct*100:.0f}%)")
    if skin_keywords:
        warnings.append(f"ข้อความมีคำเกี่ยวกับเนื้อหนัง: {', '.join(skin_keywords)}")

    # 5. Text length
    length_issues = check_text_length(parsed)
    checks["text_length"] = {
        "passed": len(length_issues) == 0,
        "issues": length_issues,
    }
    if length_issues:
        for li in length_issues:
            warnings.append(li)

    passed = len(issues) == 0

    result = {
        "passed": passed,
        "issues": issues,
        "warnings": warnings,
        "checks": checks,
        "summary": _format_safety_summary(passed, issues, warnings),
    }

    logger.info(
        "Safety check %s: %d issues, %d warnings",
        "PASSED" if passed else "FAILED", len(issues), len(warnings),
    )

    return result


def _format_safety_summary(
    passed: bool,
    issues: list[str],
    warnings: list[str],
) -> str:
    """สร้างข้อความสรุปผล safety check."""
    if passed and not warnings:
        return "✅ ผ่าน Safety Check ทุกข้อ — พร้อมส่ง approval"

    lines = []
    if passed:
        lines.append("✅ ผ่าน Safety Check (มีคำเตือน)")
    else:
        lines.append("❌ ไม่ผ่าน Safety Check")

    if issues:
        lines.append("\n🚫 ปัญหาที่พบ:")
        for issue in issues:
            lines.append(f"  • {issue}")

    if warnings:
        lines.append("\n⚠️ คำเตือน:")
        for warning in warnings:
            lines.append(f"  • {warning}")

    return "\n".join(lines)


async def full_safety_pipeline(
    ad_copy: dict[str, Any],
    image_description: str | None = None,
) -> dict[str, Any]:
    """รัน safety check เต็มรูปแบบ พร้อม recommendation."""
    result = run_safety_check(ad_copy, image_description)

    if result["passed"]:
        result["recommendation"] = "APPROVE"
        result["next_step"] = "ส่ง approval request ไปยัง Discord #ad-approval"
    elif result["warnings"] and not result["issues"]:
        result["recommendation"] = "REVIEW"
        result["next_step"] = "ส่ง approval request พร้อมคำเตือนให้ admin ตรวจ"
    else:
        result["recommendation"] = "REWRITE"
        result["next_step"] = "ส่งกลับให้เจมส์เขียน ad copy ใหม่"

    return result
