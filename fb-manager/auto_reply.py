"""Auto-reply Messenger inbox — เจริญพร FB Page
ตอบแชทอัตโนมัติ: ขายกลุ่ม VIP + แจกลิงก์ฟรี + ส่ง lead ให้แอดมิน
"""

import json
import time
import os
import requests
from datetime import datetime, timezone, timedelta
from fb_api import (
    get_conversations, send_message, send_message_with_buttons,
    reply_to_comment, get_comments_on_posts, format_time_ict, now_ict
)
from config import (
    PAGE_ID, PACKAGES, FREE_GROUP_LINK, SALES_BOT_LINK, SALES_BOT_NAME,
    TG_ADMIN_GROUP, TG_BOT_TOKEN
)
from customers import track_customer

ICT = timezone(timedelta(hours=7))
STATE_FILE = "/root/charoenpon/fb-manager/data/replied_ids.json"


def load_state() -> dict:
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {"replied_messages": [], "replied_comments": []}


def save_state(state: dict):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def classify_message(text: str) -> str:
    """จำแนกข้อความลูกค้า"""
    text = text.lower().strip()
    
    # ราคา / สนใจ
    price_keywords = ["ราคา", "เท่าไหร่", "แพ็ก", "vip", "god", "only", "สมัคร", "จ่าย", "โอน", "ชำระ", "PromptPay", "promptpay"]
    if any(k in text for k in price_keywords):
        return "PRICING"
    
    # ขอลิงก์ / เข้ากลุ่ม
    group_keywords = ["ขอกลุ่ม", "ขอลิงก์", "ขอลิ้ง", "เข้ากลุ่ม", "link", "ลิงก์", "ทางเข้า", "กลุ่มฟรี", "แจกกลุ่ม"]
    if any(k in text for k in group_keywords):
        return "GROUP_LINK"
    
    # ร้องเรียน
    complaint_keywords = ["โกง", "หลอก", "คืนเงิน", "ไม่ได้", "เข้าไม่ได้", "ผิดหวัง", "เสียเงิน"]
    if any(k in text for k in complaint_keywords):
        return "COMPLAINT"
    
    # ทั่วไป (ทัก สวัสดี etc)
    return "GENERAL"


def build_pricing_reply() -> str:
    """ถามราคา/สนใจ"""
    return (
        f"สวัสดีครับ 🙏 ขอบคุณที่สนใจ!\n\n"
        f"มีหลายแพ็กเกจให้เลือกครับ เริ่มต้นแค่ ฿300\n"
        f"มีทั้งกลุ่มฟรีและ VIP กลุ่มลับ อัปเดตทุกวัน 🔥\n\n"
        f"กดเข้าบอทดูรายละเอียดได้เลยครับ 👇\n"
        f"👉 {SALES_BOT_LINK}"
    )


def build_group_link_reply() -> str:
    """ขอลิงก์/เข้ากลุ่ม"""
    return (
        f"ได้เลยครับ 🙏\n\n"
        f"กดเข้าบอทนี้เลย มีลิงก์ฟรี + VIP ให้เลือกครับ 👇\n"
        f"👉 {SALES_BOT_LINK}\n\n"
        f"เข้าได้เลยครับ อัปเดตทุกวัน! 🔥"
    )


def build_general_reply() -> str:
    """ข้อความทั่วไป / สวัสดี / sticker"""
    return (
        f"สวัสดีครับ 🙏 ยินดีต้อนรับ!\n\n"
        f"มีกลุ่มลับ VIP อัปเดตทุกวัน + กลุ่มฟรีด้วยครับ 🔞\n"
        f"กดเข้าบอทดูได้เลย 👇\n"
        f"👉 {SALES_BOT_LINK}\n\n"
        f"สนใจอะไรถามมาได้เลยนะครับ 😊"
    )


def build_complaint_reply() -> str:
    """ร้องเรียน / ปัญหา"""
    return (
        f"ขอโทษด้วยครับ 🙏 เสียใจที่ประสบปัญหา\n\n"
        f"บอกรายละเอียดมาได้เลยครับ จะรีบดูแลให้!\n"
        f"หรือทักบอทแจ้งปัญหาได้เลย แอดมินจะช่วยครับ 👇\n"
        f"👉 {SALES_BOT_LINK}"
    )


def notify_admin_tg(text: str):
    """ส่งแจ้งเตือนไป Telegram Admin Group"""
    try:
        url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
        requests.post(url, json={
            "chat_id": TG_ADMIN_GROUP,
            "text": text,
            "parse_mode": "HTML",
        }, timeout=10)
    except Exception as e:
        print(f"[TG Notify Error] {e}")


