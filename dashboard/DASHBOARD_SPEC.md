# 🏢 เจริญพร Dashboard — Complete Specification

> **Version:** 1.0  
> **Date:** 2026-03-20  
> **Tech Stack:** FastAPI + HTML/CSS/JS (SPA) + PostgreSQL  
> **Port:** 8010  
> **Theme:** Dark Mode Modern  

---

## 📐 Architecture Overview

```
┌─────────────────────────────────────────────────┐
│  Browser (SPA)                                  │
│  HTML/CSS/JS — Dark Theme                       │
│  Port 8010                                      │
└──────────────┬──────────────────────────────────┘
               │ REST API (JSON)
┌──────────────▼──────────────────────────────────┐
│  FastAPI Backend                                │
│  - JWT Auth (login via Telegram ID + password)  │
│  - Role-based access control                    │
│  - Serves static SPA files                      │
└──────────────┬──────────────────────────────────┘
               │ asyncpg
┌──────────────▼──────────────────────────────────┐
│  PostgreSQL (charoenpon)                        │
│  Existing 21 tables + new dashboard tables      │
└─────────────────────────────────────────────────┘
```

---

## 🔐 ยศ + สิทธิ์ Matrix

### ยศ 3 ระดับ

| Role | Level |
|------|-------|
| **Owner** | 100 |
| **Admin** | 50 |
| **Moderator** | 10 |

### สิทธิ์ Matrix

| หน้า / Action | Owner | Admin | Moderator |
|---|:---:|:---:|:---:|
| **📊 ภาพรวม** — ดูทั้งหมด | ✅ | ✅ | ✅ (จำกัด) |
| **👥 ลูกค้า** — ดูรายการ | ✅ | ✅ | ✅ |
| **👥 ลูกค้า** — ต่อเวลา/อัพเกรด | ✅ | ✅ | ❌ |
| **👥 ลูกค้า** — เตะ/แบน | ✅ | ✅ | ❌ |
| **👥 ลูกค้า** — ส่ง DM | ✅ | ✅ | ✅ |
| **💰 การเงิน** — ดูรายการ | ✅ | ✅ | ✅ |
| **💰 การเงิน** — อนุมัติ/ปฏิเสธสลิป | ✅ | ✅ | ✅ |
| **💰 การเงิน** — ดูกราฟ/สรุป | ✅ | ✅ | ❌ |
| **📢 โปรโมชั่น** — สร้าง/แก้ไข | ✅ | ✅ | ❌ |
| **📢 โปรโมชั่น** — ดูประวัติ | ✅ | ✅ | ❌ |
| **📸 Content** — ดู/จัดการ queue | ✅ | ✅ | ✅ |
| **📸 Content** — อัพโหลด/แก้ schedule | ✅ | ✅ | ❌ |
| **📱 กลุ่ม Telegram** — ดูรายการ | ✅ | ✅ | ❌ |
| **📱 กลุ่ม Telegram** — แก้ไข/สร้าง link | ✅ | ✅ | ❌ |
| **📱 กลุ่ม Telegram** — ตั้งค่า Guardian | ✅ | ❌ | ❌ |
| **👨‍💼 ทีมงาน** — ดู | ✅ | ✅ | ❌ |
| **👨‍💼 ทีมงาน** — เพิ่ม/ลบ/เปลี่ยนยศ | ✅ | ❌ | ❌ |
| **⚙️ ตั้งค่า** — ทั้งหมด | ✅ | ❌ | ❌ |
| **⚙️ ตั้งค่า** — แพ็กเกจ/Schedule/DM | ✅ | ✅ (ยกเว้น tokens) | ❌ |
| **📊 Marketing** — ดูทั้งหมด | ✅ | ✅ | ❌ |

---

## 🗄️ New DB Tables

### 1. `dashboard_admins` — ผู้ดูแลระบบ Dashboard

```sql
CREATE TABLE dashboard_admins (
    id            SERIAL PRIMARY KEY,
    telegram_id   BIGINT NOT NULL UNIQUE,
    username      VARCHAR(255),
    display_name  VARCHAR(255) NOT NULL,
    password_hash VARCHAR(255) NOT NULL,       -- bcrypt hash
    role          VARCHAR(20) NOT NULL DEFAULT 'moderator',  -- owner / admin / moderator
    is_active     BOOLEAN NOT NULL DEFAULT TRUE,
    last_login_at TIMESTAMP,
    last_login_ip VARCHAR(45),
    created_at    TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMP NOT NULL DEFAULT NOW()
);

-- Seed initial admins
-- INSERT INTO dashboard_admins (telegram_id, display_name, password_hash, role)
-- VALUES (xxx, 'บอสไผ่', '<hash>', 'owner'),
--        (xxx, 'เฮียโค้ก', '<hash>', 'admin'),
--        (xxx, 'เซียนจู', '<hash>', 'admin');
```

### 2. `dashboard_sessions` — JWT Session tracking

```sql
CREATE TABLE dashboard_sessions (
    id          SERIAL PRIMARY KEY,
    admin_id    INTEGER NOT NULL REFERENCES dashboard_admins(id) ON DELETE CASCADE,
    token_jti   VARCHAR(64) NOT NULL UNIQUE,   -- JWT ID for revocation
    ip_address  VARCHAR(45),
    user_agent  TEXT,
    created_at  TIMESTAMP NOT NULL DEFAULT NOW(),
    expires_at  TIMESTAMP NOT NULL,
    revoked_at  TIMESTAMP
);

CREATE INDEX ix_dash_sessions_jti ON dashboard_sessions(token_jti);
CREATE INDEX ix_dash_sessions_admin ON dashboard_sessions(admin_id);
```

### 3. `dashboard_activity_log` — ทุก action ที่ทำบน Dashboard

```sql
CREATE TABLE dashboard_activity_log (
    id          SERIAL PRIMARY KEY,
    admin_id    INTEGER NOT NULL REFERENCES dashboard_admins(id),
    action      VARCHAR(100) NOT NULL,     -- e.g. 'approve_payment', 'ban_user', 'create_flash_sale'
    entity_type VARCHAR(50),               -- e.g. 'payment', 'user', 'flash_sale'
    entity_id   INTEGER,
    details     JSONB,                     -- flexible metadata
    ip_address  VARCHAR(45),
    created_at  TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX ix_dash_activity_admin ON dashboard_activity_log(admin_id);
CREATE INDEX ix_dash_activity_action ON dashboard_activity_log(action);
CREATE INDEX ix_dash_activity_created ON dashboard_activity_log(created_at DESC);
```

### 4. `promo_codes` — Promo Code (ยังไม่มีในระบบ)

```sql
CREATE TABLE promo_codes (
    id             SERIAL PRIMARY KEY,
    code           VARCHAR(30) NOT NULL UNIQUE,
    discount_pct   INTEGER NOT NULL,           -- ส่วนลด %
    max_uses       INTEGER NOT NULL DEFAULT 1,
    used_count     INTEGER NOT NULL DEFAULT 0,
    package_id     INTEGER REFERENCES packages(id),  -- NULL = ใช้ได้ทุกแพ็กเกจ
    min_amount     NUMERIC(10,2),
    starts_at      TIMESTAMP NOT NULL DEFAULT NOW(),
    expires_at     TIMESTAMP NOT NULL,
    is_active      BOOLEAN NOT NULL DEFAULT TRUE,
    created_by     INTEGER REFERENCES dashboard_admins(id),
    created_at     TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX ix_promo_code ON promo_codes(code);
```

