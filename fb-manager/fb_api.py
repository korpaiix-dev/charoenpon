"""Facebook Graph API wrapper"""

import requests
import json
import time
from datetime import datetime, timezone, timedelta
from config import PAGE_ID, PAGE_TOKEN, BASE_URL

ICT = timezone(timedelta(hours=7))


def _headers():
    return {"Authorization": f"Bearer {PAGE_TOKEN}"}


# ─── POST ─────────────────────────────────────────────
def create_post(message: str) -> dict:
    """สร้างโพสต์ใหม่บนเพจ"""
    url = f"{BASE_URL}/{PAGE_ID}/feed"
    r = requests.post(url, headers=_headers(), data={"message": message}, timeout=30)
    r.raise_for_status()
    return r.json()


def create_photo_post(message: str, image_url: str) -> dict:
    """สร้างโพสต์พร้อมรูป"""
    url = f"{BASE_URL}/{PAGE_ID}/photos"
    r = requests.post(url, headers=_headers(), 
                      data={"message": message, "url": image_url}, timeout=30)
    r.raise_for_status()
    return r.json()


def delete_post(post_id: str) -> bool:
    url = f"{BASE_URL}/{post_id}"
    r = requests.delete(url, params={"access_token": PAGE_TOKEN}, timeout=15)
    return r.json().get("success", False)


# ─── FEED / STATS ─────────────────────────────────────
def get_feed(limit=10) -> list:
    """ดึงโพสต์ล่าสุด"""
    url = f"{BASE_URL}/{PAGE_ID}/feed"
    params = {
        "fields": "message,created_time,likes.summary(true),comments.summary(true),shares",
        "limit": limit,
        "access_token": PAGE_TOKEN,
    }
    r = requests.get(url, params=params, timeout=15)
    r.raise_for_status()
    return r.json().get("data", [])


def get_page_info() -> dict:
    url = f"{BASE_URL}/{PAGE_ID}"
    params = {
        "fields": "name,fan_count,category,about,new_like_count",
        "access_token": PAGE_TOKEN,
    }
    r = requests.get(url, params=params, timeout=15)
    r.raise_for_status()
    return r.json()


def get_page_insights(period="day", metrics=None) -> dict:
    """ดึง insights ของเพจ (reach, impressions, engagement)"""
    if metrics is None:
        metrics = "page_impressions,page_engaged_users,page_post_engagements,page_fan_adds"
    url = f"{BASE_URL}/{PAGE_ID}/insights"
    params = {
        "metric": metrics,
        "period": period,
        "access_token": PAGE_TOKEN,
    }
    r = requests.get(url, params=params, timeout=15)
    if r.status_code == 200:
        return r.json()
    return {"error": r.json()}


# ─── CONVERSATIONS / MESSENGER ─────────────────────────
def get_conversations(limit=20) -> list:
    """ดึงการสนทนาล่าสุด"""
    url = (
        f"{BASE_URL}/{PAGE_ID}/conversations"
        f"?fields=participants,messages.limit(5){{message,from,created_time}},updated_time"
        f"&limit={limit}&access_token={PAGE_TOKEN}"
    )
    r = requests.get(url, timeout=15)
    r.raise_for_status()
    return r.json().get("data", [])


def send_message(psid: str, text: str) -> dict:
    """ส่งข้อความ Messenger (ต้องภายใน 24 ชม. หลังลูกค้าทักมา)"""
    url = f"{BASE_URL}/{PAGE_ID}/messages"
    payload = {
        "recipient": {"id": psid},
        "message": {"text": text},
    }
    r = requests.post(url, headers=_headers(), json=payload, timeout=15)
    return r.json()


def send_message_with_buttons(psid: str, text: str, buttons: list) -> dict:
    """ส่งข้อความพร้อมปุ่ม"""
    url = f"{BASE_URL}/{PAGE_ID}/messages"
    payload = {
        "recipient": {"id": psid},
        "message": {
            "attachment": {
                "type": "template",
                "payload": {
                    "template_type": "button",
                    "text": text,
                    "buttons": buttons,
                }
            }
        },
    }
    r = requests.post(url, headers=_headers(), json=payload, timeout=15)
    return r.json()


# ─── COMMENTS ──────────────────────────────────────────
def get_comments_on_posts(limit=10) -> list:
    """ดึงคอมเมนต์จากโพสต์ล่าสุด"""
    url = (
        f"{BASE_URL}/{PAGE_ID}/feed"
        f"?fields=message,comments.limit(20){{message,from,created_time,id}}"
        f"&limit={limit}&access_token={PAGE_TOKEN}"
    )
    r = requests.get(url, timeout=15)
    r.raise_for_status()
    return r.json().get("data", [])


def reply_to_comment(comment_id: str, message: str) -> dict:
    """ตอบคอมเมนต์"""
    url = f"{BASE_URL}/{comment_id}/comments"
    r = requests.post(url, headers=_headers(), data={"message": message}, timeout=15)
    return r.json()


# ─── UTILITY ───────────────────────────────────────────
# ─── CROSS-POST (แชร์ไปเพจอื่น) ──────────────────────
def cross_post_to_page(target_page_id: str, target_token: str,
                       message: str, image_url: str = None) -> dict:
    """โพสต์เนื้อหาเดียวกันไปเพจอื่น (เหมือน share)"""
    if image_url:
        url = f"{BASE_URL}/{target_page_id}/photos"
        r = requests.post(url, headers={"Authorization": f"Bearer {target_token}"},
                          data={"message": message, "url": image_url}, timeout=30)
    else:
        url = f"{BASE_URL}/{target_page_id}/feed"
        r = requests.post(url, headers={"Authorization": f"Bearer {target_token}"},
                          data={"message": message}, timeout=30)
    r.raise_for_status()
    return r.json()


def now_ict() -> datetime:
    return datetime.now(ICT)


def format_time_ict(iso_str: str) -> str:
    """แปลง ISO time เป็น ICT string"""
    dt = datetime.fromisoformat(iso_str.replace("+0000", "+00:00"))
    return dt.astimezone(ICT).strftime("%d/%m %H:%M น.")
