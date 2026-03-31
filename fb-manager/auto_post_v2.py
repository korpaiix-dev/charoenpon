#!/usr/bin/env python3
"""Auto-Post v2 — เจริญพร Facebook Page
- หยิบรูปจาก queue → โพสต์ + AI caption → ย้ายไป used
- Hashtag 5 ตัว (บังคับ 3 + สุ่ม 2)
- ลิงก์ Sales Bot
- Lock กันโพสต์ซ้ำ
"""

import os
import json
import random
import time
import requests
from datetime import datetime, timezone, timedelta
from pathlib import Path

ICT = timezone(timedelta(hours=7))

# === Config ===
PAGE_ID = "896245606913574"
PAGE_TOKEN = "EAADhsxfbE8sBRGKvPZBEJFkkkM9AjznceXsCQyINrkZCLOCEhjdNd3G1B9axMidCFxjGXyE4JKbxnHwqkrzZALKY3TKaoOgCgGhX0SFm0vuPz15EsuTdsG1exFHnLSLqTKLTXlQXS2z3BNN9WVG7xZBrg6KCq9eXAxJfBrkGijXC3Sg0UvjjInarbFwT7AZCcy5oA"
SALES_BOT = "https://t.me/NamwarnJarern_bot"
FREE_GROUP = "https://t.me/+EihPcGnV5V8zYzE9"

QUEUE_DIR = "/root/charoenpon/fb-manager/images/queue"
USED_DIR = "/root/charoenpon/fb-manager/images/used"
LOCK_FILE = "/root/charoenpon/fb-manager/data/post_lock.json"
LOG_FILE = "/root/charoenpon/fb-manager/data/post_log_v2.json"

# === Hashtags ===
REQUIRED_TAGS = ["#เจริญพร", "#แจกกลุ่มเทเลแกรม"]
EXTRA_TAGS = [
    ["#ของดีบอกต่อ", "#เด็ดๆ"],
    ["#กลุ่มฟรี", "#กลุ่มไทย"],
    ["#กำลังมาแรง", "#แจกกลุ่มเทเลแกรมฟรี"],
]

