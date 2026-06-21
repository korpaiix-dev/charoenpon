# 🆘 Disaster Recovery — Charoenpon DB

## เมื่อใช้:
- VPS ตาย → ต้อง restore ที่อื่น
- DB corrupt / table หาย / delete ผิดพลาด
- ต้องการ rollback ไปจุดก่อนหน้า

---

## ที่เก็บ backup

```
/root/backups/charoenpon_YYYYMMDD_HHMMSS.sql.gz
```

- รัน cron daily 03:00 BKK
- เก็บ 7 วัน rotation
- ขนาด ~20-30 MB
- Format: pg_dump --clean --if-exists | gzip

---

## วิธี restore (step-by-step)

### กรณี 1: DB เสียบางส่วน (table หาย / data corrupt)

```bash
# 1. เลือก backup ที่ต้องการ
ls -lh /root/backups/

# 2. ทดสอบ gzip integrity ก่อน
gunzip -t /root/backups/charoenpon_20260621_030000.sql.gz

# 3. Stop bots (กัน write ขัดกัน)
cd /root/charoenpon
docker compose stop sales-bot guardian-bot admin-bot

# 4. Restore (pg_dump --clean --if-exists จะ drop tables ก่อน insert)
zcat /root/backups/charoenpon_20260621_030000.sql.gz | \
  docker exec -i charoenpon-postgres psql -U postgres -d charoenpon

# 5. Verify
docker exec charoenpon-postgres psql -U postgres -d charoenpon -c "
SELECT 'users' AS table, COUNT(*) FROM users
UNION ALL SELECT 'payments', COUNT(*) FROM payments
UNION ALL SELECT 'subscriptions', COUNT(*) FROM subscriptions;
"

# 6. Run pre-deploy check
bash /root/charoenpon/pre_deploy.sh

# 7. Start bots
docker compose start sales-bot guardian-bot admin-bot
```

### กรณี 2: VPS ตาย — สร้างใหม่ที่อื่น

```bash
# 1. SSH เข้า VPS ใหม่
ssh root@NEW_IP

# 2. Install docker + clone repo
apt-get update && apt-get install -y docker.io docker-compose
git clone https://github.com/charoenpon/charoenpon.git /root/charoenpon

# 3. Copy .env (จาก backup secret หรือพิมพ์ใหม่)
nano /root/charoenpon/.env

# 4. Copy backup file มา VPS ใหม่ (ผ่าน scp)
scp /root/backups/charoenpon_LATEST.sql.gz root@NEW_IP:/root/

# 5. Start postgres เปล่า
cd /root/charoenpon
docker compose up -d postgres
sleep 10

# 6. Restore
zcat /root/charoenpon_LATEST.sql.gz | \
  docker exec -i charoenpon-postgres psql -U postgres -d charoenpon

# 7. Start ทุก service
docker compose up -d

# 8. Verify
bash /root/charoenpon/pre_deploy.sh
```

### กรณี 3: Rollback (เผลอลบ data)

```bash
# 1. หา backup ก่อนเกิดเหตุ (ดูเวลา)
ls -lh /root/backups/

# 2. Drop database + restore
docker exec charoenpon-postgres psql -U postgres -c "DROP DATABASE charoenpon WITH (FORCE);"
docker exec charoenpon-postgres psql -U postgres -c "CREATE DATABASE charoenpon;"
zcat /root/backups/charoenpon_PRE_INCIDENT.sql.gz | \
  docker exec -i charoenpon-postgres psql -U postgres -d charoenpon
```

⚠️ **WARNING:** ทำ Step 2 = ลูกค้าที่ activity หลัง backup จะหาย — ใช้เป็นทางเลือกสุดท้าย

---

## วิธี restart cron backup ถ้าไม่รัน

```bash
# ดู cron entries
crontab -l

# Edit
crontab -e
# เพิ่ม:
# 0 3 * * * /root/charoenpon/backup_daily.sh >> /var/log/charoenpon_backup.log 2>&1

# Reload cron
systemctl restart cron
```

---

## ทดสอบ backup ทำงาน (manual run)

```bash
bash /root/charoenpon/backup_daily.sh
# → ดู alert ที่ห้อง Report
# → ดูไฟล์ /root/backups/
```

---

## Off-site backup (อนาคต)

ตอนนี้ backup อยู่บน VPS เดียวเท่านั้น — ถ้า VPS ตาย backup ก็หาย

**TODO Sprint E:** Auto-upload เข้า Google Drive หรือ S3 (weekly)

---

*สร้างโดย แพนด้า — 2026-06-21*

---

## ✅ DR Drill Results — 2026-06-21

**Backup tested:** charoenpon_20260621_135018.sql.gz (21MB)
**Restore time:** 4 seconds
**Verification:** 
- 6 tables count match (with expected delta for 10+ hours of production activity)
- 0 FK violations
- Complex queries (top spenders, joins) work correctly
- gzip integrity check passed

**Conclusion:** Backup procedure verified working. Can restore production DB in < 1 minute if disaster occurs.

**Next drill scheduled:** Monthly (every 22nd) via cron reminder
