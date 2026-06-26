# 06 — บอกข่าว (Broadcast)

> ส่งข้อความหา**หลายคนพร้อมกัน** — กลุ่ม, DM, Discord
>
> **5 items** · [26] DM ลูกค้า → [27] โพสต์กลุ่ม → [28] Discord post → [29] daily content → [30] promo banner

(ตัด [29] schedule — รวมใน scheduled)

---

## [26] Broadcast DM (Direct Message ลูกค้า)

### 🔍 ตอนนี้เป็นยังไง

มีหน้า 📣 บรอดแคสต์ใน dashboard (Sprint 2.6 / 2.4)
- ส่ง DM หาลูกค้า
- Filter: tier, last active, total spent
- Preview ก่อน send
- มี audit log

ปัญหา:
- ลูกน้อง confused ระหว่าง 'broadcast' กับ 'group broadcast'
- ไม่มี **schedule** (ส่งทีหลัง)
- ไม่มี **A/B subject test** (ตัดออกแล้ว)

### 📝 ลูกน้องเห็นอะไร

```
┌─── 📣 Broadcast DM ─────────────────────────────────┐
│                                                       │
│  ขั้น 1: เลือกใครจะได้รับ                              │
│                                                       │
│  Preset filters:                                      │
│  ○ ทุกคน (1,234 คน)                                  │
│  ○ VIP active (456 คน)                              │
│  ○ VIP จะหมดใน 7 วัน (89 คน)                         │
│  ○ ลูกค้าหมดอายุแล้ว (567 คน)                          │
│  ● Custom                                            │
│                                                       │
│  Custom (กรองเอง):                                    │
│   Tier:    [☑ 100] [☑ 300] [☑ 500] [☑ 1299]         │
│   Status:  [☑ active] [☐ expired]                    │
│   ยอดรวม:  ≥ [______] ≤ [______]                     │
│   เคย active ใน: [☑ 7d] [☐ 30d] [☐ 90d]            │
│   Tag:    [☐ VVIP] [☐ Churn risk]                   │
│                                                       │
│  → ผลลัพธ์: 156 คน                                    │
│                                                       │
│  ขั้น 2: เขียนข้อความ                                  │
│  [Textarea — รองรับ HTML]                            │
│                                                       │
│  ขั้น 3: ปุ่มที่ใส่ใต้ข้อความ (optional)               │
│  [+ เพิ่มปุ่ม]                                        │
│                                                       │
│  ขั้น 4: ส่งเมื่อไหร่                                   │
│  ● ส่งทันที                                          │
│  ○ ตั้งเวลา: [📅 2026-06-27 09:00]                  │
│                                                       │
│  ขั้น 5: Preview + confirm                            │
│  [👀 ดูตัวอย่าง]                                       │
│  [⚠️ ตรวจคำที่อาจทำให้บอตโดน ban]                       │
│  [📤 ส่ง 156 ข้อความ]                                  │
│                                                       │
└───────────────────────────────────────────────────────┘
```

### 🗄 DB schema

ใช้ `broadcasts` (มีแล้ว) + เพิ่ม:
```sql
ALTER TABLE broadcasts ADD COLUMN scheduled_for TIMESTAMP;
ALTER TABLE broadcasts ADD COLUMN status VARCHAR(20) DEFAULT 'draft'; -- draft/scheduled/running/done
```

### ⚠️ Safety

- Rate limit: 30 msg/sec (Telegram limit ~30/sec)
- Auto-skip ผู้ที่ blocked bot
- Auto-skip user banned
- มี kill switch (admin กดหยุดได้)

### ⏱ Effort
**2 วัน**

---

## [27] โพสต์ในกลุ่ม VIP (Group Broadcast)

### 🔍 ตอนนี้เป็นยังไง