# === Captions (หลากหลาย — AI style) ===
CAPTIONS = [
    # --- 1-12: ของเดิม (ปรับเล็กน้อย) ---
    "🔥 ของดีมาแล้ว... วันนี้จัดหนัก!\n\nอยากดูแบบเต็มๆ?\n👉 ทักมาเลย {sales}\n\nกลุ่ม VIP อัปเดตทุกวัน ไม่มีผิดหวังครับ 💎",
    "😏 เห็นแค่นี้ยังอยากรู้ต่อมั้ย...\n\nของจริงอยู่ในกลุ่ม VIP!\n👉 สนใจทักมา {sales}\n\nสมาชิกใหม่เข้ามาทุกวันครับ 🙌",
    "🤔 เคยสงสัยมั้ยว่า... ทำไมเพื่อนบางคนยิ้มทั้งวัน?\n\nเพราะเขารู้ในสิ่งที่คุณยังไม่รู้ 😏\n👉 ทักมาเลยครับ {sales}",
    "🔞 วันนี้อัปคอนเทนต์ใหม่... แค่ preview ก็แทบจะ 🥵\n\nของจริงอยู่ในกลุ่ม VIP เท่านั้น\n👉 ทัก inbox มาเลยครับ {sales}",
    "💎 สมาชิกใหม่เข้ามาเพียบ!\n\nขอบคุณทุกคนที่ไว้วางใจครับ 🙏\nคนที่เข้ามาแล้วบอกเลยว่า \"คุ้มกว่าที่คิด\"\n\n👉 ยังไม่ได้เข้า? ทักมา {sales}",
    "⚡ อัปเดตใหม่ล่าสุด! ของดีคัดมาแล้ว\n\nเข้ากลุ่มก่อนได้ 👉 {free}\nอยากได้ VIP ทักมาเลยครับ 💬 {sales}",
    "🌙 ดึกๆ แบบนี้... มีของดีมาฝาก 😏\n\nใครพร้อมรับ ทักมาเลยครับ\n👉 {sales}\n\nไม่ผิดหวังแน่นอน 🔥",
    "📢 ใครยังไม่ได้เข้ากลุ่ม? พลาดอยู่นะครับ!\n\nกลุ่มตัวอย่าง 👉 {free}\nVIP สนใจทักมา 💬 {sales}",
    "🔥 วันนี้จัดเต็ม! ของดีรอคุณอยู่\n\nเข้ามาดูเอง แล้วจะรู้ว่าคุ้มครับ\n👉 ทักเลย {sales}",
    "😈 มีคนถามว่า \"กลุ่มดีจริงมั้ย?\"\n\nไม่ต้องเชื่อคำพูด... ลองเข้ามาดูเองครับ\nกลุ่มตัวอย่าง 👉 {free}\nVIP 👉 {sales}",
    "🎯 ตรงปก ตรงใจ จัดให้ทุกวัน!\n\nสมาชิก VIP รู้ดี ของดีไม่ต้องโฆษณาเยอะครับ\n👉 {sales}",
    "💬 \"พี่ เข้ามาแล้วคุ้มมาก!\" — รีวิวจากสมาชิกจริง\n\nอยากรู้ว่าคุ้มแค่ไหน?\n👉 ทักมาเลยครับ {sales}",
    # --- 13-25: ใหม่ ---
    "🚀 {time_greeting}มีอะไรดีๆ มาอัปเดตครับ\n\nคอนเทนต์ใหม่เพิ่งลง VIP สดๆ ร้อนๆ\nสนใจทักมาเลย 👉 {sales}",
    "👀 วัน{day}แบบนี้ ต้องมีของดีเสิร์ฟถึงที่!\n\nดูตัวอย่างก่อนได้ 👉 {free}\nชอบใจ? ทักมาต่อ 💬 {sales}",
    "🎬 คอนเทนต์ใหม่เข้า {time_greeting} เลยครับ\n\nใครอยากดูก่อนใคร ทักมาเลย\n👉 {sales}\n\nไม่ต้องรอนาน อัปให้ทุกวัน 🔥",
    "💡 เพื่อนๆ ชอบแบบไหน? คอมเมนต์ \".\" ไว้เลยครับ\n\nแล้วจะทักไปหาให้ 😏\n👉 หรือทักมาเองก็ได้ {sales}",
    "🙋‍♂️ ใครเข้ากลุ่มแล้วบ้าง? ยกมือหน่อยครับ\n\nคนที่ยังไม่ได้เข้า... นี่แหละของดีที่รอคุณอยู่\n👉 {sales}",
    "🏆 วัน{day}นี้คัดมาให้แล้ว ของดีชุดใหม่!\n\nเข้า VIP ดูก่อนใครครับ\n👉 ทักมา {sales}",
    "📲 ใครอยากได้ลิงก์? DM มาเลยครับ\n\nหรือกดตรงนี้ได้เลย 👉 {sales}\n\nอัปเดตคอนเทนต์ใหม่ทุกวัน ไม่ซ้ำ 💯",
    "🤫 บอกกันปากต่อปาก... กลุ่มนี้ของดีจริง\n\nไม่เชื่อลองเข้ามาดูเองครับ\n👉 {free}\nอยากได้แบบจัดเต็ม 👉 {sales}",
    "⏰ {time_greeting}ว่างๆ เข้ามาดูของดีกันครับ!\n\nสมาชิกเพิ่มขึ้นทุกวัน ไม่ใช่เรื่องบังเอิญ\n👉 {sales}",
    "🔑 เปิดกลุ่มใหม่อีกแล้ว! สมาชิกบอกว่าคุ้มสุดๆ\n\nแชร์ให้เพื่อนด้วยนะครับ 🙏\n👉 ทักมาเลย {sales}",
    "💪 ลองดูแล้วจะติดใจ! สมาชิกเก่ารู้ดี\n\nใครใหม่ยังไม่แน่ใจ ดูตัวอย่างก่อนได้ 👉 {free}\nพร้อมก็ทักมาครับ 💬 {sales}",
    "🌟 วัน{day}{time_greeting} ของดีพร้อมเสิร์ฟครับ!\n\nกดลิงก์ได้เลย 👉 {sales}\nหรือเข้ากลุ่มดูตัวอย่างก่อน 👉 {free}",
    "🎉 ขอบคุณสมาชิกทุกคนที่เข้ามาครับ!\n\nใครยังไม่ได้เข้า... มาเถอะครับ ของดีรอคุณอยู่\nคอมเมนต์ \".\" เดี๋ยวทักไปหาให้เอง 😎\n👉 {sales}",
]


