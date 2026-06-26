# 03 — ส่งลิ้ง + Discord (Link Delivery)

> หลังอนุมัติสลิป → ส่งลิ้งกลุ่ม VIP ให้ลูกค้า
>
> **3 items + Discord audit** · [10] welcome VIP → [11] ส่งลิ้งกลุ่ม → [12] roster

---

## 🎯 Discord Audit (สำคัญ — บอสห่วง)

**คำถาม:** เปลี่ยน dashboard ใหม่ → Discord bot กระทบไหม?

### ✅ คำตอบ: ไม่กระทบลูกค้า — Discord เป็น 'ห้องบัญชาการของบอส' ไม่ใช่ของลูกค้า

| Discord ทำหน้าที่อะไร | ผลกระทบจาก dashboard ใหม่ |
|---|---|
| รายงานยอดประจำวัน (sent to boss + team) | ไม่กระทบ — ใช้ DB เดียวกัน อ่านยอดได้ |
| Approval payment (จากบอส/ลูกน้องในทีม) | ไม่กระทบ — กดในห้องสลิป Telegram ดีกว่า |
| Prae AI ตอบในห้องทีม | ไม่กระทบ — ใช้ prompt เดียวกับ Telegram |
| AFK auto-move + Gacha shout-out + Briefing | ไม่กระทบ — internal automation |
| Marketing tracking (Ivy/Wasu/Pai) | ไม่กระทบ — channel feed อ่านจาก DB |

### ❗ จุดเดียวที่กระทบ: 'ราคา + ข้อความ' ใน Discord notification

ถ้าลูกน้องแก้:
- ราคาแพ็กเกจ → Discord daily report ต้อง sync (มันอ่านจาก DB อยู่แล้ว ✅)
- ข้อความบอท → Discord ไม่กระทบ (เพราะลูกค้าไม่ได้ขาย Discord)

### 📦 Discord ส่งลิ้ง?

ค้นในโค้ด: `grep invite discord_bot` → ใช้แค่ `_generate_qr_for_invite` (แปลงลิ้ง Telegram → QR ใน Discord)

**สรุป: Discord ไม่ออกลิ้งให้ลูกค้า — แค่แปลง QR ของลิ้ง Telegram ที่ Prae ส่งให้ทีม**

→ ไม่ต้อง redesign Discord delivery system ✅

---

## [10] ข้อความ Welcome VIP

### 🔍 ตอนนี้เป็นยังไง

หลัง `apply_payment_approval` → ส่ง DM ลูกค้า:
```
🎉 ยินดีต้อนรับสู่ VIP เจริญพร!
สิทธิ์ของคุณเริ่มแล้ว
ลิงก์เข้ากลุ่ม: {invite_link}
หมดอายุ: {expire_date}
```

(hardcode ใน `shared/customer_dm.py`)

### 📝 ลูกน้องเห็นอะไร

```
┌─── 💬 ข้อความบอท / Welcome VIP ──────────────────────┐
│                                                       │
│  ข้อความที่ลูกค้าได้หลังจ่ายเงินสำเร็จ:                    │
│                                                       │
│  [Tab: ลูกค้าใหม่ครั้งแรก] [Tab: ต่ออายุ]               │
│                                                       │
│  ┌────────────────────────────────────────┐         │
│  │ 🎉 ยินดีต้อนรับสู่ <b>VIP เจริญพร</b>!      │         │
│  │                                          │         │
│  │ คุณซื้อ: <b>{package_name}</b>           │         │
│  │ ราคา: ฿{price}                           │         │
│  │ ใช้ได้: {duration_days} วัน               │         │
│  │ หมดวันที่: {expire_date}                   │         │
│  │                                          │         │
│  │ 🔗 ลิ้งกลุ่ม VIP:                          │         │
│  │ {invite_link}                            │         │
│  │                                          │         │
│  │ มีปัญหาทักแอดมินที่นี่:                   │         │
│  │ {admin_link}                             │         │
│  └────────────────────────────────────────┘         │
│                                                       │
│  [👀 ดูตัวอย่าง] [💾 บันทึก]                           │
└───────────────────────────────────────────────────────┘
```

