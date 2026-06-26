# 🧪 Test Prompt — Dashboard เจริญพร (สำหรับ Claude in Chrome / Claude.ai / AI อื่น)

> Copy & paste prompt นี้ทั้งหมดให้ AI · AI จะ test dashboard ให้บอสและส่งรายงานกลับ

---

## 📋 Prompt ที่ให้ AI

```
คุณคือ QA tester ของ dashboard ระบบขาย VIP เจริญพร (เว็บแอดมิน) 
โหมด TEST MODE — ทุก action ถูก block ไม่กระทบลูกค้า (ปลอดภัย 100%)

═══════════════════════════════════════════
📍 URL ที่ทดสอบ
═══════════════════════════════════════════

URL : http://139.59.123.146:8012
Login:
  Telegram ID : [บอสใส่ของบอส]
  Password    : [บอสใส่ของบอส]

ระวัง: 8010 = production จริง · 8012 = test mode (มี banner แดงบนสุด)
ถ้าไม่เห็น banner "🟡 TEST MODE" → หยุดทันที + แจ้งฉัน

═══════════════════════════════════════════
🎯 จุดประสงค์
═══════════════════════════════════════════

เป้าหมายงาน: ตรวจสอบ dashboard ทั้งหมดว่า
1. ทุกหน้าโหลดได้ไม่ error
2. ทุกปุ่มกดได้
3. การจัดหมวดสมเหตุสมผล (ลูกน้องที่ไม่ใช่ tech ใช้ได้)
4. ไม่มี link ตาย / ปุ่มหาย / labels งง
5. UI consistency
6. Mobile-friendly ในขนาดมือถือ (390×844)

═══════════════════════════════════════════
🗂 หน้าทั้งหมดที่ต้อง test (15 หน้า)
═══════════════════════════════════════════

Sidebar:
  📋 งานวันนี้           — homepage
  
  งานเร่งด่วน:
    📥 Inbox สลิป
    👥 ลูกค้า
  
  สื่อสาร + โปร:
    🎁 โปรโมชั่น + บอท  (7 tabs ภายใน)
    📸 Content
    🎰 กาชา
    💭 บทสนทนา Prae
  
  การเงิน + รายงาน:
    💰 การเงิน + Receivers
    💳 บัญชีรับเงิน
    📊 ภาพรวม
    📈 Marketing ROI
  
  ดูแลระบบ:
    👨‍💼 ทีมงาน
    🏛 กลุ่ม VIP/ฟรี
    ⚙️ ตั้งค่าระบบ      (5 tabs: packages, banned, prae_prompt, flags, botmsg)
  
  ประวัติ:
    📜 Activity Log

Top bar (ทุกหน้า):
  🔍 ค้นหา       — Cmd+K palette
  📣 บรอดแคสต์    — ส่งข้อความเข้ากลุ่ม
  🛒 ออเดอร์     — ดูรายการซื้อ
  📋 รายงาน      — daily summary
  📥 Export      — ดาวน์โหลด Excel
  🔔 (badge)     — แจ้งเตือนสลิปรอ → คลิกไป Inbox
  🟢 (dot)      — WebSocket live indicator

═══════════════════════════════════════════
✅ Test Checklist ต่อหน้า
═══════════════════════════════════════════

สำหรับแต่ละหน้า ตรวจดังนี้:

1. โหลดได้ใน 3 วินาทีไหม?
2. มี loading spinner ตอนรอข้อมูลไหม?
3. ข้อมูลแสดงครบไหม (table, chart, card)?
4. มี error message ที่งง / ภาษาอังกฤษไหม?
5. ปุ่มมีตำแหน่งสมเหตุสมผลไหม?
6. กดปุ่มทุกอันได้ไหม? เด้ง modal เปิด/ปิดได้ไหม?
7. Filter / search ใช้ได้ไหม?
8. ปุ่ม [+] / [✏️] / [🗑] ใช้ได้ไหม? confirm dialog ขึ้นไหม?
9. Pagination ทำงานไหม?
10. ภาษาไทยถูกไหม (ไม่ใช่ engrish)?
11. responsive ในมือถือ (390×844) ดูได้ไหม?
12. กดปุ่มกลับ / navigate ระหว่างหน้าได้ไหม?

═══════════════════════════════════════════
🔬 จุดเช็คเฉพาะหน้า
═══════════════════════════════════════════

📋 งานวันนี้:
  - เห็น greeting + วันเวลาไหม
  - การ์ดงานเร่งด่วน (สลิป/SOS/พรุ่งนี้หมดอายุ) ขึ้นไหม
  - สถิติวันนี้ (รายได้/ใหม่/ต่อ) ตรงกับใน 📊 ภาพรวมไหม
  - Quick Actions 6 ปุ่ม คลิกได้ทุกอันไหม

📥 Inbox สลิป:
  - filter 4 ปุ่ม (All / Pending / Confirmed / Rejected) ใช้ได้ไหม
  - กดปุ่ม ❌ Reject → modal preset 5 เหตุผลขึ้นไหม
  - กด ✅ Approve → confirm dialog ขึ้นไหม
  - Bulk action (เลือกหลายใบ) ทำงานไหม?
  - กด 👀 ดูสลิป → popup รูป + รายละเอียดเปิดไหม

👥 ลูกค้า:
  - search ลูกค้าได้ไหม (พิมพ์ชื่อ/@user/tg_id)
  - filter status / tier / loyalty rank
  - คลิกลูกค้า → Customer 360 เปิดไหม?
  - ใน Customer 360:
    * Timeline แสดงเหตุการณ์ครบไหม
    * 📝 Notes ทีม (มุมขวา) เพิ่มได้ไหม? ปักหมุดได้ไหม?
    * ปุ่ม actions (Extend / DM / Ban / Kick / Upgrade) ขึ้น modal ครบไหม?
    * Regen invite link → กดได้ไหม?

🎁 โปรโมชั่น + บอท (7 tabs):
  - Tab 1: 📩 Comeback DM       — config 7 อัน edit ได้ไหม
  - Tab 2: ⚡ ซื้อเร็ว /start     — config 2 อัน
  - Tab 3: 💰 ส่วนลดกาชา         — config 2 อัน + JSON edit
  - Tab 4: 👋 Welcome 24h        — config 6 อัน
  - Tab 5: ⏰ เตือนต่ออายุ        — config 5 อัน
  - Tab 6: 🚪 Exit Survey         — config 6 อัน
  - Tab 7: 🏛 บอทในกลุ่ม           — config 2 อัน
  - Tab 8: 📜 Campaign เก่า       — legacy UI
  - กด 💾 บันทึก → toast "test mode" ขึ้นไหม?

📸 Content:
  - มี Queue + สถิติ tabs
  - แสดง content รออัพไหม

🎰 กาชา:
  - prize pool table + spin pricing
  - ปุ่ม 💰 ราคา/หมุน + 🎁 เพิ่มรางวัล + 🗑 ลบรางวัล ใช้ได้ไหม
  - probability calculator แสดงไหม

💭 บทสนทนา Prae:
  - รายการลูกค้าที่คุยกับ Prae
  - คลิกลูกค้า → ดูประวัติ chat กับ Prae
  - filter 1/7/30 วัน

💰 การเงิน + Receivers:
  - มี Receivers + Reports + Fraud alerts ในหน้าเดียวไหม?
  - หรือยังแยกหน้า 💳 บัญชีรับเงิน อยู่?
  - กราฟ pie / bar แสดงไหม

💳 บัญชีรับเงิน:
  - รายการบัญชี (KBank/SCB/etc) ขึ้นครบไหม
  - cumulative balance ตรงกับ DB ไหม?
  - ปุ่ม ➕ เพิ่มบัญชี / 💸 reset / ✏️ แก้ ใช้ได้ไหม

📊 ภาพรวม:
  - chart รายได้ today / week / month แสดงไหม
  - tier breakdown table
  - filter วันได้ไหม

📈 Marketing ROI:
  - leaderboard 3 คน (Pai/Ivy/Wasu) ขึ้นไหม
  - ลิ้งทั้งหมด table + ปุ่ม revoke + edit
  - heatmap conversion ปรากฏไหม

👨‍💼 ทีมงาน:
  - partners + staff list
  - role badges ถูกต้องไหม

🏛 กลุ่ม VIP/ฟรี:
  - tabs (VIP/GOD/ฟรี/ห้องชัก)
  - relay-bot sync status
  - ปุ่ม ➕ ลงทะเบียนกลุ่มใหม่

⚙️ ตั้งค่าระบบ (5 tabs):
  - Tab 1: 📦 แพ็กเกจ          — 10 แพ็กเกจ
  - Tab 2: 🚫 รายการแบน          — banned users + slips
  - Tab 3: 🤖 บุคลิก Prae         — system prompt editor
  - Tab 4: 🚦 ฟีเจอร์ใหม่        — 13 flags toggle
  - Tab 5: 💬 คำพูดบอท          — 20 ข้อความ + version history
  - การกด toggle ฟีเจอร์ → confirm dialog แรงๆ ขึ้นไหม (โดยเฉพาะ ON for ALL)
  - แก้คำพูดบอท → preview ใน Telegram bubble ขึ้นไหม

📜 Activity Log:
  - รายการ admin actions ล่าสุด
  - filter ด้วย action type / admin

═══════════════════════════════════════════
🚨 ที่ต้องระวัง (Issues ที่อาจเจอ)
═══════════════════════════════════════════

A. UI bugs:
   - ปุ่มซ้อนกัน / overflow
   - text ภาษาอังกฤษโผล่ในส่วนภาษาไทย
   - icons ใช้ซ้ำ (📊 / 📋 etc)
   - การจัดหมวดสับสน
   - คำเทคนิคที่ลูกน้อง non-tech ไม่เข้าใจ

B. UX bugs:
   - กดปุ่มแล้วไม่มี feedback (loading / toast / changes)
   - confirm dialog แสดง info ไม่ครบ (เช่น "จะส่ง DM ถึง X คน")
   - modal เปิดแล้วปิดไม่ได้ (ติดค้าง)
   - กลับหน้าเดิมไม่ได้ (no back button)

C. Functional bugs:
   - ปุ่มกดแล้ว 404 / 500
   - chart ไม่ render
   - search ไม่ทำงาน
   - filter ไม่ทำงาน
   - pagination หาย
   - data ไม่ match กันระหว่างหน้า

D. Test mode-specific:
   - destructive action ไม่ block (ลูกค้าได้รับผลกระทบ — STOP!)
   - banner "🟡 TEST MODE" ไม่ขึ้นแม้อยู่ 8012

E. Mobile:
   - sidebar ไม่ collapse ในมือถือ
   - top bar buttons ล้น
   - modal เกินจอ

═══════════════════════════════════════════
📊 รูปแบบรายงาน
═══════════════════════════════════════════

ส่งกลับเป็น markdown ตามนี้:

# Test Report — Dashboard เจริญพร 2026-06-27

## สถานะรวม
- หน้าทดสอบ: X / 15
- ปุ่มกด: Y / Z
- issues พบ: A

## 🔴 Critical (ทำงานไม่ได้ / กระทบลูกค้า)
1. [page] — [issue] — [steps to reproduce]

## 🟡 Major (UX แย่ / สับสน)
1. ...

## 🟢 Minor (cosmetic / nice-to-have)
1. ...

## ✅ ที่ทำงานดีแล้ว
- [list]

## 💡 ข้อเสนอเพิ่มเติม
- [ideas]
```

---

## 💡 หลังจากบอสเอาไป test

ส่ง report กลับมาที่แชทผม → ผมจะ:
1. แก้ทุก critical bug ทันที (ใน volume mount — 0 downtime)
2. ปรับ UX ตามคำแนะนำ
3. ไล่แก้ Minor ในรอบหลัง

---

## 🔗 ลิงก์เกี่ยวข้อง

- [01-pages-actions.md](./01-pages-actions.md) — endpoint risk map (อ้างอิงตอน test)
- [02-findings-proposal.md](./02-findings-proposal.md) — ข้อเสนอจัดหมวด (เปรียบเทียบกับสิ่งที่เห็น)
