#!/bin/bash
# Helper — ส่ง alert เข้าห้อง Telegram ผ่าน docker exec
# Usage: alert_report.sh "message" [room=report|payment]
# AUDIT FIX M3: ส่ง MSG/ROOM ผ่าน env (-e) แทน interpolate เข้า python -c (กัน injection)
MSG="$1"
ROOM="${2:-report}"
docker exec -e ALERT_MSG="$MSG" -e ALERT_ROOM="$ROOM" charoenpon-sales-bot python3 -c "
import sys, asyncio, os
sys.path.insert(0, '/app')
msg = os.environ.get('ALERT_MSG', '').replace('%0A', '\n')
room = os.environ.get('ALERT_ROOM', 'report')
async def t():
    if room == 'report':
        from shared.admin_alert import notify_admin_report
        await notify_admin_report(msg, parse_mode='HTML')
    else:
        from shared.admin_alert import notify_admin_group
        await notify_admin_group(msg, parse_mode='HTML')
asyncio.run(t())
" 2>/dev/null || true