### 5. `promo_code_usage` — Log การใช้โค้ด

```sql
CREATE TABLE promo_code_usage (
    id            SERIAL PRIMARY KEY,
    promo_code_id INTEGER NOT NULL REFERENCES promo_codes(id),
    user_id       INTEGER NOT NULL REFERENCES users(id),
    payment_id    INTEGER REFERENCES payments(id),
    discount_amount NUMERIC(10,2) NOT NULL,
    created_at    TIMESTAMP NOT NULL DEFAULT NOW()
);
```

### 6. `marketing_daily_reports` — เก็บ KPI รายวัน

```sql
CREATE TABLE marketing_daily_reports (
    id              SERIAL PRIMARY KEY,
    report_date     DATE NOT NULL UNIQUE,
    revenue         NUMERIC(12,2) NOT NULL DEFAULT 0,
    new_members     INTEGER NOT NULL DEFAULT 0,
    active_members  INTEGER NOT NULL DEFAULT 0,
    expired_members INTEGER NOT NULL DEFAULT 0,
    trials_sold     INTEGER NOT NULL DEFAULT 0,
    vip_sold        INTEGER NOT NULL DEFAULT 0,
    god_mode_sold   INTEGER NOT NULL DEFAULT 0,
    flash_sale_sold INTEGER NOT NULL DEFAULT 0,
    teaser_sent     INTEGER NOT NULL DEFAULT 0,
    teaser_clicks   INTEGER NOT NULL DEFAULT 0,
    comeback_sent   INTEGER NOT NULL DEFAULT 0,
    comeback_respond INTEGER NOT NULL DEFAULT 0,
    comeback_convert INTEGER NOT NULL DEFAULT 0,
    trial_dm_sent   INTEGER NOT NULL DEFAULT 0,
    trial_dm_click  INTEGER NOT NULL DEFAULT 0,
    trial_dm_convert INTEGER NOT NULL DEFAULT 0,
    funnel_data     JSONB,                    -- detailed funnel breakdown
    ai_insights     TEXT,                     -- AI-generated action items
    created_at      TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX ix_mdr_date ON marketing_daily_reports(report_date DESC);
```

### 7. `scheduled_promotions` — โปรโมทตั้งเวลา

```sql
CREATE TABLE scheduled_promotions (
    id            SERIAL PRIMARY KEY,
    name          VARCHAR(255) NOT NULL,
    message_text  TEXT NOT NULL,
    target_groups JSONB NOT NULL,               -- array of group slugs
    scheduled_at  TIMESTAMP NOT NULL,
    repeat_type   VARCHAR(20) DEFAULT 'once',   -- once / daily / weekly
    is_active     BOOLEAN NOT NULL DEFAULT TRUE,
    is_sent       BOOLEAN NOT NULL DEFAULT FALSE,
    sent_at       TIMESTAMP,
    created_by    INTEGER REFERENCES dashboard_admins(id),
    created_at    TIMESTAMP NOT NULL DEFAULT NOW()
);
```

### Existing Tables — ไม่ต้องแก้

ตารางเดิมทั้ง 21 ตารางใช้ได้ตรงๆ ไม่ต้อง migrate:
- `users`, `payments`, `subscriptions`, `packages`
- `flash_sales`, `content_queue`, `content_schedule`
- `group_registry`, `admin_logs`
- `comeback_dm_log`, `trial_dm_log`, `teaser_clicks`
- `leads`, `ad_campaigns`, `ad_performance`
- `broadcast_log`, `broadcasts`, `expiry_notifications`
- `fetched_content_log`, `group_migrations`

---

## 📄 Wireframe แต่ละหน้า

---

### 🔑 Login Page

```
┌─────────────────────────────────────────┐
│                                         │
│         🙏 เจริญพร Dashboard            │
│                                         │
│    ┌─────────────────────────────┐      │
│    │ Telegram ID                 │      │
│    └─────────────────────────────┘      │
│    ┌─────────────────────────────┐      │
│    │ Password          👁        │      │
│    └─────────────────────────────┘      │
│                                         │
│    [    เข้าสู่ระบบ    ]                │
│                                         │
│    ─── หรือ ───                          │
│    [ 📱 ส่ง OTP ผ่าน Bot ]              │
│                                         │
└─────────────────────────────────────────┘
```

---

### หน้า 1: 📊 ภาพรวม (Dashboard Home)

```
┌──────────────────────────────────────────────────────────────────────┐
│ 🔧 Sidebar              │  📊 ภาพรวม                               │
│                          │                                          │
│ 📊 ภาพรวม    ◄──        │  ┌──────────┐ ┌──────────┐ ┌──────────┐  │
│ 👥 ลูกค้า               │  │ วันนี้    │ │ สัปดาห์   │ │ เดือนนี้  │  │
│ 💰 การเงิน              │  │ ฿12,300  │ │ ฿45,200  │ │ ฿180,500 │  │
│ 📢 โปรโมชั่น             │  │ +15% ▲   │ │ +8% ▲    │ │ -3% ▼    │  │
│ 📸 Content              │  └──────────┘ └──────────┘ └──────────┘  │
│ 📱 กลุ่ม                │                                          │
│ 👨‍💼 ทีมงาน              │  ┌────────────────────────────────────┐  │
│ ⚙️ ตั้งค่า               │  │  📈 กราฟรายได้ 30 วัน (Line Chart) │  │
│ 📊 Marketing            │  │                                    │  │
│                          │  │  ~~~~~~~~~/\~~~~                   │  │
│ ─────────               │  │  ~~~~~/\~~    ~~~~~/\~~            │  │
│ 👤 บอสไผ่               │  │  ~~~/          ~~~~   ~~~~~        │  │
│ 🏷️ Owner                │  │                                    │  │
│ [🚪 ออก]                │  └────────────────────────────────────┘  │
│                          │                                          │
│                          │  ┌──────────┐ ┌──────────┐ ┌──────────┐  │
│                          │  │ Active   │ │ Expired  │ │ New Today│  │
│                          │  │ 245      │ │ 89       │ │ 12       │  │
│                          │  └──────────┘ └──────────┘ └──────────┘  │
│                          │                                          │
│                          │  ┌─────────────────┐ ┌────────────────┐  │
│                          │  │ ⚡ Flash Sale     │ │ 📨 COMEBACK DM │  │
│                          │  │ ● เปิดอยู่       │ │ ส่ง: 150       │  │
│                          │  │ 12/30 sold      │ │ ตอบ: 23        │  │
│                          │  │ เหลือ 4 ชม.     │ │ สมัคร: 8       │  │
│                          │  └─────────────────┘ └────────────────┘  │
│                          │                                          │
│                          │  ┌─────────────────┐ ┌────────────────┐  │
│                          │  │ 🎯 Trial DM      │ │ 📸 Content Bot │  │
│                          │  │ ส่ง: 200         │ │ Teaser: 45/12  │  │
│                          │  │ คลิก: 34         │ │ Queue: 28 รูป  │  │
│                          │  │ สมัคร: 11        │ │                │  │
│                          │  └─────────────────┘ └────────────────┘  │
│                          │                                          │
│                          │  🚨 Alert                                │
│                          │  ┌────────────────────────────────────┐  │
│                          │  │ ⏳ 3 สลิปรอ approve                 │  │
│                          │  │ ⚠️ 1 ลูกค้าแจ้งปัญหา (SOS)         │  │
│                          │  │ 🔔 5 สมาชิกหมดอายุวันนี้            │  │
│                          │  └────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────────┘
```

