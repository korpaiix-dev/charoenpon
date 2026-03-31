#!/usr/bin/env python3
"""Analytics & Self-Learning — เจริญพร FB Auto-Post
ดึง engagement ทุกโพสต์ → วิเคราะห์ → ปรับ weights อัตโนมัติ
"""

import json
import os
import requests
from datetime import datetime, timezone, timedelta
from collections import defaultdict

ICT = timezone(timedelta(hours=7))

PAGE_ID = "896245606913574"
PAGE_TOKEN = "EAADhsxfbE8sBRGKvPZBEJFkkkM9AjznceXsCQyINrkZCLOCEhjdNd3G1B9axMidCFxjGXyE4JKbxnHwqkrzZALKY3TKaoOgCgGhX0SFm0vuPz15EsuTdsG1exFHnLSLqTKLTXlQXS2z3BNN9WVG7xZBrg6KCq9eXAxJfBrkGijXC3Sg0UvjjInarbFwT7AZCcy5oA"

LOG_FILE = "/root/charoenpon/fb-manager/data/post_log_v2.json"
WEIGHTS_FILE = "/root/charoenpon/fb-manager/data/weights.json"
ANALYTICS_FILE = "/root/charoenpon/fb-manager/data/analytics_history.json"


def load_json(path, default=None):
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return default if default is not None else {}


def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_post_engagement(post_id: str) -> dict:
    """ดึง engagement ของโพสต์จาก FB API"""
    try:
        r = requests.get(
            f"https://graph.facebook.com/v21.0/{post_id}",
            params={
                "fields": "likes.summary(true),comments.summary(true),shares,created_time",
                "access_token": PAGE_TOKEN,
            },
            timeout=10,
        )
        data = r.json()
        return {
            "likes": data.get("likes", {}).get("summary", {}).get("total_count", 0),
            "comments": data.get("comments", {}).get("summary", {}).get("total_count", 0),
            "shares": data.get("shares", {}).get("count", 0),
            "created_time": data.get("created_time", ""),
        }
    except Exception as e:
        print(f"  ❌ Error fetching {post_id}: {e}")
        return {"likes": 0, "comments": 0, "shares": 0, "created_time": ""}


def calculate_score(eng: dict) -> float:
    """คำนวณ engagement score (shares มีน้ำหนักสูงสุด)"""
    return eng["likes"] * 1.0 + eng["comments"] * 2.0 + eng["shares"] * 3.0