def process_inbox():
    """เช็ค inbox + ตอบข้อความใหม่"""
    state = load_state()
    replied = set(state.get("replied_messages", []))
    
    conversations = get_conversations(limit=20)
    new_replies = 0
    
    for conv in conversations:
        messages = conv.get("messages", {}).get("data", [])
        if not messages:
            continue
        
        # เอาข้อความล่าสุดของลูกค้า (ไม่ใช่ของเพจ)
        latest = messages[0]
        msg_id = latest.get("id", "")
        sender = latest.get("from", {})
        sender_id = sender.get("id", "")
        sender_name = sender.get("name", "ไม่ทราบชื่อ")
        text = latest.get("message", "").strip()
        created = latest.get("created_time", "")
        
        # ข้ามถ้าเป็นข้อความของเพจเอง
        if sender_id == PAGE_ID:
            continue
        
        # ข้ามถ้าตอบไปแล้ว
        if msg_id in replied:
            continue
        
        # เช็คว่าอยู่ใน 24 ชม. messaging window
        if created:
            msg_time = datetime.fromisoformat(created.replace("+0000", "+00:00"))
            age_hours = (datetime.now(timezone.utc) - msg_time).total_seconds() / 3600
            if age_hours > 23:  # เผื่อ buffer 1 ชม.
                print(f"[SKIP] {sender_name} — ข้อความเก่ากว่า 23 ชม. ({age_hours:.1f}h)")
                replied.add(msg_id)
                continue
        
        # ถ้าข้อความว่าง (sticker/attachment) → ส่งข้อความทั่วไป
        if not text:
            text = "สวัสดี"
        
        # จำแนก + ตอบ
        category = classify_message(text)
        
        if category == "PRICING":
            reply_text = build_pricing_reply()
        elif category == "GROUP_LINK":
            reply_text = build_group_link_reply()
        elif category == "COMPLAINT":
            reply_text = build_complaint_reply()
            print(f"  🚨 COMPLAINT: {sender_name} — {text[:100]}")
        else:
            reply_text = build_general_reply()
        
        # ส่ง
        result = send_message(sender_id, reply_text)
        
        if "error" in result:
            error_msg = result["error"].get("message", "unknown")
            print(f"[ERROR] {sender_name}: {error_msg[:100]}")
            # ถ้าเป็น messaging window error → ข้าม
            if "outside" in error_msg.lower() or "นอกช่วงเวลา" in error_msg:
                print(f"  → นอก 24h window, ข้าม")
        else:
            print(f"[REPLIED] {sender_name} ({category}) — {text[:50]}")
            new_replies += 1
            
            # เก็บพฤติกรรมลูกค้า
            try:
                customer = track_customer(sender_id, sender_name, text, category)
                lead_score = customer.get("lead_score", "COLD")
                total_msgs = customer.get("total_messages", 1)
            except Exception as e:
                print(f"  [Track Error] {e}")
                lead_score = "COLD"
                total_msgs = 1
            
            # Log lead score (ไม่แจ้ง Telegram — จัดการใน FB เท่านั้น)
            if lead_score == "HOT":
                print(f"  🔥 HOT LEAD: {sender_name} (ทักมา {total_msgs} ครั้ง)")
        
        replied.add(msg_id)
        time.sleep(1)  # rate limit
    
    # เก็บแค่ 500 IDs ล่าสุด
    state["replied_messages"] = list(replied)[-500:]
    save_state(state)
    
    return new_replies


def process_comments():
    """เช็คคอมเมนต์ + ตอบอัตโนมัติ"""
    state = load_state()
    replied = set(state.get("replied_comments", []))
    
    posts = get_comments_on_posts(limit=5)
    new_replies = 0
    
    for post in posts:
        comments = post.get("comments", {}).get("data", [])
        for comment in comments:
            cid = comment.get("id", "")
            if cid in replied:
                continue
            
            sender = comment.get("from", {})
            sender_name = sender.get("name", "")
            text = comment.get("message", "").strip()
            
            # ตอบคอมเมนต์: ให้ลิงก์ + ชวน DM
            reply = (
                f"สวัสดีครับ {sender_name} 🙏\n"
                f"🆓 กลุ่มฟรี: {FREE_GROUP_LINK}\n"
                f"💎 สมัคร VIP ทักบอท: {SALES_BOT_LINK}\n"
                f"หรือทักแชทเพจได้เลยครับ 😊"
            )
            
            result = reply_to_comment(cid, reply)
            if "error" not in result:
                print(f"[COMMENT REPLY] {sender_name}: {text[:50]}")
                new_replies += 1
            else:
                print(f"[COMMENT ERROR] {result['error'].get('message','')[:100]}")
            
            replied.add(cid)
            time.sleep(1)
    
    state["replied_comments"] = list(replied)[-500:]
    save_state(state)
    
    return new_replies


if __name__ == "__main__":
    print(f"[{now_ict().strftime('%H:%M')}] เช็ค Inbox...")
    n1 = process_inbox()
    print(f"  ตอบ Messenger: {n1} ข้อความ")
    
    print(f"[{now_ict().strftime('%H:%M')}] เช็ค Comments...")
    n2 = process_comments()
    print(f"  ตอบ Comment: {n2} คอมเมนต์")
    
    print(f"เสร็จ! ตอบรวม {n1 + n2} ข้อความ")
