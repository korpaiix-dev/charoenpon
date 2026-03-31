# 🧪 QA Report — ระบบเจริญพร
**วันที่:** 2026-03-23 04:34 ICT (2026-03-22 21:34 UTC)
**QA Engineer:** แพนด้า (AI Agent)

---

## 1. 📘 FB Manager (`charoenpon-fb-manager`)

| # | Test Case | ผลลัพธ์ | รายละเอียด |
|---|-----------|---------|-----------|
| 1.1 | Container UP | ✅ | Up 20 minutes |
| 1.2 | Auto-post ทำงานจริง | ✅ | Post ID: `896245606913574_122124644883189586` — Template #4, Image #20 |
| 1.3 | ลบโพสต์ทดสอบ | ✅ | `delete_post()` → `True` — ลบสำเร็จ |
| 1.4 | Auto-reply (Inbox) | ✅ | `process_inbox()` → `0` (ไม่มีข้อความใหม่ — ทำงานปกติ) |
| 1.5 | Auto-reply (Comments) | ✅ | function พร้อมใช้งาน (classify → build reply) |
| 1.6 | Customer tracking | ✅ | `generate_customer_report()` → แสดงรายงานลูกค้า (0 คน — ระบบใหม่) |
| 1.7 | Stats report | ✅ | Followers: 1,111 / Likes: 7 / Engagement Rate: 0.07% |
| 1.8 | Weekly analysis | ✅ | 25 โพสต์, Avg Engagement 0.9, มีคำแนะนำปรับปรุง |
| 1.9 | ลิงก์ Telegram เป็น https:// | ✅ | `SALES_BOT_LINK = "https://t.me/NamwarnJarern_bot"` — ไม่ใช่ @NamwarnJarern_bot |
| 1.10 | ข้อความตอบแยกตามประเภท | ✅ | 4 categories: `PRICING` / `GROUP_LINK` / `GENERAL` / `COMPLAINT` — มี `classify_message()` + `build_*_reply()` ครบ |
| 1.11 | ไม่โชว์ราคาในโพสต์ | ✅ | โพสต์มีแค่ "กลุ่มลับ VIP" + "กลุ่มฟรี" — ไม่มีราคา 300/500/1299/2499 |
| 1.12 | ไม่ส่งแจ้งเตือน Telegram | ✅ | ไม่มี `send_telegram` / `notify_telegram` / `_notify_admin` ใน auto_reply.py |

---

## 2. 🤖 Bots

| # | Bot | Status | ผลลัพธ์ | รายละเอียด |
|---|-----|--------|---------|-----------|
| 2.1 | `charoenpon-sales-bot` | UP 3h | ✅ | Log ปกติ — New user registered, TeaserClick |
| 2.2 | `charoenpon-admin-bot` | UP 3h | ✅ | Log ปกติ — Database initialized, Scheduler started |
| 2.3 | `charoenpon-guardian-bot` | UP 16h | ✅ | Log ปกติ — check_unauthorized: 18 groups, 540 members, 0 errors |
| 2.4 | `charoenpon-content-bot` | UP 12h | ✅ | Log ปกติ — Album teaser round done 9/9 groups |
| 2.5 | `charoenpon-announce-bot` | UP 15h | ⚠️ | **NetworkError: httpx.ConnectError** — transient Telegram API connection error แต่ bot ยัง UP อยู่ |
| 2.6 | `charoenpon-discord-bot` | UP 15h | ✅ | Log ปกติ — RESUMED session, Marketing Report completed |

---

## 3. 🕵️ Agents

| # | Agent | Status | ผลลัพธ์ | รายละเอียด |
|---|-------|--------|---------|-----------|
| 3.1 | `charoenpon-marketing-scheduler` | UP 17min | ✅ | `"เจมส์ Marketing Agent พร้อมทำงาน 🎯"` — พร้อม FB Manager control |
| 3.2 | `charoenpon-finance-scheduler` | UP 3d | ✅ | Daily finance routine completed |
| 3.3 | `charoenpon-growth-scheduler` | UP 3d | ✅ | Ad tracker running (0 campaigns) |
| 3.4 | `charoenpon-manager-agent` | UP 3d | ✅ | Daily manager report sent |

---

## 4. 🏗️ Infrastructure

