#!/usr/bin/env bash
set -e
cd /root/charoenpon
set -a; source /root/charoenpon/.env 2>/dev/null; set +a
mkdir -p /root/charoenpon/logs
python3 /root/charoenpon/scripts/end_promo_revert.py \
  >> /root/charoenpon/logs/promo_revert_cron.log 2>&1