---

### หน้า 2: 👥 ลูกค้า

```
┌──────────────────────────────────────────────────────────────────────┐
│ Sidebar │  👥 ลูกค้า                                                │
│         │                                                           │
│         │  🔍 ค้นหา: [ชื่อ / Telegram ID / Username_________]      │
│         │                                                           │
│         │  Filter: [All ▼] [Active] [Expired] [Trial] [GOD MODE]   │
│         │                                                           │
│         │  ┌─────┬────────────┬────────────┬────────┬───────┬──────┬──────────┐
│         │  │ #   │ ชื่อ        │ Telegram ID│ แพ็กเกจ │ สถานะ │ หมดอายุ│ ยอดจ่าย  │
│         │  ├─────┼────────────┼────────────┼────────┼───────┼──────┼──────────┤
│         │  │ 1   │ @somchai   │ 123456789  │ VIP 30 │ 🟢    │ 15/4 │ ฿1,800   │
│         │  │ 2   │ @nattapon  │ 987654321  │ GOD 90 │ 🟢    │ 22/6 │ ฿5,097   │
│         │  │ 3   │ @expired01 │ 111222333  │ VIP 30 │ 🔴    │ 1/3  │ ฿300     │
│         │  │ ... │            │            │        │       │      │          │
│         │  └─────┴────────────┴────────────┴────────┴───────┴──────┴──────────┘
│         │                                                           │
│         │  ◀ 1 2 3 ... 12 ▶     แสดง [25 ▼] รายการ                  │
│         │                                                           │
│         │  ═══════════════════════════════════════════════           │
│         │  📋 รายละเอียดลูกค้า (Modal / Side Panel)                 │
│         │  ┌────────────────────────────────────────────┐           │
│         │  │ 👤 @somchai (ID: 123456789)                │           │
│         │  │ แพ็กเกจ: VIP 30 วัน │ สถานะ: 🟢 Active     │           │
│         │  │ หมดอายุ: 15 เม.ย. 2026 │ สมาชิกตั้งแต่: 1 ม.ค.│        │
│         │  │                                            │           │
│         │  │ 💳 ประวัติ Payment                          │           │
│         │  │ ┌──────┬──────┬────────┬───────┐           │           │
│         │  │ │ วันที่ │ จำนวน│ วิธี    │ สถานะ │           │           │
│         │  │ │ 15/3  │ ฿300│ SLIP   │ ✅     │           │           │
│         │  │ │ 15/2  │ ฿300│ SLIP   │ ✅     │           │           │
│         │  │ └──────┴──────┴────────┴───────┘           │           │
│         │  │                                            │           │
│         │  │ 📊 Subscription History                     │           │
│         │  │ VIP 30 → VIP 30 → VIP 30 (ต่อ 3 ครั้ง)     │           │
│         │  │                                            │           │
│         │  │ 📱 กลุ่มที่อยู่: G300, G500                 │           │
│         │  │                                            │           │
│         │  │ ── Actions ──                               │           │
│         │  │ [✅ ต่อเวลา] [🆙 อัพเกรด] [📩 ส่ง DM]       │           │
│         │  │ [🔨 เตะ]     [🚫 แบน]                       │           │
│         │  └────────────────────────────────────────────┘           │
└──────────────────────────────────────────────────────────────────────┘
```

**Sub-modals:**
- **ต่อเวลา:** Dropdown เลือกจำนวนวัน (7/15/30/60/90/365) + ปุ่มยืนยัน
- **อัพเกรด:** เลือกแพ็กเกจใหม่ → คำนวณส่วนต่าง → ยืนยัน
- **ส่ง DM:** Text area พิมพ์ข้อความ → ส่งผ่าน bot
- **เตะ:** เลือกกลุ่ม(s) → ยืนยัน → เรียก bot API
- **แบน:** ยืนยัน + เหตุผล → แบน + เตะทุกกลุ่ม

---

### หน้า 3: 💰 การเงิน

```
┌──────────────────────────────────────────────────────────────────────┐
│ Sidebar │  💰 การเงิน                                              │
│         │                                                           │
│         │  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐     │
│         │  │ วันนี้    │ │ สัปดาห์   │ │ เดือนนี้  │ │ ปีนี้    │     │
│         │  │ ฿12,300  │ │ ฿45,200  │ │ ฿180,500 │ │ ฿1.2M   │     │
│         │  └──────────┘ └──────────┘ └──────────┘ └──────────┘     │
│         │                                                           │
│         │  🚨 สลิปรอ Approve (3 รายการ)                             │
│         │  ┌──────────────────────────────────────────────────┐     │
│         │  │ 👤 @user1 │ ฿300 │ 10:23 │ [🖼 ดูสลิป]          │     │
│         │  │                  [✅ อนุมัติ] [❌ ปฏิเสธ]         │     │
│         │  │ 👤 @user2 │ ฿500 │ 09:45 │ [🖼 ดูสลิป]          │     │
│         │  │                  [✅ อนุมัติ] [❌ ปฏิเสธ]         │     │
│         │  └──────────────────────────────────────────────────┘     │
│         │                                                           │
│         │  Filter: [วันที่ 📅] [สถานะ ▼] [วิธีชำระ ▼]               │
│         │                                                           │
│         │  ┌─────┬────────┬──────────┬───────┬────────┬───────┐    │
│         │  │ วันที่│ ชื่อ    │ จำนวน    │ วิธี  │ แพ็กเกจ │ สถานะ │    │
│         │  ├─────┼────────┼──────────┼───────┼────────┼───────┤    │
│         │  │ 20/3│@user1  │ ฿300     │ SLIP  │ VIP 30 │ ⏳     │    │
│         │  │ 20/3│@user2  │ ฿1,299   │ SLIP  │ GOD 90 │ ✅     │    │
│         │  │ 19/3│@user3  │ ฿99      │ PROMPT│ Trial  │ ✅     │    │
│         │  └─────┴────────┴──────────┴───────┴────────┴───────┘    │
│         │                                                           │
│         │  ┌─────────────────────┐ ┌───────────────────────┐       │
│         │  │ 📊 รายได้ตามแพ็กเกจ  │ │ 📊 รายได้ตามวิธีชำระ   │       │
│         │  │ (Pie/Bar Chart)     │ │ (Pie Chart)           │       │
│         │  │                     │ │                       │       │
│         │  │ Trial ██░░ 15%      │ │ SLIP █████ 70%        │       │
│         │  │ VIP30 ████░ 40%     │ │ PROMPT ██░ 20%        │       │
│         │  │ VIP90 ███░░ 25%     │ │ TRUE  █░░ 8%          │       │
│         │  │ GOD   ██░░░ 20%     │ │ CRYPTO ░░ 2%          │       │
│         │  └─────────────────────┘ └───────────────────────┘       │
└──────────────────────────────────────────────────────────────────────┘
```

