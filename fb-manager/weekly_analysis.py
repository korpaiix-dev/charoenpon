"""Weekly Facebook Analysis — วิเคราะห์ข้อมูลรายสัปดาห์เพื่อพัฒนา

วิเคราะห์:
1. Post Performance — โพสต์ไหนได้ engagement ดี/ไม่ดี
2. Template Performance — template ไหนใช้ได้ผล
3. Customer Patterns — ลูกค้าทักช่วงไหน / ถามอะไรบ่อย
4. Conversion Funnel — FB → Sales Bot → สมัคร VIP
5. แนะนำปรับปรุง — เวลาโพสต์ / template / รูป
"""

import json
import os
from datetime import datetime, timezone, timedelta
from fb_api import get_feed, get_page_info, now_ict

ICT = timezone(timedelta(hours=7))
CUSTOMERS_FILE = "/root/charoenpon/fb-manager/data/customers.json"
POST_LOG_FILE = "/root/charoenpon/fb-manager/data/post_log.json"


def analyze_posts() -> dict:
    """วิเคราะห์ performance ของโพสต์"""
    feed = get_feed(limit=25)

    posts_data = []
    for post in feed:
        likes = post.get("likes", {}).get("summary", {}).get("total_count", 0)
        comments = post.get("comments", {}).get("summary", {}).get("total_count", 0)
        shares = post.get("shares", {}).get("count", 0)
        engagement = likes + comments + shares
        msg = post.get("message", "")[:100]
        created = post.get("created_time", "")

        posts_data.append({
            "message_preview": msg,
            "likes": likes,
            "comments": comments,
            "shares": shares,
            "engagement": engagement,
            "created_time": created,
        })

    # Sort by engagement
    posts_data.sort(key=lambda x: x["engagement"], reverse=True)

    total_engagement = sum(p["engagement"] for p in posts_data)
    avg_engagement = total_engagement / len(posts_data) if posts_data else 0

    return {
        "total_posts": len(posts_data),
        "total_engagement": total_engagement,
        "avg_engagement": round(avg_engagement, 1),
        "best_post": posts_data[0] if posts_data else None,
        "worst_post": posts_data[-1] if posts_data else None,
        "all_posts": posts_data,
    }


def analyze_templates() -> dict:
    """วิเคราะห์ template ไหนใช้บ่อย/ได้ผลดี"""
    if not os.path.exists(POST_LOG_FILE):
        return {"templates_used": {}, "images_used": {}}

    with open(POST_LOG_FILE) as f:
        logs = json.load(f)

    template_count = {}
    image_count = {}
    for entry in logs:
        t_idx = entry.get("template_idx", -1)
        i_idx = entry.get("image_idx", -1)
        template_count[t_idx] = template_count.get(t_idx, 0) + 1
        if i_idx >= 0:
            image_count[i_idx] = image_count.get(i_idx, 0) + 1

    return {
        "templates_used": template_count,
        "images_used": image_count,
        "total_posts_logged": len(logs),
    }


def analyze_customers() -> dict:
    """วิเคราะห์พฤติกรรมลูกค้า"""
    if not os.path.exists(CUSTOMERS_FILE):
        return {"total": 0}

    with open(CUSTOMERS_FILE) as f:
        customers = json.load(f)

    # ช่วงเวลาที่ทักมาบ่อย
    hour_distribution = {}
    category_totals = {}
    returning_count = 0

    for c in customers.values():
        # นับ returning (ทัก 2+ ครั้ง)
        if c.get("total_messages", 0) >= 2:
            returning_count += 1

        # นับ categories
        for cat, cnt in c.get("categories", {}).items():
            category_totals[cat] = category_totals.get(cat, 0) + cnt

        # เวลาที่ทักมา
        for msg in c.get("messages", []):
            time_str = msg.get("time", "")
            if time_str:
                try:
                    hour = int(time_str[11:13])
                    hour_distribution[hour] = hour_distribution.get(hour, 0) + 1
                except (ValueError, IndexError):
                    pass

    # หา peak hours
    peak_hours = sorted(hour_distribution.items(), key=lambda x: x[1], reverse=True)[:3]

    return {
        "total": len(customers),
        "returning": returning_count,
        "returning_rate": round(returning_count / max(len(customers), 1) * 100, 1),
        "category_totals": category_totals,
        "peak_hours_ict": [f"{h}:00 ({cnt} ข้อความ)" for h, cnt in peak_hours],
    }


def generate_weekly_report() -> str:
    """สร้างรายงานวิเคราะห์รายสัปดาห์"""
    info = get_page_info()
    posts = analyze_posts()
    templates = analyze_templates()
    customers = analyze_customers()

    report = f"""📊 Facebook Weekly Analysis — เจริญพร
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

📘 เพจ: {info.get('name', 'N/A')}
👥 Followers: {info.get('fan_count', 0):,}

━━━ 📝 Post Performance ━━━
โพสต์ทั้งหมด: {posts['total_posts']}
Engagement รวม: {posts['total_engagement']}
Avg Engagement/Post: {posts['avg_engagement']}"""

    if posts["best_post"]:
        bp = posts["best_post"]
        report += f"\n🏆 Best Post: ❤️{bp['likes']} 💬{bp['comments']} 🔁{bp['shares']}"
        report += f"\n   {bp['message_preview'][:60]}..."

    if posts["worst_post"] and posts["total_posts"] > 1:
        wp = posts["worst_post"]
        report += f"\n📉 Worst Post: ❤️{wp['likes']} 💬{wp['comments']} 🔁{wp['shares']}"

    report += f"""

━━━ 🎨 Template Usage ━━━
Templates ที่ใช้: {templates.get('templates_used', {})}
โพสต์ที่ log ไว้: {templates.get('total_posts_logged', 0)}

━━━ 👥 Customer Analysis ━━━
ลูกค้าทั้งหมด: {customers.get('total', 0)}
กลับมาทักอีก: {customers.get('returning', 0)} ({customers.get('returning_rate', 0)}%)
คำถามที่ถามบ่อย: {customers.get('category_totals', {})}
ช่วงเวลาที่ทักบ่อย: {', '.join(customers.get('peak_hours_ict', ['ยังไม่มีข้อมูล']))}

━━━ 💡 แนะนำปรับปรุง ━━━"""

    # Auto recommendations
    recommendations = []

    if posts["avg_engagement"] < 1:
        recommendations.append("⚠️ Engagement ต่ำมาก — ลองเปลี่ยนเวลาโพสต์ หรือใช้รูป/คลิปที่ดึงดูดมากขึ้น")

    if customers.get("returning_rate", 0) < 10:
        recommendations.append("⚠️ ลูกค้ากลับมาทักน้อย — ปรับข้อความตอบให้น่าสนใจ หรือ follow-up หลัง 24 ชม.")

    if not recommendations:
        recommendations.append("✅ ยังเร็วเกินจะวิเคราะห์ ต้องเก็บข้อมูลอีก 1-2 สัปดาห์")

    for r in recommendations:
        report += f"\n{r}"

    report += f"\n\n⏰ วิเคราะห์เมื่อ: {now_ict().strftime('%d/%m/%Y %H:%M น.')}"

    return report


def generate_analysis_json() -> dict:
    """สร้าง JSON สำหรับให้ AI วิเคราะห์ต่อ"""
    return {
        "page_info": get_page_info(),
        "posts": analyze_posts(),
        "templates": analyze_templates(),
        "customers": analyze_customers(),
        "generated_at": now_ict().isoformat(),
    }


if __name__ == "__main__":
    print(generate_weekly_report())
