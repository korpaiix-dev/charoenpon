#!/usr/bin/env bash
# Cron wrapper — runs the Python script directly on host using host's python.
set -e
cd /root/charoenpon
set -a; source /root/charoenpon/.env 2>/dev/null; set +a
mkdir -p /root/charoenpon/logs
python3 /root/charoenpon/scripts/post_promo_to_groups.py \
  >> /root/charoenpon/logs/promo_may_cron.log 2>&1
