# 00 — หลักการ + ข้อจำกัด (Principles)

> **Doc nature:** เอกสารฉบับนี้คือ "**รัฐธรรมนูญ**" ของโปรเจกต์ Dashboard 2.0 — ทุก spec ถัดไปต้องไม่ขัดกับข้อในนี้
>
> **Audience:** บอส (decision-maker), Claude (executor), ลูกน้องในอนาคต (operator)
>
> **Last updated:** 2026-06-26

---

## 1. บริบทธุรกิจ (ทำไมต้องทำ)

### ปัญหาที่กำลังจะเกิด
- บอส (korpaiix, tg=8502597269) กำลังจะ**ย้ายไปทำธุรกิจอื่น** ไม่มีเวลามาคุมเจริญพร
- จะส่งต่อให้**ลูกน้อง** เป็นคนคุมแทน
- ลูกน้อง**ไม่มีพื้นฐาน tech** ไม่สามารถพิมพ์คุยกับ AI เพื่อแก้ระบบได้
- ลูกน้อง**เข้าใจภาพรวมธุรกิจ + ภาษาคน** แต่ไม่สามารถจัดการ AI/code ได้

### โจทย์
สร้าง dashboard ที่**ลูกน้อง (non-tech) ใช้คนเดียวจัดการธุรกิจได้ทั้งหมด** ผ่าน UI คลิก
ไม่ต้องพิมพ์ คุย AI ไม่ต้องแก้ code

### มูลค่าธุรกิจที่ห้ามพัง
- ลูกค้าจ่ายเงิน → **บอตต้องตอบ 100%**
- VIP membership ที่จ่ายไปแล้ว → **ต้องได้ใช้จนครบกำหนด**
- ระบบส่งลิ้งกลุ่ม → **ต้องส่งให้ได้**
- คำพูดของบอตในกลุ่ม → **ห้ามเปลี่ยน accident**

---

## 2. กฎเหล็ก (Hard Constraints) — ห้ามทำลาย

### 2.1 ห้ามกระทบลูกค้าปัจจุบัน
- ลูกค้าที่ซื้อแล้ว membership อยู่ → **ห้ามให้สิทธิ์ลด**
- คำพูดบอตที่ลูกค้าคุ้นชิน → **ห้ามเปลี่ยนทันที** (ต้องผ่าน A/B หรือ canary)
- ราคาเดิม → **ห้ามขึ้น** (ลดได้ขึ้นไม่ได้)

### 2.2 ห้ามทำให้บอตบินไม่ได้
- **บอตตัวนี้สำคัญมาก ถือลูกค้าไว้เยอะ**
- ทุก change ต้อง backward compatible
- Feature flag ทุกอย่าง — เปิด/ปิดได้
- Rollback ภายใน 1 นาที (กดปุ่ม flag OFF)

### 2.3 ห้ามทับซ้อน
- มี **Source of Truth เดียว** ต่อ 1 ค่า
- ถ้าราคา VIP 300 อยู่ DB table A — table B ห้ามเก็บราคาซ้ำ
- ทุก action ต้อง sync ทั้งระบบ (sales bot, dashboard, daily report, Excel)

### 2.4 ห้ามขายแพ็กเกจให้ทีม
- ทีมขายของบอส (Ivy, Wasu, Pai) **ไม่จ่ายเงินซื้อ VIP**
- ระบบจะเห็น telegram_id ของทีม → block การจ่ายเงิน

### 2.5 ใช้ง่าย ดูง่าย เข้าใจง่าย
- ภาษาคน ไม่ใช่ jargon (เช่น "ยกระดับ VIP" ไม่ใช่ "promote tier")
- ปุ่มชัด สีตามความหมาย (เขียว=ปลอดภัย, แดง=อันตราย, ส้ม=ต้องระวัง)
- 1 หน้า 1 จุดประสงค์ — ไม่ยัด feature ทั้งโลกในหน้าเดียว

### 2.6 ทุก change ต้อง push git
- ทุก commit มี message ที่อ่านรู้เรื่อง
- ห้ามแก้บน server โดยไม่ commit
- ทุกการ deploy ต้องตามหลังการ push

