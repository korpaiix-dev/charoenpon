"""Customer Tracking — เก็บพฤติกรรมลูกค้าจาก FB Messenger
เก็บ: ใครทักมา, ทักกี่ครั้ง, ถามอะไร, สนใจระดับไหน, ครั้งแรก/ล่าสุด
"""

import json
import os
from datetime import datetime, timezone, timedelta

ICT = timezone(timedelta(hours=7))
CUSTOMERS_FILE = "/root/charoenpon/fb-manager/data/customers.json"


def load_customers() -> dict:
    os.makedirs(os.path.dirname(CUSTOMERS_FILE), exist_ok=True)
    if os.path.exists(CUSTOMERS_FILE):
        with open(CUSTOMERS_FILE) as f:
            return json.load(f)
    return {}


def save_customers(data: dict):
    os.makedirs(os.path.dirname(CUSTOMERS_FILE), exist_ok=True)
    with open(CUSTOMERS_FILE, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def track_customer(psid: str, name: str, message: str, category: str):
    """บันทึกพฤติกรรมลูกค้า"""
    customers = load_customers()
    now = datetime.now(ICT).isoformat()

    if psid not in customers:
        # ลูกค้าใหม่
        customers[psid] = {
            "name": name,
            "psid": psid,
            "first_contact": now,
            "last_contact": now,
            "total_messages": 0,
            "categories": {},       # นับว่าถามอะไรบ้าง
            "messages": [],         # เก็บข้อความล่าสุด 20 อัน
            "lead_score": "COLD",   # COLD → WARM → HOT
            "source": "fb_messenger",
            "notes": "",
        }

    c = customers[psid]
    c["name"] = name  # อัปเดตชื่อ (อาจเปลี่ยน)
    c["last_contact"] = now
    c["total_messages"] = c.get("total_messages", 0) + 1

    # นับประเภทคำถาม
    cats = c.get("categories", {})
    cats[category] = cats.get(category, 0) + 1
    c["categories"] = cats

    # เก็บข้อความล่าสุด 20 อัน
    msgs = c.get("messages", [])
    msgs.append({
        "text": message[:200],
        "category": category,
        "time": now,
    })
    c["messages"] = msgs[-20:]  # เก็บแค่ 20 ล่าสุด

    # คำนวณ Lead Score
    c["lead_score"] = calculate_lead_score(c)

    customers[psid] = c
    save_customers(customers)

    return c


def calculate_lead_score(customer: dict) -> str:
    """คำนวณระดับความสนใจ"""
    cats = customer.get("categories", {})
    total = customer.get("total_messages", 0)

    # HOT: ถามราคา หรือ ทักมา 3+ ครั้ง
    if cats.get("PRICING", 0) >= 1:
        return "HOT"
    if total >= 3:
        return "HOT"

    # WARM: ขอลิงก์ หรือ ทัก 2 ครั้ง
    if cats.get("GROUP_LINK", 0) >= 1:
        return "WARM"
    if total >= 2:
        return "WARM"

    return "COLD"


def get_customer_stats() -> dict:
    """สรุปสถิติลูกค้า"""
    customers = load_customers()
    total = len(customers)
    hot = sum(1 for c in customers.values() if c.get("lead_score") == "HOT")
    warm = sum(1 for c in customers.values() if c.get("lead_score") == "WARM")
    cold = sum(1 for c in customers.values() if c.get("lead_score") == "COLD")

    # ลูกค้าที่ทักมาบ่อยสุด
    top_customers = sorted(
        customers.values(),
        key=lambda x: x.get("total_messages", 0),
        reverse=True
    )[:10]

    # ลูกค้าใหม่วันนี้
    today = datetime.now(ICT).strftime("%Y-%m-%d")
    new_today = sum(
        1 for c in customers.values()
        if c.get("first_contact", "").startswith(today)
    )

    return {
        "total": total,
        "hot": hot,
        "warm": warm,
        "cold": cold,
        "new_today": new_today,
        "top_customers": top_customers,
    }


def generate_customer_report() -> str:
    """สร้างรายงานลูกค้า"""
    stats = get_customer_stats()

    report = f"""👥 รายงานลูกค้า FB Messenger
━━━━━━━━━━━━━━━━━━━━━━
📊 ลูกค้าทั้งหมด: {stats['total']} คน
🆕 ใหม่วันนี้: {stats['new_today']} คน

🔴 HOT (สนใจมาก): {stats['hot']} คน
🟡 WARM (สนใจ): {stats['warm']} คน
🟢 COLD (ทักทั่วไป): {stats['cold']} คน

📋 Top 10 ทักบ่อยสุด:"""

    for i, c in enumerate(stats["top_customers"], 1):
        score_emoji = {"HOT": "🔴", "WARM": "🟡", "COLD": "🟢"}.get(c.get("lead_score", ""), "⚪")
        report += f"\n  {i}. {score_emoji} {c['name']} — {c['total_messages']} ข้อความ"

    report += f"\n━━━━━━━━━━━━━━━━━━━━━━\n⏰ {datetime.now(ICT).strftime('%d/%m/%Y %H:%M น.')}"
    return report


if __name__ == "__main__":
    print(generate_customer_report())
