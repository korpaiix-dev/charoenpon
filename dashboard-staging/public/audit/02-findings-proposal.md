# 📋 Dashboard Audit — สรุปปัญหา + ข้อเสนอจัดใหม่

> ตอบคำถามบอส: "จัดที่ทางหมวดหมู่ให้เข้ากันทำงานง่ายแบ่งชัดเจน"

---

## 🔍 ปัญหาที่เจอ

### 1. Sidebar มี 15 รายการ — ยาว เรียงสับสน

ลำดับปัจจุบัน (flat list):
```
📋 งานวันนี้
📊 ภาพรวม           ← ซ้ำกับ "งานวันนี้"?
📥 กล่องรอจัดการ
👥 ลูกค้า
💰 การเงิน
💳 บัญชีรับเงิน      ← ควรอยู่ใน 💰 การเงิน
🎁 โปรโมชั่น + บอท
📸 Content
🎰 กาชา
💬 Prae Logs
📱 กลุ่ม
👨‍💼 ทีมงาน
⚙️ ตั้งค่า
📊 Marketing
📋 Activity Log
```

**ปัญหา:**
- ไอคอน 📊 ใช้ซ้ำ (ภาพรวม + Marketing)
- ไอคอน 📋 ใช้ซ้ำ (งานวันนี้ + Activity Log)
- 💳 บัญชีรับเงิน แยกจาก 💰 การเงิน — ลูกน้องสับสน
- 📊 ภาพรวม กับ 📋 งานวันนี้ คล้ายกัน
- Marketing อยู่ปลายๆ ทั้งที่สำคัญ
- Activity Log อยู่ล่างสุด — หาเจอยากตอนสืบ

### 2. หน้าที่มีของซ้ำกัน (อาจซ้ำซ้อน)

| Action | อยู่ที่ไหนบ้าง |
|---|---|
| ดูสลิป | Inbox + Today (มี shortcut?) + Customer 360 (history) |
| ส่ง DM ลูกค้า | Customer 360 + SOS Console (ใน Finance) |
| ขยายสิทธิ์ลูกค้า | Customer 360 + Today (?) |
| Approve/Reject สลิป | Inbox + Customer 360 (history) |

**ดี:** เข้าได้หลายทาง  
**ไม่ดี:** ลูกน้องไม่รู้ว่า "ที่ถูกที่ควรเข้าตรงไหน"

### 3. 32 HIGH RISK endpoints — อาจกดผิด

ปุ่มอันตรายเหล่านี้กดผิดครั้งเดียวกระทบลูกค้า:
- approve / reject สลิป
- ban / unban / kick ลูกค้า
- extend / upgrade / cancel sub
- broadcast / DM
- regen / resend lines
- toggle feature flags

**ปัจจุบัน:** มี `confirm()` บางอัน แต่ไม่ใช่ทุกอัน

---

## 💡 ข้อเสนอจัดหมวดใหม่ — Sidebar 5 กลุ่ม

```
📋 งานวันนี้                ← เปิดมาเจอที่นี่

─── 🔥 งานเร่งด่วน ───
📥 Inbox สลิป              (5)
🛟 SOS                    (2)
👥 ลูกค้า                  (ดู + แก้รายตัว)

─── 💬 สื่อสาร + ดึงดูด ───
🎁 โปรโมชั่น + บอท         (ครอบคลุมทั้ง Comeback/Welcome/Retention/Exit)
📣 Broadcast              (NEW: แยกออกมาเด่นๆ)
📸 Content                (ตารางโพสต์ในกลุ่ม)
🎰 กาชา                   (prize + spin)
💬 Prae Logs              (ดูบทสนทนา)

─── 💰 การเงิน + รายงาน ───
💰 การเงิน + Receivers     (รวมกัน — รับเงิน + รายงาน + fraud)
📊 ภาพรวม                 (chart + KPI)
📈 Marketing ROI

─── 🏢 ทีม + ระบบ ───
👨‍💼 ทีมงาน
📱 กลุ่ม VIP/ฟรี           (registry + relay sync)
⚙️ ตั้งค่าระบบ              (admin + flags + prae)

─── 📜 ประวัติ ───
📋 Activity Log
```

**ลดจาก 15 → 13 รายการ** (รวม Receivers→Finance + รวม Broadcast แยกออก)

---

## 🛡 ข้อเสนอป้องกัน HIGH RISK

### 1. Add "⚠️ คอนเฟิร์ม 2 ครั้ง" สำหรับ HIGH RISK ทั้งหมด

ปุ่มอันตรายต้องเปิด modal มีข้อความ:
```
⚠️ จะส่ง DM ถึง 1,234 ลูกค้า

ข้อความที่จะส่ง:
[ตัวอย่างข้อความ]

[ยกเลิก]  [ส่งจริง]
```

### 2. Add "📊 Stats Bar" ก่อนกด

ก่อน Broadcast ให้แสดง:
```
✓ จะส่ง: 1,234 คน
✗ Skip: 56 คน (blocked bot)
⏱ คาดการณ์: 4 นาที
```

### 3. Add "⏰ Cooldown" ระหว่างปุ่มอันตราย

หลังกด "Approve" / "Broadcast" ปุ่ม disabled 3 วินาที กันกดซ้ำ

### 4. Add "💾 Undo button"

หลัง destructive action เด้ง toast 5 วินาที "↶ Undo" — ลูกน้องกดผิดยังย้อนได้

---

## 📊 ตัวเลขสรุป

| Metric | Count |
|---|---|
| Pages (renderXxx) | 19 |
| Interactive elements (onclick) | 159 |
| Unique API endpoints | 143 |
| 🟢 SAFE (GET) | 76 |
| 🟡 MED (POST/PATCH/DELETE — system) | 35 |
| 🔴 HIGH (POST/PATCH/DELETE — customer-facing) | 32 |

**32 ปุ่มอันตราย** = ต้องระวังพิเศษ + ควรมี confirm 2 step
