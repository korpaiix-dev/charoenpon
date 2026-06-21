# 🏗 Charoenpon — System Architecture & Runbook

## ภาพรวมระบบ

**Stack:** Python 3.11 + FastAPI + SQLAlchemy async + asyncpg + PostgreSQL + Docker Compose
**VPS:** DigitalOcean Singapore (139.59.123.146)
**Domain:** telebord.net (dashboard) + 5 bots ที่ Telegram

## Containers (14)

| Container | หน้าที่ |
|---|---|
| postgres | PostgreSQL DB |
| sales-bot | bot ขาย / ลูกค้าจ่ายเงิน / Slip2Go / loyalty schedulers |
| guardian-bot | kick expired members + retry worker + shaker draw |
| admin-bot | คำสั่งแอดมิน (/find, /receivers, /welcome_stats, manual approve buttons) |
| content-bot | Distribute content เข้าห้อง VIP |
| discord-bot | sync ลูกค้า + activity log |
| broadcast-worker | broadcast queue processor |
| finance-scheduler | weekly/monthly reports → Discord/Sheets |
| manager-agent | AI analysis |
| monitor | system health metrics |
| relay-bot | repost content จาก source bot ไป multi-groups |
| dashboard | FastAPI backend + panda-monitor |
| gacha-api | gacha game API |
| backup-cron | reserved (not used) |

## Critical flows

### 1. Customer Pays (Slip2Go auto)
```
User uploads slip → sales-bot
  → check duplicate slip_hash → blacklist sender/slip → sender_ring
  → Slip2Go OCR → receiver match → amount match
  → apply_payment_approval(SLIP2GO_AUTO)
    → STEP 0: Idempotency check (skip ถ้า payment_id+CONFIRMED+sub already exists)
    → STEP 1-2.5: User + sender_ring + blacklist
    → STEP 3-8: Tier resolve → end_date → expire old subs
    → STEP 9: Mark payment CONFIRMED
    → STEP 10: Create Subscription (skip GACHA)
    → STEP 11: GACHA credit upsert
    → STEP 12: SHAKER number assignment (TIER_100)
    → STEP 12.5: First-payment onboarding rewards (gacha + discount credit)
    → STEP 13-14: Mark birthday/comeback promo used
    → STEP 15: Discount credit apply
    → STEP 17: log_admin_action
    → STEP 19: Generate invite links via Guardian bot
    → STEP 20-22: Send customer DM with links + admin notify
```

### 2. Loyalty rank-up (every 6h scheduler)
```
loyalty_rank_check_6h scheduler in sales-bot
  → compute_rank_for_user (DIAMOND/SILVER/BRONZE/NONE)
  → promote_user_to_rank
    → pg_advisory_xact_lock(7777, user_id) — prevent concurrent
    → SELECT users FOR UPDATE
    → Check rank_higher
    → Atomic UPDATE WHERE prev_rank
    → If success: _award_silver/_award_bronze/_award_diamond
    → Silver: generate_invite_links_for_user(TIER_1299) → DM with links
```

### 3. Welcome Journey (4 stages)
```
User /start → send_instant_welcome (stage 301, instant)
  → check user_paid + has_received_round (skip if yes)
  → save_log (UNIQUE constraint kicks in if duplicate)
  → DM with promo code

welcome_journey_hourly scheduler
  → stages 302 (3h), 303 (12h), 304 (23h)
  → discount 25% off
```

### 4. Comeback DM (daily 10:00)
```
comeback_dm_daily_1000
  → find expired users 7-30 days
  → A/B variant test (round 1-3)
  → DM with promo code → user clicks → buys with discount
```

### 5. Exit Survey (daily 11:00)
```
exit_survey_daily_1100
  → find users who expired in last 24h
  → DM 4-stage ladder (50/40/30/20%)
```

### 6. Kick Expired (every 6h)
```
kick_expired_6h in guardian-bot
  → SELECT subs ACTIVE WHERE end_date < NOW (skip lifetime)
  → MARK as EXPIRED
  → kick from all VIP groups
```

