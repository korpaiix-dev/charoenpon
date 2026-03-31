"""สถิติเพจเจริญพร"""

from fb_api import get_feed, get_page_info, get_page_insights, now_ict, format_time_ict
from config import PACKAGES


def generate_stats_report() -> str:
    """สร้างรายงานสถิติเพจ"""
    info = get_page_info()
    feed = get_feed(limit=10)
    
    # Page info
    name = info.get("name", "N/A")
    fans = info.get("fan_count", 0)
    
    # Feed stats
    total_likes = 0
    total_comments = 0
    total_shares = 0
    post_count = len(feed)
    
    for post in feed:
        total_likes += post.get("likes", {}).get("summary", {}).get("total_count", 0)
        total_comments += post.get("comments", {}).get("summary", {}).get("total_count", 0)
        total_shares += post.get("shares", {}).get("count", 0)
    
    avg_likes = total_likes / post_count if post_count else 0
    avg_comments = total_comments / post_count if post_count else 0
    engagement_rate = ((total_likes + total_comments + total_shares) / (fans * post_count) * 100) if fans and post_count else 0
    
    # Try insights
    insights_text = ""
    insights = get_page_insights(period="day")
    if "data" in insights:
        for metric in insights["data"]:
            metric_name = metric.get("name", "")
            values = metric.get("values", [])
            if values:
                latest = values[-1].get("value", 0)
                if metric_name == "page_impressions":
                    insights_text += f"📊 Impressions วันนี้: {latest:,}\n"
                elif metric_name == "page_engaged_users":
                    insights_text += f"👥 Engaged Users: {latest:,}\n"
                elif metric_name == "page_post_engagements":
                    insights_text += f"🤝 Post Engagements: {latest:,}\n"
                elif metric_name == "page_fan_adds":
                    insights_text += f"➕ New Fans: {latest:,}\n"
    
    report = f"""📊 สถิติเพจ "{name}"
━━━━━━━━━━━━━━━━━━━
👥 Followers: {fans:,}
📝 โพสต์ล่าสุด 10 อัน:
  ❤️ Likes รวม: {total_likes}  (avg {avg_likes:.1f}/โพสต์)
  💬 Comments: {total_comments}
  🔁 Shares: {total_shares}
  📈 Engagement Rate: {engagement_rate:.2f}%

{insights_text if insights_text else "ℹ️ Insights ไม่พร้อมใช้งาน (อาจต้องรอ 24 ชม.)"}
━━━━━━━━━━━━━━━━━━━
⏰ อัปเดต: {now_ict().strftime('%d/%m/%Y %H:%M น.')}"""
    
    return report


def feed_summary() -> str:
    """สรุปโพสต์ล่าสุด"""
    feed = get_feed(limit=5)
    lines = ["📋 โพสต์ล่าสุด 5 อัน:\n"]
    
    for i, post in enumerate(feed, 1):
        msg = post.get("message", "")[:60]
        likes = post.get("likes", {}).get("summary", {}).get("total_count", 0)
        comments = post.get("comments", {}).get("summary", {}).get("total_count", 0)
        shares = post.get("shares", {}).get("count", 0)
        created = post.get("created_time", "")
        time_str = format_time_ict(created) if created else ""
        
        lines.append(f"{i}. {time_str}")
        lines.append(f"   {msg}...")
        lines.append(f"   ❤️{likes} 💬{comments} 🔁{shares}\n")
    
    return "\n".join(lines)


if __name__ == "__main__":
    print(generate_stats_report())
    print()
    print(feed_summary())