WEIGHTS_FILE = "/root/charoenpon/fb-manager/data/weights.json"


def load_weights() -> dict:
    if os.path.exists(WEIGHTS_FILE):
        with open(WEIGHTS_FILE) as f:
            return json.load(f)
    return {}


def get_hashtags() -> str:
    """สร้าง hashtag 5 ตัว — ถ้ามี weights ให้เลือกชุดที่ดีที่สุด"""
    weights = load_weights()
    hashtag_w = weights.get("hashtag_weights", {})

    if hashtag_w:
        # เลือกชุดที่ score สูง (weighted random)
        items = list(hashtag_w.items())
        scores = [max(v, 0.1) for _, v in items]
        total = sum(scores)
        probs = [s / total for s in scores]
        chosen_tags = random.choices([t for t, _ in items], weights=probs, k=1)[0]
        return chosen_tags
    else:
        extra_set = random.choice(EXTRA_TAGS)
        # สุ่ม 1-2 ตัวจาก extra_set (รวมไม่เกิน 4 ตัว/โพสต์)
        n = random.randint(1, min(2, len(extra_set)))
        picked = random.sample(extra_set, n)
        tags = REQUIRED_TAGS + picked
        return " ".join(tags)


def get_caption() -> str:
    """สุ่ม caption — ถ้ามี weights ให้เลือก template ที่ engagement ดี"""
    log = load_log()
    recent = [e.get("caption_idx", -1) for e in log[-3:]]
    available = [i for i in range(len(CAPTIONS)) if i not in recent]
    if not available:
        available = list(range(len(CAPTIONS)))

    weights = load_weights()
    caption_w = weights.get("caption_weights", {})

    if caption_w:
        # Weighted random — template ที่ได้ score สูงมีโอกาสถูกเลือกมากขึ้น
        scored = {int(k): v for k, v in caption_w.items() if int(k) in available}
        if scored:
            indices = list(scored.keys())
            scores = [max(scored[i], 0.1) for i in indices]
            total = sum(scores)
            probs = [s / total for s in scores]
            idx = random.choices(indices, weights=probs, k=1)[0]
        else:
            idx = random.choice(available)
    else:
        idx = random.choice(available)

    # Dynamic variables
    now = datetime.now(ICT)
    hour = now.hour
    if hour < 12:
        time_greeting = "เช้านี้"
    elif hour < 17:
        time_greeting = "กลางวันนี้"
    else:
        time_greeting = "คืนนี้"

    days = ["จันทร์", "อังคาร", "พุธ", "พฤหัส", "ศุกร์", "เสาร์", "อาทิตย์"]
    day = days[now.weekday()]

    caption = CAPTIONS[idx].format(
        sales=SALES_BOT, free=FREE_GROUP,
        time_greeting=time_greeting, day=day,
    )
    return idx, caption


