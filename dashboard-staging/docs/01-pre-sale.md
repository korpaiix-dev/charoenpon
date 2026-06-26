# 01 — ก่อนขาย (Pre-Sale)

> ส่วนที่ลูกค้าเจอ**ก่อนตัดสินใจซื้อ** — สำคัญสุดเพราะกระทบ conversion
>
> **6 items** · [1] welcome → [2] menu → [3] packages → [4] confirm → [5] payment info → [6] slip prompt

---

## [1] ข้อความต้อนรับ /start

### 🔍 ตอนนี้เป็นยังไง

ลูกค้ากด /start → บอตตอบ **ข้อความเดียวกันทุกคน** (hardcode ใน `start.py:23`):

```
หวัดดีค่า~ ยินดีต้อนรับสู่ กลุ่ม VIP เจริญพร 🎉
แพรเองค่า 😊 มีอะไรให้ช่วยบอกได้เลยนะ
จะดูแพ็กเกจ จะสมัคร หรือมีคำถามอะไร กดด้านล่างเลยค่า 👇
```

ปัญหา:
- ลูกน้องอยากเปลี่ยนคำต้อนรับ → ต้องแก้ code
- ลูกค้าใหม่ vs ลูกค้าเก่ากลับมา → **ใช้ข้อความเดียวกัน** (โอกาส personalize หาย)

### 📝 ลูกน้องเห็นอะไรใน dashboard

```
┌─── 💬 ข้อความบอท / ข้อความ /start ────────────────┐
│                                                  │
│  📩 ข้อความต้อนรับ                                │
│  ─────────────────                              │
│                                                  │
│  [Tab: ลูกค้าใหม่] [Tab: ลูกค้าเก่ากลับมา]            │
│                                                  │
│  ┌────────────────────────────────────────┐    │
│  │ หวัดดีค่า~ ยินดีต้อนรับสู่ <b>กลุ่ม VIP        │    │
│  │ เจริญพร</b> 🎉                            │    │
│  │                                          │    │
│  │ แพรเองค่า 😊 ...                         │    │
│  └────────────────────────────────────────┘    │
│                                                  │
│  Tags ที่ใช้ได้:                                 │
│   {customer_name}  — ชื่อลูกค้า                 │
│   {greeting}       — สวัสดีตอนเช้า/บ่าย/ค่ำ    │
│                                                  │
│  [👀 ดูตัวอย่างใน Telegram]                       │
│  [⏮ ย้อนกลับ version ก่อน]                       │
│  [💾 บันทึก]                                     │
│                                                  │
└──────────────────────────────────────────────────┘
```

### 🎯 ลูกน้องทำอะไรได้

- แก้ข้อความสำหรับลูกค้าใหม่
- แก้ข้อความสำหรับลูกค้าเก่ากลับมา (auto-detect: tg_id เคยมีใน `users` table)
- ใช้ HTML tag: `<b>`, `<i>`, `<code>`, `<a href>`
- ใช้ placeholder: `{customer_name}`, `{greeting}`
- **Preview** ใน mock Telegram bubble ก่อน save
- **Undo** ย้อนกลับ version ก่อนหน้า

### 🗄 DB schema

```sql
CREATE TABLE bot_messages (
  message_key VARCHAR(64) PRIMARY KEY,  -- เช่น 'welcome_new', 'welcome_returning'
  content_html TEXT NOT NULL,
  description TEXT,                     -- คำอธิบายให้ลูกน้องเข้าใจ
  available_tags JSONB,                 -- ['customer_name', 'greeting']
  updated_at TIMESTAMP DEFAULT NOW(),
  updated_by BIGINT
);

CREATE TABLE bot_message_versions (
  id SERIAL PRIMARY KEY,
  message_key VARCHAR(64) REFERENCES bot_messages(message_key),
  content_html TEXT NOT NULL,
  changed_at TIMESTAMP DEFAULT NOW(),
  changed_by BIGINT
);
```

### ⚙️ Code changes

ไฟล์: `bots/sales_bot/handlers/start.py`