## Critical Tables

| Table | Purpose |
|---|---|
| users | ลูกค้า — telegram_id, first_name, total_spent (trigger sync), loyalty_rank |
| payments | ทุก transaction — status (CONFIRMED/PENDING/REJECTED), slip_hash, slip_trans_ref |
| subscriptions | sub records — payment_id NULL = grant (loyalty หรือ admin) |
| packages | TIER_100/300/500/1299/2499/ADD500/GACHA_1/3/10 |
| group_registry | กลุ่ม VIP/FREE/SHAKER (slug + chat_id) |
| gachapon_credits | per-user credit balance |
| user_discount_credits | wheel discount balance |
| comeback_dm_log | log ทุก DM (round 1-3 = comeback, 301-304 = welcome) |
| exit_survey_log | log Exit Survey DM |
| slip2go_retry_queue | retry queue สำหรับ Slip2Go timeout |
| admin_logs | audit trail ทุก action |
| birthday_upgrade_offers | birthday bonus offers |
| banned_senders | scam sender names |
| banned_slips | scam slip hashes |

## Security checks (ลำดับ in apply_payment_approval)

1. **slip_trans_ref dup** (Slip2Go transRef unique)
2. **slip_hash dup** (SHA256 of slip image bytes)
3. **sender_ring** (ชื่อผู้ส่งใช้กับ user_id อื่นในระยะเวลา)
4. **banned_senders** (scam blacklist)
5. **banned_slips** (slip_hash blacklist)
6. **receiver match** (receiver_pool — ตรงกับบัญชีที่บอสเปิด)
7. **amount match** (acceptable_amounts — TIER price)

## Scheduler timeline (BKK timezone)

| เวลา | Job | Frequency | Purpose |
|---|---|---|---|
| 03:30 | DB backup | daily | pg_dump → /root/backups (7d rotate) |
| 09:00 | send_expiring_list | daily | DM ลูกค้าหมดอายุพรุ่งนี้ |
| 10:00 | comeback_dm_daily | daily | ลูกค้าหมดอายุ 7-30d |
| 11:00 | exit_survey_daily | daily | ลูกค้าหมดอายุ 24h |
| 15:00 | god_mode_upsell_dm | daily | TIER_300→1299 upsell |
| 22:00 | daily_report | daily | yesterday revenue/customer summary |
| every 2 min | slip2go_retry_2m | interval | retry Slip2Go OCR ที่ค้าง |
| every 6h | kick_expired_6h | interval | kick expired members |
| every 6h | loyalty_rank_check_6h | interval | rank up + award rewards |
| every 1h | welcome_journey_hourly | interval | DM 4 stages |
| every 1h | payment_health_hourly | interval | 11 anomaly checks → alert |
| every 6h | slip2go_balance_check_6h | interval | balance + alert |
| Sunday 14:00 | referral_promo_broadcast | weekly | broadcast |

## Files ของ Code Critical

