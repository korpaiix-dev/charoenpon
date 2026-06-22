# กฎสำหรับ batch DM script — บังคับใช้ทุกครั้งที่ส่ง DM ลูกค้าเป็นกลุ่ม

> เขียนหลังเหตุการณ์ 2026-06-22 ที่ผมส่ง DM ซ้ำ 2-3 รอบให้ลูกค้า 13 คน

## RULE 1: Marker ก่อน DM (idempotency)
- **ก่อน** ส่ง DM ให้ INSERT marker เข้า admin_logs ก่อน
- ถ้า INSERT แล้ว FAIL → marker อยู่แล้ว = หยุด ไม่ส่ง
- ห้ามใช้ "in-memory set" — ถ้า script ตาย/restart จะลืม
- ตัวอย่าง marker: action='link_resent', target_id=user_id

## RULE 2: Foreground เท่านั้น
- **ห้าม `docker exec -d`** (detached) — ทำให้เห็น timeout จาก SSH แต่จริงๆ ยังรันอยู่
- ใช้ `docker exec <c> python3 script.py` เห็นทุกบรรทัด
- ถ้า timeout → script ตายจริง = OK
- ถ้าจำเป็นต้องรันยาว → ใช้ scheduler/cron job ที่ register ใน main.py แทน

## RULE 3: Rate limit
- ใส่ `await asyncio.sleep(1.0)` ระหว่างทุก message
- Telegram bot จำกัด 30 msg/sec — แต่ 1/sec ปลอดภัยกว่า + ดูเป็นมนุษย์
- Batch ใหญ่ (>50 คน) → ใช้ 2 วินาที

## RULE 4: ตรวจก่อนรัน — กฎ "3 อย่าง"
ก่อนรัน batch ใดๆ ต้องตอบได้:
1. **ใครได้บ้าง?** — เช็ค SELECT query ก่อน คนกี่คน รายชื่อใคร
2. **ส่งแล้วยัง?** — มี marker แล้วไหม ถ้ามี = skip
3. **ผลถ้าพัง?** — ถ้า script ตาย/รันซ้ำ จะเกิดอะไร

## RULE 5: Test mode FIRST
- รันด้วย `DRY_RUN=1` ครั้งแรก — print เท่านั้น ไม่ส่งจริง
- ถ้าเห็น output ตรงตามคาด → ลบ DRY_RUN รันจริง
- ถ้าไม่มี dry_run mode → ห้ามรัน

## RULE 6: ห้ามรัน worker ครั้งแรกบน production
- Worker ใหม่ที่จะส่ง DM ลูกค้า — รันเฉพาะ container ที่ไม่มี SALES_BOT_TOKEN
- หรือ mock Bot.send_message ให้แค่ print
- ดูว่าจะส่งให้ใครบ้าง — ถ้าเกินคาด = แก้ filter

## RULE 7: Backfill markers ก่อน register worker
- ถ้า worker จะค้นหา "ใครยังไม่ได้รับ X" — ต้อง backfill marker ของคนที่ได้รับแล้วก่อน
- เพราะ worker เห็น "ไม่มี marker = ส่ง" → spam ทุกคนที่เคยได้แล้ว
- ทำซ้ำเหตุการณ์ 2026-06-22

## RULE 8: ผลลัพธ์จับต้องได้
- ทุก batch script ต้อง print:
  - `sent_count`
  - `skipped_count` (เพราะมี marker)
  - `failed_count`
  - `blocked_count`
- ถ้าจำนวนต่างจากที่คาด → หยุดทันที + investigate

## เหตุการณ์ที่ฝ่าฝืน RULE
- **2026-06-22 RULE 2** ใช้ `docker exec -d` → script รัน 2 ตัวขนาน
- **2026-06-22 RULE 1** ไม่มี marker ก่อน send → ส่งซ้ำได้
- **2026-06-22 RULE 5** ไม่มี dry_run → ไม่เห็นจะส่งให้ใครบ้าง
- **2026-06-22 RULE 7** ไม่ backfill marker → worker test ส่งให้คนที่ได้แล้ว 3 คน

## Lesson Learned
1. Batch DM = action ที่ irreversible → ตรวจ 10 ครั้งก่อนรันครั้งเดียว
2. "Background script" = ไม่เห็นว่าเกิดอะไรขึ้น = อันตราย
3. "Worker test" บน production = พ่นข้อความจริงให้ลูกค้า ห้ามทำ