แก้ตอน `start_command`:
```python
# OLD:
WELCOME_TEXT = '...' # hardcode

# NEW (read DB first, fallback to hardcode):
async def _get_welcome_text(is_new_user: bool, user: User) -> str:
    key = 'welcome_new' if is_new_user else 'welcome_returning'
    db_msg = await get_bot_message(key)
    if db_msg:
        return render_placeholders(db_msg, user)
    return WELCOME_TEXT  # fallback = current behavior
```

### 🔗 Links to other items

- กระทบ **[2] Main menu buttons** — แสดงพร้อมข้อความนี้
- กระทบ **[42] Prae prompt** — Prae ต้องรู้ว่าใช้ข้อความไหน
- กระทบ **[28] Daily report** — track ว่าลูกค้าใหม่/เก่ากี่คน

### ⚠️ Risk + Mitigation

| Risk | Mitigation |
|---|---|
| ลูกน้องใส่ HTML ผิด → บอตส่งไม่ได้ → /start ไม่ตอบ | Validate HTML ด้วย parser ก่อน save + fallback ถึง hardcode ถ้า parse error |
| ลูกน้องลบข้อความเป็นว่าง | Validation: length > 10 chars |
| Placeholder ผิด ({wrong_tag}) | preview render ทันที → แสดง warning ถ้าไม่ใช่ tag ที่อนุญาต |

### ⏱ Effort

**1 วัน** (DB + API + UI + integration)

---

## [2] ปุ่มเมนูหลัก (Inline Buttons)

### 🔍 ตอนนี้เป็นยังไง

ปุ่ม inline ใต้ข้อความต้อนรับ — **dynamic ตาม user state** แล้ว (ดี!):

ปุ่มที่มี:
1. ⚡ FLASH SALE (ถ้า active) - dynamic
2. 🆙 อัพเกรดเป็น GOD MODE (ถ้า VIP active) - dynamic
3. 🎰 VIPมีคนชัก ฿100
4. 💰 ส่วนลดของฉัน ฿X (ถ้า balance > 0) - dynamic
5. 🎁 เติมสิทธิ์หมุนกาชาปอง
6. 📦 ดูแพ็กเกจ
7. 📊 ข้อมูลของฉัน (WebApp)
8. 🎁 ชวนเพื่อน ได้ VIP ฟรี!
9. 📋 เช็คเครดิต/รีวิว — URL hardcode
10. 👀 ดูตัวอย่างงาน — URL hardcode
11. 🆓 ห้องฟรี — URL hardcode
12. 👩‍💼 ติดต่อแอดมิน — URL @sperm6969 hardcode

ปัญหา:
- URL hardcode ปุ่มที่ 9-12 → ลูกน้องเปลี่ยนกลุ่มฟรี/แอดมินไม่ได้
- ลำดับปุ่ม hardcode → ลูกน้องเรียงใหม่ไม่ได้
- ข้อความปุ่ม hardcode → แก้ 📦 ดูแพ็กเกจ → 🛍 เลือกของ ไม่ได้

### 📝 ลูกน้องเห็นอะไรใน dashboard

```
┌─── 💬 ข้อความบอท / ปุ่มเมนูหลัก ──────────────────────┐
│                                                       │
│  🎮 ปุ่มที่แสดงในข้อความ /start                          │
│  ─────────────────────────────────                  │
│                                                       │
│  [↕] [✏️] [🗑] ⚡ FLASH SALE — กำลังลด!                │
│       เงื่อนไข: มี flash sale active                  │
│                                                       │
│  [↕] [✏️] [🗑] 📦 ดูแพ็กเกจ                            │
│       Action: เปิดหน้าแพ็กเกจ                         │
│                                                       │
│  [↕] [✏️] [🗑] 📋 เช็คเครดิต/รีวิว                       │
│       URL: https://t.me/+hv7uXYj4bxFhODZl           │
│                                                       │
│  [↕] [✏️] [🗑] 🆓 ห้องฟรี                              │
│       URL: https://t.me/addlist/w0YSyuHC_aE2ZGVl    │
│                                                       │
│  [↕] [✏️] [🗑] 👩‍💼 ติดต่อแอดมิน                          │
│       URL: https://t.me/sperm6969                    │
│                                                       │
│  [➕ เพิ่มปุ่ม]                                        │
│                                                       │
│  💡 [↕] ลากเรียงใหม่ · [✏️] แก้ · [🗑] ลบ              │
│                                                       │
│  [👀 ดูตัวอย่าง] [💾 บันทึก]                            │
└───────────────────────────────────────────────────────┘
```

