# 10 — ตั้งค่าระบบ (System Config)

> ของที่ตั้งครั้งเดียวแล้วไม่ค่อยยุ่ง
>
> **5 items** · [49] admin IDs → [50] group IDs → [51] cron + feature flags

(Note: เลข item ปรับใหม่หลังตัด refund + affiliate)

---

## [49] Admin Telegram IDs

✅ ทำแล้ววันนี้ (Task #301 + ใน DELETE endpoint)
- เพิ่ม / ลบ admin
- Bots restart auto

### ⏱ Effort
**0 วัน**

---

## [50] Group IDs

ใช้ `group_registry` (มีแล้ว) — แค่ UI ใน Settings

```
┌─── 🏛 Groups ───────────────────────────────────────┐
│                                                       │
│  [Tab: VIP] [Tab: GOD] [Tab: ฟรี] [Tab: ห้องชัก]    │
│                                                       │
│  VIP Groups:                                          │
│  ✅ VIP_1 (-100123456) · 234 members                  │
│   [⚙️ rename] [🔗 invite] [⏸ pause]                   │
│  ✅ VIP_2 (-100234567) · 198 members                  │
│  ...                                                  │
│                                                       │
│  [➕ register กลุ่มใหม่]                              │
└───────────────────────────────────────────────────────┘
```

### ⏱ Effort
**1 วัน**

---

## [51] Cron + Feature Flags

### 🔍 ตอนนี้เป็นยังไง

Cron jobs ใน VPS:
- 19:00 backup
- 09:00 daily expiry report
- 16:59 charoenpon daily sales

ลูกน้องไม่ควรแก้ cron ตรง — แต่ดูได้

### 📝 ลูกน้องเห็นอะไร

```
┌─── ⚙️ Settings / System ────────────────────────────┐
│                                                       │
│  [Tab: Admin IDs] [Tab: Groups] [Tab: Cron Jobs]    │
│  [Tab: Feature Flags] [Tab: Backup]                  │
│                                                       │
│  Cron Jobs:                                           │
│  ✅ Daily backup        19:00 ทุกวัน · ล่าสุด 19:00     │
│   [▶ run now] [ดู log]                              │
│                                                       │
│  ✅ Daily expiry report 09:00 ทุกวัน · ล่าสุด 09:00     │
│   [▶ run now] [ดู log]                              │
│                                                       │
│  ✅ Daily sales summary 16:59 ทุกวัน                  │
│   [▶ run now] [ดู log]                              │
│                                                       │
│  ─── Feature Flags ─────                              │
│  [☑] PROMO_WIZARD_ENABLED      (canary: บอส only)    │
│  [☐] BOT_MSG_LIBRARY_ENABLED   (OFF)                 │
│  [☐] PRAE_KNOWLEDGE_ENABLED    (OFF)                 │
│                                                       │
│  ⚠️ Flags ที่ OFF = ใช้ behavior เดิม                  │
└───────────────────────────────────────────────────────┘
```

### ⏱ Effort
**2 วัน** (UI + integrate with feature_flags table)

---

## 📊 สรุป Section 10

| # | Item | Effort | Phase |
|---|---|---|---|
| 49 | Admin IDs (DONE) | 0d | — |
| 50 | Group IDs UI | 1d | A |
| 51 | Cron + Feature flags | 2d | A |
| **รวม** | | **3 วัน** | |
