"""Facebook Page Manager — เจมส์ (Marketing Agent)
จัดการเพจ FB เจริญพร: auto-post, auto-reply, customer tracking, stats

ใช้ระบบ fb-manager ที่อยู่ใน /root/charoenpon/fb-manager/
เจมส์เป็นคนควบคุม ไม่ใช่แพนด้า (CEO)
"""

import asyncio
import logging
import subprocess
import json
import os
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

ICT = timezone(timedelta(hours=7))
FB_MANAGER_CONTAINER = "charoenpon-fb-manager"


def _run_fb_command(python_code: str, timeout: int = 30) -> str:
    """รันคำสั่ง Python ใน fb-manager container"""
    cmd = [
        "docker", "exec", FB_MANAGER_CONTAINER,
        "python", "-c",
        f"import sys; sys.path.insert(0,'/app'); {python_code}"
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return result.stdout + result.stderr
    except subprocess.TimeoutExpired:
        return "[TIMEOUT]"
    except Exception as e:
        return f"[ERROR] {e}"


async def trigger_auto_post() -> str:
    """สั่งโพสต์ 1 โพสต์"""
    output = _run_fb_command("from auto_post_v2 import auto_post; print(auto_post())")
    logger.info(f"FB Auto-post: {output.strip()}")
    return output


async def trigger_inbox_check() -> str:
    """สั่งเช็ค inbox + ตอบ"""
    output = _run_fb_command(
        "from auto_reply import process_inbox, process_comments; "
        "n1=process_inbox(); n2=process_comments(); "
        "print(f'Messenger: {n1}, Comments: {n2}')"
    )
    logger.info(f"FB Inbox check: {output.strip()}")
    return output


async def get_stats() -> str:
    """ดึงสถิติเพจ"""
    output = _run_fb_command("from stats import generate_stats_report; print(generate_stats_report())")
    return output


async def get_customer_report() -> str:
    """ดึงรายงานลูกค้า"""
    output = _run_fb_command("from customers import generate_customer_report; print(generate_customer_report())")
    return output


async def get_feed_summary() -> str:
    """ดึงสรุปโพสต์ล่าสุด"""
    output = _run_fb_command("from stats import feed_summary; print(feed_summary())")
    return output