### 🎯 ลูกน้องทำอะไรได้

- เพิ่มปุ่มใหม่ (เลือก: link / callback)
- แก้ข้อความปุ่ม + emoji
- เปลี่ยน URL ของปุ่ม link
- ลากเรียงลำดับใหม่ (drag-drop)
- ลบปุ่ม
- **ไม่ลบปุ่มที่สำคัญได้** (เช่น 'ดูแพ็กเกจ') → validation บล็อก

### 🗄 DB schema

```sql
CREATE TABLE bot_menu_buttons (
  id SERIAL PRIMARY KEY,
  menu_key VARCHAR(32) NOT NULL,  -- 'start_main', 'packages_list'
  position INT NOT NULL,
  label TEXT NOT NULL,
  action_type VARCHAR(16) NOT NULL,  -- 'callback' / 'url' / 'webapp'
  action_value TEXT NOT NULL,        -- callback_data / URL / webapp URL
  condition_key VARCHAR(64),         -- 'flash_active' / 'vip_active' / 'balance_gt_0' / null
  is_protected BOOLEAN DEFAULT FALSE, -- TRUE = ลูกน้องลบไม่ได้
  enabled BOOLEAN DEFAULT TRUE,
  updated_at TIMESTAMP DEFAULT NOW(),
  updated_by BIGINT
);
```

### ⚙️ Code changes

ไฟล์: `bots/sales_bot/handlers/start.py` → `_build_main_keyboard`

แก้จาก hardcode เป็น loop จาก DB:
```python
async def _build_main_keyboard(telegram_id):
    buttons = await get_menu_buttons('start_main', user=telegram_id)
    rows = []
    for btn in buttons:
        if btn.condition_key and not await check_condition(btn.condition_key, telegram_id):
            continue  # skip
        rows.append(build_telegram_button(btn))
    if not rows:
        return DEFAULT_HARDCODED_KEYBOARD  # fallback
    return InlineKeyboardMarkup(rows)
```

### 🔗 Links to other items

- กระทบ **[1] /start** — ปุ่มแสดงพร้อมข้อความ
- กระทบ **[3] หน้าแพ็กเกจ** — ปุ่ม 'ดูแพ็กเกจ' link มาที่นี่
- กระทบ **[26] Broadcast** — ใช้ปุ่มแบบเดียวกันใน broadcast
- กระทบ **[42] Prae prompt** — Prae ต้องรู้ปุ่มที่มี

### ⚠️ Risk + Mitigation

| Risk | Mitigation |
|---|---|
| ลูกน้องลบปุ่มสำคัญหมด → menu ว่าง | is_protected=TRUE บล็อกลบ + fallback hardcode |
| ลูกน้องใส่ URL ผิด → ลูกค้ากดแล้วเด้งหน้า 404 | validate URL format + ทดสอบกดได้ (head check) |
| ลำดับปุ่มสับสน → ลูกค้าหาไม่เจอ | preview ก่อน save + position drag-drop UI |

### ⏱ Effort

**2 วัน**

---

## [3] หน้าแพ็กเกจ (Package Menu)

### 🔍 ตอนนี้เป็นยังไง

ลูกค้ากด 📦 ดูแพ็กเกจ → บอตแสดง:
- โปร Flash Sale (ถ้ามี)
- รายการแพ็กเกจ 5 อัน (VIP 300, OF+VIP 500, GOD 1299, GOD ถาวร 2499, TIER_100 ห้องมีคนชัก)
- ราคาเดิม + ราคาโปร (ถ้ามีโปร)
- ปุ่มเลือกแพ็กเกจ