---

### หน้า 4: 📢 โปรโมชั่น

```
┌──────────────────────────────────────────────────────────────────────┐
│ Sidebar │  📢 โปรโมชั่น                                            │
│         │                                                           │
│         │  Tab: [⚡ Flash Sale] [🎟 Promo Code] [📅 ตั้งเวลาโปรโมท]  │
│         │                                                           │
│         │  ═══ ⚡ Flash Sale ═══                                     │
│         │  [+ สร้าง Flash Sale ใหม่]                                 │
│         │                                                           │
│         │  Form (expand):                                           │
│         │  ┌─────────────────────────────────────────┐              │
│         │  │ ชื่อ: [_________________________]       │              │
│         │  │ แพ็กเกจ: [เลือกแพ็กเกจ ▼]               │              │
│         │  │ ราคา Flash: [____] (เดิม: ฿300)         │              │
│         │  │ จำนวน Slot: [30__]                      │              │
│         │  │ เริ่ม: [📅 วันที่] [🕐 เวลา]              │              │
│         │  │ สิ้นสุด: [📅 วันที่] [🕐 เวลา]            │              │
│         │  │         [💾 บันทึก] [❌ ยกเลิก]           │              │
│         │  └─────────────────────────────────────────┘              │
│         │                                                           │
│         │  ประวัติ Flash Sale:                                       │
│         │  ┌──────┬──────────┬────────┬──────┬───────┬───────┐     │
│         │  │ ชื่อ  │ ราคา     │ slot   │ sold │ วันที่ │ สถานะ │     │
│         │  │ Sale1│ ฿199     │ 30     │ 30   │ 15/3  │ ✅ จบ │     │
│         │  │ Sale2│ ฿149     │ 20     │ 12   │ 20/3  │ 🟢 live│    │
│         │  └──────┴──────────┴────────┴──────┴───────┴───────┘     │
│         │                                                           │
│         │  ═══ 🎟 Promo Code ═══                                    │
│         │  [+ สร้าง Promo Code ใหม่]                                │
│         │                                                           │
│         │  Form:                                                    │
│         │  ┌─────────────────────────────────────────┐              │
│         │  │ Code: [____________] [🎲 สุ่ม]          │              │
│         │  │ ส่วนลด: [__]%                           │              │
│         │  │ ใช้ได้: [__] ครั้ง                       │              │
│         │  │ แพ็กเกจ: [ทุกแพ็กเกจ ▼]                  │              │
│         │  │ หมดอายุ: [📅_________]                   │              │
│         │  │         [💾 สร้าง]                       │              │
│         │  └─────────────────────────────────────────┘              │
│         │                                                           │
│         │  ═══ 📅 ตั้งเวลาโปรโมท ═══                                │
│         │  [+ สร้างโปรโมทอัตโนมัติ]                                  │
│         │  ┌─────────────────────────────────────────┐              │
│         │  │ ข้อความ: [textarea__________________]   │              │
│         │  │ กลุ่ม: [☑ ฟรี1] [☑ ฟรี2] [☑ VIP1]      │              │
│         │  │ เวลา: [🕐 ___] ทุก: [วัน ▼]             │              │
│         │  │         [💾 บันทึก]                      │              │
│         │  └─────────────────────────────────────────┘              │
└──────────────────────────────────────────────────────────────────────┘
```

---

### หน้า 5: 📸 Content Management

```
┌──────────────────────────────────────────────────────────────────────┐
│ Sidebar │  📸 Content Management                                    │
│         │                                                           │
│         │  Tab: [📦 Queue] [📅 Schedule] [📊 สถิติ] [✏️ Caption]     │
│         │                                                           │
│         │  ═══ 📦 Content Queue (28 รูปรอโพสต์) ═══                  │
│         │                                                           │
│         │  ┌─ Drop Zone ─────────────────────────────┐              │
│         │  │  🖼️ ลากรูปมาวางที่นี่ หรือ [📂 เลือกไฟล์]│              │
│         │  └─────────────────────────────────────────┘              │
│         │                                                           │
│         │  ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐           │
│         │  │ 🖼️ 1 │ │ 🖼️ 2 │ │ 🖼️ 3 │ │ 🖼️ 4 │ │ 🖼️ 5 │           │
│         │  │photo │ │photo │ │video │ │photo │ │photo │           │
│         │  │[🗑️]  │ │[🗑️]  │ │[🗑️]  │ │[🗑️]  │ │[🗑️]  │           │
│         │  └──────┘ └──────┘ └──────┘ └──────┘ └──────┘           │
│         │  (drag to reorder)                                       │
│         │                                                           │
│         │  ═══ 📅 Teaser Schedule ═══                               │
│         │  ┌──────────┬──────────┬──────────┬────────┐             │
│         │  │ เวลา     │ กลุ่ม    │ สถานะ    │ แก้ไข  │             │
│         │  │ 10:00    │ ฟรี ทั้งหมด│ ✅ ส่งแล้ว│        │             │
│         │  │ 14:00    │ ฟรี ทั้งหมด│ ⏳ รอส่ง │ [✏️]   │             │
│         │  │ 20:00    │ ฟรี ทั้งหมด│ ⏳ รอส่ง │ [✏️]   │             │
│         │  └──────────┴──────────┴──────────┴────────┘             │
│         │                                                           │
│         │  ═══ 📊 สถิติ Teaser ═══                                  │
│         │  ┌──────────┬────────┬────────┬────────────┐             │
│         │  │ วันที่    │ ส่ง    │ คลิก   │ Conversion │             │
│         │  │ 20/3     │ 3      │ 45     │ 8 (17.8%) │             │
│         │  │ 19/3     │ 3      │ 38     │ 5 (13.2%) │             │
│         │  └──────────┴────────┴────────┴────────────┘             │
│         │                                                           │
│         │  ═══ ✏️ Caption Template ═══                              │
│         │  ┌─────────────────────────────────────────┐              │
│         │  │ [textarea - แก้ caption ได้]            │              │
│         │  │ ตัวแปร: {name}, {price}, {link}         │              │
│         │  │                     [💾 บันทึก]          │              │
│         │  └─────────────────────────────────────────┘              │
└──────────────────────────────────────────────────────────────────────┘
```

---

### หน้า 6: 📱 กลุ่ม Telegram

