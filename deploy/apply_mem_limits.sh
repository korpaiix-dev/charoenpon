#!/usr/bin/env bash
# Audit fix: per-container memory limits (VPS RAM = 3.8G, กัน 1 container OOM ลากทั้ง host)
# live + re-runnable. รันใหม่ได้ทุกครั้งหลัง recreate/reboot. format: "<mem> <mem+swap>"
set -u
declare -A MEM=(
  [postgres]="768m 1024m"  [redis]="192m 192m"
  [sales-bot]="512m 768m"  [clip-poster-bot]="768m 1536m"  [content-bot]="384m 512m"
  [dashboard]="384m 512m"  [dashboard-test]="256m 256m"     [gacha-api]="256m 256m"
  [admin-bot]="320m 320m"  [guardian-bot]="320m 320m"       [discord-bot]="320m 320m"
  [finance-scheduler]="256m 256m" [manager-agent]="320m 320m" [monitor]="256m 256m"
  [broadcast-worker]="256m 256m"  [backup-cron]="256m 256m"   [relay-bot]="256m 256m"
  [dashboard-staging]="96m 96m"
)
for name in "${!MEM[@]}"; do
  read -r m s <<< "${MEM[$name]}"
  if docker update --memory "$m" --memory-swap "$s" "charoenpon-$name" >/dev/null 2>&1; then
    echo "OK   $name -> mem=$m swap_total=$s"
  else
    echo "SKIP $name (ไม่มี container นี้ หรือ update ไม่ได้)"
  fi
done
