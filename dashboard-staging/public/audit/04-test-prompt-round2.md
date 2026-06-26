# 🧪 Test Prompt — รอบ 2 (Verify fixes + ตรวจที่เหลือ)

> Round 1 มี issues 9 อัน (Major 4 / Minor 5) — แก้แล้ว 6 อัน
> Round 2 = ยืนยันที่แก้ + ทดสอบที่เหลือ + หา bug ใหม่

---

## 📋 Prompt ที่ให้ AI

```
คุณเคย test dashboard เจริญพรไปแล้วครั้งหนึ่ง · รอบนี้ทำต่อรอบ 2

═══════════════════════════════════════════
📍 URL ที่ทดสอบ (เหมือนเดิม)
═══════════════════════════════════════════

URL : http://139.59.123.146:8012   (TEST MODE — banner แดงต้องขึ้น)
Login:
  Telegram ID : [บอสใส่]
  Password    : [บอสใส่]

ห้าม test ที่ 8010 (production จริง)

═══════════════════════════════════════════
🎯 จุดประสงค์รอบ 2
═══════════════════════════════════════════

แบ่งเป็น 3 ส่วน:

ส่วน A: ยืนยัน 6 fixes ที่ developer บอกว่าแก้แล้ว
ส่วน B: ทดสอบ items ที่รอบแรกผมยังไม่ได้ทดสอบ / ไม่ได้สังเกต
ส่วน C: หา bug ใหม่ที่อาจเกิดจากการแก้ครั้งนี้ (regression)

═══════════════════════════════════════════
🅰️ ส่วน A — Verify 6 fixes (ละเอียดให้สุด)
═══════════════════════════════════════════

A1. 🟡 Prae chat HTML render
─────────────────────────────
1. ไปที่: 💭 บทสนทนา Prae
2. คลิกลูกค้าคนใดก็ได้ที่ Prae ตอบไป (ดูปุ่ม 💬 ดูแชท)
3. ตรวจในแชท bubble ของ Prae (ฝั่งซ้าย พื้นเทา):
   ✓ <b>VIP 30 วัน</b> → ควรเห็นเป็น "VIP 30 วัน" ตัวหนา (ไม่ใช่ tag ดิบ)
   ✓ <a href="t.me/...">link</a> → ควรเป็นลิ้งคลิกได้ (สีน้ำเงิน underline)
   ✓ tag แบบ ["handle_group_access_issue"] → ไม่ควรเห็น text ดิบ
   ✓ ควรเห็นปุ่ม "⚙️ tools (N)" คลิกขยายดู JSON ได้
   ✗ ถ้า <script>, <iframe>, <style> ยังเห็น = security issue!

A2. 🟡 Bot Messages live Telegram preview
─────────────────────────────────────────
1. ไปที่: ⚙️ ตั้งค่า → 💬 คำพูดบอท
2. คลิก ✏️ แก้ ที่ row "welcome_new" (หรืออันใดก็ได้)
3. ตรวจ:
   ✓ มีกล่อง "👀 Preview ใน Telegram" ขึ้นใต้ textarea (สีพื้นเทาฟ้า)
   ✓ Telegram chat bubble สีขาว แสดงข้อความปัจจุบัน
   ✓ พิมพ์ลงใน textarea → preview update ทันที (live)
   ✓ ใส่ <b>test</b> → ต้องเห็น "test" ตัวหนาใน preview
   ✓ ใส่ <script>alert("x")</script> → ต้องไม่ alert (block แล้ว)
   ✗ ถ้าไม่มี preview = fix ไม่สำเร็จ

A3. 🟡 Inbox page title sync
─────────────────────────────
1. ไปที่: 📥 Inbox สลิป (จากเมนูซ้าย)
2. ตรวจ top bar กลางจอ:
   ✓ ต้องเห็น "📥 Inbox สลิป" (ไม่ใช่ "กล่องรอจัดการ" อีกแล้ว)

A4. 🟡 Today date mismatch (สำคัญ — ตัวเลขต้องตรง)
─────────────────────────────────────────────────
1. เปิด 2 tabs ใน browser:
   - Tab 1: 📊 ภาพรวม
   - Tab 2: 💰 การเงิน + Receivers
2. ดูตัวเลข "วันนี้" หรือ "Today":
   ✓ ต้องตรงกันทั้ง 2 หน้า (เช่น ฿270 = ฿270)
   ✗ ถ้ายังไม่ตรง รายงานเลขที่เห็นทั้ง 2 หน้า

A5. 🟢 Thai labels feature flags
─────────────────────────────────
1. ไปที่: ⚙️ ตั้งค่า → 🚦 ฟีเจอร์ใหม่
2. ดู description ทั้ง 13 flags:
   ✓ ต้องเป็นภาษาไทย ทั้งหมด
   ✓ อ่านแล้วเข้าใจว่า flag นี้ทำอะไร · กระทบใคร
   ✗ ถ้ายังเห็น "Read main menu inline buttons..." = ไม่ได้ update DB

A6. ℹ️ Bot Manage page (ดูถ้า role = owner)
─────────────────────────────────────────────
1. เช็คใน sidebar ว่ามี "⚙️ Bot Manage" อยู่หรือไม่
2. ถ้ามี → คลิกเข้าไปดูหน้าตา (เป็น hidden owner-only page)
3. รายงานว่ามีฟีเจอร์อะไรบ้าง

═══════════════════════════════════════════
🅱️ ส่วน B — ทดสอบที่รอบแรกยังไม่ได้ทดสอบ
═══════════════════════════════════════════

B1. 📥 Inbox สลิป workflow (สำคัญ)
────────────────────────────────────
รอบที่แล้วผมบอกว่าไม่มี Approve/Reject — จริงๆ มี แต่อยู่ในแถวที่มี type=payment
1. ใน Inbox: หาแถวที่เป็น Payment (มีเงิน + แพ็กเกจ)
2. ตรวจ:
   ✓ เห็นปุ่ม ✅ Approve + ❌ Reject ในแถวไหม
   ✓ คลิก ❌ Reject → ต้องเด้ง modal preset 5 เหตุผล (ไม่ใช่ prompt() popup เก่า)
     • จำนวนเงินไม่ตรงกับแพ็กเกจ
     • ปลายทางไม่ใช่บัญชีเรา
     • สลิปไม่ชัด/อ่านไม่ได้
     • สลิปซ้ำ/เคยใช้แล้ว
     • อื่นๆ (พิมพ์เอง)
   ✓ คลิก ✅ Approve → ต้องเด้ง confirm dialog (test mode block)
3. Bulk action (ถ้ามีหลายแถว):
   ✓ Checkbox เลือกได้ไหม
   ✓ มี bar ด้านล่าง "เลือก N รายการ" + ปุ่ม Bulk Approve/Reject
   ✓ Bulk Approve เด้ง confirm พร้อมแสดงจำนวน + ผลกระทบ

B2. 📈 Marketing ROI (ลิสต์ที่หาย)
────────────────────────────────────
รอบแรกพบ: ไม่มี Ivy + ไม่มี links table + ไม่มี heatmap
1. ตรวจอีกที:
   - มี marketer 3 คนไหม (Pai/Ivy/Wasu) หรือแค่ 2 คน?
   - มี table ลิ้งทั้งหมดด้านล่างไหม?
   - มี heatmap conversion ไหม?
2. ถ้า Ivy ไม่อยู่ → อาจจะ role ของ Ivy ไม่ใช่ marketer หรือยังไม่ได้สร้าง

B3. 🏛 กลุ่ม VIP/ฟรี (sync status)
────────────────────────────────
1. ดูในหน้านี้:
   ✓ มี tabs สลับ VIP/GOD/ฟรี/ห้องชัก ไหม
   ✓ มี Relay-bot sync status ไหม (กี่กลุ่ม sync · ครั้งล่าสุด)
   ✗ ถ้าไม่มี → รายงานสิ่งที่เห็นจริง

B4. 📜 Activity Log (JSON escape)
────────────────────────────────
1. ดู Details column:
   ✗ ถ้ายังเห็น JSON escape ดิบ เช่น {\"ip\": \"...\"}
   ✓ ควร render เป็น JSON อ่านง่าย หรือซ่อนใน <details>

B5. 💳 บัญชีรับเงิน header overlap
─────────────────────────────────
1. Scroll up/down: 
   ✗ หัวข้อ "บัญชีรับเงิน" overlap กับ top bar?

B6. 🚦 Feature flag toggle (test mode)
─────────────────────────────────────
1. ไปที่: ⚙️ ตั้งค่า → 🚦 ฟีเจอร์ใหม่
2. คลิก ▶ เปิด ที่ flag ไหนก็ได้:
   ✓ ต้องเด้ง confirm dialog แรงๆ (มีข้อความ "จะเปิดให้ลูกค้าทุกคน" / canary)
   ✓ กดยืนยัน → toast "✅ flag → ON" (test mode block)
   ✗ ถ้าไม่มี confirm = security issue

B7. 📣 บรอดแคสต์ลงกลุ่ม (top bar button)
─────────────────────────────────────────
1. คลิก 📣 บรอดแคสต์ ใน top bar
2. ตรวจ:
   ✓ Modal เปิด · เลือกกลุ่มได้ · นับจำนวน
   ✓ Preview ข้อความ
   ✓ กด "📤 ส่ง" → test mode block + toast

B8. 🛒 ออเดอร์ + 📋 รายงาน (top bar)
─────────────────────────────────────
1. คลิกแต่ละปุ่มใน top bar
2. ตรวจ modal เปิดได้ · ข้อมูลขึ้น · กดปิดได้

B9. ⚙️ ตั้งค่า → 🤖 บุคลิก Prae
─────────────────────────────
1. ดู editor:
   ✓ มี textarea ใหญ่ + version history
   ✓ ปุ่ม Save / Restore version ทำงานไหม (test mode)
   ✓ Diff viewer แสดงไหม

═══════════════════════════════════════════
🅲 ส่วน C — Regression (ของเดิมยังทำงานไหม)
═══════════════════════════════════════════

ตรวจให้แน่ใจว่าการแก้ครั้งนี้ไม่ทำให้ของอื่นพัง:

C1. ทุก page ใน sidebar → กดทุกอันได้ · ไม่ error?
C2. WebSocket green dot 🟢 ขึ้นไหม?
C3. กระดิ่ง 🔔 (ถ้ามีสลิปรอ) คลิกแล้วไป Inbox?
C4. Customer 360:
    - Timeline events ครบ?
    - 📝 Notes ทีม ยังเพิ่มได้ + ปักหมุดได้ + ลบได้?
    - ปุ่ม Extend/DM/Ban/etc ยังทำงาน?
C5. Modal close button (×) ทุก modal ปิดได้?
C6. Login → logout → login → ใช้ได้?
C7. กด refresh (F5) ตอนอยู่หน้า X → กลับมาเปิดหน้าเดิม?

═══════════════════════════════════════════
📊 รูปแบบรายงาน รอบ 2
═══════════════════════════════════════════

# Test Report Round 2 — Dashboard เจริญพร [วันที่]

## ส่วน A — Fix Verification
- A1 Prae HTML: ✅/❌ + [detail]
- A2 Bot Msg preview: ✅/❌ + [detail]
- A3 Inbox title: ✅/❌
- A4 Today mismatch: ✅/❌ + ตัวเลขที่เห็นทั้ง 2 หน้า
- A5 Thai labels: ✅/❌
- A6 Bot Manage: เห็น? + features ใน page

## ส่วน B — New tests
### 🔴 Critical
### 🟡 Major
### 🟢 Minor

## ส่วน C — Regression
- ของเดิมพังอะไรไหม?

## ✅ ทำงานดีโดยรวม
## 💡 ข้อเสนอ
```

---

## 🔗 ลิงก์อ้างอิง

- [03-test-prompt.md](./03-test-prompt.md) — รอบ 1 (สำหรับ test ครั้งแรก)
- [01-pages-actions.md](./01-pages-actions.md) — map endpoints
- [02-findings-proposal.md](./02-findings-proposal.md) — UX proposal
