# 09 — Prae AI Agent

> **ยากที่สุด** — ลูกน้องจะเปลี่ยน 'บุคลิก' ของ Prae ได้ยังไง?
>
> **6 items** · [43] system prompt → [44] personality → [45] tools → [46] knowledge base → [47] escalation → [48] off-topic

---

## [43] System Prompt Editor

### 🔍 ตอนนี้เป็นยังไง

มีหน้า Prae Prompt Editor (Sprint 2.5) — ✅ ทำแล้ว
- Edit system prompt
- Version history + rollback
- Diff viewer

ปัญหา:
- ลูกน้อง edit prompt = อันตราย (โดน prompt injection / overflow / token limit)
- Prompt ตอนนี้ ~5000 tokens — ลูกน้องอ่านไม่หมด

### 📝 ลูกน้องเห็นอะไร (เวอร์ชั่นง่าย)

```
┌─── 🤖 Prae Personality ────────────────────────────┐
│                                                       │
│  ⚠️ ลูกน้อง: ห้ามแก้ system prompt ทั้งหมด             │
│    ใช้ 'ช่องเฉพาะ' แทน                                │
│                                                       │
│  📝 Persona (บุคลิก Prae):                            │
│  ┌────────────────────────────────────────┐         │
│  │ Prae เป็น AI ที่ขายสุภาพ เป็นกันเอง...     │         │
│  └────────────────────────────────────────┘         │
│  [ลูกน้องแก้ได้]                                       │
│                                                       │
│  🎭 น้ำเสียง:                                          │
│  ○ เป็นกันเอง (default)                              │
│  ○ ทางการ                                            │
│  ○ ขี้เล่น                                            │
│                                                       │
│  💬 ปิดท้ายข้อความ:                                    │
│  [✏️ '...ค่า' / '...ค่ะ' / 'จ้า']                    │
│                                                       │
│  ─── ⚙️ Advanced (บอสเท่านั้น) ───                     │
│  [แก้ system prompt ทั้งหมด] (ปิดสำหรับลูกน้อง)         │
│                                                       │
└───────────────────────────────────────────────────────┘
```

### ⚙️ Code changes

- Split prompt เป็น 'core' (immutable) + 'persona' (lukenong editable)
- Render: `{core}\n\n## Persona\n{persona}`
- Lukenong editable: persona, tone, sign-off

### ⏱ Effort
**3 วัน**

---

## [44] Personality / Tone

✅ อยู่ใน [43]

---

## [45] Tools (ที่ Prae ใช้ได้)

### 🔍 ตอนนี้เป็นยังไง

Prae มี tools ~10 อัน (suggest_link, check_subscription, create_payment, etc)

ลูกน้องไม่ต้อง enable/disable tools — มันต้องใช้ทุกอันถึงทำงานได้

❌ **ไม่อยู่ใน scope ลูกน้อง**

### ⏱ Effort
**0 วัน**

---

## [46] Knowledge Base (FAQ)

### 🔍 ตอนนี้เป็นยังไง

Prae ตอบจากเครื่อง knowledge ใน prompt — ถ้าไม่รู้ → escalate

ปัญหา: ลูกน้องเพิ่ม FAQ ไม่ได้ ต้องผ่าน prompt

### 📝 ลูกน้องเห็นอะไร

```
┌─── 📚 Knowledge Base ──────────────────────────────┐
│                                                       │
│  คำถามที่ Prae รู้คำตอบ:                                │
│                                                       │
│  Q: 'เปิด VIP กี่บาท?'                                 │
│  A: 'VIP 30 วัน ฿300 ค่า~ คุ้มมาก กดดูข้อ...'         │
│  Tags: [packages, price]                              │
│  [✏️ แก้] [🗑]                                        │
│                                                       │
│  Q: 'อ่านมาแล้วต้องทำไง?'                              │
│  A: 'กดดูแพ็กเกจที่ปุ่มล่างเลยค่า~'                     │
│  Tags: [navigation]                                   │
│  [✏️ แก้] [🗑]                                        │
│                                                       │
│  [➕ เพิ่มคำถามใหม่]                                   │
│                                                       │
│  💡 Prae อ่าน Q+A พวกนี้ก่อนตอบทุกครั้ง                │
└───────────────────────────────────────────────────────┘
```

