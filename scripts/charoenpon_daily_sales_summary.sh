#!/usr/bin/env bash
# charoenpon_daily_summary.sh
# Daily sales summary for เจริญพร — sends a formatted HTML message to Admin Telegram group.
# Replaces the openclaw "daily-sales-summary" cron job.
#
# Cron: 59 16 * * *  /root/scripts/charoenpon_daily_summary.sh   (= 23:59 ICT)
#
# Env required (sourced from /root/charoenpon/.env):
#   ADMIN_BOT_TOKEN       Telegram bot token
#   ADMIN_GROUP_CHAT_ID   Telegram chat id of admin group (e.g. -1003830920430)
#
# Usage:
#   ./charoenpon_daily_summary.sh           -> send to Telegram
#   ./charoenpon_daily_summary.sh --dry     -> print to stdout only, do not send
#   ./charoenpon_daily_summary.sh --test    -> send with [TEST] prefix

set -euo pipefail

ENV_FILE="/root/charoenpon/.env"
PG_CONTAINER="charoenpon-postgres"
PG_DB="charoenpon"
PG_USER="postgres"
DRY=0
TEST_PREFIX=""

for arg in "$@"; do
  case "$arg" in
    --dry)  DRY=1 ;;
    --test) TEST_PREFIX="🧪 <b>[TEST]</b>%0A" ;;
  esac
done

# Load env (ADMIN_BOT_TOKEN, ADMIN_GROUP_CHAT_ID)
if [ -f "$ENV_FILE" ]; then
  # shellcheck disable=SC1090
  set -a; source "$ENV_FILE"; set +a
else
  echo "ERROR: $ENV_FILE not found" >&2; exit 1
fi

: "${ADMIN_BOT_TOKEN:?ADMIN_BOT_TOKEN missing in env}"
: "${ADMIN_GROUP_CHAT_ID:?ADMIN_GROUP_CHAT_ID missing in env}"

# Helper: run a SQL query, return result as plain text (one cell or one column)
psql_q() {
  docker exec "$PG_CONTAINER" psql -U "$PG_USER" -d "$PG_DB" -t -A -F'|' -c "$1" 2>/dev/null
}

TODAY=$(date +%F)
YESTERDAY=$(date -d 'yesterday' +%F)
MONTH_START=$(date +%Y-%m-01)
NOW_STR=$(date '+%Y-%m-%d %H:%M')

