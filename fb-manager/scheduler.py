#!/usr/bin/env python3
"""Main scheduler — รันทุก 30 นาที ทำ 3 อย่าง:
1. Auto-reply inbox (Messenger + Comments)
2. Auto-post ตามตาราง (4 รอบ/วัน)
3. Log สถิติ
"""

import time
import sys
import os
from datetime import datetime, timezone, timedelta

# Add current dir to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import json
from auto_reply import process_inbox, process_comments
from auto_post_v2 import auto_post
from stats import generate_stats_report
from config import POST_SCHEDULE_UTC

ICT = timezone(timedelta(hours=7))


POSTED_TODAY_FILE = "/root/charoenpon/fb-manager/data/posted_slots.json"

def _load_posted_slots():
    try:
        with open(POSTED_TODAY_FILE) as f:
            return json.load(f)
    except:
        return {}

def _mark_posted(slot):
    data = _load_posted_slots()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if data.get("date") != today:
        data = {"date": today, "slots": []}
    data["slots"].append(slot)
    os.makedirs(os.path.dirname(POSTED_TODAY_FILE), exist_ok=True)
    with open(POSTED_TODAY_FILE, "w") as f:
        json.dump(data, f)

def should_post_now() -> str | None:
    """เช็คว่าถึงเวลาโพสต์หรือยัง — return slot name ถ้าถึงเวลาและยังไม่โพสต์"""
    now = datetime.now(timezone.utc)
    current_h = now.hour
    current_m = now.minute
    
    posted = _load_posted_slots()
    today = now.strftime("%Y-%m-%d")
    if posted.get("date") != today:
        posted_slots = []
    else:
        posted_slots = posted.get("slots", [])
    
    for schedule_time in POST_SCHEDULE_UTC:
        sh, sm = map(int, schedule_time.split(":"))
        diff_minutes = abs((current_h * 60 + current_m) - (sh * 60 + sm))
        if diff_minutes <= 15 and schedule_time not in posted_slots:
            return schedule_time
    return None


def run_cycle():
    """รัน 1 รอบ — quiet mode: print เฉพาะเมื่อมีงานทำ"""
    n1 = 0
    n2 = 0
    
    # 1. Auto-reply
    try:
        n1 = process_inbox()
    except Exception as e:
        print(f"[{datetime.now(ICT).strftime('%H:%M')}] ❌ Inbox Error: {e}")
    
    try:
        n2 = process_comments()
    except Exception as e:
        print(f"[{datetime.now(ICT).strftime('%H:%M')}] ❌ Comment Error: {e}")
    
    if n1 > 0 or n2 > 0:
        print(f"[{datetime.now(ICT).strftime('%H:%M')}] ✅ ตอบ Messenger: {n1}, Comments: {n2}")
    
    # 2. Auto-post (ถ้าถึงเวลาและยังไม่โพสต์ slot นี้)
    slot = should_post_now()
    if slot:
        try:
            result = auto_post()
            if result.get("status") == "ok":
                _mark_posted(slot)
                print(f"[{datetime.now(ICT).strftime('%H:%M')}] 📝 โพสต์แล้ว slot {slot}: {result.get('post_id', 'N/A')}")
            else:
                print(f"[{datetime.now(ICT).strftime('%H:%M')}] ⚠️ Post result: {result.get('status')}")
        except Exception as e:
            print(f"[{datetime.now(ICT).strftime('%H:%M')}] ❌ Post Error: {e}")


def run_once():
    """รัน 1 รอบแล้วจบ (สำหรับ cron)"""
    run_cycle()


def run_loop(interval_minutes=1):
    """รัน loop ตลอด (สำหรับ Docker) — ทุก 1 นาที เช็ค inbox"""
    print(f"🚀 FB Manager เริ่มทำงาน (interval: {interval_minutes} นาที)")
    while True:
        try:
            run_cycle()
        except Exception as e:
            print(f"💥 Fatal Error: {e}")
        
        time.sleep(interval_minutes * 60)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "loop":
        run_loop()
    else:
        run_once()