```
┌──────────────────────────────────────────────────────────────────────┐
│ Sidebar │  📱 กลุ่ม Telegram                                       │
│         │                                                           │
│         │  Tab: [🆓 กลุ่มฟรี (11)] [👑 กลุ่ม VIP (6)]              │
│         │                                                           │
│         │  [+ เพิ่มกลุ่มใหม่]                                       │
│         │                                                           │
│         │  ┌──────┬──────────────────┬────────────┬────────┬──────┐ │
│         │  │ Slug │ ชื่อกลุ่ม          │ Chat ID    │ สมาชิก │ Tier │ │
│         │  ├──────┼──────────────────┼────────────┼────────┼──────┤ │
│         │  │ G300 │ เจริญพร VIP 300   │ -100xxx    │ 156    │ 300  │ │
│         │  │ G500 │ เจริญพร VIP 500   │ -100xxx    │ 89     │ 500  │ │
│         │  │ SSS  │ เจริญพร GOD       │ -100xxx    │ 34     │ GOD  │ │
│         │  │ ...  │                  │            │        │      │ │
│         │  └──────┴──────────────────┴────────────┴────────┴──────┘ │
│         │                                                           │
│         │  กดเลือกกลุ่ม → Panel ขวา:                                │
│         │  ┌─────────────────────────────────────────┐              │
│         │  │ 📱 G300 — เจริญพร VIP 300                │              │
│         │  │ Chat ID: -100xxx  │ สมาชิก: 156          │              │
│         │  │                                         │              │
│         │  │ [🔗 สร้าง Invite Link]                   │              │
│         │  │ [✏️ แก้ไขข้อมูล]                          │              │
│         │  │ [🗑️ ลบกลุ่ม]                             │              │
│         │  │                                         │              │
│         │  │ 🛡️ Guardian Bot Settings:                │              │
│         │  │ เวลาเช็ค: [ทุก __ ชม.]                   │              │
│         │  │ เตะหลัง expired: [__ ชม.]                │              │
│         │  │ [💾 บันทึก Guardian]                      │              │
│         │  │                                         │              │
│         │  │ 👥 สมาชิก (ล่าสุด 50):                   │              │
│         │  │ @user1 (VIP30, 🟢), @user2 (GOD, 🟢)... │              │
│         │  └─────────────────────────────────────────┘              │
└──────────────────────────────────────────────────────────────────────┘
```

---

### หน้า 7: 👨‍💼 ทีมงาน

```
┌──────────────────────────────────────────────────────────────────────┐
│ Sidebar │  👨‍💼 ทีมงาน                                              │
│         │                                                           │
│         │  [+ เพิ่มทีมงาน] (Owner เท่านั้น)                         │
│         │                                                           │
│         │  ┌──────────────┬────────────┬──────────┬───────┬──────┐  │
│         │  │ ชื่อ          │ Telegram ID│ ยศ       │ สถานะ │ จัดการ│  │
│         │  ├──────────────┼────────────┼──────────┼───────┼──────┤  │
│         │  │ บอสไผ่       │ xxx        │ 👑 Owner │ 🟢    │ -    │  │
│         │  │ เฮียโค้ก     │ xxx        │ 🛡️ Admin │ 🟢    │ [✏️] │  │
│         │  │ เซียนจู      │ xxx        │ 🛡️ Admin │ 🟢    │ [✏️] │  │
│         │  └──────────────┴────────────┴──────────┴───────┴──────┘  │
│         │                                                           │
│         │  Form เพิ่มทีมงาน:                                        │
│         │  ┌─────────────────────────────────────────┐              │
│         │  │ Telegram ID: [_______________]          │              │
│         │  │ ชื่อ: [_______________]                  │              │
│         │  │ รหัสผ่าน: [_______________]              │              │
│         │  │ ยศ: [Owner ▼ / Admin / Moderator]       │              │
│         │  │            [💾 เพิ่ม]                    │              │
│         │  └─────────────────────────────────────────┘              │
│         │                                                           │
│         │  📋 Activity Log (กดดูแต่ละคน):                           │
│         │  ┌──────────┬──────────────────────────┬──────────┐      │
│         │  │ เวลา     │ Action                   │ รายละเอียด│      │
│         │  │ 20/3 10:23│ approve_payment          │ #1234    │      │
│         │  │ 20/3 09:15│ ban_user                 │ @user99  │      │
│         │  │ 19/3 22:00│ create_flash_sale        │ Sale 3   │      │
│         │  └──────────┴──────────────────────────┴──────────┘      │
└──────────────────────────────────────────────────────────────────────┘
```

---

### หน้า 8: ⚙️ ตั้งค่า

```
┌──────────────────────────────────────────────────────────────────────┐
│ Sidebar │  ⚙️ ตั้งค่า                                              │
│         │                                                           │
│         │  Tab: [🤖 Bots] [📦 แพ็กเกจ] [⏰ Schedule] [📩 DM] [💾 Backup]│
│         │                                                           │
│         │  ═══ 🤖 Bot Tokens (Owner เท่านั้น) ═══                   │
│         │  ┌─────────────────────────────────────────┐              │
│         │  │ Sales Bot:    ****...Lg_g  [👁 แสดง] [✏️]│              │
│         │  │ Admin Bot:    ****...Xx2w  [👁 แสดง] [✏️]│              │
│         │  │ Guardian Bot: ****...Yp3q  [👁 แสดง] [✏️]│              │
│         │  │ Content Bot:  ****...Zk4r  [👁 แสดง] [✏️]│              │
│         │  │ Announce Bot: ****...Lg_g  [👁 แสดง] [✏️]│              │
│         │  └─────────────────────────────────────────┘              │
│         │                                                           │
│         │  Admin Telegram IDs:                                      │
│         │  [xxx, xxx, xxx____________] [💾 บันทึก]                   │
│         │                                                           │
│         │  ═══ 📦 แพ็กเกจ ═══                                      │
│         │  ┌──────────────┬──────┬──────┬────────┬──────┐          │
│         │  │ ชื่อ          │ ราคา │ วัน  │ Tier   │ จัดการ│          │
│         │  │ Trial 24 ชม.  │ ฿99  │ 1    │ TRIAL  │ [✏️🗑️]│          │
│         │  │ VIP 30 วัน    │ ฿300 │ 30   │ VIP    │ [✏️🗑️]│          │
│         │  │ VIP 90 วัน    │ ฿500 │ 90   │ VIP    │ [✏️🗑️]│          │
│         │  │ GOD 90 วัน    │฿1,299│ 90   │ GOD    │ [✏️🗑️]│          │
│         │  │ GOD 365 วัน   │฿2,499│ 365  │ GOD    │ [✏️🗑️]│          │
│         │  └──────────────┴──────┴──────┴────────┴──────┘          │
│         │  [+ เพิ่มแพ็กเกจ]                                         │
│         │                                                           │
│         │  ═══ ⏰ Schedule ═══                                      │
│         │  Teaser เวลา: [10:00] [14:00] [20:00] [+ เพิ่ม]          │
│         │  Flash Sale auto: [เปิด/ปิด] เวลา: [___]                 │
│         │                                                           │
│         │  ═══ 📩 DM Settings ═══                                   │
│         │  COMEBACK DM: [__] คน/วัน │ Delay: [__] วินาที            │
│         │  Trial DM: [__] คน/วัน │ Delay: [__] วินาที               │
│         │  ข้อความ COMEBACK: [textarea___________] [💾]             │
│         │  ข้อความ Trial: [textarea___________] [💾]                │
│         │                                                           │
│         │  ═══ 💾 Backup ═══                                       │
│         │  Auto backup: [เปิด/ปิด] ทุก: [วัน ▼]                    │
│         │  [📥 Backup ตอนนี้] [📤 Restore]                          │
│         │  ประวัติ backup:                                           │
│         │  2026-03-20 03:00 — 12.3MB [📥 ดาวน์โหลด]                 │
│         │  2026-03-19 03:00 — 12.1MB [📥 ดาวน์โหลด]                 │
└──────────────────────────────────────────────────────────────────────┘
```