### 🗄 DB schema

```sql
CREATE TABLE prae_knowledge (
  id SERIAL PRIMARY KEY,
  question TEXT NOT NULL,
  answer TEXT NOT NULL,
  tags TEXT[],
  enabled BOOLEAN DEFAULT TRUE,
  updated_at TIMESTAMP DEFAULT NOW(),
  updated_by BIGINT
);
```

### ⚙️ Code changes

Prepend knowledge ในตอนเริ่ม conversation:
```python
async def build_prae_messages(user_msg):
    knowledge = await get_active_knowledge()
    system = f'{base_prompt}\n\nKnowledge:\n' + '\n'.join(f'Q: {k.question}\nA: {k.answer}' for k in knowledge)
    return [system, ..., user_msg]
```

### ⏱ Effort
**3 วัน**

---

## [47] Escalation Rules (เมื่อไหร่ส่งต่อให้คน)

### 🔍 ตอนนี้เป็นยังไง

Prae detect SOS แล้ว alert ลูกน้อง (มีอยู่)

ลูกน้องเพิ่ม keyword trigger ไม่ได้

### 📝 ลูกน้องเห็นอะไร

```
┌─── 🛟 Escalation Rules ─────────────────────────────┐
│                                                       │
│  Prae จะ alert ลูกน้องเมื่อ:                            │
│                                                       │
│  ✅ ลูกค้าพูดคำเหล่านี้ (auto):                          │
│   refund, คืนเงิน, ลิ้งไม่ได้, scam, ฟ้อง               │
│   [➕ เพิ่ม keyword]                                  │
│                                                       │
│  ✅ ลูกค้าใช้ Prae > 5 นาที ไม่ purchase                │
│  ✅ ลูกค้าโกรธ (sentiment detect)                       │
│  ✅ ลูกค้าถามเรื่องนอก scope บอท                          │
│                                                       │
│  Alert จะส่งไปที่:                                     │
│  ☑ ห้องสลิป Telegram                                  │
│  ☑ Dashboard SOS console                              │
│  ☐ Discord #sos channel                              │
│                                                       │
└───────────────────────────────────────────────────────┘
```

### ⏱ Effort
**2 วัน**

---

## [48] Off-topic Block List

### 🔍 ตอนนี้เป็นยังไง

Prae ตอบทุกอย่าง แม้นอก scope (เสี่ยง prompt injection + waste tokens)

### 📝 ลูกน้องเห็นอะไร

```
┌─── 🚫 Off-topic Blocklist ─────────────────────────┐
│                                                       │
│  หัวข้อที่ Prae จะปฏิเสธตอบ:                            │
│  ☑ การเมือง                                          │
│  ☑ ศาสนา                                            │
│  ☑ ข่าวเหตุการณ์ปัจจุบัน                                │
│  ☑ แนะนำลงทุน                                         │
│  ☑ ขอ phone number / address                          │
│                                                       │
│  คำตอบเมื่อเจอ:                                        │
│  ┌─────────────────────────────────┐                  │
│  │ ขออภัยค่ะ Prae ตอบเรื่องนี้ไม่ได้ │                  │
│  │ ลองถามเรื่องแพ็กเกจดูไหมคะ~       │                  │
│  └─────────────────────────────────┘                  │
│                                                       │
└───────────────────────────────────────────────────────┘
```

### ⏱ Effort
**2 วัน**

---

## 📊 สรุป Section 09

| # | Item | Effort | Phase |
|---|---|---|---|
| 43 | Persona editor (safe) | 3d | C |
| 44 | Tone (in [43]) | 0d | — |
| 45 | Tools (out of scope) | 0d | — |
| 46 | Knowledge base | 3d | C |
| 47 | Escalation rules | 2d | C |
| 48 | Off-topic block | 2d | C |
| **รวม** | | **10 วัน** | |
