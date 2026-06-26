# 08 — การเงิน + Receiver (Finance)

> Receiver pool, payment monitoring, reports
>
> **6 items** · [37] receiver pool → [38] cumulative → [39] reset → [40] daily/weekly report → [41] Excel → [42] หาสลิปปลอม

---

## [37] Receiver Pool (บัญชีรับเงิน)

### 🔍 ตอนนี้เป็นยังไง

✅ มีหน้า Receivers (Sprint 2.1) ใน dashboard:
- เพิ่ม / ลบบัญชี
- ดู balance (มี bug แก้แล้ว)
- อัพโหลด QR
- เปิด/ปิด

### 📝 ลูกน้องเห็นอะไร

```
┌─── 💳 Receiver Pool ────────────────────────────────┐
│                                                       │
│  บัญชีที่ใช้รับเงิน:                                    │
│                                                       │
│  ✅ KBank XXX-X-XX964-2 · นาย ก                      │
│   วันนี้: ฿4,500 · สัปดาห์: ฿32,000                  │
│   [✏️ แก้] [⏸ ปิด] [💸 reset วันนี้]                   │
│                                                       │
│  ✅ SCB XXX-XXX-1234 · นาง ข                          │
│   วันนี้: ฿2,300 · สัปดาห์: ฿18,400                  │
│   [✏️ แก้] [⏸ ปิด] [💸 reset วันนี้]                   │
│                                                       │
│  ⏸ KTB (paused)                                      │
│   [▶ เปิดใหม่]                                        │
│                                                       │
│  [➕ เพิ่มบัญชีใหม่]                                   │
└───────────────────────────────────────────────────────┘
```

### ⏱ Effort
**0 วัน** (DONE)

---

## [38] Cumulative Limit (ยอดสะสมต่อบัญชี)

### 🔍 ตอนนี้เป็นยังไง

มี cumulative_threshold — ถ้าบัญชีรับเกิน threshold → rotate ไปบัญชีอื่น

ปัญหา:
- ลูกน้องไม่เห็น threshold ปัจจุบัน
- เปลี่ยน threshold ไม่ได้

### 📝 ลูกน้องเห็นอะไร

```
┌─── 💳 Receiver Settings ────────────────────────────┐
│                                                       │
│  KBank XXX-X-XX964-2:                                │
│   Threshold ยอดสะสม: ฿100,000 / วัน                  │
│   เมื่อเกิน → rotate ไปบัญชีอื่น                       │
│   [✏️ แก้]                                            │
│                                                       │
│  Auto-reset: ❌ ไม่ทำ (ต้อง reset มือ)                 │
│  หรือเปิด: [☑ reset ทุกวัน 00:00]                    │
│                                                       │
└───────────────────────────────────────────────────────┘
```

### ⏱ Effort
**1 วัน**

---

## [39] Manual Receiver Reset

✅ มีแล้วใน Sprint 2.1.1

### ⏱ Effort
**0 วัน**

---

## [40] Daily / Weekly Report

### 🔍 ตอนนี้เป็นยังไง

มี daily report ส่ง Discord + Telegram (Task #267 consolidate)

### 📝 ลูกน้องเห็นอะไร

```
┌─── 📊 Reports ──────────────────────────────────────┐
│                                                       │
│  [Today] [Yesterday] [Custom date]                   │
│                                                       │
│  📈 รายได้: ฿18,500 (47 transactions)               │
│  ⬆️ +12% vs เมื่อวาน                                  │
│                                                       │
│  💎 แบ่งตาม tier:                                     │
│   - VIP 30: ฿9,000 (30 คน)                          │
│   - OF+VIP: ฿5,000 (10 คน)                          │
│   - GOD 90: ฿2,598 (2 คน)                           │
│   - Gacha: ฿1,902 (5 ครั้ง)                          │
│                                                       │
│  🆕 ลูกค้าใหม่: 12 คน                                  │
│  🔄 ต่ออายุ: 18 คน                                     │
│                                                       │
│  📥 [Export Excel]                                    │
│  📤 [ส่ง Discord ทันที]                               │
└───────────────────────────────────────────────────────┘
```

### ⏱ Effort
**1 วัน** (UI polish)

---

## [41] Excel Export

### 🔍 ตอนนี้เป็นยังไง

✅ Sprint 3.5 — มีปุ่ม export Excel

### ⏱ Effort
**0 วัน**

---

## [42] Anti-fraud (หาสลิปปลอม)

### 🔍 ตอนนี้เป็นยังไง

- Slip2Go AI + Gemini Vision
- Sender_ring detection (block ลูกค้าที่โอนกันเอง)
- Dup detection (transRef ซ้ำ)
- Blacklist

ลูกน้องไม่ต้องตั้งค่า — ระบบทำเอง

ต้องมี dashboard alert:

### 📝 ลูกน้องเห็นอะไร

```
┌─── 🚨 Fraud Alerts ─────────────────────────────────┐
│                                                       │
│  🔴 High risk (รอตรวจ):                                │
│   - @bad_user · transRef ซ้ำ 5 ครั้ง                  │
│     [👀 ดูประวัติ] [🚫 Block]                          │
│                                                       │
│  🟡 Medium risk:                                       │
│   - @user_456 · sender ring กับ @user_789            │
│     [👀 ดูประวัติ]                                     │
│                                                       │
│  📊 30 วันที่ผ่านมา:                                  │
│   - บล็อกสลิปปลอม: 23 ใบ                              │
│   - ผ่านมา / รวม: 5/2,156 (0.23%)                    │
│                                                       │
└───────────────────────────────────────────────────────┘
```

### ⏱ Effort
**1 วัน**

---

## 📊 สรุป Section 08

| # | Item | Effort | Phase |
|---|---|---|---|
| 37 | Receiver pool (DONE) | 0d | — |
| 38 | Cumulative threshold edit | 1d | A |
| 39 | Manual reset (DONE) | 0d | — |
| 40 | Reports UI polish | 1d | A |
| 41 | Excel (DONE) | 0d | — |
| 42 | Fraud alerts | 1d | A |
| **รวม** | | **3 วัน** | |