---

### หน้า 9: 📊 Marketing Analytics

```
┌──────────────────────────────────────────────────────────────────────┐
│ Sidebar │  📊 Marketing Analytics                                   │
│         │                                                           │
│         │  📅 ช่วงเวลา: [7 วัน ▼] [14 วัน] [30 วัน] [Custom 📅]    │
│         │                                                           │
│         │  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐     │
│         │  │ Revenue  │ │ New Mbr  │ │ Churn    │ │ Conv.Rate│     │
│         │  │ ฿180.5K  │ │ 89       │ │ 12       │ │ 17.8%    │     │
│         │  │ +12% ▲   │ │ +5% ▲    │ │ -8% ▼    │ │ +2.1% ▲  │     │
│         │  └──────────┘ └──────────┘ └──────────┘ └──────────┘     │
│         │                                                           │
│         │  ┌────────────────────────────────────┐                   │
│         │  │ 📈 สัปดาห์ vs สัปดาห์ (Bar Chart)   │                   │
│         │  │                                    │                   │
│         │  │ W1: ████████ ฿45K                  │                   │
│         │  │ W2: ██████████ ฿52K                │                   │
│         │  │ W3: █████████ ฿48K                 │                   │
│         │  │ W4: ████████████ ฿55K              │                   │
│         │  └────────────────────────────────────┘                   │
│         │                                                           │
│         │  🔄 Conversion Funnel                                     │
│         │  ┌────────────────────────────────────┐                   │
│         │  │ กลุ่มฟรี (2,340 คน)                │                   │
│         │  │ ████████████████████████████ 100%   │                   │
│         │  │                                    │                   │
│         │  │ คลิก Teaser (456 คน)               │                   │
│         │  │ █████████░░░░░░░░░░░░░░░░░░ 19.5%  │                   │
│         │  │                                    │                   │
│         │  │ ซื้อ Trial (89 คน)                  │                   │
│         │  │ ███░░░░░░░░░░░░░░░░░░░░░░░░ 3.8%   │                   │
│         │  │                                    │                   │
│         │  │ ซื้อ VIP/GOD (34 คน)               │                   │
│         │  │ █░░░░░░░░░░░░░░░░░░░░░░░░░░ 1.5%   │                   │
│         │  └────────────────────────────────────┘                   │
│         │                                                           │
│         │  🤖 AI Action Items                                       │
│         │  ┌────────────────────────────────────┐                   │
│         │  │ 1. Trial→VIP conv. ต่ำ → ลองลด    │                   │
│         │  │    ราคา VIP หรือเพิ่ม incentive     │                   │
│         │  │ 2. COMEBACK DM round 2 ได้ผลดี     │                   │
│         │  │    → เพิ่ม round 3 discount 30%    │                   │
│         │  │ 3. Teaser เวลา 20:00 คลิกเยอะสุด  │                   │
│         │  │    → เพิ่ม slot เวลานี้              │                   │
│         │  └────────────────────────────────────┘                   │
│         │                                                           │
│         │  📋 ประวัติ Daily Report                                   │
│         │  ┌──────────┬────────┬──────┬──────┬──────────┐          │
│         │  │ วันที่    │ Revenue│ New  │ Churn│ Conv.    │          │
│         │  │ 20/3     │ ฿12.3K │ 12   │ 2    │ 17.8%   │          │
│         │  │ 19/3     │ ฿10.8K │ 8    │ 3    │ 15.2%   │          │
│         │  │ 18/3     │ ฿14.1K │ 15   │ 1    │ 19.3%   │          │
│         │  └──────────┴────────┴──────┴──────┴──────────┘          │
└──────────────────────────────────────────────────────────────────────┘
```

---

## 🔌 API Endpoints

### Auth

| Method | Endpoint | Description | Role |
|--------|----------|-------------|------|
| POST | `/api/auth/login` | Login (telegram_id + password) → JWT | Public |
| POST | `/api/auth/otp/request` | ส่ง OTP ผ่าน bot | Public |
| POST | `/api/auth/otp/verify` | ยืนยัน OTP → JWT | Public |
| POST | `/api/auth/logout` | Revoke JWT | Any |
| GET | `/api/auth/me` | ข้อมูล admin ปัจจุบัน | Any |
| PUT | `/api/auth/password` | เปลี่ยนรหัสผ่าน | Any |

### Dashboard (หน้า 1)

| Method | Endpoint | Description | Role |
|--------|----------|-------------|------|
| GET | `/api/dashboard/summary` | รายได้ วัน/สัปดาห์/เดือน + เปรียบเทียบ | Any |
| GET | `/api/dashboard/revenue-chart?days=30` | กราฟรายได้ | Any |
| GET | `/api/dashboard/members-stats` | Active/Expired/New | Any |
| GET | `/api/dashboard/flash-sale-status` | Flash Sale สถานะปัจจุบัน | Any |
| GET | `/api/dashboard/dm-stats` | COMEBACK + Trial DM สถิติ | Admin+ |
| GET | `/api/dashboard/content-stats` | Teaser + Queue สถิติ | Admin+ |
| GET | `/api/dashboard/alerts` | รายการ alert สำคัญ | Any |

### ลูกค้า (หน้า 2)

| Method | Endpoint | Description | Role |
|--------|----------|-------------|------|
| GET | `/api/customers` | รายการลูกค้า (pagination, search, filter) | Any |
| GET | `/api/customers/{id}` | รายละเอียดลูกค้า | Any |
| GET | `/api/customers/{id}/payments` | ประวัติ payment | Any |
| GET | `/api/customers/{id}/subscriptions` | subscription history | Any |
| GET | `/api/customers/{id}/groups` | กลุ่มที่อยู่ | Any |
| POST | `/api/customers/{id}/extend` | ต่อเวลา (body: days) | Admin+ |
| POST | `/api/customers/{id}/upgrade` | อัพเกรดแพ็กเกจ (body: package_id) | Admin+ |
| POST | `/api/customers/{id}/kick` | เตะออกจากกลุ่ม (body: group_ids) | Admin+ |
| POST | `/api/customers/{id}/ban` | แบน (body: reason) | Admin+ |
| POST | `/api/customers/{id}/unban` | ปลดแบน | Admin+ |
| POST | `/api/customers/{id}/dm` | ส่ง DM (body: message) | Any |

### การเงิน (หน้า 3)

