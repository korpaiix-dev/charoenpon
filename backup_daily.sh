#!/bin/bash
# Charoenpon DB Backup — รันโดย cron daily 03:00 BKK
#
# พฤติกรรม:
# 1. pg_dump → gzip → /root/backups/charoenpon_YYYYMMDD_HHMMSS.sql.gz
# 2. ลบไฟล์เก่ากว่า 7 วัน (rotation)
# 3. ตรวจขนาดไฟล์ — ถ้า < 5MB = น่าสงสัย → alert
# 4. ส่ง summary ห้อง Report (success/fail + size + duration)

set -e

BACKUP_DIR="/root/backups"
KEEP_DAYS=7
MIN_SIZE_BYTES=5242880  # 5 MB
TS=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="$BACKUP_DIR/charoenpon_${TS}.sql.gz"

mkdir -p "$BACKUP_DIR"

START_TS=$(date +%s)
ALERT_SCRIPT="/root/charoenpon/alert_report.sh"

# ─── Backup ──────────────────────────────────────────────────────
if docker exec charoenpon-postgres pg_dump -U postgres -d charoenpon --clean --if-exists 2>/dev/null | gzip > "$BACKUP_FILE"; then
    SIZE=$(stat -c%s "$BACKUP_FILE")
    SIZE_MB=$((SIZE / 1024 / 1024))
    DURATION=$(($(date +%s) - START_TS))

    # ─── Verify gzip integrity ──────────────────────────────────
    if ! gunzip -t "$BACKUP_FILE" 2>/dev/null; then
        # corrupt → alert + เก็บไฟล์ไว้ debug
        bash "$ALERT_SCRIPT" "🚨 <b>DB Backup CORRUPT</b>%0A━━━━━━━━%0A📁 $BACKUP_FILE%0A⚠️ gzip integrity check fail" "report"
        exit 1
    fi

    # ─── ถ้าเล็กกว่า threshold = น่าสงสัย ──────────────────────
    if [ "$SIZE" -lt "$MIN_SIZE_BYTES" ]; then
        bash "$ALERT_SCRIPT" "⚠️ <b>DB Backup เล็กผิดปกติ</b>%0A━━━━━━━━%0A📦 ${SIZE_MB}MB (threshold ${MIN_SIZE_BYTES}B)%0A📁 $BACKUP_FILE%0A%0A<i>เช็คว่า DB ปกติไหม</i>" "report"
    fi

    # ─── ลบเก่ากว่า 7 วัน ────────────────────────────────────
    DELETED=$(find "$BACKUP_DIR" -name "charoenpon_*.sql.gz" -mtime +$KEEP_DAYS -delete -print | wc -l)

    # ─── Summary ─────────────────────────────────────────────
    TOTAL_BACKUPS=$(find "$BACKUP_DIR" -name "charoenpon_*.sql.gz" | wc -l)
    bash "$ALERT_SCRIPT" "✅ <b>DB Backup สำเร็จ</b>%0A━━━━━━━━%0A📦 size: <b>${SIZE_MB}MB</b>%0A⏱ duration: <b>${DURATION}s</b>%0A🗂 keep: <b>${TOTAL_BACKUPS} files</b> (deleted ${DELETED} old)%0A📁 $BACKUP_FILE" "report"
else
    # Backup fail → critical alert
    bash "$ALERT_SCRIPT" "🚨🚨 <b>DB BACKUP FAIL</b>%0A━━━━━━━━%0A❌ pg_dump ล้มเหลว%0A⏰ $(date)%0A%0A<i>ต้องเช็คด่วน — DB อาจเสีย</i>" "report"
    exit 1
fi
