#!/usr/bin/env python3
"""Image Collector — รับรูปจาก @jarern5_bot เก็บเข้า queue
บอสส่งรูป + #เจริญพร → เก็บอัตโนมัติ
รูปเฉยๆ ไม่มี # → เก็บเหมือนกัน (bot นี้ใช้รับรูปอย่างเดียว)
"""

import os
import sys
import time
import json
import requests
from datetime import datetime, timezone, timedelta

ICT = timezone(timedelta(hours=7))
QUEUE_DIR = "/root/charoenpon/fb-manager/images/queue"
BOT_TOKEN = "8489081171:AAE-mVDUbB2B1pNlUYimHbFamyYtb-iIDNI"
BOSS_ID = 8502597269
OFFSET_FILE = "/root/charoenpon/fb-manager/data/img_collector_offset.json"


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
    r = requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/getFile",
                     params={"file_id": file_id}, timeout=10)
    result = r.json()
    if not result.get("ok"):
        print(f"❌ getFile failed: {result}")
        return 0
    file_path = result["result"]["file_path"]
    img = requests.get(f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}", timeout=30)
    with open(save_path, "wb") as f:
        f.write(img.content)
    return len(img.content)


def send_reply(text):
    try:
        requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                      json={"chat_id": BOSS_ID, "text": text}, timeout=10)
    except:
        pass


def check_updates():
    os.makedirs(QUEUE_DIR, exist_ok=True)
    offset = get_offset()

    r = requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates",
                     params={"offset": offset, "timeout": 30}, timeout=40)
    updates = r.json().get("result", [])

    saved = 0
    for update in updates:
        offset = update["update_id"] + 1
        msg = update.get("message", {})

        # เฉพาะจากบอสเท่านั้น
        if msg.get("from", {}).get("id") != BOSS_ID:
            continue

        # เช็คว่ามีรูปไหม (bot นี้ใช้รับรูปอย่างเดียว ส่งมาเก็บหมด)
        photo = msg.get("photo")
        if not photo:
            continue

        # เก็บ caption ของบอส (ถ้ามี) ไว้ใช้เป็น custom caption
        caption = msg.get("caption", "")

        # ดาวน์โหลดรูปใหญ่สุด
        biggest = photo[-1]
        file_id = biggest["file_id"]

        ts = datetime.now(ICT).strftime("%Y%m%d_%H%M%S")
        filename = f"jp_{ts}_{saved}.jpg"
        save_path = os.path.join(QUEUE_DIR, filename)

        size = download_photo(file_id, save_path)
        if size > 0:
            saved += 1
            print(f"✅ {filename} ({size // 1024}KB)")

            # เก็บ custom caption ถ้ามี
            if caption and caption.strip() not in ("#เจริญพร", "#jp", "#jarern"):
                # ลบ hashtag tag ออก เหลือแค่ข้อความจริง
                clean = caption.replace("#เจริญพร", "").replace("#jp", "").replace("#jarern", "").strip()
                if clean:
                    meta_path = save_path.rsplit(".", 1)[0] + ".txt"
                    with open(meta_path, "w") as f:
                        f.write(clean)

    save_offset(offset)

    if saved > 0:
        queue_count = len([f for f in os.listdir(QUEUE_DIR)
                           if f.endswith(('.jpg', '.png', '.webp'))])
        send_reply(f"✅ เก็บรูปแล้ว {saved} รูป\n📂 คิวรอโพสต์: {queue_count} รูป")

    return saved


def run_loop():
    print(f"📸 Image Collector เริ่มทำงาน — @jarern5_bot")
    print(f"📂 Queue: {QUEUE_DIR}")
    send_reply("🟢 ระบบรับรูปพร้อมแล้วครับ!\nส่งรูปมาได้เลย ผมเก็บเข้าคิวให้อัตโนมัติ 📸")
    while True:
        try:
            check_updates()
        except Exception as e:
            print(f"❌ Error: {e}")
            time.sleep(5)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "loop":
        run_loop()
    else:
        check_updates()