ปัญหา:
- ข้อความ header / footer hardcode
- ราคาในข้อความ vs ราคาจริงต้อง sync มือ (เสี่ยงทับซ้อน)
- โปรพิเศษวันนี้ — ส่วนที่ผมเพิ่งทำ (vันที่ 26 มิ.ย.) แต่ยังไม่ครบ

### 📝 ลูกน้องเห็นอะไรใน dashboard

```
┌─── 💬 ข้อความบอท / หน้าแพ็กเกจ ──────────────────────┐
│                                                       │
│  📦 ข้อความนำหน้าแพ็กเกจ                                │
│  ─────────────────────────                          │
│  ┌────────────────────────────────────────┐         │
│  │ <b>🎀 แพ็กเกจของเจริญพรค่า~</b>            │         │
│  │ เลือกแพ็กเกจที่เหมาะกับคุณเลยนะ 💕         │         │
│  └────────────────────────────────────────┘         │
│                                                       │
│  💰 ราคา ดึงจาก: ตาราง packages [จัดการ]              │
│                                                       │
│  🎁 โปรพิเศษ ดึงจาก: Promo Wizard [จัดการ]            │
│                                                       │
│  📦 ลำดับการแสดงผล:                                 │
│  [↕] VIP 30 วัน               ฿300                  │
│  [↕] OF + VIP 30 วัน          ฿500                  │
│  [↕] GOD MODE 90 วัน          ฿1,299                │
│  [↕] GOD MODE ถาวร            ฿2,499                │
│  [↕] ห้องมีคนชัก 30 วัน         ฿100                  │
│                                                       │
│  ✏️ ข้อความปิดท้าย                                   │
│  ┌────────────────────────────────────────┐         │
│  │ 💬 มีคำถาม? พิมพ์ถามแพรได้เลยค่า~         │         │
│  └────────────────────────────────────────┘         │
│                                                       │
│  [👀 ดูตัวอย่าง] [💾 บันทึก]                           │
└───────────────────────────────────────────────────────┘
```

### 🎯 ลูกน้องทำอะไรได้

- แก้ข้อความ header / footer
- ลากเรียงลำดับแพ็กเกจ
- ปิด/เปิดแพ็กเกจ (toggle is_active)
- แก้ชื่อแพ็กเกจ (จะ sync ทุกที่)
- แก้ราคาแพ็กเกจ (จะ trigger confirm modal เพราะกระทบหลายระบบ)

### 🗄 DB schema

ใช้ table `packages` เดิม + เพิ่ม column:
```sql
ALTER TABLE packages ADD COLUMN sort_order_v2 INT DEFAULT 0;
ALTER TABLE packages ADD COLUMN bot_emoji VARCHAR(8);
ALTER TABLE packages ADD COLUMN visible_in_menu BOOLEAN DEFAULT TRUE;
```

ใช้ `bot_messages` table (จาก [1]) สำหรับข้อความ header/footer:
- key: 'packages_header', 'packages_footer'

### ⚙️ Code changes

ไฟล์: `bots/sales_bot/handlers/packages.py`

```python
async def _build_package_list_text():
    header = await get_bot_message('packages_header') or DEFAULT_HEADER
    packages = await get_visible_packages()  # ORDER BY sort_order_v2
    campaigns = await get_active_campaigns()  # dashboard promos
    
    lines = [header]
    for pkg in packages:
        camp = campaigns.get(pkg.id)
        if camp:
            lines.append(f'{camp.badge} {pkg.name}: <s>{pkg.price}</s> {camp.promo_price}')
        else:
            lines.append(f'{pkg.bot_emoji} {pkg.name}: ฿{pkg.price}')
    lines.append(await get_bot_message('packages_footer') or DEFAULT_FOOTER)
    return '\n'.join(lines)
```

### 🔗 Links

- กระทบ **[20] Promo Wizard** — ราคาโปรมาจากที่นั่น
- กระทบ **[8] Daily report** — sync ชื่อแพ็กเกจ
- กระทบ **[42] Prae prompt** — Prae ต้องรู้ราคาปัจจุบัน
- กระทบ **[37-39] Finance** — sync ราคา → receiver matching

