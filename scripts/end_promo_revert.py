#!/usr/bin/env python3
"""End-of-promo revert — restore all 4 files from snapshot, remove cron, restart bots, notify admin.

Run by cron at 2026-05-31 17:05 UTC (= 2026-06-01 00:05 ICT).
"""
from __future__ import annotations
import os, sys, json, base64, hashlib, subprocess, logging, time, urllib.request
from pathlib import Path
from datetime import datetime, timezone, timedelta

logging.basicConfig(level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler("/root/charoenpon/logs/promo_revert.log"),
              logging.StreamHandler()])
log = logging.getLogger("end-promo")

SNAP = Path("/root/charoenpon/promo_snapshot_may_latest.json")
ADMIN_GROUP_CHAT_ID = int(os.environ.get("ADMIN_GROUP_CHAT_ID", "-1003830920430"))


def restore_files() -> dict:
    log.info("loading snapshot: %s", SNAP)
    if not SNAP.exists():
        log.error("snapshot file missing — aborting"); return {"ok": False, "err": "no_snapshot"}
    data = json.loads(SNAP.read_text())
    results = {}
    for fpath, meta in data["files"].items():
        original = base64.b64decode(meta["content_b64"])
        cur_path = Path(fpath)
        # backup current (post-promo) file
        ts = int(time.time())
        cur_path.with_suffix(cur_path.suffix + f".pre_revert.{ts}").write_bytes(cur_path.read_bytes())
        cur_path.write_bytes(original)
        new_sha = hashlib.sha256(original).hexdigest()
        ok = (new_sha == meta["sha256"])
        results[fpath] = {"ok": ok, "size": len(original)}
        log.info("restored %s (%d bytes, sha_match=%s)", fpath, len(original), ok)
    return {"ok": all(r["ok"] for r in results.values()), "files": results}


def remove_cron_block():
    """Remove Phase B + self-revert cron entries."""
    log.info("removing cron entries…")
    cur = subprocess.run(["crontab","-l"], capture_output=True, text=True).stdout
    new_lines = []
    in_block_b = False
    in_block_c = False
    for line in cur.splitlines():
        if "MAY26_COMBO_PROMO_AUTO_POST <<<" in line: in_block_b = True; continue
        if "MAY26_COMBO_PROMO_AUTO_POST >>>" in line: in_block_b = False; continue
        if "MAY26_END_PROMO_REVERT <<<" in line: in_block_c = True; continue
        if "MAY26_END_PROMO_REVERT >>>" in line: in_block_c = False; continue
        if in_block_b or in_block_c: continue
        new_lines.append(line)
    new_crontab = "\n".join(new_lines) + "\n"
    p = subprocess.Popen(["crontab","-"], stdin=subprocess.PIPE)
    p.communicate(new_crontab.encode())
    log.info("cron updated (removed Phase B + revert blocks)")


def rebuild_bots() -> str:
    log.info("rebuilding sales-bot + admin-bot…")
    # # >>> BUG12_REVERT_DASH <<<
    r = subprocess.run(
        ["docker","compose","-f","/root/charoenpon/docker-compose.yml",
         "up","-d","--build","sales-bot","admin-bot","dashboard"],
        capture_output=True, text=True, timeout=420
    )
    log.info("rebuild rc=%d", r.returncode)
    log.info("stdout: %s", r.stdout[-500:])
    if r.returncode != 0: log.error("stderr: %s", r.stderr[-500:])
    return f"rc={r.returncode}"


def notify_admin(restore_ok: bool, build_status: str):
    token = os.environ.get("ADMIN_BOT_TOKEN", "")
    if not token:
        log.warning("no admin token — skipping notification"); return
    now_th = datetime.now(timezone(timedelta(hours=7))).strftime("%d/%m/%Y %H:%M")
    icon = "✅" if restore_ok else "⚠️"
    text = (
        f"{icon} <b>โปรสิ้นเดือน พ.ค. หมดเขตแล้ว — ระบบกลับเป็นปกติ</b>\n\n"
        f"🕒 {now_th}\n\n"
        f"• Files restored: {'OK' if restore_ok else 'FAIL — ตรวจ /root/charoenpon/logs/promo_revert.log'}\n"
        f"• Bot rebuild: {build_status}\n"
        f"• Cron auto-post: ลบแล้ว\n\n"
        f"ปุ่ม approve โปร 349/999 จะไม่ขึ้นในสลิปใหม่อีก\n"
        f"ราคาแสดงในแพ็กเกจกลับเป็น 500 + 1,299 ตามปกติ"
    )
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = json.dumps({
        "chat_id": ADMIN_GROUP_CHAT_ID, "text": text, "parse_mode": "HTML"
    }).encode()
    req = urllib.request.Request(url, data=payload,
        headers={"Content-Type": "application/json"})
    try:
        r = urllib.request.urlopen(req, timeout=10)
        log.info("admin notify: %s", r.status)
    except Exception as e:
        log.error("admin notify fail: %s", e)


def main():
    log.info("=== END-PROMO REVERT START ===")
    r = restore_files()
    if not r["ok"]:
        notify_admin(False, "skipped (no snapshot)")
        return 1
    build = rebuild_bots()
    remove_cron_block()
    notify_admin(True, build)
    log.info("=== END-PROMO REVERT DONE ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
