#!/bin/bash
# Helper script — ส่ง alert เข้าห้อง Telegram ผ่าน docker exec
# Usage: alert_report.sh "message" [room=report|payment]

MSG="$1"
ROOM="${2:-report}"

docker exec charoenpon-sales-bot python3 -c "
import sys, asyncio, os
sys.path.insert(0, '/app')

async def t():
    if '$ROOM' == 'report':
        from shared.admin_alert import notify_admin_report
        await notify_admin_report('''$MSG'''.replace('%0A', '\n'), parse_mode='HTML')
    else:
        from shared.admin_alert import notify_admin_group
        await notify_admin_group('''$MSG'''.replace('%0A', '\n'), parse_mode='HTML')

asyncio.run(t())
" 2>/dev/null || true