```
shared/
  payment_approval.py     ← service หลัก (22 steps + STEP 0 idempotency)
  customer_dm.py          ← DM ลูกค้าผ่าน sales bot (มี retry + fail alert)
  admin_alert.py          ← notify_admin_group (ห้อง สลิป) + notify_admin_report (ห้อง Report)
  notify.py               ← routing hub (event → channels)
  loyalty_rank.py         ← V2 + advisory lock
  welcome_journey.py      ← 4 stages + partial UNIQUE index
  slip2go.py              ← Slip2Go HTTP (มี circuit breaker for 429)
  slip2go_retry_worker.py ← retry queue (มี skip-if-CONFIRMED guard)
  payment_health_check.py ← 11 anomaly checks
  pricing.py              ← TIER prices + active_campaigns
  ban_service.py          ← /ban + blacklist
  models.py               ← SQLAlchemy ORM
  database.py             ← async engine + get_session
  
bots/sales_bot/
  main.py                 ← schedulers + handlers register
  handlers/
    start.py              ← /start + send_instant_welcome
    payment.py            ← slip flow (รับสลิป → Slip2Go → apply_approval)
    packages.py           ← เลือก tier
  payment_util/
    approve.py            ← _approve_payment wrapper (must pass source)
    truemoney_handler.py  ← TrueWallet voucher (dup guard 120s)
    
bots/guardian_bot/
  main.py                 ← scheduler register (kick_expired, retry, etc.)
  scheduler.py            ← kick_expired_members, shaker_draw, send_expiring_list
  group_monitor.py        ← generate_invite_links_for_user
  
bots/admin_bot/
  handlers/admin_tools.py ← /find, /receivers, /welcome_stats, manual approve buttons
  
tests/regression/         ← 12 tests
  test_idempotency_naca.py
  test_loyalty_advisory_lock.py
  test_welcome_unique.py
  test_system_invariants.py
  
pre_deploy.sh             ← ห้าม deploy ถ้า test fail
backup_daily.sh           ← cron 03:30
```

## Common runbook procedures

### บอตค้าง / ไม่ตอบ
```
docker ps | grep charoenpon
docker logs --tail 50 charoenpon-sales-bot
docker restart charoenpon-sales-bot
```

### Slip2Go ไม่อ่าน
```
# ดู balance
curl -s "https://api.slip2go.com/..."

# Manual approve button ใน admin chat (✅ 300/500/1299/2499 buttons)
# หรือใช้ /approvepid 869 ใน admin-bot
```

### ลูกค้าจ่ายแต่ไม่ได้รับลิงก์
```
# 1. หา payment
docker exec charoenpon-postgres psql -U postgres -d charoenpon -c "
SELECT * FROM payments WHERE user_id = (SELECT id FROM users WHERE telegram_id = XXX);"

# 2. ดู sub
SELECT * FROM subscriptions WHERE user_id = ... ORDER BY id DESC;

# 3. ถ้า sub มีแต่ลูกค้าไม่มีลิงก์ → manual generate
docker exec charoenpon-sales-bot python3 -c "
import asyncio, sys; sys.path.insert(0, '/app')
async def t():
    import telegram, os
    from bots.guardian_bot.group_monitor import generate_invite_links_for_user
    g = telegram.Bot(token=os.environ['GUARDIAN_BOT_TOKEN'])
    await g.initialize()
    links = await generate_invite_links_for_user(g, TG_ID, PACKAGE_ID)
    print(links)
    await g.shutdown()
asyncio.run(t())"
```

### ลูกค้าหมดอายุไม่ถูก kick
```
# Trigger manual
docker exec charoenpon-guardian-bot python3 -c "
import asyncio, sys; sys.path.insert(0, '/app')
async def t():
    import telegram, os
    from bots.guardian_bot.scheduler import kick_expired_members
    b = telegram.Bot(token=os.environ['GUARDIAN_BOT_TOKEN'])
    await b.initialize()
    print(await kick_expired_members(b))
    await b.shutdown()
asyncio.run(t())"
```

### Health check
```
docker exec charoenpon-sales-bot python3 -c "
import asyncio, sys; sys.path.insert(0, '/app')
from shared.payment_health_check import health_check_payment_system
print(asyncio.run(health_check_payment_system()))"

# หรือดู Panda Monitor — https://telebord.net/panda-monitor?token=panda2026
```

### Pre-deploy ก่อน push code ใหม่
```
bash /root/charoenpon/pre_deploy.sh
# ต้อง pass ทุก 5 checks
```

### Restore DB from backup
```
ls /root/backups/charoenpon_*.sql.gz
zcat /root/backups/charoenpon_LATEST.sql.gz | \
  docker exec -i charoenpon-postgres psql -U postgres -d charoenpon
# ดู RECOVERY.md สำหรับขั้นตอนเต็ม
```

---

*สร้างโดย แพนด้า — 2026-06-21*
