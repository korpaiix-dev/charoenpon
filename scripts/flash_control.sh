#!/bin/bash
# Flash sale auto-toggle — called by cron at start/end of windows.
# Usage:
#   flash_control.sh activate "Mid-Month%"   # set is_active=true
#   flash_control.sh deactivate "Mid-Month%" # set is_active=false
# Logs to /var/log/charoenpon-flash.log

set -e
ACTION="$1"
PATTERN="${2:-Mid-Month%}"
LOG=/var/log/charoenpon-flash.log
TS=$(date '+%Y-%m-%d %H:%M:%S')

case "$ACTION" in
  activate)
    SQL="UPDATE flash_sales SET is_active=true WHERE name LIKE '$PATTERN' RETURNING id, name, flash_price, is_active;"
    ;;
  deactivate)
    SQL="UPDATE flash_sales SET is_active=false WHERE name LIKE '$PATTERN' RETURNING id, name, is_active;"
    ;;
  *)
    echo "Usage: $0 {activate|deactivate} <name_pattern>"
    exit 1
    ;;
esac

OUT=$(docker exec charoenpon-postgres psql -U postgres charoenpon -c "$SQL" 2>&1)
echo "[$TS] $ACTION pattern='$PATTERN':" >> "$LOG"
echo "$OUT" >> "$LOG"
echo "$OUT"

# Notify admin group
ADMIN_GROUP_ID="-1003830920430"
BOT_TOKEN=$(grep ADMIN_BOT_TOKEN /root/charoenpon/.env | head -1 | cut -d= -f2 | tr -d '"')
if [ -n "$BOT_TOKEN" ]; then
  TEXT="⚡ Flash Sale $ACTION: $PATTERN
$(date '+%Y-%m-%d %H:%M:%S BKK')
$(echo "$OUT" | head -8)"
  curl -sS -X POST "https://api.telegram.org/bot$BOT_TOKEN/sendMessage" \
    -d "chat_id=$ADMIN_GROUP_ID" \
    -d "text=$TEXT" >/dev/null 2>&1 || echo "notify failed" >> "$LOG"
fi
