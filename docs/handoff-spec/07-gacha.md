# 07 — กาชา + กิจกรรม (Gacha + Events)

> ระบบกาชา + Promo Wizard ใหญ่ (แทน Lucky/Flash/Birthday hardcode)
>
> **6 items** · [31] spin price → [32] prize pool → [33] probability → [34] events → [35] claim → [36] discount redeem

---

## ⭐ [Promo Wizard] — งานใหญ่ที่สุดของ Phase B

### 🎯 จุดประสงค์

แทน Lucky 6 / Flash / Birthday / Endmonth ที่ hardcode ใน `shared/endmonth_vip_promo.py` ฯลฯ

ลูกน้องตั้งโปรเองได้โดยไม่ต้องแตะ code

### 📝 ลูกน้องเห็นอะไร

```
┌─── 🎁 Promo Wizard ─────────────────────────────────┐
│                                                       │
│  [📣 + จัดโปรใหม่]                                    │
│                                                       │
│  โปรที่ active ตอนนี้:                                 │
│  ┌────────────────────────────────────┐              │
│  │ 🔥 Lucky 6 — VIP 300 → 199           │              │
│  │ เริ่ม 26 มิ.ย. → หมด 6 ก.ค.             │              │
│  │ ส่งใน: [Bot menu] [DM ลูกค้าเก่า]       │              │
│  │ [✏️ แก้] [🗑 ปิด]                       │              │
│  └────────────────────────────────────┘              │
│                                                       │
│  ┌────────────────────────────────────┐              │
│  │ ⚡ Flash 30 นาที — GOD 1299 → 999     │              │
│  │ active 14:00-14:30 (3 นาที)         │              │
│  │ [✏️ แก้] [🗑 ปิด]                       │              │
│  └────────────────────────────────────┘              │
│                                                       │
│  โปรหมดแล้ว / รออนุมัติ:                              │
│  ...                                                  │
│                                                       │
│  📊 30 วันที่ผ่านมา:                                  │
│   - Lucky 6 รัน 3 ครั้ง · ขาย 47 ครั้ง                │
│   - Flash รัน 12 ครั้ง · ขาย 89 ครั้ง                  │
└───────────────────────────────────────────────────────┘

[📣 + จัดโปรใหม่] →
┌─── Wizard Step 1 ──────────────────────────────────┐
│                                                       │
│  เลือกประเภทโปร:                                       │
│                                                       │
│  ● 🔥 ลดราคา (Discount)                              │
│  ○ ⚡ Flash Sale (จำกัดเวลา + จำกัดจำนวน)              │
│  ○ 🎂 วันเกิด (ส่ง DM 1 ครั้งต่อปี)                   │
│  ○ 🎁 ของแถม (ซื้อ X แถม Y)                          │
│  ○ 🎰 กิจกรรม Gacha (เปลี่ยน prize pool ชั่วคราว)      │
│                                                       │
│  [ถัดไป →]                                            │
└───────────────────────────────────────────────────────┘

→ Step 2: เลือกแพ็กเกจ
→ Step 3: ตั้งราคา + เวลา + กลุ่มที่จะส่ง
→ Step 4: ตัวอย่าง (preview ใน bot menu + DM)
→ Step 5: Launch หรือ Save Draft
```

### 🗄 DB schema

ขยาย `promotion_campaigns` (มีแล้ว) + เพิ่ม `promo_type`:
```sql
ALTER TABLE promotion_campaigns ADD COLUMN promo_type VARCHAR(20) NOT NULL DEFAULT 'discount';
-- discount / flash / birthday / bundle / gacha_event

ALTER TABLE promotion_campaigns ADD COLUMN flash_total_slots INT;
ALTER TABLE promotion_campaigns ADD COLUMN flash_sold_slots INT DEFAULT 0;
ALTER TABLE promotion_campaigns ADD COLUMN bundle_extra_package_id INT;
```

### ⚙️ Code changes

เปลี่ยน:
- `shared/endmonth_vip_promo.py` → อ่านจาก `promotion_campaigns` (deprecate hardcoded)
- `is_lucky_6_active()` → check campaign with `promo_type='discount'` + name LIKE '%Lucky%'
- `is_birthday_promo_active()` → check campaign with `promo_type='birthday'`

Feature flag: `PROMO_WIZARD_ENABLED` — ถ้า OFF → ใช้ hardcoded เดิม

### ⏱ Effort

**5 วัน** (DB + 5-step wizard + integration)

---

## [31] Spin Pricing (ราคากาชา/หมุน)

### 🔍 ตอนนี้เป็นยังไง