---

## 3. หลักการออกแบบ (Design Principles)

### 3.1 Source of Truth Principle

```
ทุกค่าที่อาจเปลี่ยน → เก็บใน DB
ทุกค่าที่ hardcode → ต้องมีเหตุผล (เช่น secret key)

อ่าน flow:
  bot code → ถาม DB ก่อน
    └─ มีค่า? → ใช้ค่า DB
    └─ ไม่มี? → ใช้ค่า hardcode (= behavior เดิม)
```

ตัวอย่าง:
- ราคา VIP 300 → DB table `package_prices` (ถ้าไม่มี → ใช้ 300 จาก code)
- ข้อความ /start → DB table `bot_messages` (ถ้าไม่มี → ใช้ string เดิม)
- รายการ receiver → DB table `receivers` (ถ้าไม่มี → reject และแจ้ง admin)

### 3.2 Backward Compatible (Default = Old Behavior)

ทุก feature ใหม่ต้องตอบโจทย์:
> "ถ้าฉันไม่ตั้งอะไรเลย ระบบทำงานยังไง?"

**คำตอบที่ถูก:** ทำงานเหมือนเดิมก่อนมี feature นี้ (เหมือนวันที่ 2026-06-26)

**คำตอบที่ผิด:** ระบบพัง / ลูกค้าเห็นข้อความแปลก / บอตไม่ตอบ

### 3.3 Feature Flag System

ทุก feature ใหม่ต้องมี flag ใน table `feature_flags`:

```sql
CREATE TABLE feature_flags (
  flag_key VARCHAR(64) PRIMARY KEY,
  enabled BOOLEAN NOT NULL DEFAULT FALSE,
  scope VARCHAR(32) DEFAULT 'all',  -- all / admin / canary_user_ids
  canary_user_ids BIGINT[],         -- ถ้า scope=canary
  description TEXT,
  updated_at TIMESTAMP DEFAULT NOW(),
  updated_by BIGINT
);
```

Flow การเปิด feature:
1. **OFF** (default) → ระบบเดิม
2. **admin only** → ทดสอบกับบอส
3. **canary 5 users** → ทดสอบกับลูกค้าตัวอย่าง
4. **all** → เปิดเต็มที่
5. ถ้าผิดที่ขั้นไหน → กดกลับ OFF (rollback ภายใน 1 นาที)

### 3.4 Audit Log + Undo

ทุก action ที่ลูกน้องทำ → log ไป `admin_logs`:
```
admin_id   | tg ของคนทำ
action     | ชื่อ action (เช่น "promo_create")
target_type| ตารางที่กระทบ
target_id  | ID record ที่กระทบ
details    | JSONB ของค่าเดิม + ค่าใหม่
created_at | เวลา
```

ทุก action ที่**เปลี่ยนค่า** ต้องเก็บ value ก่อน-หลัง → กดปุ่ม "ย้อนกลับ" ได้

### 3.5 Validation Strict

ฟอร์มทุกหน้าต้อง validate ก่อน submit:
- ราคา = ตัวเลข > 0
- วันที่หมด > วันที่เริ่ม
- ราคาโปร < ราคาปกติ
- ข้อความ HTML — strip tag อันตราย (`<script>`, `<iframe>`)
- ID telegram = ตัวเลข + ตรวจว่าเป็น admin อยู่แล้วหรือไม่

ถ้า validate ไม่ผ่าน → **ไม่ให้กด save** + แสดง error แดงชัดเจน

### 3.6 Preview Before Save

ทุก change ที่กระทบลูกค้าต้องมีปุ่ม "👀 ดูตัวอย่าง"
- preview ใน UI ก่อนกด save
- preview text ใน mock Telegram bubble
- preview banner ใน mock menu

---

## 4. หลักความปลอดภัย (Safety)

### 4.1 Tiered Permission

ลูกน้องไม่ใช่บอส — ต้องจำกัด:

