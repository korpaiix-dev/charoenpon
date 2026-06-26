# 99 — Implementation Roadmap

> ลำดับงาน + dependency + effort estimate
>
> **Total: 51 items · ~70 working days · 3 phases**

---

## 🔗 Dependency Graph

```
                    [00 PRINCIPLES]
                          ↓
        ┌─────────────────┼─────────────────┐
        ↓                 ↓                 ↓
   [Phase A]         [Phase B]         [Phase C]
   รากฐาน           โปร + ข้อความ      Prae AI
   (Day 1 use)      (Sales boost)     (Advanced)
```

---

## 📅 Phase A — รากฐาน + งานประจำวัน (4-5 สัปดาห์)

**โจทย์:** ลูกน้องเริ่มทำงาน**คนเดียวได้** วันแรกที่ deploy

### A.1 Foundation (1 สัปดาห์)

| # | Item | Effort | Risk |
|---|---|---|---|
| 51 | Feature flag system + UI | 2d | Low |
| - | bot_messages table + read helper | 2d | Low |
| - | bot_menu_buttons table + read helper | 1d | Low |

ทำก่อนเพราะทุก feature ใหม่ depends on this

### A.2 Inbox + Customer ops (1.5 สัปดาห์)

| # | Item | Effort | Risk |
|---|---|---|---|
| 7 | Inbox สลิป + override | 1d | Low |
| 9 | Reject + custom reasons | 1d | Low |
| 20 | Customer 360 polish + notes | 2d | Low |
| 21 | Manual extend | 1d | Low |
| 13 | SOS Console | 2d | Med |

### A.3 Daily Tasks Homepage (1 สัปดาห์)

| # | Item | Effort | Risk |
|---|---|---|---|
| - | Today's Checklist UI | 2d | Low |
| 18 | เปลี่ยน Tier UI | 1.5d | Med |
| 24 | Block / Unblock | 1d | Low |
| 36 | Discount UI | 1d | Low |

### A.4 Broadcast + Reports (1 สัปดาห์)

| # | Item | Effort | Risk |
|---|---|---|---|
| 26 | Broadcast DM + schedule | 2d | Med |
| 27 | Group broadcast polish | 1d | Low |
| 40 | Reports UI polish | 1d | Low |
| 42 | Fraud alerts | 1d | Low |

### A.5 System + Receivers (0.5 สัปดาห์)

| # | Item | Effort | Risk |
|---|---|---|---|
| 38 | Cumulative threshold | 1d | Low |
| 50 | Group IDs UI | 1d | Low |
| 15 | Renewal reminder | 2d | Med |
| 10 | Welcome VIP DM | 1d | Low |

**Phase A Total: ~22 วัน (4-5 สัปดาห์)**

---

## 📅 Phase B — Promo + Bot Messages (4 สัปดาห์)

**โจทย์:** ลูกน้องตั้งโปร + แก้คำพูดบอตได้ทุกที่

### B.1 Bot Message Library (1.5 สัปดาห์)

| # | Item | Effort |
|---|---|---|
| 1 | Welcome (new/returning) | 1d |
| 2 | Main menu buttons | 2d |
| 3 | Package menu | 2d |
| 4 | After-select msg | 0.5d |
| 6 | Slip prompt | 0.5d |
| 11 | Group invite map | 1d |

### B.2 Promo Wizard (1.5 สัปดาห์)

| ⭐ Promo Wizard core | 5d |
| Promo Wizard tests | 2d |

### B.3 Gacha + Events (1 สัปดาห์)

| # | Item | Effort |
|---|---|---|
| 33 | Probability calculator | 1d |
| 34 | Limited-time event | 3d |
| 16 | Comeback DM | 1.5d |
| 29 | Daily content schedule | 2d |

### B.4 Customer Mgmt (0.5 สัปดาห์)

| # | Item | Effort |
|---|---|---|
| 25 | Tags + filtering | 2d |
| 23 | Merge accounts | 3d |

**Phase B Total: ~24 วัน (4 สัปดาห์)**

---

## 📅 Phase C — Prae AI (3 สัปดาห์)

**โจทย์:** ลูกน้องปรับ Prae ได้ปลอดภัย