| Method | Endpoint | Description | Role |
|--------|----------|-------------|------|
| GET | `/api/payments` | รายการ payment (pagination, filter) | Any |
| GET | `/api/payments/pending` | สลิปรอ approve | Any |
| POST | `/api/payments/{id}/approve` | อนุมัติสลิป | Any |
| POST | `/api/payments/{id}/reject` | ปฏิเสธสลิป (body: reason) | Any |
| GET | `/api/payments/summary` | สรุปรายได้ วัน/สัปดาห์/เดือน/ปี | Admin+ |
| GET | `/api/payments/chart/by-package?days=30` | กราฟแยกแพ็กเกจ | Admin+ |
| GET | `/api/payments/chart/by-method?days=30` | กราฟแยกวิธีชำระ | Admin+ |
| GET | `/api/payments/{id}/slip` | ดูรูปสลิป | Any |

### โปรโมชั่น (หน้า 4)

| Method | Endpoint | Description | Role |
|--------|----------|-------------|------|
| GET | `/api/flash-sales` | รายการ Flash Sale ทั้งหมด | Admin+ |
| POST | `/api/flash-sales` | สร้าง Flash Sale ใหม่ | Admin+ |
| PUT | `/api/flash-sales/{id}` | แก้ไข Flash Sale | Admin+ |
| DELETE | `/api/flash-sales/{id}` | ลบ Flash Sale | Admin+ |
| POST | `/api/flash-sales/{id}/toggle` | เปิด/ปิด Flash Sale | Admin+ |
| GET | `/api/promo-codes` | รายการ Promo Code | Admin+ |
| POST | `/api/promo-codes` | สร้าง Promo Code | Admin+ |
| PUT | `/api/promo-codes/{id}` | แก้ไข Promo Code | Admin+ |
| DELETE | `/api/promo-codes/{id}` | ลบ Promo Code | Admin+ |
| POST | `/api/promo-codes/{id}/toggle` | เปิด/ปิด | Admin+ |
| GET | `/api/scheduled-promotions` | รายการตั้งเวลาโปรโมท | Admin+ |
| POST | `/api/scheduled-promotions` | สร้างโปรโมทตั้งเวลา | Admin+ |
| PUT | `/api/scheduled-promotions/{id}` | แก้ไข | Admin+ |
| DELETE | `/api/scheduled-promotions/{id}` | ลบ | Admin+ |

### Content (หน้า 5)

| Method | Endpoint | Description | Role |
|--------|----------|-------------|------|
| GET | `/api/content/queue` | รายการ content queue | Any |
| POST | `/api/content/queue/upload` | อัพโหลดรูป (multipart) | Admin+ |
| DELETE | `/api/content/queue/{id}` | ลบรูปจาก queue | Admin+ |
| POST | `/api/content/queue/reorder` | เรียงลำดับ (body: id_order[]) | Admin+ |
| GET | `/api/content/schedule` | ตาราง teaser schedule | Any |
| PUT | `/api/content/schedule/{id}` | แก้ schedule | Admin+ |
| POST | `/api/content/schedule` | เพิ่ม schedule | Admin+ |
| GET | `/api/content/teaser-stats?days=30` | สถิติ teaser | Admin+ |
| GET | `/api/content/caption-template` | ดู caption template | Any |
| PUT | `/api/content/caption-template` | แก้ caption template | Admin+ |

### กลุ่ม Telegram (หน้า 6)

| Method | Endpoint | Description | Role |
|--------|----------|-------------|------|
| GET | `/api/groups` | รายการกลุ่มทั้งหมด | Admin+ |
| POST | `/api/groups` | เพิ่มกลุ่ม | Admin+ |
| PUT | `/api/groups/{id}` | แก้ไขกลุ่ม | Admin+ |
| DELETE | `/api/groups/{id}` | ลบกลุ่ม | Owner |
| GET | `/api/groups/{id}/members` | สมาชิกในกลุ่ม | Admin+ |
| POST | `/api/groups/{id}/invite-link` | สร้าง invite link | Admin+ |
| GET | `/api/groups/{id}/guardian-settings` | ตั้งค่า Guardian | Owner |
| PUT | `/api/groups/{id}/guardian-settings` | แก้ตั้งค่า Guardian | Owner |

### ทีมงาน (หน้า 7)

| Method | Endpoint | Description | Role |
|--------|----------|-------------|------|
| GET | `/api/team` | รายการทีมงาน | Admin+ |
| POST | `/api/team` | เพิ่มทีมงาน | Owner |
| PUT | `/api/team/{id}` | แก้ไข (ยศ, สถานะ) | Owner |
| DELETE | `/api/team/{id}` | ลบทีมงาน | Owner |
| GET | `/api/team/{id}/activity` | Activity log ของคน | Admin+ |
| PUT | `/api/team/{id}/password-reset` | Reset รหัสผ่าน | Owner |

### ตั้งค่า (หน้า 8)

| Method | Endpoint | Description | Role |
|--------|----------|-------------|------|
| GET | `/api/settings/bots` | Bot tokens (masked) | Owner |
| PUT | `/api/settings/bots` | แก้ bot tokens | Owner |
| GET | `/api/settings/admin-ids` | Admin Telegram IDs | Owner |
| PUT | `/api/settings/admin-ids` | แก้ Admin IDs | Owner |
| GET | `/api/settings/packages` | รายการแพ็กเกจ | Admin+ |
| POST | `/api/settings/packages` | เพิ่มแพ็กเกจ | Owner |
| PUT | `/api/settings/packages/{id}` | แก้แพ็กเกจ | Owner |
| DELETE | `/api/settings/packages/{id}` | ลบแพ็กเกจ | Owner |
| GET | `/api/settings/schedule` | Schedule settings | Admin+ |
| PUT | `/api/settings/schedule` | แก้ schedule | Admin+ |
| GET | `/api/settings/dm` | DM settings | Admin+ |
| PUT | `/api/settings/dm` | แก้ DM settings | Admin+ |
| GET | `/api/settings/backup` | ประวัติ backup | Owner |
| POST | `/api/settings/backup/now` | Backup ตอนนี้ | Owner |
| POST | `/api/settings/backup/restore` | Restore | Owner |
| GET | `/api/settings/backup/{id}/download` | ดาวน์โหลด backup | Owner |

### Marketing Analytics (หน้า 9)

| Method | Endpoint | Description | Role |
|--------|----------|-------------|------|
| GET | `/api/marketing/kpi?days=30` | KPI ทั้งหมด | Admin+ |
| GET | `/api/marketing/weekly-comparison` | สัปดาห์ vs สัปดาห์ | Admin+ |
| GET | `/api/marketing/funnel?days=30` | Conversion funnel | Admin+ |
| GET | `/api/marketing/ai-insights` | AI action items (ล่าสุด) | Admin+ |
| GET | `/api/marketing/daily-reports` | ประวัติ daily report (pagination) | Admin+ |
| GET | `/api/marketing/daily-reports/{date}` | Report วันนั้นๆ | Admin+ |

---

## 📁 File Structure