def get_image() -> tuple:
    """หยิบรูปจาก queue — ถ้าหมดก็วนจาก used"""
    os.makedirs(QUEUE_DIR, exist_ok=True)
    os.makedirs(USED_DIR, exist_ok=True)
    
    images = sorted([f for f in os.listdir(QUEUE_DIR) if f.endswith(('.jpg', '.png', '.webp'))])
    
    if not images:
        # วนรูปจาก used กลับมา
        used_images = sorted([f for f in os.listdir(USED_DIR) if f.endswith(('.jpg', '.png', '.webp'))])
        if not used_images:
            return None, None
        # สุ่ม 1 รูปจาก used
        pick = random.choice(used_images)
        return os.path.join(USED_DIR, pick), pick
    
    # หยิบรูปแรกจาก queue
    pick = images[0]
    return os.path.join(QUEUE_DIR, pick), pick


def check_lock() -> bool:
    """เช็คว่าโพสต์ไปแล้วในชั่วโมงนี้หรือยัง"""
    if not os.path.exists(LOCK_FILE):
        return False
    with open(LOCK_FILE) as f:
        data = json.load(f)
    last = data.get("last_post", "")
    if not last:
        return False
    # ล็อค 2 ชั่วโมง
    from datetime import datetime as dt
    try:
        last_dt = dt.fromisoformat(last)
        now = dt.now(ICT)
        diff = (now - last_dt).total_seconds()
        return diff < 7200  # 2 ชั่วโมง
    except:
        return False


def set_lock():
    os.makedirs(os.path.dirname(LOCK_FILE), exist_ok=True)
    with open(LOCK_FILE, "w") as f:
        json.dump({"last_post": datetime.now(ICT).isoformat()}, f)


def load_log() -> list:
    if os.path.exists(LOG_FILE):
        with open(LOG_FILE) as f:
            return json.load(f)
    return []


def save_log(log: list):
    os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
    with open(LOG_FILE, "w") as f:
        json.dump(log[-100:], f, ensure_ascii=False, indent=2)


def post_to_facebook(image_path: str, message: str) -> dict:
    """โพสต์รูป + caption ลง Facebook Page"""
    with open(image_path, "rb") as f:
        r = requests.post(
            f"https://graph.facebook.com/v21.0/{PAGE_ID}/photos",
            data={
                "message": message,
                "access_token": PAGE_TOKEN,
                "published": "true",
            },
            files={"source": f},
            timeout=30,
        )
    return r.json()


def auto_post() -> dict:
    """โพสต์อัตโนมัติ 1 โพสต์"""
    # เช็ค lock
    if check_lock():
        print(f"[{datetime.now(ICT).strftime('%H:%M')}] ⏳ ล็อคอยู่ — โพสต์ไปแล้วภายใน 2 ชม.")
        return {"status": "locked"}
    
    # หยิบรูป
    img_path, img_name = get_image()
    if not img_path:
        print(f"[{datetime.now(ICT).strftime('%H:%M')}] ❌ ไม่มีรูปใน queue และ used")
        return {"status": "no_image"}
    
    # สร้าง caption + hashtag
    cap_idx, caption = get_caption()
    hashtags = get_hashtags()
    full_message = f"{caption}\n\n{hashtags}"
    
    # โพสต์
    result = post_to_facebook(img_path, full_message)
    post_id = result.get("post_id", result.get("id", ""))
    
    if post_id:
        # ย้ายรูปจาก queue ไป used (ถ้าอยู่ใน queue)
        if QUEUE_DIR in img_path:
            dest = os.path.join(USED_DIR, img_name)
            os.rename(img_path, dest)
        
        # ล็อค + บันทึก log
        set_lock()
        log = load_log()
        log.append({
            "post_id": post_id,
            "image": img_name,
            "caption_idx": cap_idx,
            "hashtags": hashtags,
            "time": datetime.now(ICT).isoformat(),
        })
        save_log(log)
        
        now = datetime.now(ICT).strftime("%H:%M")
        print(f"[{now}] ✅ โพสต์สำเร็จ! ID: {post_id} | รูป: {img_name}")
        return {"status": "ok", "post_id": post_id, "image": img_name}
    else:
        print(f"[{datetime.now(ICT).strftime('%H:%M')}] ❌ โพสต์ล้มเหลว: {result}")
        return {"status": "error", "error": result}


if __name__ == "__main__":
    auto_post()
