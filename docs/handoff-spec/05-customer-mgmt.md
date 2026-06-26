# 05 — จัดการลูกค้า (Customer Management)

> เครื่องมือสำหรับลูกน้องดูแลลูกค้าเป็นรายๆ
>
> **6 items** · [20] Customer 360 → [21] manual extend → [22] manual send link → [23] merge → [24] block → [25] tag

---

## [20] Customer 360 — ดูประวัติทั้งหมด

### 🔍 ตอนนี้เป็นยังไง

มีหน้า Customer 360 ใน dashboard (มีจาก Sprint 1.2) แสดง:
- ข้อมูลพื้นฐาน
- ประวัติ payment
- Subscription active
- Group membership
- SOS history
- DM history

ลูกน้อง search ด้วย @username, tg_id, ชื่อ

### ✏️ ต้องเพิ่ม

- ปุ่ม actions รวมศูนย์: extend / send link / block / tag / promote
- ดู Prae conversation log (มีหน้าแยก ต้อง embed)
- ดู note ลูกน้องเขียนเอง (ลูกน้องคนอื่นเห็นด้วย)

### 📝 ลูกน้องเห็นอะไร

```
┌─── 👤 Customer 360 ────────────────────────────────┐
│                                                       │
│  @user789 · ชื่อ: นาย ก                              │
│  tg=12345 · เพิ่ง chat: 5 นาที                         │
│                                                       │
│  Status: 💎 GOD MODE active (45 วัน)                  │
│  📊 ยอดรวม: ฿3,099 (3 payments)                     │
│  💎 ยศ: Silver (ครบ 60 วัน)                          │
│                                                       │
│  ─── ⚡ Quick Actions ─────────────                   │
│  [📩 DM] [🔄 ส่งลิ้งใหม่] [🆙 อัพเกรด]                  │
│  [💰 manual extend] [🏷 Tag] [🚫 Block]               │
│                                                       │
│  ─── 📜 ประวัติ ────────────────────                  │
│  [Tab: Payment] [Tab: Subscription] [Tab: SOS]       │
│  [Tab: DM] [Tab: กลุ่ม] [Tab: Notes]                  │
│                                                       │
│  ─── 📝 Notes (ลูกน้องเขียน) ───────                 │
│  [+ เพิ่ม note]                                       │
│  2026-06-25 (admin: Ivy)                            │
│   'ลูกค้าพิเศษ ขอติดต่อตอนกลางคืนเท่านั้น'              │
│  2026-06-20 (admin: Pai)                            │
│   'เคยขอ refund แต่ไม่ให้'                            │
│                                                       │
└───────────────────────────────────────────────────────┘
```

### ⏱ Effort
**2 วัน** (UI polish + notes feature)

---

## [21] Manual Extend (เพิ่มวันให้ลูกค้า)

### 🔍 ตอนนี้เป็นยังไง

มี endpoint extend แล้ว — แต่ flow ไม่ชัด

### 📝 ลูกน้องเห็นอะไร

```
┌─── 💰 Manual Extend ────────────────────────────────┐
│                                                       │
│  ลูกค้า: @user789                                     │
│  ปัจจุบัน: VIP เหลือ 25 วัน                            │
│                                                       │
│  เพิ่มกี่วัน?                                          │
│  [- 7] [- 1] [____] [+ 1] [+ 7]                       │
│                                                       │
│  หรือเปลี่ยนเป็น:                                      │
│  ○ 7 วัน  ○ 30 วัน  ○ 90 วัน  ○ ถาวร                   │
│                                                       │
│  เหตุผล (required):                                   │
│  [_______________________________]                    │
│                                                       │
│  ⚠️ การกระทำนี้จะ:                                    │
│  • ขยายสิทธิ์ลูกค้า                                    │
│  • DM ลูกค้าแจ้งเตือน                                  │
│  • Log ใน admin_logs                                  │
│  • ไม่กระทบรายงานรายได้                                │
│                                                       │
│  [ยกเลิก] [✅ ขยายให้]                               │
└───────────────────────────────────────────────────────┘
```

### ⏱ Effort
**1 วัน**

---

## [22] Manual Send Link

ทำใน [14] แล้ว — ส่งลิ้งใหม่

✅ Effort: **0 วัน**

---

## [23] Merge Accounts

### 🔍 ตอนนี้เป็นยังไง

ลูกค้ามี 2 บัญชี (เปลี่ยน tg_id หรือ username) → ระบบเห็นเป็น 2 คน

