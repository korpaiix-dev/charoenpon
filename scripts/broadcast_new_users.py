import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import asyncpg
import telegram as tg
from telegram.error import BadRequest, Forbidden, RetryAfter, TimedOut, NetworkError

IMAGE_PATH = Path('/tmp/charoenpon-vip-promo-never-bought-v3.png')
LOG_PATH = Path('/tmp/charoenpon_broadcast_new_users_20260426.jsonl')
RUN_ID = 'new_users_7d_never_bought_20260426_1530utc'
RATE_DELAY = 0.22  # ~4.5 users/sec, under Telegram free broadcast limit
LIMIT = int(os.environ.get('BROADCAST_LIMIT', '0'))  # 0 = no limit
DRY_RUN = os.environ.get('DRY_RUN', '0') == '1'

CAPTION = (
    '🎁 โปรสมาชิกใหม่ เจริญพร VIP\n\n'
    'ถ้ายังไม่เคยสมัคร ตอนนี้เริ่มได้ง่าย ๆ\n'
    'แพ็กเริ่มต้นเพียง 300 บาท / 30 วัน\n\n'
    '✅ เข้ากลุ่ม VIP\n'
    '✅ ดูคอนเทนต์พรีเมียม\n'
    '✅ สมัครผ่านบอทได้ทันที\n'
    '✅ แอดมินตรวจให้เร็ว\n\n'
    'สนใจสมัคร กดดูแพ็กเกจได้เลยค่ะ 👇'
)

KEYBOARD = tg.InlineKeyboardMarkup([
    [tg.InlineKeyboardButton('📦 ดูแพ็กเกจ / สมัครเลย', url='https://t.me/NamwarnJarern_bot?start=promo_new')],
    [tg.InlineKeyboardButton('💬 ติดต่อแอดมิน', url='https://t.me/zeinju_bunker')],
])

QUERY = """
SELECT u.id, u.telegram_id, u.username, u.first_name, u.created_at
FROM users u
WHERE u.created_at >= now() - interval '7 days'
  AND u.telegram_id IS NOT NULL
  AND coalesce(u.is_banned,false) = false
  AND NOT EXISTS (
    SELECT 1 FROM payments p
    WHERE p.user_id = u.id AND p.status::text IN ('CONFIRMED','confirmed')
  )
  AND NOT EXISTS (
    SELECT 1 FROM campaign_broadcast_log bl
    WHERE bl.user_id = u.id AND bl.campaign = $1
  )
ORDER BY u.created_at DESC
"""

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS campaign_broadcast_log (
    id SERIAL PRIMARY KEY,
    campaign VARCHAR(100) NOT NULL,
    user_id INTEGER NOT NULL,
    telegram_id BIGINT NOT NULL,
    status VARCHAR(30) NOT NULL,
    error TEXT,
    message_id BIGINT,
    sent_at TIMESTAMP DEFAULT NOW(),
    UNIQUE (campaign, user_id)
);
"""

async def main():
    token = os.environ['SALES_BOT_TOKEN']
    dsn = os.environ.get('DATABASE_URL')
    if not dsn:
        dsn = f"postgresql://{os.environ.get('POSTGRES_USER','postgres')}:{os.environ.get('POSTGRES_PASSWORD','postgres')}@postgres:5432/{os.environ.get('POSTGRES_DB','charoenpon')}"
    dsn = dsn.replace('postgresql+asyncpg://', 'postgresql://')

    conn = await asyncpg.connect(dsn)
    await conn.execute(CREATE_TABLE_SQL)
    rows = await conn.fetch(QUERY + (f' LIMIT {LIMIT}' if LIMIT > 0 else ''), RUN_ID)
    print(json.dumps({'event': 'target_loaded', 'count': len(rows), 'dry_run': DRY_RUN, 'limit': LIMIT}, ensure_ascii=False))

    if DRY_RUN:
        await conn.close()
        return

    bot = tg.Bot(token=token)
    await bot.initialize()

    stats = {'sent': 0, 'blocked': 0, 'bad_request': 0, 'retry': 0, 'error': 0}
    image_bytes = IMAGE_PATH.read_bytes()

    for idx, r in enumerate(rows, 1):
        user_id = r['id']
        telegram_id = r['telegram_id']
        status = 'error'
        error = None
        message_id = None
        try:
            # Use fresh BytesIO per send because Telegram client consumes the stream.
            import io
            bio = io.BytesIO(image_bytes)
            bio.name = 'charoenpon-vip-promo.png'
            msg = await bot.send_photo(
                chat_id=telegram_id,
                photo=bio,
                caption=CAPTION,
                reply_markup=KEYBOARD,
            )
            status = 'sent'
            message_id = msg.message_id
            stats['sent'] += 1
        except RetryAfter as e:
            stats['retry'] += 1
            wait_s = int(getattr(e, 'retry_after', 5)) + 1
            error = f'retry_after_{wait_s}'
            await asyncio.sleep(wait_s)
            try:
                import io
                bio = io.BytesIO(image_bytes)
                bio.name = 'charoenpon-vip-promo.png'
                msg = await bot.send_photo(chat_id=telegram_id, photo=bio, caption=CAPTION, reply_markup=KEYBOARD)
                status = 'sent'
                message_id = msg.message_id
                stats['sent'] += 1
                error = None
            except Exception as e2:
                status = 'error'
                error = f'after_retry:{type(e2).__name__}:{e2}'[:500]
                stats['error'] += 1
        except Forbidden as e:
            status = 'blocked'
            error = str(e)[:500]
            stats['blocked'] += 1
        except BadRequest as e:
            status = 'bad_request'
            error = str(e)[:500]
            stats['bad_request'] += 1
        except (TimedOut, NetworkError) as e:
            status = 'network_error'
            error = str(e)[:500]
            stats['error'] += 1
        except Exception as e:
            status = 'error'
            error = f'{type(e).__name__}:{e}'[:500]
            stats['error'] += 1

        await conn.execute(
            """
            INSERT INTO campaign_broadcast_log (campaign,user_id,telegram_id,status,error,message_id)
            VALUES ($1,$2,$3,$4,$5,$6)
            ON CONFLICT (campaign,user_id) DO UPDATE
              SET status=EXCLUDED.status, error=EXCLUDED.error, message_id=EXCLUDED.message_id, sent_at=NOW()
            """,
            RUN_ID, user_id, telegram_id, status, error, message_id,
        )
        LOG_PATH.open('a', encoding='utf-8').write(json.dumps({
            'ts': datetime.now(timezone.utc).isoformat(), 'idx': idx, 'total': len(rows),
            'user_id': user_id, 'telegram_id': telegram_id, 'status': status,
            'error': error, 'message_id': message_id,
        }, ensure_ascii=False) + '\n')

        if idx % 50 == 0 or idx == len(rows):
            print(json.dumps({'event': 'progress', 'idx': idx, 'total': len(rows), **stats}, ensure_ascii=False), flush=True)
        await asyncio.sleep(RATE_DELAY)

    await conn.close()
    print(json.dumps({'event': 'done', **stats, 'total': len(rows), 'log': str(LOG_PATH)}, ensure_ascii=False))

asyncio.run(main())
