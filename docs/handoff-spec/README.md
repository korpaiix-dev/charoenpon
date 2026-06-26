# 📚 Dashboard 2.0 — Handoff Spec

> ระบบ dashboard ใหม่สำหรับให้ลูกน้อง (non-tech) ใช้แทนบอสในการจัดการธุรกิจเจริญพร 100%
>
> **Started:** 2026-06-26 · **Status:** 🟡 Drafting

---

## 📖 อ่านตามลำดับนี้

| ลำดับ | ไฟล์ | เนื้อหา | สถานะ |
|---|---|---|---|
| 1 | [00-PRINCIPLES.md](./00-PRINCIPLES.md) | หลักการ + ข้อจำกัด + safety | ✅ |
| 2 | [01-pre-sale.md](./01-pre-sale.md) | ก่อนขาย: /start, menu, packages | ⏳ |
| 3 | [02-sale.md](./02-sale.md) | ตอนขาย: สลิป, อนุมัติ, รีเจ็กต์ | ⏳ |
| 4 | [03-link-delivery.md](./03-link-delivery.md) | ส่งลิ้ง: VIP + Discord | ⏳ |
| 5 | [04-after-sale.md](./04-after-sale.md) | บริการหลังขาย: SOS, renewal | ⏳ |
| 6 | [05-customer-mgmt.md](./05-customer-mgmt.md) | จัดการลูกค้า: 360, extend, merge | ⏳ |
| 7 | [06-broadcast.md](./06-broadcast.md) | บอกข่าว: DM, group, Discord | ⏳ |
| 8 | [07-gacha.md](./07-gacha.md) | กาชา: prize pool, pricing, events | ⏳ |
| 9 | [08-finance.md](./08-finance.md) | การเงิน: receiver, report | ⏳ |
| 10 | [09-ai-agent.md](./09-ai-agent.md) | Prae AI: prompt, tools, escalation | ⏳ |
| 11 | [10-system.md](./10-system.md) | ตั้งค่าระบบ: admin IDs, cron | ⏳ |
| 12 | [99-IMPLEMENTATION.md](./99-IMPLEMENTATION.md) | ลำดับงาน + dependency | ⏳ |

---

## 🎯 จุดประสงค์

บอส (korpaiix) กำลังย้ายไปทำธุรกิจอื่น — ส่งต่อให้**ลูกน้องที่ไม่ใช่ tech** เป็นคนคุม

โจทย์:
> "ลูกน้องคนเดียวคุมเจริญพรได้ทั้งหมดผ่าน dashboard — ไม่ต้องพิมพ์ ไม่ต้องคุย AI ไม่ต้องแก้ code"

## 📊 จำนวน items

- **Total:** 51 items (ตัด refund + affiliate ออกแล้วจาก 53)
- **Phase A:** 18 items (งานประจำวัน + รากฐาน)
- **Phase B:** 21 items (โปรโม + บอตเทกซ์ + gacha)
- **Phase C:** 12 items (Prae AI + advanced)

## 🛡 Safety guarantee

> ทุก feature ใหม่ต้อง: backward compatible + feature flag + audit log + rollback

ระบบเดิมยังทำงาน 100% — ใหม่ทำงานเฉพาะตอน flag ON เท่านั้น

---

## 🔗 Links

- **Production dashboard:** http://139.59.123.146:8010
- **Staging preview:** http://139.59.123.146:8011 (basic auth: panda)
- **Git repo:** korpaiix-dev/charoenpon (master branch)