```
/root/charoenpon/dashboard/
├── DASHBOARD_SPEC.md          # This file
├── backend/
│   ├── main.py                # FastAPI app, CORS, static mount
│   ├── config.py              # Settings, DB URL, JWT secret
│   ├── database.py            # asyncpg pool
│   ├── auth/
│   │   ├── __init__.py
│   │   ├── router.py          # /api/auth/*
│   │   ├── jwt.py             # JWT create/verify
│   │   ├── otp.py             # OTP via Telegram bot
│   │   └── dependencies.py    # get_current_admin, require_role
│   ├── routers/
│   │   ├── dashboard.py       # /api/dashboard/*
│   │   ├── customers.py       # /api/customers/*
│   │   ├── payments.py        # /api/payments/*
│   │   ├── promotions.py      # /api/flash-sales/*, /api/promo-codes/*
│   │   ├── content.py         # /api/content/*
│   │   ├── groups.py          # /api/groups/*
│   │   ├── team.py            # /api/team/*
│   │   ├── settings.py        # /api/settings/*
│   │   └── marketing.py       # /api/marketing/*
│   ├── models/
│   │   ├── __init__.py
│   │   └── schemas.py         # Pydantic models (request/response)
│   ├── services/
│   │   ├── telegram.py        # Bot API wrapper (DM, kick, invite)
│   │   ├── backup.py          # pg_dump / restore
│   │   └── analytics.py       # KPI calculation, AI insights
│   └── migrations/
│       └── 001_dashboard_tables.sql  # New tables SQL
├── frontend/
│   ├── index.html             # SPA shell
│   ├── css/
│   │   └── style.css          # Dark theme styles
│   ├── js/
│   │   ├── app.js             # Router, auth state, navigation
│   │   ├── api.js             # Fetch wrapper with JWT
│   │   ├── components/
│   │   │   ├── sidebar.js
│   │   │   ├── header.js
│   │   │   ├── modal.js
│   │   │   ├── table.js       # Reusable data table
│   │   │   ├── chart.js       # Chart.js wrapper
│   │   │   └── toast.js       # Notification toasts
│   │   └── pages/
│   │       ├── login.js
│   │       ├── dashboard.js
│   │       ├── customers.js
│   │       ├── payments.js
│   │       ├── promotions.js
│   │       ├── content.js
│   │       ├── groups.js
│   │       ├── team.js
│   │       ├── settings.js
│   │       └── marketing.js
│   └── assets/
│       └── logo.png
└── docker/
    └── Dockerfile.dashboard   # Python + static files
```

---

## 🎨 Design System (Dark Theme)

### Colors
```
Background:    #0f0f23 (deep navy)
Surface:       #1a1a2e (card bg)
Surface-2:     #16213e (elevated cards)
Primary:       #e94560 (accent red-pink)
Primary-hover: #c81d4e
Success:       #00d2d3
Warning:       #feca57
Error:         #ff6b6b
Text:          #eaeaea
Text-muted:    #8892b0
Border:        #233554
```

### Typography
```
Font:          'Inter', sans-serif (Google Fonts)
Heading:       font-weight: 700
Body:          font-weight: 400
Mono:          'JetBrains Mono' (for IDs, codes)
```

### Components
- **Cards:** rounded-lg (12px), border 1px border-color, shadow
- **Buttons:** rounded-md (8px), transition 200ms
- **Tables:** striped rows (alternate surface/surface-2), hover highlight
- **Sidebar:** fixed left 260px, collapsible on mobile
- **Modals:** backdrop blur, centered, max-width 600px
- **Charts:** Chart.js with dark theme config
- **Toasts:** bottom-right stack, auto-dismiss 5s

---

## 🚀 Priority — ทำหน้าไหนก่อน

### Phase 1 — Core (สัปดาห์ 1-2) ⭐
1. **🔑 Login + Auth system** — JWT, role-based middleware
2. **📊 ภาพรวม (Dashboard Home)** — ใช้งานได้ทันที, เห็นภาพรวม
3. **💰 การเงิน** — อนุมัติสลิปจากเว็บ (pain point หลัก!)
4. **👥 ลูกค้า** — ดู + ค้นหา + actions พื้นฐาน

### Phase 2 — Operations (สัปดาห์ 3-4)
5. **📢 โปรโมชั่น** — สร้าง Flash Sale + Promo Code จากเว็บ
6. **📸 Content** — จัดการ queue + schedule
7. **📱 กลุ่ม Telegram** — ดู + จัดการกลุ่ม

### Phase 3 — Admin & Analytics (สัปดาห์ 5-6)
8. **👨‍💼 ทีมงาน** — จัดการยศ + activity log
9. **⚙️ ตั้งค่า** — config ต่างๆ
10. **📊 Marketing Analytics** — KPI + funnel + AI insights

### เหตุผล Priority:
- **การเงิน** ก่อนเพราะอนุมัติสลิปผ่านเว็บ = ลด friction ทันที
- **ลูกค้า** ตามมาเพราะต้องจัดการสมาชิกทุกวัน
- **โปรโมชั่น** สำคัญเพราะ Flash Sale เป็นรายได้หลัก
- **Marketing** ทำท้ายสุดเพราะ data ต้องสะสมจากการใช้งานจริงก่อน

---

## 🔗 Integration Notes

### Telegram Bot API Integration
Dashboard backend จะเรียก Telegram Bot API โดยตรงสำหรับ:
- **ส่ง DM ลูกค้า** → ใช้ Sales Bot token
- **เตะสมาชิก** → ใช้ Guardian Bot token (`banChatMember`)
- **สร้าง invite link** → ใช้ Admin Bot token (`createChatInviteLink`)
- **ส่ง OTP** → ใช้ Admin Bot token (`sendMessage` to admin)

### Shared DB
Dashboard อ่าน/เขียน DB เดียวกับ bots ทั้งหมด (charoenpon PostgreSQL)
- **อ่านอย่างเดียว:** users, payments, subscriptions, content_queue, teaser_clicks, etc.
- **เขียนด้วย:** payments (approve/reject), flash_sales, group_registry, packages
- **Tables ใหม่ของ dashboard:** dashboard_admins, dashboard_sessions, dashboard_activity_log, promo_codes, promo_code_usage, marketing_daily_reports, scheduled_promotions

### Port Mapping (ไม่ชนกับ port อื่น)
```
5432  — PostgreSQL (existing)
8010  — Dashboard (NEW) ✅
```

---

## 📝 Notes

1. **SPA Architecture:** ทุก page render จาก `index.html` เดียว, JS router จัดการ path
2. **No framework:** ใช้ vanilla JS เหมือน PATA FOODS — ไม่มี React/Vue
3. **Chart.js:** CDN load สำหรับกราฟทั้งหมด
4. **JWT Storage:** เก็บใน `localStorage`, แนบ `Authorization: Bearer <token>` ทุก request
5. **Real-time alerts:** ใช้ polling ทุก 30 วินาที สำหรับสลิปรอ approve (ไม่ต้อง WebSocket)
6. **Mobile responsive:** Sidebar collapse เป็น hamburger menu บนมือถือ
7. **Activity logging:** ทุก write action ต้อง log ใน `dashboard_activity_log`