| Role | สิทธิ์ |
|---|---|
| **owner** (บอส) | ทำได้ทุกอย่าง + แก้ system prompt + เพิ่ม/ลบ admin |
| **admin** (ลูกน้อง) | ทุกอย่างยกเว้น Prae system prompt + admin IDs + DB direct |
| **staff** (ถ้ามีในอนาคต) | view only + อนุมัติสลิป + ตอบ SOS |

### 4.2 Confirm on Destructive Actions

ทุก action ที่กลับไม่ได้ ต้อง confirm 2 ครั้ง:
- ลบโปรที่ active อยู่ → "แน่ใจไหม? ลูกค้าอาจเห็นโปรหายทันที"
- ลบรางวัล gacha ที่มีคนได้ไปแล้ว → soft delete only (disable)
- เปลี่ยน receiver → "ลูกค้าจะเริ่มโอนเข้าบัญชีใหม่ตั้งแต่ตอนนี้"

### 4.3 No Direct DB Access

- ลูกน้อง**ไม่มีรหัส DB**
- ทุก operation ผ่าน FastAPI endpoint ที่ validate แล้ว
- ไม่มี SQL textarea ในหน้า UI

### 4.4 Daily Backup + Restore Test

- backup DB ทุก 19:00 → DigitalOcean Spaces (มีอยู่แล้ว)
- เทสต์ restore เดือนละครั้ง (verify backup ใช้ได้จริง)

---

## 5. Source of Truth ที่จะ migrate

| ค่าปัจจุบัน hardcode ที่ไหน | จะย้ายไป DB table |
|---|---|
| ราคาแพ็กเกจ (`packages.tier_price`) | คงไว้ + override layer ใหม่ `package_prices_override` |
| Lucky 6/Flash/Birthday เงื่อนไข | `promotion_campaigns` (มีแล้ว) + ทำ wizard |
| ข้อความ /start | `bot_messages` (สร้างใหม่) |
| ข้อความหน้าแพ็กเกจ | `bot_messages` |
| ปุ่ม inline labels | `bot_messages` (เก็บ JSONB) |
| Prae system prompt | `prae_prompt_versions` (มีแล้ว) |
| Receiver pool | `receivers` (มีแล้ว) |
| Gacha price | `gacha_spin_pricing` (สร้างเสร็จแล้ววันนี้) |
| Gacha prize pool | `gacha_prize_pool` (มีแล้ว) |
| Admin telegram IDs | `.env` (ENV) + UI ใน dashboard |
| Group IDs | `group_registry` (มีแล้ว) |

---

## 6. หลักการ Implementation

### 6.1 Staging First — Production Last

```
ขั้น 1: ออกแบบ + spec doc (markdown)
ขั้น 2: build mockup UI ใน staging port 8011 (ไม่ต่อ DB)
ขั้น 3: บอส review + comment
ขั้น 4: แก้ตาม comment → repeat ขั้น 2-3
ขั้น 5: lock spec
ขั้น 6: implement ใน production behind feature flag (OFF)
ขั้น 7: บอสเปิด flag canary → test กับตัวเอง
ขั้น 8: ขยาย scope ทีละ 10% → 50% → 100%
ขั้น 9: ถ้าทุกอย่าง OK → ลบ code เก่า (clean up)
```

### 6.2 No Big-Bang Release

- ห้าม merge feature ใหญ่รวดเดียว
- ทำ 1 feature complete cycle ก่อน → start อันถัดไป
- ทุก feature มี test (manual + automated)

### 6.3 Test Coverage

- **Unit test**: validation rules, business logic
- **Integration test**: API endpoints
- **E2E manual test**: บอสคลิกในหน้าจริง
- **Canary**: เปิดให้บอสคนเดียวก่อน

### 6.4 Documentation Always Current

- ทุก feature ที่ build เสร็จ → update spec doc ใน `docs/handoff-spec/`
- ทุก env variable ใหม่ → update `.env.example`
- ทุก DB table ใหม่ → update `SYSTEM_ARCHITECTURE.md`

---

## 7. ข้อตกลงในการสื่อสาร