| # | Item | Effort | Risk |
|---|---|---|---|
| 43-44 | Persona editor (safe split) | 3d | High |
| 46 | Knowledge base | 3d | Med |
| 47 | Escalation rules | 2d | Med |
| 48 | Off-topic block | 2d | Low |
| - | Prae prompt testing harness | 3d | High |

**Phase C Total: ~13 วัน (3 สัปดาห์)**

---

## 📊 ตารางสรุป

| Phase | Effort | สิ่งที่ได้ |
|---|---|---|
| A | 22d | ลูกน้องเริ่มทำงานคนเดียวได้ |
| B | 24d | โปร + คำพูดบอตจัดการได้ |
| C | 13d | Prae ปรับได้ปลอดภัย |
| **รวม** | **59d** | **handoff สมบูรณ์** |

(บวก buffer 20% สำหรับ test + bug fix = **~70 วัน ≈ 14 สัปดาห์ ≈ 3-4 เดือน**)

---

## 🚦 Process ของแต่ละ Sprint

```
┌─────────────────────────────────────────────┐
│  1. Spec review (ทุกครั้ง)                  │
│     บอส OK → start                          │
│                                              │
│  2. Build mockup ใน staging port 8011      │
│     บอส คลิก preview                        │
│                                              │
│  3. Implement หลัง feature flag (OFF)      │
│     code → push git → deploy                │
│                                              │
│  4. Internal test (admin only)              │
│     บอส canary test                         │
│                                              │
│  5. Production rollout                      │
│     flag ON → all users                     │
│                                              │
│  6. Monitor 48 hrs                          │
│     ถ้า OK → mark sprint done               │
│     ถ้าเสีย → flag OFF → debug → retry      │
│                                              │
│  7. Update spec + close sprint             │
└─────────────────────────────────────────────┘
```

---

## ⚠️ Critical Path (อันที่ต้องทำก่อนเสมอ)

1. **bot_messages + bot_menu_buttons + feature_flags tables** — ทุก feature ใหม่ depends
2. **Promo Wizard** — เพราะ Lucky/Flash/Birthday hardcode ตอนนี้ทำงานปัจจุบัน ต้อง replace อย่างปลอดภัย
3. **Prae persona split** — ต้องไม่ทำให้ Prae 'จำของเดิมไม่ได้'

---

## 🎯 Definition of Done (ต่อแต่ละ item)

- ✅ DB migration test pass
- ✅ Feature flag toggle ทำงาน (ON ใช้ใหม่ / OFF ใช้เก่า)
- ✅ Mockup ใน staging port 8011 ผ่าน boss review
- ✅ Audit log บันทึก change
- ✅ Rollback flow ทดสอบจริง (flag OFF ภายใน 1 นาที)
- ✅ Spec doc updated
- ✅ Push git พร้อม commit message ชัดเจน

---

## 🚨 Risk Register

| Risk | Probability | Impact | Mitigation |
|---|---|---|---|
| Promo Wizard ทำลายลูกค้าที่อยู่ระหว่างซื้อ | Low | High | Flag canary 1 user → 10 users → all |
| Bot message library — HTML ผิด → บอตส่งไม่ได้ | Med | High | HTML validator + fallback ถึง hardcode |
| Prae persona split → ลูกค้าจำไม่ได้ | Low | High | Soft test 2 weeks ก่อน rollout |
| Lukenong กดผิดในการ delete | Med | Med | Confirm 2 ครั้ง + soft delete + audit log |
| DB migration ใน production fail | Low | Critical | Backup ก่อนทุก migration + DRY-RUN |

---

## 📅 Timeline ที่เสนอ

| สัปดาห์ | Phase | Focus |
|---|---|---|
| 1-5 | A | รากฐาน + งานประจำวัน |
| 6-9 | B | Promo + Bot msg |
| 10-12 | C | Prae AI |
| 13-14 | Buffer | bug fix + polish |

**Start:** 2026-06-26 (today)
**Phase A end:** 2026-07-31 (5 สัปดาห์)
**Phase B end:** 2026-08-31
**Phase C end:** 2026-09-30
**Full handoff:** 2026-10-15 (after 2 weeks buffer)