✅ เพิ่งทำเสร็จวันนี้ (Task #303)
- ปุ่ม 💰 ราคา/หมุน ใน Gacha admin
- Edit GACHA_1/GACHA_3/GACHA_10 ราคา
- DB-backed: `gacha_spin_pricing` table
- 60-sec cache

### ⏱ Effort
**0 วัน** (DONE)

---

## [32] Prize Pool (จัดการรางวัล)

### 🔍 ตอนนี้เป็นยังไง

✅ เพิ่งทำเสร็จวันนี้ (Task #303 + #306)
- เพิ่ม / ลบ / เปิดปิดรางวัล
- Soft delete ถ้ามีคนได้ไปแล้ว
- DB: `gacha_prize_pool`

### ⏱ Effort
**0 วัน** (DONE)

---

## [33] Probability Balance

### 🔍 ตอนนี้เป็นยังไง

มี probability_pct column — แก้ผ่าน UI ที่ทำแล้ว

ปัญหา:
- ลูกน้องไม่รู้ว่าตั้งยังไงให้ RTP เหมาะสม
- ไม่มี calculator: 'ลูกค้าจ่าย ฿890/10 หมุน → expected return เท่าไหร่'

### 📝 ลูกน้องเห็นอะไร

```
┌─── 🎰 Gacha Probability Calculator ────────────────┐
│                                                       │
│  Bundle: 10 หมุน · ฿890                              │
│                                                       │
│  รางวัลที่มีในตอนนี้:                                    │
│  • COIN_50 (฿50)         · 70%                       │
│  • COIN_200 (฿200)        · 25%                       │
│  • SUB_VIP (฿300)         · 4.9%                      │
│  • SUB_GOD (฿1,299)        · 0.1%                     │
│                                                       │
│  📊 Expected return:                                  │
│   ต่อหมุน: 50×0.7 + 200×0.25 + 300×0.049 + 1299×0.001    │
│           = 35 + 50 + 14.7 + 1.3 = ฿101/หมุน           │
│                                                       │
│   ต่อ 10 หมุน: ฿1,010                                  │
│   ลูกค้าจ่าย: ฿890                                     │
│   📉 RTP: 113.5%                                      │
│                                                       │
│   ⚠️ RTP > 100% — บริษัทขาดทุน!                       │
│                                                       │
│  [⚙️ ปรับ probability]                                │
└───────────────────────────────────────────────────────┘
```

### ⏱ Effort
**1 วัน** (calculator + visualization)

---

## [34] Limited-time Event

### 🔍 ตอนนี้เป็นยังไง

ไม่มี — Gacha pool เดียวกันตลอด

### 📝 ลูกน้องเห็นอะไร

```
┌─── 🎪 Limited-time Gacha Event ────────────────────┐
│                                                       │
│  Event ที่กำลังจะมี:                                    │
│                                                       │
│  🎊 Anniversary Event (1-7 ก.ค.)                     │
│   - เพิ่ม SUB_VIP probability 5% → 10%               │
│   - เพิ่มรางวัลใหม่: ของแถมเล็กๆ                       │
│   [✏️ แก้] [🗑 ยกเลิก]                                │
│                                                       │
│  [➕ สร้าง event ใหม่]                                │
└───────────────────────────────────────────────────────┘
```

### ⏱ Effort
**3 วัน** (event override system)

---

## [35] Prize Claim Flow

### 🔍 ตอนนี้เป็นยังไง

Event-driven gacha delivery (Task #224) — ลูกค้ากดหมุน → ได้รางวัลทันที

✅ ทำงาน OK — ไม่ต้องแก้

### ⏱ Effort
**0 วัน**

---

## [36] Discount Redemption (เครดิตจากกาชา)

### 🔍 ตอนนี้เป็นยังไง

มี discount_button ใน sales bot — ลูกค้ามี balance > 0 → กดใช้ส่วนลด

### 📝 ลูกน้องเห็นอะไร

```
┌─── 💰 Discount Credits ─────────────────────────────┐
│                                                       │
│  📊 รวมทั้งระบบ:                                       │
│   - Outstanding balance: ฿45,200                     │
│   - คิด vs liability tax: ✅ ปลอดภัย                  │
│                                                       │
│  ลูกค้ามียอด balance > 0:                              │
│  [Filter: tier / balance amount]                      │
│                                                       │
│  @user789 · ฿200 [💸 reset]                          │
│  @user456 · ฿150 [💸 reset]                          │
│  ...                                                  │
│                                                       │
└───────────────────────────────────────────────────────┘
```

### ⏱ Effort
**1 วัน** (UI only — backend exists)

---

## 📊 สรุป Section 07

| # | Item | Effort | Phase |
|---|---|---|---|
| Promo Wizard ⭐ | แทน Lucky/Flash/Birthday | 5d | B |
| 31 | Spin pricing (DONE) | 0d | — |
| 32 | Prize pool (DONE) | 0d | — |
| 33 | Probability calculator | 1d | B |
| 34 | Limited-time event | 3d | B |
| 35 | Claim (existing) | 0d | — |
| 36 | Discount UI | 1d | A |
| **รวม** | | **10 วัน** | |