มี Group Broadcast UI (Task #296) ส่งโพสต์ในกลุ่ม VIP ผ่าน Guardian bot

ปัญหา:
- ลูกน้องเลือกกลุ่มไม่ใช่ — มี dropdown ยาวเฟื้อย
- รูปต้องอัปโหลด — flow แปลก

### 📝 ลูกน้องเห็นอะไร

```
┌─── 📨 โพสต์ในกลุ่ม ─────────────────────────────────┐
│                                                       │
│  ขั้น 1: เลือกกลุ่มที่จะโพสต์                            │
│                                                       │
│  Preset:                                              │
│  ○ ทุกกลุ่ม VIP                                       │
│  ○ ทุกกลุ่มฟรี (FREE1-FREE19)                         │
│  ○ ทุกกลุ่ม GOD                                       │
│  ● Custom                                            │
│                                                       │
│  Custom (เลือกเอง):                                   │
│   ☑ VIP_1, VIP_2                                    │
│   ☑ FREE1, FREE2 ...                                │
│                                                       │
│  → ผลลัพธ์: 14 กลุ่ม                                  │
│                                                       │
│  ขั้น 2: เนื้อหา                                       │
│  รูป: [📎 อัปโหลด] หรือ URL                          │
│  ข้อความ: [Textarea]                                  │
│  ปุ่ม: [+ เพิ่มปุ่ม]                                   │
│                                                       │
│  ขั้น 3: เมื่อไหร่                                     │
│  ● ทันที  ○ ตั้งเวลา                                  │
│                                                       │
│  ขั้น 4: Preview                                      │
│  [👀 mock ใน Telegram]                                │
│  [📤 โพสต์ 14 กลุ่ม]                                  │
└───────────────────────────────────────────────────────┘
```

### 🗄 DB schema

ใช้ `group_broadcast` (มีแล้ว) — แค่ปรับ UI

### ⏱ Effort
**1 วัน** (UI polish)

---

## [28] โพสต์ใน Discord

### 🔍 ตอนนี้เป็นยังไง

Discord ใช้ภายในทีม → ไม่ใช่ลูกค้า → ไม่ต้องมี broadcast UI

**ส่วน notification** สำหรับทีม (รายงานยอด/marketing) → auto จาก Discord bot

✅ ลูกน้องไม่ต้องโพสต์ใน Discord เอง

### ⏱ Effort
**0 วัน** (out of scope)

---

## [29] Daily Content (โพสต์รายวันในกลุ่ม)

### 🔍 ตอนนี้เป็นยังไง

มี content-bot ที่โพสต์รูป + แคปชั่นในกลุ่มต่างๆ ตามตารางที่บอสตั้ง

ปัญหา:
- ตารางตั้งใน DB ตรงๆ — ลูกน้องแก้ไม่ได้
- ไม่มี UI

### 📝 ลูกน้องเห็นอะไร

```
┌─── 📅 Daily Content Schedule ───────────────────────┐
│                                                       │
│  รายการโพสต์ที่ตั้งไว้:                                 │
│                                                       │
│  🕐 09:00 ทุกวัน → กลุ่ม VIP_1, VIP_2 (โพสต์รูป)       │
│  🕐 12:00 จันทร์,พฤหัส → กลุ่ม FREE1-19 (รูปวันใหม่)    │
│  🕐 18:00 ทุกวัน → กลุ่ม GOD_1-3 (รูปพิเศษ)            │
│                                                       │
│  [➕ เพิ่มตารางใหม่]                                   │
│                                                       │
│  Library รูป:                                         │
│  [📁 มี 234 รูป] [⬆️ เพิ่มรูป]                        │
│                                                       │
└───────────────────────────────────────────────────────┘
```

### ⏱ Effort
**2 วัน**

---

## [30] Promo Banner ในเมนู

### 🔍 ตอนนี้เป็นยังไง

เพิ่งทำเสร็จวันนี้ (Task #305) — banner แสดงโปรในหน้า /packages

อยู่ใน scope [3] หน้าแพ็กเกจ + [Promo Wizard]

### ⏱ Effort
**0 วัน** (in [3])

---

## 📊 สรุป Section 06

| # | Item | Effort | Phase |
|---|---|---|---|
| 26 | Broadcast DM + schedule | 2d | A |
| 27 | Group broadcast polish | 1d | A |
| 28 | Discord (out of scope) | 0d | — |
| 29 | Daily content schedule | 2d | B |
| 30 | Promo banner (in [3]) | 0d | — |
| **รวม** | | **5 วัน** | |
