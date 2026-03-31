"""Facebook Page Manager Configuration — เจริญพร"""

PAGE_ID = "896245606913574"
PAGE_TOKEN = "EAADhsxfbE8sBRGKvPZBEJFkkkM9AjznceXsCQyINrkZCLOCEhjdNd3G1B9axMidCFxjGXyE4JKbxnHwqkrzZALKY3TKaoOgCgGhX0SFm0vuPz15EsuTdsG1exFHnLSLqTKLTXlQXS2z3BNN9WVG7xZBrg6KCq9eXAxJfBrkGijXC3Sg0UvjjInarbFwT7AZCcy5oA"
API_VERSION = "v21.0"
BASE_URL = f"https://graph.facebook.com/{API_VERSION}"

# Telegram Admin Group สำหรับแจ้งเตือน
TG_ADMIN_GROUP = -1003830920430
TG_BOT_TOKEN = "8324161948:AAE7qCN_-Rm5LQ8v-Jk0bo0i4Y9SRzgLg_g"  # ประกาศ Bot

# กลุ่มฟรี
FREE_GROUP_LINK = "https://t.me/+EihPcGnV5V8zYzE9"
FREE_GROUP_NAME = "กลุ่มเจริญพร (ฟรี)"

# แพ็กเกจ VIP
PACKAGES = [
    {"name": "VIP 30 วัน", "price": 300, "emoji": "🥉", "rooms": "1 ห้อง (VIP)", "duration": "30 วัน"},
    {"name": "OnlyFans + VIP 30 วัน", "price": 500, "emoji": "👙", "rooms": "2 ห้อง (OnlyFans + VIP)", "duration": "30 วัน"},
    {"name": "GOD MODE 90 วัน", "price": 1299, "emoji": "🥈", "rooms": "ครบ 6 ห้อง", "duration": "90 วัน"},
    {"name": "GOD MODE ถาวร", "price": 2499, "emoji": "💎", "rooms": "ครบ 6 ห้อง ตลอดชีพ", "duration": "ถาวร"},
]

# Sales Bot สำหรับรับชำระ (ใช้ลิงก์เต็ม กดได้ใน FB)
SALES_BOT_LINK = "https://t.me/NamwarnJarern_bot"
SALES_BOT_NAME = "NamwarnJarern_bot"

# Auto-post schedule (ICT = UTC+7)
# เวลาโพสต์: 10:00, 14:00, 18:00, 22:00 ICT
POST_SCHEDULE_UTC = ["03:00", "07:00", "11:00", "15:00"]



# Hashtags
HASHTAGS = (
    "#Telegram #เทเลเกรม #กลุ่มเทเล #กลุ่มลับ #กลุ่มVIP "
    "#VK #งานดี #ทีเด็ด #ของดีบอกต่อ #วาร์ป #แจกวาร์ป "
    "#ทางเข้า #สายดาร์ก #สายงาน #งานลับ #เปิดการมองเห็น "
    "#ดันขึ้นฟีด #ฟีด #reels #กำลังมาแรง"
)