### ⚠️ Risk + Mitigation

| Risk | Mitigation |
|---|---|
| ลูกน้องเปลี่ยนราคา → บอตกับ Excel report ไม่ตรง | trigger sync hook ทุกครั้งที่ price เปลี่ยน |
| ลูกน้องปิดแพ็กเกจที่มีคน active | confirm modal + แสดงจำนวน active subs |

### ⏱ Effort

**2 วัน**

---

## [4] ข้อความขอบคุณ + วิธีจ่ายเงิน

### 🔍 ตอนนี้เป็นยังไง

ลูกค้าเลือกแพ็กเกจ → บอตส่ง:
- ขอบคุณ
- ราคา
- เลขบัญชี / PromptPay
- TrueMoney instructions

ข้อความ hardcode + receiver pool แสดงตามรอบ rotate

### 📝 ลูกน้องเห็นอะไร

```
┌─── 💬 ข้อความบอท / หลังเลือกแพ็กเกจ ───────────────────┐
│                                                       │
│  Template: หลังเลือก {package_name}                  │
│                                                       │
│  ┌────────────────────────────────────────┐         │
│  │ ขอบคุณค่า~ ✨                            │         │
│  │ คุณเลือก <b>{package_name}</b>          │         │
│  │ ราคา <b>฿{price}</b>                    │         │
│  │                                          │         │
│  │ 💳 โอนเข้า:                              │         │
│  │ {receiver_account}                       │         │
│  │ ชื่อ: {receiver_name}                    │         │
│  │                                          │         │
│  │ 📱 หรือ TrueMoney:                       │         │
│  │ ส่งลิงก์ gift.truemoney.com มาเลยค่ะ      │         │
│  └────────────────────────────────────────┘         │
│                                                       │
│  Tags: {package_name}, {price}, {receiver_*}         │
│                                                       │
└───────────────────────────────────────────────────────┘
```

### ⏱ Effort
**0.5 วัน**

---

## [5] QR PromptPay

### 🔍 ตอนนี้เป็นยังไง

แสดง QR ภาพจาก `receiver.qr_url` (มี dashboard receivers page แล้ว)

### 📝 ลูกน้องเห็นอะไร

ลูกน้องอัพโหลด QR ในหน้า Receivers ที่มีอยู่แล้ว → บอตใช้ทันที

✅ **ส่วนนี้ทำงาน OK ไม่ต้องเปลี่ยน**

### ⏱ Effort
**0 วัน** (existing)

---

## [6] ขอสลิป (Slip Upload Prompt)

### 🔍 ตอนนี้เป็นยังไง

หลังส่ง QR → บอตรอลูกค้าส่งรูปสลิป (handle ใน `payment.py`)

ข้อความ hardcode

### 📝 ลูกน้องเห็นอะไร

```
┌─── 💬 ข้อความบอท / ขอสลิป ──────────────────────────┐
│                                                       │
│  ┌────────────────────────────────────────┐         │
│  │ 📸 กรุณาส่งรูปสลิปการโอน                  │         │
│  │ หรือ ลิงก์ TrueMoney ส่งของในแชทนี้      │         │
│  │ แพรจะตรวจให้ภายใน 1 นาทีค่า 💕            │         │
│  └────────────────────────────────────────┘         │
│                                                       │
└───────────────────────────────────────────────────────┘
```

### ⏱ Effort
**0.5 วัน**

---

## 📊 สรุป Section 01

| # | Item | Effort | Phase |
|---|---|---|---|
| 1 | /start welcome (ใหม่/เก่า) | 1d | B |
| 2 | Main menu buttons | 2d | B |
| 3 | Package menu + header/footer | 2d | B |
| 4 | ข้อความหลังเลือก | 0.5d | B |
| 5 | QR (existing) | 0d | — |
| 6 | ขอสลิป | 0.5d | B |
| **รวม** | | **6 วัน** | |