# ── Aggregations ─────────────────────────────────────────────────────────────
# Today (CONFIRMED): count + sum
TODAY_CONFIRMED=$(psql_q "
  SELECT COALESCE(COUNT(*),0) || '|' || COALESCE(SUM(amount),0)::text
  FROM payments
  WHERE status='CONFIRMED' AND created_at::date = CURRENT_DATE
")
TODAY_COUNT=${TODAY_CONFIRMED%%|*}
TODAY_SUM=${TODAY_CONFIRMED##*|}

# Yesterday (CONFIRMED): count + sum
YDAY_CONFIRMED=$(psql_q "
  SELECT COALESCE(COUNT(*),0) || '|' || COALESCE(SUM(amount),0)::text
  FROM payments
  WHERE status='CONFIRMED' AND created_at::date = CURRENT_DATE - INTERVAL '1 day'
")
YDAY_COUNT=${YDAY_CONFIRMED%%|*}
YDAY_SUM=${YDAY_CONFIRMED##*|}

# Today by package
BY_PACKAGE=$(psql_q "
  SELECT pkg.name || ' — ' || COUNT(*)::text || ' ออเดอร์ / ' || SUM(p.amount)::text || ' บาท'
  FROM payments p LEFT JOIN packages pkg ON pkg.id=p.package_id
  WHERE p.status='CONFIRMED' AND p.created_at::date = CURRENT_DATE
  GROUP BY pkg.name
  ORDER BY SUM(p.amount) DESC
")

# Subscriptions Active
SUB_ACTIVE=$(psql_q "SELECT COUNT(*) FROM subscriptions WHERE status='ACTIVE'")

# Pending payments (anything not CONFIRMED/REJECTED — adjust if your enum has explicit PENDING)
PENDING=$(psql_q "
  SELECT COUNT(*) FROM payments
  WHERE status NOT IN ('CONFIRMED','REJECTED')
")

# Month-to-date (CONFIRMED): count + sum
MTD=$(psql_q "
  SELECT COALESCE(COUNT(*),0) || '|' || COALESCE(SUM(amount),0)::text
  FROM payments
  WHERE status='CONFIRMED' AND created_at >= date_trunc('month', CURRENT_DATE)
")
MTD_COUNT=${MTD%%|*}
MTD_SUM=${MTD##*|}

# Compare today vs yesterday (delta)
delta() {
  awk -v a="$1" -v b="$2" 'BEGIN{
    d = a - b; pct = (b==0) ? 0 : (d*100.0/b);
    sign = (d>=0) ? "🟢 +" : "🔴 ";
    printf "%s%.0f บาท (%+.1f%%)", sign, d, pct
  }'
}
DELTA_SUM=$(delta "${TODAY_SUM:-0}" "${YDAY_SUM:-0}")

# ── Format message (HTML) ────────────────────────────────────────────────────
# Telegram HTML: supports <b>, <i>, <code>, <pre>, <a href>
# Use \n for newlines, %0A in URL-encoded form for sendMessage GET
nl=$'\n'

# Build per-package block
BY_PKG_BLOCK=""
if [ -n "$BY_PACKAGE" ]; then
  while IFS= read -r line; do
    [ -n "$line" ] && BY_PKG_BLOCK+="• $line${nl}"
  done <<< "$BY_PACKAGE"
else
  BY_PKG_BLOCK="<i>(ไม่มี order ใน CONFIRMED วันนี้)</i>${nl}"
fi

MSG="📊 <b>สรุปยอดขายเจริญพร — ${TODAY}</b>${nl}"
MSG+="<i>${NOW_STR}</i>${nl}${nl}"
MSG+="💰 <b>วันนี้</b>: ${TODAY_COUNT} ออเดอร์ / ${TODAY_SUM} บาท${nl}"
MSG+="↳ vs เมื่อวาน: ${DELTA_SUM}${nl}"
MSG+="<i>(เมื่อวาน: ${YDAY_COUNT} ออเดอร์ / ${YDAY_SUM} บาท)</i>${nl}${nl}"
MSG+="📦 <b>แยกตามแพ็กเกจ (วันนี้):</b>${nl}${BY_PKG_BLOCK}${nl}"
MSG+="👥 <b>สมาชิก Active</b>: ${SUB_ACTIVE} คน${nl}"
MSG+="⏳ <b>รอ Approve</b>: ${PENDING} ออเดอร์${nl}${nl}"
MSG+="📅 <b>ยอดสะสมเดือน</b> (${MONTH_START} → วันนี้): ${MTD_COUNT} ออเดอร์ / ${MTD_SUM} บาท${nl}"

# ── Output / Send ────────────────────────────────────────────────────────────
if [ "$DRY" = "1" ]; then
  echo "=== DRY RUN — would send to chat $ADMIN_GROUP_CHAT_ID ==="
  echo "$MSG"
  exit 0
fi

# Send to Telegram
RESP=$(curl -s -X POST "https://api.telegram.org/bot${ADMIN_BOT_TOKEN}/sendMessage" \
  -d "chat_id=${ADMIN_GROUP_CHAT_ID}" \
  -d "parse_mode=HTML" \
  -d "disable_web_page_preview=true" \
  --data-urlencode "text=${TEST_PREFIX}${MSG}")

OK=$(echo "$RESP" | grep -oE '"ok":(true|false)' | head -1)
if [ "$OK" = '"ok":true' ]; then
  echo "Sent OK at $(date)"
else
  echo "Send FAILED at $(date): $RESP" >&2
  exit 2
fi