### 7.1 ภาษา
- เอกสาร spec: **ภาษาไทย** เป็นหลัก + อังกฤษเฉพาะ technical term
- UI: **ภาษาไทยทั้งหมด**
- Comment ใน code: ไทย/อังกฤษได้ — เลือกที่อ่านง่ายกว่า

### 7.2 บอสกับ Claude
- บอสตัดสินใจ — Claude execute
- ก่อนทำงานใหญ่ — Claude ถามก่อน ห้ามด่วน
- บอสไม่อยู่ → Claude หยุดรอ ไม่ assume

### 7.3 ลูกน้องกับระบบ
- ลูกน้องคลิกใน dashboard เท่านั้น
- ห้ามให้ลูกน้อง SSH, ห้ามให้รหัส DB, ห้ามให้บอท token
- ทุก action ที่ลูกน้องทำ → audit log

---

## 8. รายการสิ่งที่ตัดออกจาก scope

| ฟีเจอร์ | สถานะ | เหตุผล |
|---|---|---|
| Refund / คืนเงิน | ❌ ตัด | บอสไม่ทำ refund |
| Affiliate / โปรแกรมแนะนำ | ❌ ตัด | ยังไม่จำเป็น |
| A/B testing UI | ❌ ตัด | ลูกน้องอ่านผลไม่เป็น |
| Multi-tenant | ❌ ตัด | มีแค่เจริญพร 1 brand |
| ขายข้ามแพลตฟอร์ม (Shopee/Lazada) | ❌ ตัด | ไม่ใช่กลุ่มเป้า |

---

## 9. รายการสิ่งที่ห้ามแตะ (Frozen)

| สิ่ง | สถานะ | เหตุผล |
|---|---|---|
| Dashboard port 8010 (production) | 🟢 FROZEN | จะแตะหลัง spec lock + canary |
| DB ลูกค้าเดิม (subscriptions, users, payments) | 🟢 FROZEN | ห้ามแก้ schema ที่กระทบ |
| Bot tokens | 🔒 SECRET | บอสถือคนเดียว ลูกน้องไม่เห็น |
| Bot ตัวที่ run อยู่ (sales/admin/content/discord/guardian) | 🟢 FROZEN | จะปรับให้อ่าน DB ก่อน (backward compat) |
| Group IDs (real groups) | 🟢 FROZEN | ใช้ของจริงต่อ |

---

## 10. รายการสิ่งที่จะสร้างใหม่

| สิ่ง | สถานะ | scope |
|---|---|---|
| Dashboard 2.0 UI | 🆕 NEW | port 8011 (staging) → 8010 (prod) |
| Promo Wizard | 🆕 NEW | แทน Lucky/Flash/Birthday hardcode |
| Bot Message Library | 🆕 NEW | DB table + UI editor |
| Source of Truth migration | 🆕 NEW | ค่อยๆ ย้าย hardcode → DB |
| Feature Flag system | 🆕 NEW | table + UI toggle |
| Audit log viewer | 🆕 NEW | ดูประวัติ + undo |

---

## ภาคผนวก: คำพูดบอสที่บันทึกไว้

> "ห้ามกระทบลูกค้าที่มีอยู่ตอนนี้"
> "ห้ามพัง ห้ามบัก"
> "ต้องเช็คงานที่ตัวเองทำ"
> "บอตตัวนี้บินไม่ได้สำคัญมาก ถือลูกค้าไว้เยอะมาก"
> "อย่าแก้ลวก ไปดีบั๊กจริงๆ ลึก"
> "ห้ามขายแพ็กเกจให้ทีม"
> "push git ทุกครั้งที่แก้งานสิครับ"
> "ห้ามทับซ้อน"
> "ใช้ง่าย ดูง่าย เข้าใจง่าย"
> "ทำเสร็จแล้ว test review ลึก ดีบั๊กและจัดการให้ดีทั้งระบบ"
> "ถ้ามี action ในที่ใดที่หนึ่ง ทุกที่ต้องสอดคล้องกันทั้งหมด"
> "ลูกน้องไม่มีความรู้เรื่อง AI ไม่สามารถมานั่งทำแบบนี้ได้"