def analyze_and_update():
    """วิเคราะห์ engagement ทุกโพสต์ → ปรับ weights"""
    log = load_json(LOG_FILE, [])
    if not log:
        print("❌ ไม่มีข้อมูลโพสต์ใน log")
        return

    print(f"📊 วิเคราะห์ {len(log)} โพสต์...")

    # ดึง engagement แต่ละโพสต์
    caption_scores = defaultdict(list)  # caption_idx → [scores]
    hashtag_scores = defaultdict(list)  # hashtag_set → [scores]
    hour_scores = defaultdict(list)     # hour → [scores]

    for entry in log:
        post_id = entry.get("post_id", "")
        if not post_id:
            continue

        eng = get_post_engagement(post_id)
        score = calculate_score(eng)

        entry["engagement"] = eng
        entry["score"] = score

        # Caption analysis
        cap_idx = entry.get("caption_idx", -1)
        if cap_idx >= 0:
            caption_scores[cap_idx].append(score)

        # Hashtag analysis
        tags = entry.get("hashtags", "")
        if tags:
            hashtag_scores[tags].append(score)

        # Time analysis
        post_time = entry.get("time", "")
        if post_time:
            try:
                dt = datetime.fromisoformat(post_time)
                hour_scores[dt.hour].append(score)
            except:
                pass

        print(f"  📝 {post_id[:30]}... | 👍{eng['likes']} 💬{eng['comments']} 🔄{eng['shares']} | score={score:.1f}")

    # บันทึก log ที่มี engagement กลับ
    save_json(LOG_FILE, log)

    # คำนวณ weights
    weights = {
        "caption_weights": {},
        "hashtag_weights": {},
        "hour_weights": {},
        "updated": datetime.now(ICT).isoformat(),
        "total_posts_analyzed": len(log),
    }

    # Caption weights (avg score → higher = more likely to be picked)
    if caption_scores:
        for idx, scores in caption_scores.items():
            avg = sum(scores) / len(scores)
            weights["caption_weights"][str(idx)] = round(avg, 2)
        print(f"\n📝 Caption scores:")
        for idx in sorted(weights["caption_weights"], key=lambda x: weights["caption_weights"][x], reverse=True):
            print(f"  Template #{idx}: avg score = {weights['caption_weights'][idx]}")

    # Hashtag weights
    if hashtag_scores:
        for tags, scores in hashtag_scores.items():
            avg = sum(scores) / len(scores)
            weights["hashtag_weights"][tags] = round(avg, 2)
        print(f"\n#️⃣ Hashtag scores:")
        for tags in sorted(weights["hashtag_weights"], key=lambda x: weights["hashtag_weights"][x], reverse=True):
            print(f"  {tags[:50]}... = {weights['hashtag_weights'][tags]}")

    # Hour weights
    if hour_scores:
        for hour, scores in hour_scores.items():
            avg = sum(scores) / len(scores)
            weights["hour_weights"][str(hour)] = round(avg, 2)
        print(f"\n⏰ Hour scores:")
        for h in sorted(weights["hour_weights"], key=lambda x: weights["hour_weights"][x], reverse=True):
            print(f"  {h}:00 ICT = {weights['hour_weights'][h]}")

    # Best performers
    best_caption = max(weights["caption_weights"], key=weights["caption_weights"].get) if weights["caption_weights"] else "N/A"
    best_hour = max(weights["hour_weights"], key=weights["hour_weights"].get) if weights["hour_weights"] else "N/A"
    weights["best_caption_idx"] = best_caption
    weights["best_hour"] = best_hour

    save_json(WEIGHTS_FILE, weights)

    # บันทึก analytics history
    history = load_json(ANALYTICS_FILE, [])
    history.append({
        "date": datetime.now(ICT).isoformat(),
        "posts_analyzed": len(log),
        "best_caption": best_caption,
        "best_hour": best_hour,
        "avg_score": round(sum(calculate_score(e.get("engagement", {})) for e in log if e.get("engagement")) / max(len(log), 1), 2),
    })
    save_json(ANALYTICS_FILE, history[-52:])  # เก็บ 52 สัปดาห์

    print(f"\n✅ บันทึก weights แล้ว: {WEIGHTS_FILE}")
    print(f"🏆 Best caption: Template #{best_caption}")
    print(f"🏆 Best hour: {best_hour}:00 ICT")

    return weights


def generate_report() -> str:
    """สร้างรายงานสรุปสำหรับส่งให้บอส"""
    weights = load_json(WEIGHTS_FILE, {})
    log = load_json(LOG_FILE, [])

    if not weights:
        return "❌ ยังไม่มีข้อมูล analytics — รอวิเคราะห์รอบแรกก่อน"

    total = weights.get("total_posts_analyzed", 0)
    best_cap = weights.get("best_caption_idx", "N/A")
    best_hour = weights.get("best_hour", "N/A")

    # หาโพสต์ที่ดีที่สุด
    best_post = max(log, key=lambda x: x.get("score", 0)) if log else {}
    best_eng = best_post.get("engagement", {})

    report = f"""📊 รายงาน Analytics เจริญพร FB

📝 โพสต์ทั้งหมด: {total}
🏆 Caption ที่ดีที่สุด: Template #{best_cap}
⏰ เวลาที่ดีที่สุด: {best_hour}:00 ICT

🥇 โพสต์ที่ Engagement สูงสุด:
  👍 {best_eng.get('likes', 0)} | 💬 {best_eng.get('comments', 0)} | 🔄 {best_eng.get('shares', 0)}
  📅 {best_post.get('time', 'N/A')[:10]}

📈 อัปเดตล่าสุด: {weights.get('updated', 'N/A')[:16]}"""

    return report


if __name__ == "__main__":
    analyze_and_update()