ไม่มี merge tool — ลูกน้องต้องแก้ DB มือ (อันตราย)

### 📝 ลูกน้องเห็นอะไร

```
┌─── 🔗 Merge Accounts ───────────────────────────────┐
│                                                       │
│  Source (รวมเข้ากับ target):                          │
│  tg=12345 · @user789 · 1 payment                     │
│                                                       │
│  Target (จะเก็บไว้):                                   │
│  tg=67890 · @user789_new · 3 payments                 │
│                                                       │
│  ⚠️ การ merge จะ:                                     │
│  • รวม payments + subs + DM history                  │
│  • Keep target tg_id                                  │
│  • ลบ source user (soft)                              │
│  • ไม่ rollback ได้ — confirm 2 ครั้ง                  │
│                                                       │
│  [ยกเลิก] [🔗 รวมบัญชี]                                │
└───────────────────────────────────────────────────────┘
```

### ⚠️ Risk

- กระทบหลาย table — ต้องทำ atomic transaction
- ตรวจให้ดีว่าจริงๆ เป็นคนเดียวกัน (เห็นเงินสองคนรวม)

### ⏱ Effort
**3 วัน** (เพราะ tricky)

---

## [24] Block / Unblock

### 🔍 ตอนนี้เป็นยังไง

มี ban system + Guardian kick all groups (Task #102)

### 📝 ลูกน้องเห็นอะไร

```
┌─── 🚫 Block User ──────────────────────────────────┐
│                                                       │
│  ลูกค้า: @user789                                     │
│                                                       │
│  ระดับการ block:                                       │
│  ○ Soft — บอตไม่ตอบ (ลูกค้ายังอยู่กลุ่ม)              │
│  ● Medium — เตะออกจากทุกกลุ่ม                          │
│  ○ Hard — Medium + คืนเงินไม่ได้ + บล็อกสลิป            │
│                                                       │
│  เหตุผล:                                              │
│  ○ Scam slip                                          │
│  ○ Spam                                              │
│  ○ ก่อกวน                                            │
│  ● อื่นๆ                                             │
│  [____________________________]                       │
│                                                       │
│  [ยกเลิก] [🚫 Block]                                  │
└───────────────────────────────────────────────────────┘
```

### ⏱ Effort
**1 วัน**

---

## [25] Tag VIP / VVIP / Churn Risk

### 🔍 ตอนนี้เป็นยังไง

มี loyalty_rank (Bronze/Silver/Diamond) auto-assigned by tenure

ต้องเพิ่ม manual tag

### 📝 ลูกน้องเห็นอะไร

```
┌─── 🏷 Tag Customer ─────────────────────────────────┐
│                                                       │
│  ลูกค้า: @user789                                     │
│                                                       │
│  Tag ที่มี:                                           │
│  ☑ VVIP                                              │
│  ☑ ลูกค้าประจำ                                        │
│  ☐ Churn risk                                        │
│  ☐ พิเศษ — อย่าส่งโปร                                 │
│                                                       │
│  สร้าง tag ใหม่: [_________] [➕]                     │
│                                                       │
│  💡 Tag ใช้ทำอะไร:                                    │
│  • Filter ใน broadcast                               │
│  • Highlight ใน Inbox                                │
│  • Custom report                                     │
│                                                       │
│  [💾 บันทึก]                                          │
└───────────────────────────────────────────────────────┘
```

### 🗄 DB schema

```sql
CREATE TABLE customer_tags (
  id SERIAL PRIMARY KEY,
  name VARCHAR(50) UNIQUE NOT NULL,
  color VARCHAR(7),  -- #hex
  description TEXT,
  created_by BIGINT,
  created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE customer_tag_assignments (
  user_id INT REFERENCES users(id),
  tag_id INT REFERENCES customer_tags(id),
  assigned_by BIGINT,
  assigned_at TIMESTAMP DEFAULT NOW(),
  PRIMARY KEY (user_id, tag_id)
);
```

### ⏱ Effort
**2 วัน**

---

## 📊 สรุป Section 05

| # | Item | Effort | Phase |
|---|---|---|---|
| 20 | Customer 360 polish + notes | 2d | A |
| 21 | Manual extend | 1d | A |
| 22 | Send link (in [14]) | 0d | — |
| 23 | Merge accounts | 3d | B |
| 24 | Block / Unblock | 1d | A |
| 25 | Tags + filtering | 2d | B |
| **รวม** | | **9 วัน** | |
