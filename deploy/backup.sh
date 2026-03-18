#!/bin/bash
# ==============================================
# Backup PostgreSQL → DigitalOcean Spaces
# รัน: ทุกคืน 02:00 น. Bangkok time
# เก็บ: 30 วัน (ลบเก่าอัตโนมัติ)
# ==============================================

set -e

# Load .env
export $(grep -v '^#' /root/charoenpon/.env | xargs)

# Config
TIMESTAMP=$(date +"%Y-%m-%d_%H-%M-%S")
BACKUP_FILE="charoenpon_${TIMESTAMP}.sql.gz"
BACKUP_DIR="/tmp/charoenpon_backup"
RETAIN_DAYS=30

echo "🗄️  เริ่ม backup: ${TIMESTAMP}"

# สร้าง tmp dir
mkdir -p ${BACKUP_DIR}

# pg_dump จาก Docker container
echo "📦 Dumping PostgreSQL..."
docker exec charoenpon-postgres pg_dump \
  -U ${POSTGRES_USER} \
  -d ${POSTGRES_DB} \
  | gzip > "${BACKUP_DIR}/${BACKUP_FILE}"

FILESIZE=$(du -sh "${BACKUP_DIR}/${BACKUP_FILE}" | cut -f1)
echo "✅ Dump เสร็จ: ${BACKUP_FILE} (${FILESIZE})"

# Upload ขึ้น DO Spaces
echo "☁️  อัพโหลดขึ้น DO Spaces..."
python3 /root/charoenpon/deploy/backup_upload.py \
  "${BACKUP_DIR}/${BACKUP_FILE}" \
  "${BACKUP_FILE}"

# ลบไฟล์ tmp
rm -f "${BACKUP_DIR}/${BACKUP_FILE}"
echo "🗑️  ลบ tmp เรียบร้อย"

# ลบ backup เก่าเกิน 30 วัน
echo "🧹 ลบ backup เก่าเกิน ${RETAIN_DAYS} วัน..."
python3 /root/charoenpon/deploy/backup_cleanup.py ${RETAIN_DAYS}

echo "🎉 Backup เสร็จสมบูรณ์: ${BACKUP_FILE}"
