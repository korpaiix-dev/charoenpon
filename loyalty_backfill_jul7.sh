#!/bin/bash
# One-time backfill: demote underqualified Silver → Bronze (boss decision 2026-06-22)
# Runs once on 2026-07-07 12:00 BKK via cron, then disables itself.
set -e

LOG="/var/log/loyalty_backfill_jul7.log"
exec >> "$LOG" 2>&1

echo "=== $(date +'%F %T') Loyalty backfill starting ==="

# 1. Verify free TIER_1299 subs are all expired (safety check)
ACTIVE_FREE=$(docker exec charoenpon-postgres psql -U postgres -d charoenpon -tAc "
SELECT COUNT(*) FROM subscriptions s
JOIN users u ON u.id = s.user_id
JOIN packages pk ON pk.id = s.package_id
WHERE u.loyalty_rank='SILVER' AND u.total_spent < 1000
  AND s.payment_id IS NULL AND pk.tier = 'TIER_1299' AND s.status = 'ACTIVE';
")

if [ "$ACTIVE_FREE" -gt "0" ]; then
  echo "ABORT: $ACTIVE_FREE TIER_1299 free subs still ACTIVE — not safe to demote yet"
  bash /root/charoenpon/alert_report.sh "⚠️ Loyalty backfill 7/7 ABORTED — $ACTIVE_FREE free sub ยัง ACTIVE อยู่ ไม่ปลอดภัยต่อ" "report"
  exit 1
fi

# 2. Count affected before
BEFORE=$(docker exec charoenpon-postgres psql -U postgres -d charoenpon -tAc "
SELECT COUNT(*) FROM users
WHERE loyalty_rank = 'SILVER' AND total_spent < 1000 AND telegram_id < 9000000000;
")
echo "Will demote $BEFORE users"

# Safety: abort if count is suspicious (expected ~25)
if [ "$BEFORE" -gt "50" ]; then
  echo "ABORT: count $BEFORE > 50, suspicious"
  bash /root/charoenpon/alert_report.sh "⚠️ Loyalty backfill 7/7 ABORTED — count=$BEFORE > 50 ผิดปกติ" "report"
  exit 1
fi

# 3. Backup just-in-case
docker exec charoenpon-postgres pg_dump -U postgres -d charoenpon --table=users --data-only \
  | gzip > /root/backups/users_pre_loyalty_backfill_$(date +%Y%m%d).sql.gz

# 4. Demote + log
docker exec charoenpon-postgres psql -U postgres -d charoenpon <<SQL
BEGIN;
-- log first (for audit)
INSERT INTO admin_logs (admin_id, action, target_type, target_id, details, created_at)
SELECT 0, 'loyalty_rank_backfill_demote', 'user', id,
       'SILVER→BRONZE per boss 2026-06-22 (new rule: spend≥1000 only). total_spent=' || total_spent,
       NOW()
FROM users
WHERE loyalty_rank = 'SILVER' AND total_spent < 1000 AND telegram_id < 9000000000;

-- demote
UPDATE users SET loyalty_rank = 'BRONZE', updated_at = NOW()
WHERE loyalty_rank = 'SILVER' AND total_spent < 1000 AND telegram_id < 9000000000;
COMMIT;
SQL

# 5. Verify after
AFTER=$(docker exec charoenpon-postgres psql -U postgres -d charoenpon -tAc "
SELECT COUNT(*) FROM users
WHERE loyalty_rank = 'SILVER' AND total_spent < 1000 AND telegram_id < 9000000000;
")
DEMOTED=$((BEFORE - AFTER))

echo "Done: demoted=$DEMOTED, remaining_underqualified=$AFTER"

# 6. Alert + self-disable
bash /root/charoenpon/alert_report.sh "✅ <b>Loyalty backfill เสร็จ</b>
━━━━━━━━━━━
⬇️ Demoted Silver→Bronze: <b>$DEMOTED คน</b>
📝 ตามนโยบายใหม่ 22/6 (Silver ต้องจ่าย ฿1,000+)
🎁 Free TIER_1299 14-day ที่แจกไปแล้ว → ใช้สิทธิ์จนหมดแล้ว
🤐 ไม่ DM ลูกค้า (silent demotion)" "report"

# 7. Self-disable cron line (run once)
crontab -l | grep -v "loyalty_backfill_jul7.sh" | crontab -
echo "=== $(date +'%F %T') Cron line removed, backfill complete ==="
