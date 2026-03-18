#!/usr/bin/env python3
"""Telegram Policy Updater - บริษัทเจริญพร.

ดึงนโยบายจาก Telegram ToS → สรุปเป็นภาษาไทยด้วย AI → เขียนไฟล์ telegram-policy.md

Usage:
    python3 telegram_policy_updater.py

Cron (ทุกวันจันทร์ 08:00 TH / 01:00 UTC):
    0 1 * * 1 python3 /root/charoenpon/agents/content_agent/telegram_policy_updater.py >> /var/log/charoenpon-policy-update.log 2>&1
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

logging.basicConfig(
    format="%(asctime)s [telegram_policy_updater] %(levelname)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

TH_TZ = timezone(timedelta(hours=7))

OUTPUT_PATH = Path("/root/.openclaw/workspace/shared/telegram-policy.md")
TELEGRAM_TOS_URL = "https://telegram.org/tos"
OPENROUTER_BASE = "https://openrouter.ai/api/v1"
MODEL = "deepseek/deepseek-chat-v3-0324:free"


def fetch_telegram_tos() -> str:
    """ดึงเนื้อหา Telegram Terms of Service."""
    logger.info("Fetching Telegram ToS from %s", TELEGRAM_TOS_URL)
    resp = requests.get(TELEGRAM_TOS_URL, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
    resp.raise_for_status()

    # Extract text from HTML using simple approach
    from html.parser import HTMLParser

    class TextExtractor(HTMLParser):
        def __init__(self):
            super().__init__()
            self.text_parts: list[str] = []
            self._skip_tags = {"script", "style", "nav", "footer", "header"}
            self._current_skip = 0

        def handle_starttag(self, tag, attrs):
            if tag in self._skip_tags:
                self._current_skip += 1

        def handle_endtag(self, tag):
            if tag in self._skip_tags and self._current_skip > 0:
                self._current_skip -= 1

        def handle_data(self, data):
            if self._current_skip == 0:
                stripped = data.strip()
                if stripped:
                    self.text_parts.append(stripped)

    extractor = TextExtractor()
    extractor.feed(resp.text)
    raw_text = "\n".join(extractor.text_parts)

    # Limit to ~8000 chars to stay within token budget
    if len(raw_text) > 8000:
        raw_text = raw_text[:8000] + "\n\n[... ตัดทอนเพื่อประหยัด token ...]"

    logger.info("Extracted %d chars of ToS text", len(raw_text))
    return raw_text


def summarize_with_ai(tos_text: str, api_key: str) -> str:
    """ใช้ DeepSeek ผ่าน OpenRouter สรุปนโยบายเป็นภาษาไทย."""
    logger.info("Summarizing with %s via OpenRouter...", MODEL)

    prompt = (
        "คุณคือผู้ช่วยที่เชี่ยวชาญด้านกฎหมายและนโยบาย\n\n"
        "สรุปนโยบายการใช้งาน Telegram ต่อไปนี้เป็นภาษาไทย "
        "ในรูปแบบที่อ่านง่าย เป็นประโยชน์สำหรับผู้ดูแลกลุ่ม Telegram ไทย\n\n"
        "ครอบคลุม:\n"
        "1. สิ่งที่ห้ามทำ (prohibited content)\n"
        "2. กฎเกี่ยวกับกลุ่มและช่อง\n"
        "3. นโยบายเกี่ยวกับ bots\n"
        "4. ผลที่ตามมาเมื่อละเมิด\n"
        "5. การเปลี่ยนแปลงสำคัญที่ควรรู้\n\n"
        "เนื้อหานโยบาย:\n"
        "---\n"
        f"{tos_text}\n"
        "---\n\n"
        "สรุปเป็นภาษาไทย ใช้ bullet points และ emoji ให้อ่านง่าย:"
    )

    payload = {
        "model": MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 2000,
        "temperature": 0.3,
    }

    resp = requests.post(
        f"{OPENROUTER_BASE}/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://charoenpon.com",
            "X-Title": "Charoenpon Policy Updater",
        },
        json=payload,
        timeout=60,
    )
    resp.raise_for_status()

    data = resp.json()
    summary = data["choices"][0]["message"]["content"]
    logger.info("AI summary generated (%d chars)", len(summary))
    return summary


def write_policy_file(summary: str) -> None:
    """เขียนไฟล์ telegram-policy.md."""
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    now_th = datetime.now(TH_TZ).strftime("%d/%m/%Y %H:%M น.")

    content = (
        f"# 📋 นโยบายการใช้งาน Telegram (สรุปภาษาไทย)\n\n"
        f"> อัปเดตล่าสุด: {now_th}  \n"
        f"> แหล่งข้อมูล: {TELEGRAM_TOS_URL}  \n"
        f"> สรุปโดย: {MODEL} ผ่าน OpenRouter\n\n"
        f"---\n\n"
        f"{summary}\n\n"
        f"---\n\n"
        f"*ไฟล์นี้ถูกสร้างอัตโนมัติโดย `telegram_policy_updater.py`*\n"
        f"*อัปเดตทุกวันจันทร์ 08:00 น. (Asia/Bangkok)*\n"
    )

    OUTPUT_PATH.write_text(content, encoding="utf-8")
    logger.info("Wrote policy file to %s (%d bytes)", OUTPUT_PATH, len(content))


def main() -> None:
    """Main entry point."""
    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    if not api_key:
        # Try loading from .env file in project root
        env_path = Path(__file__).parent.parent.parent / ".env"
        if env_path.exists():
            for line in env_path.read_text().splitlines():
                if line.startswith("OPENROUTER_API_KEY="):
                    api_key = line.split("=", 1)[1].strip()
                    break

    if not api_key:
        logger.error("OPENROUTER_API_KEY not set. Exiting.")
        sys.exit(1)

    try:
        tos_text = fetch_telegram_tos()
        summary = summarize_with_ai(tos_text, api_key)
        write_policy_file(summary)
        logger.info("✅ Policy update complete!")
    except requests.RequestException as exc:
        logger.error("HTTP error: %s", exc)
        sys.exit(1)
    except Exception as exc:
        logger.error("Unexpected error: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
