#!/usr/bin/env python3
"""รับรูปจาก Telegram → เก็บเข้า queue
บอสส่งรูปมาที่ bot พร้อม caption #เจริญพร หรือ #jp
"""

import os
import requests
import json
from datetime import datetime, timezone, timedelta

ICT = timezone(timedelta(hours=7))
QUEUE_DIR = "/root/charoenpon/fb-manager/images/queue"
BOT_TOKEN = "8720162477:AAHkUL-qGvL1S46nRyn8fP4no01_WpqgTQI"  # Patafood bot
BOSS_ID = 8502597269
OFFSET_FILE = "/root/charoenpon/fb-manager/data/img_offset.json"


def get_offset():
    if os.path.exists(OFFSET_FILE):
        with open(OFFSET_FILE) as f:
            return json.load(f).get("offset", 0)
    return 0


def save_offset(offset):
    os.makedirs(os.path.dirname(OFFSET_FILE), exist_ok=True)
    with open(OFFSET_FILE, "w") as f:
        json.dump({"offset": offset}, f)


def download_photo(file_id, save_path):
    """ดาวน์โหลดรูปจาก Telegram"""
    r = requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/getFile",
                     params={"file_id": file_id}, timeout=10)
    file_path = r.json()["result"]["file_path"]
    
    img = requests.get(f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}", timeout=30)
    with open(save_path, "wb") as f:
        f.write(img.content)
    return len(img.content)


def check_new_photos():
    """เช็ค updates ใหม่จาก Telegram"""
    os.makedirs(QUEUE_DIR, exist_ok=True)
    offset = get_offset()
    
    r = requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates",
                     params={"offset": offset, "timeout": 5}, timeout=15)
    updates = r.json().get("result", [])
    
    saved = 0
    for update in updates:
        offset = update["update_id"] + 1
        msg = update.get("message", {})
        
        # เฉพาะจากบอสเท่านั้น
        if msg.get("from", {}).get("id") != BOSS_ID:
            continue
        
        # เช็คว่ามีรูปไหม
        photo = msg.get("photo")
        if not photo:
            continue
        
        # เช็ค caption (#เจริญพร หรือ #jp)
        caption = (msg.get("caption") or "").lower()
        tags = ["#เจริญพร", "#jp", "#jarern"]
        if not any(tag in caption for tag in tags):
            continue
        
        # ดาวน์โหลดรูปใหญ่สุด
        biggest = photo[-1]
        file_id = biggest["file_id"]
        
        ts = datetime.now(ICT).strftime("%Y%m%d_%H%M%S")
        filename = f"img_{ts}_{saved}.jpg"
        save_path = os.path.join(QUEUE_DIR, filename)
        
        size = download_photo(file_id, save_path)
        saved += 1
        print(f"✅ Saved: {filename} ({size//1024}KB)")
        
        # ตอบกลับ
        requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                      json={"chat_id": BOSS_ID,
                            "text": f"✅ เก็บรูปเข้าคิวแล้ว ({saved} รูป)\n📂 queue: {len(os.listdir(QUEUE_DIR))} รูปรอโพสต์"})
    
    save_offset(offset)
    return saved


if __name__ == "__main__":
    n = check_new_photos()
    if n > 0:
        print(f"📥 เก็บรูปใหม่ {n} รูป")
    else:
        print("ไม่มีรูปใหม่")