| # | Component | ผลลัพธ์ | รายละเอียด |
|---|-----------|---------|-----------|
| 4.1 | `charoenpon-postgres` | ✅ | UP 3 days (healthy) |
| 4.2 | `charoenpon-dashboard` (http://localhost:8010) | ✅ | HTTP 200 |
| 4.3 | `charoenpon-backup-cron` | ✅ | UP 16h |
| 4.4 | `charoenpon-monitor` | ✅ | UP 3 days |

---

## 5. ⏰ Cron Jobs (FB Auto-Post)

| # | Test Case | ผลลัพธ์ | รายละเอียด |
|---|-----------|---------|-----------|
| 5.1 | 4 cron jobs configured | ✅ | `POST_SCHEDULE_UTC = ["03:00", "07:00", "11:00", "15:00"]` → ICT 10:00, 14:00, 18:00, 22:00 |
| 5.2 | Scheduler ทำงานถูกต้อง | ✅ | `scheduler.py` เช็ค `should_post_now()` ±20 นาที ทุก loop |

---

## 6. 📊 Google Sheets

| # | Test Case | ผลลัพธ์ | รายละเอียด |
|---|-----------|---------|-----------|
| 6.1 | Sheet "ค่าใช้จ่าย API" มีรายการ FB Manager | ⚠️ | Sheet tab มีอยู่ แต่ FB Manager **ไม่ได้ใช้ AI API** จึงไม่มี cost log — ค่าใช้จ่ายคือ FB Graph API (ฟรี) ไม่ต้อง track |
| 6.2 | Sheet "โฆษณา" (Facebook Ads Performance) มี FB Auto-Post | ⚠️ | Sheet tab มีอยู่ (`Facebook Ads Performance`) แต่ DB `ad_campaigns` = 0 rows — ยังไม่มี campaign เพราะ FB Auto-Post ไม่ใช่ paid ads |

**หมายเหตุ:** FB Manager เป็น organic posting ไม่ใช่ paid ads ดังนั้น:
- ค่าใช้จ่าย = 0 (FB Graph API ฟรี) → ไม่จำเป็นต้องอยู่ใน cost sheet
- Campaign = organic posts ไม่ใช่ ad campaign → ไม่เข้า ads sheet

---

## 7. 📈 Marketing Analyzer

| # | Test Case | ผลลัพธ์ | รายละเอียด |
|---|-----------|---------|-----------|
| 7.1 | `_get_facebook_data()` function | ✅ | Line 369: `async def _get_facebook_data(today_str: str) -> dict` — ดึง customers + post_log + stats |
| 7.2 | AI_MODEL correct | ✅ | Line 52: `AI_MODEL = "anthropic/claude-sonnet-4-20250514"` |
| 7.3 | Prompt มีคำว่า "facebook" | ✅ | Line 453: `"วิเคราะห์ข้อมูล marketing วันนี้ (รวม Facebook Page data)"` + Line 463: `"วิเคราะห์ Facebook: engagement, lead quality"` |

---

## 📋 สรุป

| หมวด | ผ่าน | ไม่ผ่าน | เตือน |
|------|------|---------|-------|
| FB Manager | 12/12 | 0 | 0 |
| Bots | 5/6 | 0 | 1 |
| Agents | 4/4 | 0 | 0 |
| Infrastructure | 4/4 | 0 | 0 |
| Cron Jobs | 2/2 | 0 | 0 |
| Google Sheets | 0/2 | 0 | 2 |
| Marketing Analyzer | 3/3 | 0 | 0 |
| **รวม** | **30/33** | **0** | **3** |

### ⚠️ Issues ที่ต้องดู:

1. **`charoenpon-announce-bot` NetworkError** — Telegram API connection error (transient) bot ยัง UP แต่อาจ miss บาง message ควร restart ถ้า error ไม่หาย
2. **Google Sheets ค่าใช้จ่าย** — FB Manager ไม่มี cost entry เพราะไม่ใช้ AI API (ตั้งใจ หรือต้องเพิ่ม infrastructure cost?)
3. **Google Sheets โฆษณา** — FB Auto-Post เป็น organic post ไม่ใช่ paid ad campaign (ตั้งใจ หรือต้องสร้าง campaign ใหม่?)

### ✅ โพสต์ทดสอบ FB ถูกลบแล้ว!
Post ID `896245606913574_122124644883189586` → deleted ✅
