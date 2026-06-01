#!/usr/bin/env python3
"""Post May-end combo promo (TIER_500 + TIER_1299) to all 19 groups.

Tries multiple bot tokens per group; logs success/fail.
Auto-stops after PROMO_MAY_END_TH (2026-06-01 00:00 ICT).
"""
from __future__ import annotations
import os, sys, json, time, asyncio, logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

import telegram as tg
from telegram.error import BadRequest, Forbidden, RetryAfter, TimedOut

# Add /app or /root/charoenpon to path for shared imports
sys.path.insert(0, "/root/charoenpon")
sys.path.insert(0, "/app")
try:
    from shared.endmonth_vip_promo import is_may_combo_promo_active
except Exception:
    # Fallback: hardcode the gate (matches PROMO_MAY_END_TH = 2026-06-01)
    def is_may_combo_promo_active():
        return datetime.now(timezone(timedelta(hours=7))) < datetime(2026, 6, 1, tzinfo=timezone(timedelta(hours=7)))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("promo-poster")

# ─── Configuration ───────────────────────────────────────────────────────────
IMAGE_PATH = Path("/root/charoenpon/data/promo_may_2026.jpg")
SALES_BOT_USERNAME = "NamwarnJarern_bot"  # what tg://resolve?domain= points to

CAPTION = (
    "🔥 <b>โปรพิเศษถึง 31 พ.ค. นี้เท่านั้น!</b> 🔥\n\n"
    "🥈 <b>OnlyFans Combo 30 วัน</b>\n"
    "    ปกติ <s>500</s> → <b>349 บาท</b> (ลด 30%)\n\n"
    "🥇 <b>GOD MODE 90 วัน</b> (เข้าครบ 6 ห้อง + หนัง)\n"
    "    ปกติ <s>1,299</s> → <b>999 บาท</b> (ลด 23%)\n\n"
    "⏰ หมดเขต 31 พฤษภาคมนี้เท่านั้น\n\n"
    "━━━━━━━━━━━━━━━━━━\n"
    f'👉 <a href="https://t.me/{SALES_BOT_USERNAME}?start=may_promo">สมัครเลย คลิกที่นี่</a>\n'
    '📂 <a href="https://t.me/+Q0Qf-4t8TQo3YTBl">กลุ่มตัวอย่าง</a>\n'
    '⭐ <a href="https://t.me/+hv7uXYj4bxFhODZl">เช็คเครดิตรีวิว</a>\n'
    "━━━━━━━━━━━━━━━━━━"
)

# 19 groups (chat_id, slug)
GROUPS = [
  # Only groups @jakwarpz114_bot (CONTENT_BOT) is admin in.
  # VIP/Paid groups skipped per บอส 2026-05-24.
  (-1003733093219, 'FREE3'),   # ไทยเอามัน v1
  (-1003772512123, 'FREE4'),   # เย็ดมัน
  (-1003706880995, 'FREE5'),   # วุ่ยหนุ่ม
  (-1003740382332, 'FREE6'),   # นักตำแตก
  (-1003861673687, 'FREE7'),   # ตรงนี้มีกี
  (-1003841389411, 'FREE8'),   # มาดูไรกัน
  (-1003723154612, 'FREE10'),  # โห่โห่ซ้อ
  (-1003981084328, 'JAREN'),   # เจริญพรรรร (new supergroup)
]

# Bot tokens to try, in priority order per group type
BOT_TOKENS = {
    "guardian": os.environ.get("GUARDIAN_BOT_TOKEN", ""),
    "content":  os.environ.get("CONTENT_BOT_TOKEN", ""),
    "sales":    os.environ.get("SALES_BOT_TOKEN", ""),
    "announce": os.environ.get("ANNOUNCE_BOT_TOKEN", ""),
}

# Known mapping (verified 2026-05-24) — try this bot first, fallback to others
GROUP_PREFERRED_BOT = {}  # all groups use content_bot

ATTEMPT_ORDER = ["content"]  # บอส: @jakwarpz114_bot only  # announce token broken (401)


async def send_to_group(chat_id: int, slug: str, photo_bytes: bytes) -> dict:
    """Try multiple bots until one succeeds. Returns result dict."""
    preferred = GROUP_PREFERRED_BOT.get(slug)
    order = ([preferred] if preferred else []) + [b for b in ATTEMPT_ORDER if b != preferred]

    last_err = "no-bot"
    for bot_name in order:
        token = BOT_TOKENS.get(bot_name, "")
        if not token:
            continue
        try:
            bot = tg.Bot(token=token)
            await bot.initialize()
            try:
                await bot.send_photo(
                    chat_id=chat_id,
                    photo=photo_bytes,
                    caption=CAPTION,
                    parse_mode="HTML",
                )
                await bot.shutdown()
                return {"slug": slug, "chat_id": chat_id, "bot": bot_name, "ok": True}
            except RetryAfter as e:
                # honor flood control
                wait = float(getattr(e, "retry_after", 30))
                log.warning("RetryAfter on %s via %s: sleeping %ss", slug, bot_name, wait)
                await asyncio.sleep(wait + 1)
                # retry same bot once
                try:
                    await bot.send_photo(chat_id=chat_id, photo=photo_bytes, caption=CAPTION, parse_mode="HTML")
                    await bot.shutdown()
                    return {"slug": slug, "chat_id": chat_id, "bot": bot_name, "ok": True, "retried": True}
                except Exception as e2:
                    last_err = str(e2)[:80]
            except (Forbidden, BadRequest) as e:
                last_err = f"{bot_name}:{str(e)[:60]}"
                await bot.shutdown()
                continue
            except TimedOut as e:
                last_err = f"{bot_name}:timeout"
                await bot.shutdown()
                continue
            except Exception as e:
                last_err = f"{bot_name}:{type(e).__name__}:{str(e)[:60]}"
                await bot.shutdown()
                continue
        except Exception as e:
            last_err = f"{bot_name}:init:{str(e)[:60]}"
    return {"slug": slug, "chat_id": chat_id, "ok": False, "error": last_err}


async def main():
    if not is_may_combo_promo_active():
        log.info("promo window closed (PROMO_MAY_END_TH reached). exiting without posting.")
        return 0
    if not IMAGE_PATH.exists():
        log.error("image not found: %s", IMAGE_PATH); return 2
    photo_bytes = IMAGE_PATH.read_bytes()
    log.info("posting %d bytes to %d groups", len(photo_bytes), len(GROUPS))

    # rate-limit ~ 1 group / 2s (well under Telegram limits)
    results = []
    for chat_id, slug in GROUPS:
        r = await send_to_group(chat_id, slug, photo_bytes)
        results.append(r)
        log.info("%-8s %-9s %s",
                 slug,
                 ("OK via " + r.get("bot","?")) if r["ok"] else "FAIL",
                 r.get("error", ""))
        await asyncio.sleep(2.0)

    ok = sum(1 for r in results if r["ok"])
    fail = len(results) - ok
    log.info("DONE: %d ok / %d fail", ok, fail)

    # log to file
    log_dir = Path("/root/charoenpon/logs")
    log_dir.mkdir(exist_ok=True)
    with open(log_dir / "promo_may_post.log", "a") as fp:
        fp.write(f"\n=== {datetime.now().isoformat()} ok={ok} fail={fail} ===\n")
        for r in results:
            fp.write(json.dumps(r, ensure_ascii=False) + "\n")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    rc = asyncio.run(main())
    sys.exit(rc)