### 🗄 DB schema

ใช้ `bot_messages` (จาก [1]) keys:
- 'vip_welcome_new'
- 'vip_welcome_renew'

### ⚙️ Code changes

ไฟล์: `shared/customer_dm.py`

```python
async def send_welcome_dm(user, package, sub):
    is_renewal = await user_has_prior_sub(user.telegram_id)
    key = 'vip_welcome_renew' if is_renewal else 'vip_welcome_new'
    template = await get_bot_message(key) or DEFAULT_WELCOME
    return render_with_placeholders(template, user=user, package=package, sub=sub)
```

### 🔗 Links

- [11] ลิ้งกลุ่ม (depends on this)
- [22] Manual extend → ใช้ template เดียวกัน

### ⏱ Effort
**1 วัน**

---

## [11] ส่งลิ้งกลุ่ม (Group Invite Link)

### 🔍 ตอนนี้เป็นยังไง

- Guardian bot สร้าง invite link สำหรับแต่ละกลุ่ม
- ลูกค้าได้ลิ้ง 1-time use (member_limit=1)
- กลุ่มที่ส่ง depends on package tier:
  - TIER_300 → VIP groups
  - TIER_500 → VIP + OF groups
  - TIER_1299 → GOD groups (3 ห้อง)
  - TIER_2499 → GOD groups (3 ห้อง) ถาวร
  - TIER_100 → ห้องมีคนชัก
  - GACHA → ของรางวัล

### 📝 ลูกน้องเห็นอะไร

```
┌─── ⚙️ Settings / กลุ่ม VIP ────────────────────────┐
│                                                       │
│  📦 แพ็กเกจ → กลุ่มที่ได้สิทธิ์                          │
│                                                       │
│  TIER_300 (VIP 30 วัน):                              │
│    ✅ VIP_1 — Tel @+abc...                           │
│    ✅ VIP_2 — Tel @+def...                           │
│    [➕ เพิ่มกลุ่ม]                                    │
│                                                       │
│  TIER_500 (OF + VIP):                                │
│    ✅ ทั้งหมดของ TIER_300                              │
│    ✅ OF_1, OF_2                                     │
│                                                       │
│  TIER_1299 (GOD MODE):                               │
│    ✅ GOD_1, GOD_2, GOD_3                            │
│                                                       │
│  ...                                                  │
│                                                       │
└───────────────────────────────────────────────────────┘
```

### 🗄 DB schema

ใช้ `group_registry` (มีแล้ว) — แค่เพิ่ม UI

### 🔗 Links

- [10] welcome — ส่งลิ้งใน DM
- [22] manual send link
- [11.1] Discord — ไม่กระทบ (Discord ไม่ส่งลิ้งเอง)

### ⏱ Effort

**1 วัน** (UI สำหรับ map แพ็กเกจ → กลุ่ม)

---

## [12] บันทึก Member Roster

### 🔍 ตอนนี้เป็นยังไง

หลังส่งลิ้ง → save ใน `subscriptions` + `vip_members` table

### 📝 ลูกน้องเห็นอะไร

ใช้ Customer 360 ที่มีแล้ว — ดูได้ว่าลูกค้าอยู่กลุ่มไหน หมดเมื่อไหร่

### ⏱ Effort
**0 วัน** (existing)

---

## 📊 สรุป Section 03 + Discord

| # | Item | Effort | Phase |
|---|---|---|---|
| 10 | Welcome VIP DM | 1d | A |
| 11 | Group invite + map | 1d | B |
| 12 | Roster (existing) | 0d | — |
| Discord | Audit only — no changes | 0d | — |
| **รวม** | | **2 วัน** | |

---

## 🚨 Discord — สิ่งที่ต้องจำไว้

- Discord = **internal team tool** (ห้องบัญชาการบอส + team)
- Discord **ไม่ได้ขายของให้ลูกค้า** ในตอนนี้
- ถ้าจะใช้ Discord ขายของในอนาคต → spec ใหม่ (ไม่อยู่ใน scope นี้)
- ลูกน้องไม่จำเป็นต้องเข้า Discord — ทำงานใน dashboard ก็พอ
