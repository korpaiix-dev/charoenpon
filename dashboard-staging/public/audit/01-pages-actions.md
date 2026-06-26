# 📋 Dashboard Audit — ทุกหน้า ทุกปุ่ม ทุก endpoint

> Generated 2026-06-27 จาก app.js · 19 pages · 159 onclick · 143 API calls

## 🎯 วิธีอ่าน
- 🟢 SAFE = GET endpoint · ไม่กระทบลูกค้า
- 🟡 MED = POST/PATCH/DELETE · ระบบเปลี่ยน แต่ลูกค้าไม่กระทบตรง (เช่นแก้ promo code)
- 🔴 HIGH = POST/PATCH/DELETE · กระทบลูกค้าจริง · กลับยาก (เช่น approve, ban, DM)

## 📊 สรุปต่อหน้า

| Page | คลิกได้ | APIs | Risk 🔴 |
|---|---|---|---|
| 📋 Activity Log | 0 | 1 | 0 |
| 📸 Content | 2 | 0 | 0 |
| 👥 ลูกค้า | 4 | 0 | 0 |
| 📊 ภาพรวม | 6 | 8 | 0 |
| 💰 การเงิน | 4 | 1 | 0 |
| 🎰 กาชา | 7 | 4 | 0 |
| 📱 กลุ่ม | 5 | 1 | 0 |
| 📥 กล่องรอจัดการ | 11 | 1 | 0 |
| 📊 Marketing | 2 | 6 | 0 |
| 💬 Prae Logs | 4 | 2 | 0 |
| 🎁 โปรโมชั่น + บอท | 8 | 0 | 0 |
| 📜 Campaign เก่า (ในแท็บ 🎁) | 5 | 1 | 0 |
| 💳 บัญชีรับเงิน | 7 | 1 | 0 |
| ⚙️ ตั้งค่า | 5 | 0 | 0 |
| 👨‍💼 ทีมงาน | 5 | 1 | 0 |
| 📋 งานวันนี้ | 10 | 2 | 0 |
| **รวม** | **85** | **29** | **0** |

## 📄 รายละเอียดต่อหน้า

### 📋 Activity Log

- Render: `renderActivityLog`
- 1,505 chars
- 0 clickable elements
- 1 unique APIs

**API endpoints:**

- 🟢 SAFE `GET /dashboard/activity-log/filters`

---

### 🚫 รายการแบน (ใน ⚙️)

- Render: `renderBannedTable`
- 5,869 chars
- 4 clickable elements
- 1 unique APIs

**API endpoints:**

- 🟢 SAFE `GET ${endpoint}`

---

### 📸 Content

- Render: `renderContent`
- 576 chars
- 2 clickable elements
- 0 unique APIs

---

### 📝 Notes (ใน Customer 360)

- Render: `renderCustomerNotes`
- 2,511 chars
- 3 clickable elements
- 1 unique APIs

**API endpoints:**

- 🟢 SAFE `GET /customers/`

---

### 👥 ลูกค้า

- Render: `renderCustomers`
- 1,182 chars
- 4 clickable elements
- 0 unique APIs

---

### 📊 ภาพรวม

- Render: `renderDashboard`
- 15,962 chars
- 6 clickable elements
- 8 unique APIs

**API endpoints:**

- 🟢 SAFE `GET /dashboard/alerts`
- 🟢 SAFE `GET /dashboard/content-stats`
- 🟢 SAFE `GET /dashboard/dm-stats`
- 🟢 SAFE `GET /dashboard/flash-sale-status`
- 🟢 SAFE `GET /dashboard/members-stats`
- 🟢 SAFE `GET /dashboard/revenue-summary`
- 🟢 SAFE `GET /dashboard/sales-analytics`
- 🟢 SAFE `GET /dashboard/summary`

---

### 💰 การเงิน

- Render: `renderFinance`
- 2,203 chars
- 4 clickable elements
- 1 unique APIs

**API endpoints:**

- 🟢 SAFE `GET /payments/summary`

---

### 🎰 กาชา

- Render: `renderGacha`
- 11,449 chars
- 7 clickable elements
- 4 unique APIs

**API endpoints:**

- 🟢 SAFE `GET /gacha-admin/overview`
- 🟢 SAFE `GET /gacha-admin/prizes`
- 🟢 SAFE `GET /gacha-admin/recent-pulls`
- 🟢 SAFE `GET /gacha-admin/top-winners`

---

### 📱 กลุ่ม

- Render: `renderGroups`
- 2,459 chars
- 5 clickable elements
- 1 unique APIs

**API endpoints:**

- 🟢 SAFE `GET /groups/categorized`

---

### 📥 กล่องรอจัดการ

- Render: `renderInbox`
- 10,341 chars
- 11 clickable elements
- 1 unique APIs

**API endpoints:**

- 🟢 SAFE `GET /dashboard/inbox`

---

### 📊 Marketing

- Render: `renderMarketing`
- 15,099 chars
- 2 clickable elements
- 6 unique APIs

**API endpoints:**

- 🟢 SAFE `GET /marketing/ai-insights`
- 🟢 SAFE `GET /marketing/funnel`
- 🟢 SAFE `GET /marketing/kpi`
- 🟢 SAFE `GET /marketing/links`
- 🟢 SAFE `GET /marketing/roi`
- 🟢 SAFE `GET /marketing/weekly-comparison`

---

### 💬 Prae Logs

- Render: `renderPraeLogs`
- 4,622 chars
- 4 clickable elements
- 2 unique APIs

**API endpoints:**

- 🟢 SAFE `GET /prae-logs/summary`
- 🟢 SAFE `GET /prae-logs/top-users`

---

### 🎁 โปรโมชั่น + บอท

- Render: `renderPromoManager`
- 2,520 chars
- 8 clickable elements
- 0 unique APIs

---

### 📜 Campaign เก่า (ในแท็บ 🎁)

- Render: `renderPromotions`
- 1,916 chars
- 5 clickable elements
- 1 unique APIs

**API endpoints:**

- 🟢 SAFE `GET /promo-stats`

---

### 💳 บัญชีรับเงิน

- Render: `renderReceivers`
- 6,797 chars
- 7 clickable elements
- 1 unique APIs

**API endpoints:**

- 🟢 SAFE `GET /receivers`

---

### 🔄 Relay Sync (ใน กลุ่ม)

- Render: `renderRelaySync`
- 2,346 chars
- 2 clickable elements
- 1 unique APIs

**API endpoints:**

- 🟢 SAFE `GET /groups/relay-sync-status`

---

### ⚙️ ตั้งค่า

- Render: `renderSettings`
- 1,316 chars
- 5 clickable elements
- 0 unique APIs

---

### 👨‍💼 ทีมงาน

- Render: `renderTeam`
- 3,443 chars
- 5 clickable elements
- 1 unique APIs

**API endpoints:**

- 🟢 SAFE `GET /team`

---

### 📋 งานวันนี้

- Render: `renderToday`
- 8,457 chars
- 10 clickable elements
- 2 unique APIs

**API endpoints:**

- 🟢 SAFE `GET /dashboard/alerts`
- 🟢 SAFE `GET /dashboard/summary`

---

## 🔴 รายการ HIGH RISK ทั้งหมด

ทุก endpoint ในรายการนี้ถ้ากด = กระทบลูกค้าหรือระบบทันที:

