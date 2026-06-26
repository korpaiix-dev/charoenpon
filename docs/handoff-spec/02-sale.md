# 02 — ตอนขาย (Sale)

> ขั้นตอนที่ **เงินเข้า** — สำคัญสุด เพราะ business depends on this
>
> **3 items** · [7] ตรวจสลิป → [8] อนุมัติ → [9] รีเจ็กต์

---

## [7] ตรวจสลิป (Slip Verification)

### 🔍 ตอนนี้เป็นยังไง

Pipeline ปัจจุบัน:
```
ลูกค้าส่งรูป
  ↓
[Layer 1] Slip2Go API (~99% pass)
  ↓ ถ้า fail
[Layer 2] Gemini Vision (~60% pass)
  ↓ ถ้า fail
[ห้องสลิป] ลูกน้องตรวจมือ (เด้งเข้า Inbox ใน dashboard)
```

### 📝 ลูกน้องเห็นอะไร

```
┌─── 📥 Inbox / รายการสลิปรอตรวจ ──────────────────────┐
│                                                       │
│  รายการสลิปที่ระบบส่งมาให้ตรวจ:                          │
│                                                       │
│  ⏰ 14:32 · @user123 (tg=12345)                       │
│    📦 VIP 30 วัน · ฿300                              │
│    💰 รับเข้า: KBank XXX-X-XX964-2                    │
│    🔍 AI: 'จำนวนเงินไม่ตรง' (จ่าย 299)                  │
│    [👀 ดูสลิป] [✅ อนุมัติ] [❌ ปฏิเสธ]                     │
│                                                       │
│  ⏰ 14:28 · @user456 ...                              │
│                                                       │
└───────────────────────────────────────────────────────┘

ลูกน้องคลิก ✅ อนุมัติ →
┌─── ยืนยันการอนุมัติ ────────────────────────────────┐
│                                                       │
│  ✅ จะอนุมัติให้:                                       │
│  • @user123 ได้สิทธิ์ VIP 30 วัน                       │
│  • ลูกค้าจะได้รับ DM พร้อมลิ้งกลุ่ม                       │
│  • ระบบจะ record ฿300 ใน receiver KBank               │
│                                                       │
│  เหตุผล (optional):                                  │
│  [_____________________________________]              │
│                                                       │
│  [ยกเลิก] [ยืนยันอนุมัติ ✅]                           │
└───────────────────────────────────────────────────────┘
```

### 🎯 ลูกน้องทำอะไรได้

ปัจจุบัน (มีแล้ว):
- ✅ ดู Inbox queue
- ✅ ดูสลิป popup
- ✅ Approve / Reject
- ✅ Customer 360 ดูประวัติลูกค้า

ต้องเพิ่ม:
- 🆕 **แก้ราคา** — ถ้าลูกค้าจ่าย ฿300 แต่บอตเลือก ฿299 → ลูกน้อง override ได้
- 🆕 **เปลี่ยน package** — ถ้าโอนผิด tier ลูกน้องเปลี่ยนได้
- 🆕 **เปลี่ยน receiver** — ถ้า bot match ผิด ลูกน้องเปลี่ยน

### 🗄 DB schema

ใช้ `payments` + `admin_logs` ที่มีอยู่
เพิ่ม column ใน slip_review (มีแล้วบางส่วน):
```sql
ALTER TABLE slip_review ADD COLUMN override_amount NUMERIC(10,2);
ALTER TABLE slip_review ADD COLUMN override_package_id INT;
ALTER TABLE slip_review ADD COLUMN override_receiver_id INT;
```

### ⚙️ Code changes

- Backend: เพิ่ม endpoint PATCH /inbox/slip/{id} รับ override
- Frontend: เพิ่มฟอร์ม override ใน confirm modal

### 🔗 Links

- กระทบ **[8] อนุมัติ** — ใช้ `apply_payment_approval` เดิม
- กระทบ **[10-12] Link delivery** — หลังอนุมัติส่งลิ้ง
- กระทบ **[37-39] Finance** — sync ยอด receiver
- กระทบ **[40] Daily report** — count ยอดขาย

### ⚠️ Risk + Mitigation

| Risk | Mitigation |
|---|---|
| ลูกน้องอนุมัติสลิปปลอม | ต้องดูรูปสลิปเต็มก่อน + แสดง warning ที่ AI flag ไว้ |
| Approve ซ้ำ (race) | idempotency lock ที่ `apply_payment_approval` (มีแล้ว) |
| ลูกน้อง override amount → record ผิด → ยอดไม่ตรง | log ทุก override + ห้องสลิปแจ้งเตือนทันที |

### ⏱ Effort

**1 วัน** (มีฐานเดิม + เพิ่ม override)

---

## [8] อนุมัติอัตโนมัติ / มือ (Approval Flow)

### 🔍 ตอนนี้เป็นยังไง

มี `apply_payment_approval()` unified service ใช้ทุก path:
- A: Slip2Go auto-approve
- B: Gacha purchase
- C: retry worker
- D: by_price
- E: promo
- F: by_pid
- G: slip_review (Inbox)
- H: TrueMoney

ทุก path ผ่าน function เดียวกัน → ลด bug ทับซ้อน ✅

### 📝 ลูกน้องเห็นอะไร

ลูกน้องไม่ต้องเลือก path — ระบบเลือกเอง

แต่ลูกน้องเห็นใน Customer 360:
```
ประวัติการชำระเงิน:
- 2026-06-26 14:32 · VIP 30 วัน ฿300 · ผ่าน Slip2Go (auto)
- 2026-06-20 09:11 · GOD 90 วัน ฿1,299 · ผ่าน slip_review (manual by ลูกน้อง A)
- 2026-06-15 18:44 · กาชา 1 หมุน ฿99 · ผ่าน gacha branch (auto)
```

### 🎯 ลูกน้องทำอะไรได้

- ดูประวัติทุก path
- ค้นหาสลิปด้วย transRef
- ดูบัตรประจำตัว `payment_id`

### 🗄 DB schema

ใช้ `payments` + `subscriptions` + `admin_logs` เดิม

### ⏱ Effort

**0 วัน** (existing, แค่ surface ใน UI)

---

## [9] ปฏิเสธสลิป (Reject)

### 🔍 ตอนนี้เป็นยังไง

ลูกน้องกด ❌ ปฏิเสธ → ส่ง DM ลูกค้า + log reason

ปัญหา:
- เหตุผลปฏิเสธ hardcode (preset 4-5 อัน)
- ลูกค้าได้ DM แบบเดียวกันทุกครั้ง

### 📝 ลูกน้องเห็นอะไร

```
┌─── ปฏิเสธสลิป ──────────────────────────────────────┐
│                                                       │
│  เลือกเหตุผล:                                        │
│  ○ จำนวนเงินไม่ตรง                                    │
│  ○ ปลายทางไม่ใช่บัญชีเรา                                │
│  ○ สลิปปลอม                                          │
│  ○ สลิปซ้ำ                                            │
│  ○ ปลายทางเป็นพร้อมเพย์เก่า                              │
│  ● อื่นๆ (กรอกเอง)                                    │
│  [___________________________________]               │
│                                                       │
│  ข้อความที่ลูกค้าจะได้รับ (preview):                     │
│  ┌────────────────────────────────────┐              │
│  │ ขออภัยค่ะ สลิปที่ส่งมาตรวจไม่ผ่าน    │              │
│  │ เหตุผล: {เหตุผล}                    │              │
│  │ ส่งสลิปใหม่ได้ในแชทนี้เลยค่ะ 🙏    │              │
│  └────────────────────────────────────┘              │
│                                                       │
│  [ยกเลิก] [ปฏิเสธ + แจ้งลูกค้า]                        │
└───────────────────────────────────────────────────────┘
```

### 🗄 DB schema

```sql
CREATE TABLE rejection_reasons (
  id SERIAL PRIMARY KEY,
  label TEXT NOT NULL,
  customer_message TEXT NOT NULL,  -- ข้อความที่ลูกค้าจะได้
  enabled BOOLEAN DEFAULT TRUE,
  sort_order INT
);
```

ลูกน้องจัดการเหตุผลและข้อความได้ผ่านอีกหน้า [Settings]

### ⚙️ Code changes

ไฟล์: `dashboard/backend/routers/payments.py` (เพิ่ม endpoint reject + customer DM via bot_messages)

### ⏱ Effort

**1 วัน**

---

## 📊 สรุป Section 02

| # | Item | Effort | Phase |
|---|---|---|---|
| 7 | ตรวจสลิป Inbox + override | 1d | A |
| 8 | Approval flow (existing) | 0d | — |
| 9 | ปฏิเสธ + custom reasons | 1d | A |
| **รวม** | | **2 วัน** | |
