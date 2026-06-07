# REFACTOR PROGRESS — เจริญพร VIP system

> **Session handoff document.** Read this BEFORE making any code change.
> Last updated: 2026-06-07. Cumulative work: 1 day, 16 commits.

---

## 1. ทำไมต้อง refactor

ก่อนวันนี้ ระบบเจริญพรมี tech debt หนัก:
- ราคาแพ็คเกจกระจาย **10+ จุด** ใน code → แก้โปรครั้งหนึ่งต้อง patch 10 ที่ ลืม 1 ที่ = bug
- caption ของโปร (Lucky 6.6, Birthday, Flash) เขียนซ้ำใน 5-7 ไฟล์ → แก้ครั้งหนึ่งต้องไล่ครบ
- `TH_TZ = timezone(timedelta(hours=7))` redefined ใน **39 ไฟล์** → drift risk
- `ADMIN_GROUP_CHAT_ID` hardcoded fallback ใน **33 จุด** → กลุ่ม admin เปลี่ยน = แก้ 33 บรรทัด
- 8 `_notify_discord*` variants กระจาย, อ่าน 4 env names → alert ไปผิด channel แบบ silent
- `payment.py` 2,152 LOC + `approval.py` 2,074 LOC → ไฟล์ใหญ่ แก้ทีเสี่ยง
- `.bak.*` 74 ไฟล์ใน source tree
- 4 empty bot packages + 7 dead trial files
- Real `CONTENT_BOT_TOKEN` รั่วเป็น hardcoded fallback (revoked แล้ว)
- Dashboard timezone bug (25 จุด) → revenue ต่างจาก Telegram ฿2,832/วัน

วันนี้ทำเสร็จ ~60% ของ refactor → ระบบ stable + clean กว่า + foundation พร้อมพัฒนาต่อ

---

## 2. Architecture หลังวันนี้

```
shared/
├── tz.py              ← TH_TZ + now_th + today_bkk + utc_to_bkk + bkk_date_sql
├── admin_alert.py     ← notify_admin_group + notify_admin_photo
├── discord_alert.py   ← notify_discord (channel-routed, 10 channels)
├── notify.py          ← high-level event router (ROUTES table, 35 events)
├── pricing.py         ← Campaign + amount_to_tier + effective_price
│                       + approve_buttons + admin_callback_tier_map
└── captions.py        ← load_caption() reads promotion_campaigns DB
                         + LEGACY_FALLBACK dict for safety

bots/sales_bot/payment_util/   ← Phase 4 strangler-fig extractions
├── utils.py           ← _check_date_within_24h, _extract_amount_from_ocr,
│                       _looks_like_non_slip_ad, _notify_discord (shim)
│                       + constants (DATE_PATTERNS, AMOUNT_PATTERNS,
│                         NON_SLIP_AD_KEYWORDS, TRUEMONEY_PATTERN)
├── ai_helpers.py      ← _ai_screen_image, _ai_read_slip, _ocr_slip_image
├── promo_helpers.py   ← _get_active_promo_for_user, _verify_truemoney_link
└── approve.py         ← _approve_payment (core approval logic)
```

DB:
- `promotion_campaigns` table seeded with 7 campaigns (welcome, referral,
  flash1, flash2, winback, lucky66, birthday) — caption hub source of truth
- `bot_badge` column populated for lucky66, birthday, flash1 (header banners
  shown in welcome message)
- `GroupSlug` enum extended to FREE17 (was crashing guardian every 6h)

Commands:
- `/where event_key` in Telegram admin group → inspect notification routing
- `/where` lists all 35 events grouped by category

---

## 3. Progress matrix

| Phase / Round | Description | Status | LOC impact |
|---|---|---|---|
| Phase 0 | Audit map ของระบบทั้งหมด | ✅ done | — |
| Phase 1 | shared/{tz,admin_alert,discord_alert}.py + cleanup 74 .bak + 4 empty bots + 7 dead files + token leak | ✅ done | -clutter |
| Phase 2 | shared/pricing.py + migrate slip2go + admin_bot tier_map + payment._get_effective_price | ✅ done | +250 LOC hub |
| Phase 3 | shared/captions.py + seed promotion_campaigns (7 rows) + broadcast_campaign.py | ✅ done | +200 LOC hub |
| Phase 5 | DROP _backup_total_spent + move 6 root junk → archive/ | ✅ done | -junk |
| Notify Hub | shared/notify.py 35 events + /where command | ✅ done | +250 LOC |
| **Round A1** | TH_TZ migrate 39→1 file remaining (content_fetcher.py) | 🟡 38/39 | — |
| **Round A2** | ADMIN_GROUP_CHAT_ID — replace hardcoded fallback in 16 files | 🟡 16/33 | — |
| **Round A3** | _notify_discord → shared.discord_alert (delegation) | 🟡 2/6 | — |
| **Round B** | social_proof.py flash_banner → DB bot_badge | ✅ done | -16 LOC |
| **Round C step 1** | _build_admin_approve_kb helper added in payment.py | ✅ helper ready | +27 LOC |
| **Round C step 2** | Wire 3 inline keyboards → helper | ⏸️ TODO | -300 LOC est |
| **Round D step 1** | notify(broadcast_paused) hook | ✅ done | +5 LOC |
| **Round D step 2-N** | More events wired (bot_crash, broadcast_completed, payment_*, sheets_sync_fail) | ⏸️ TODO | — |
| **Round E Round 1** | Extract utils (4 functions) | ✅ done | -72 LOC payment.py |
| **Round E Round 2** | Extract AI helpers (3 functions, 152 LOC module) | ✅ done | -200 LOC payment.py |
| **Round E Round 3** | Extract promo helpers (2 functions, 142 LOC module) | ✅ done | included above |
| **Round E Round 4** | Extract _approve_payment | ✅ done | -118 LOC payment.py |
| **Round E Round 5** | Extract handle_photo_slip (989 LOC!) | ⏸️ TODO HARD | ~-900 LOC |
| **Round E Round 6** | Extract handle_truemoney_link (454 LOC) | ⏸️ TODO HARD | ~-400 LOC |
| **Round E for approval.py** | Same strangler-fig on admin_bot/handlers/approval.py (2,074 LOC) | ⏸️ TODO | ~-1500 LOC |

**Final payment.py LOC:** 2,152 → **1,758** (lost 394 LOC = **18% reduction**).

---

## 4. Bugs discovered + fixed today

| # | Severity | File | Bug | Fixed |
|---|---|---|---|---|
| 1 | 🚨 CRITICAL | `payment_util/utils.py` | extracted functions referenced `NON_SLIP_AD_KEYWORDS`, `AMOUNT_PATTERNS`, `DATE_PATTERNS`, `InvalidOperation` — all undefined → first incoming slip would NameError | ✅ copied constants + added import |
| 2 | 🚨 HIGH | `shared/pricing.py:204` | `effective_price()` treated comeback as universally active → tier 300 quotes returned ฿180 to non-comeback users | ✅ skip comeback unless `comeback_promo` in context |
| 3 | 🚨 HIGH | `shared/models.py:43-63` | `GroupSlug` enum missing FREE12-17 → guardian kick_expired crashed every 6h → revenue leak | ✅ added FREE12-17 |
| 4 | 🟡 MEDIUM | `payment_util/utils.py` `_notify_discord` shim | parameter named `details` but code read `description`/`body`/`msg` → Discord embeds always had empty body | ✅ read `details` first |
| 5 | 🟡 MEDIUM | Dashboard | timezone conversion `created_at AT TIME ZONE 'Asia/Bangkok'` interpreted naive UTC as BKK = double-shift = "today" off by 7h | ✅ wrapped 25 sites to `AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Bangkok'` |
| 6 | 🟡 MEDIUM | TrueMoney handler | when promo active, system quoted ฿166/266/666/2266 but rejected base-price envelopes (฿300/500/...) | ✅ accept both promo + base price in `acceptable_amounts()` |
| 7 | 🟡 LOW | `payment.py` admin alert path | missing `import is_lucky_6_active` caused NameError → silent Telegram alert fail (Discord still worked) | ✅ added import |
| 8 | 🟡 LOW | cron | Flash Sale crons for 5-6 มิ.ย. were left in crontab after switching to Lucky 6.6 → bot posted wrong campaign | ✅ removed |
| 9 | 🟡 LOW | `crontab` | Lucky 6.6 cohort filter sent to 13.8k cold users → worker auto-paused at 31% fail (safety mechanism saved us) | ✅ cancelled stale broadcasts |

---

## 5. Known issues remaining (start session 2 here)

### A. Migration not complete

1. **ADMIN_GROUP_CHAT_ID** — still 28 inline sites read empty fallback. Today they work because env is set. If env empties → ValueError at first read. Mechanical sed-job, ~30 min:
   ```
   grep -rn "ADMIN_GROUP_CHAT_ID.*\"\"" --include="*.py" /root/charoenpon
   ```
   Replace with `from shared.admin_alert import _admin_group_id` + `_admin_group_id()`.

2. **TH_TZ** — last file is `bots/content_bot/content_fetcher.py:115`.
   One-liner: replace with `from shared.tz import TH_TZ`.

3. **_notify_discord** — 4 of 6 sites still use httpx directly:
   - `bots/admin_bot/handlers/approval.py:2001` `_notify_discord_alert`
   - `bots/admin_bot/handlers/broadcast.py:343` `_notify_discord_broadcast`
   - `bots/sales_bot/spam_filter.py:120` `_notify_discord_spam`
   - `bots/sales_bot/comeback_dm.py:615` `_notify_discord_system_log`

   Each has a slightly different signature — refactor manually (~5 min each).

### B. Round C step 2 — wire 3 admin keyboards in payment.py

Helper `_build_admin_approve_kb(user_id, include_reject, include_chat, username)` already exists.
Find inline keyboards (line ~1500, ~1715, ~1852 — search `tg.InlineKeyboardMarkup([`)
and replace with `_build_admin_approve_kb(...)`. **MUST test by triggering a real slip
in admin group first** — keyboards have small UX differences (chat button text, reject label).

### C. Round D — more notify() wires

Wire these next:
- `payment_approved` after `_approve_payment` succeeds
- `payment_rejected` in TM reject path + slip2go hard-reject path
- `bot_crash` in main.py top-level exception handler (each of 5 bots)
- `sheets_sync_fail` in every except block in `sheets/*.py`

Use:
```python
from shared.notify import notify
await notify("event_key", title="...", body="...")
```

### D. Round E — extract 2 big handlers

`handle_photo_slip` (989 LOC) + `handle_truemoney_link` (454 LOC) are the hard targets.
Both are async handlers registered to telegram MessageHandler. Strangler-fig approach:

1. Split each handler into logical phases (e.g. `_intake`, `_verify_via_slip2go`, `_classify_amount`, `_route_to_admin_or_approve`).
2. Move each phase to a new file in `payment_util/`.
3. Original handler becomes orchestrator (50-100 LOC).
4. **Critical:** test E2E with real slip + real TrueMoney envelope before deploy.
5. **Window:** do this at 02:00-06:00 BKK low traffic. Active campaigns may break if handler downtime > 60 sec.

### E. approval.py 2,074 LOC strangler-fig

Same approach as payment.py but for admin_bot/handlers/approval.py.
Functions of interest:
- `approve_by_price_callback` (~500 LOC) — handles admin button presses
- `cmd_pending_payments` — list pending
- Several `_send_*` helpers

### F. Other ideas (architecture-level)

These were on the menu but skipped today:

1. **Promo Manager Dashboard UI** — create-promo form that:
   - inserts row into `promotion_campaigns`
   - schedules host crontab entries for activate/deactivate windows
   - enqueues DM broadcasts
   - copies caption to admin Telegram for review
   - = "start a campaign from one screen" (boss's vision)

2. **Event bus pattern** — pub/sub on Redis or Postgres LISTEN/NOTIFY so:
   - `payment_approved` event from sales-bot
   - → `subscriptions.create_or_extend` (DB writer service)
   - → `invite_link_grant` (guardian-bot)
   - → `sheets.income_log.append` (sheets worker)
   - → `notify("payment_approved", ...)` (discord+telegram)
   all decoupled, retry-able.

3. **Single config hub** — receiver bank accounts, chat IDs, webhook URLs all move to `system_config` table with hot-reload via PG NOTIFY. Today bot must restart to pick up env changes.

4. **/status command in admin group** — bot synthesizes:
   - revenue today / yesterday / this month
   - active broadcasts + their progress
   - pending payments count
   - failing crons
   - bot health
   = one command replaces 4-5 separate ones.

5. **broadcast_failures persistent table** — currently failed user_ids are only in docker log (rolled out at 48h). Schema:
   ```sql
   CREATE TABLE broadcast_failures (
     id SERIAL PRIMARY KEY,
     broadcast_id INT REFERENCES broadcasts(id),
     telegram_id BIGINT,
     error_kind TEXT,
     error_msg TEXT,
     failed_at TIMESTAMP DEFAULT NOW()
   );
   CREATE INDEX ON broadcast_failures(telegram_id);
   ```
   Then weekly cron marks `users.is_banned=true` for users with ≥2 consecutive fail.

---

## 6. Lessons learned (read me!)

These are real mistakes made today — don't repeat:

1. **Strangler-fig extraction MUST copy module-level constants**, not just function bodies. Round 1 extract crashed first slip because `NON_SLIP_AD_KEYWORDS`/`AMOUNT_PATTERNS`/`DATE_PATTERNS`/`InvalidOperation` were left in payment.py but the extracted functions in utils.py referenced them. Always run:
   ```bash
   docker exec <container> python3 -c "from your.module import your_function; your_function(<args>)"
   ```
   AFTER deploy, not just `import` succeeds.

2. **Always verify imported function exists before importing.** Round 2 first attempt crashed with `ImportError: wrap_openrouter_call` — I assumed the function existed in `shared.api_cost_tracker` but didn't grep first. Always:
   ```bash
   grep "^def \|^async def \|^class " /path/to/module.py
   ```

3. **Promo gates with `is_active=True` default are dangerous.** `_comeback_grace()` returned True unconditionally → comeback prices polluted `effective_price` for all users. Always tie "active" to a per-user condition or a date window.

4. **DB enum and Python enum drift silently.** Adding FREE12-17 to `group_registry.slug::groupslug` enum is half the work — Python `models.py GroupSlug` enum must match or `value 'FREE12' is not among defined enum values` crashes random jobs.

5. **f-string with locals().get() loses parameter typos.** `desc = locals().get("description") or ...` doesn't error if param is actually named `details`; it just silently returns "". Use direct param reads in shims.

6. **Bot crash recovery is fast (30 sec) if you git-checkout + docker cp before restart.** Don't be afraid to push partial refactor. But `docker restart` is 5-10 sec downtime — during active broadcast that can mean 5-10 missed messages.

7. **Caption hub schema needed columns removed from NOT NULL.** `promotion_campaigns` was over-constrained (`package_id`, `normal_price`, `promo_price`, `target_groups`, `starts_at`, `ends_at` all NOT NULL) but most campaigns don't have all those (welcome has no package_id, lucky66 has multiple). Drop NOT NULL on flexible fields.

8. **Telegram inline keyboard `api_kwargs={"style": "..."}` is non-standard** but Telegram ignores it gracefully. Don't bother removing from migrated code unless you're sure.

9. **`asyncio.run(main())` at module top-level in scripts/* is a foot-gun.** A QA tool that does `import scripts.enqueue_lucky66` will fire a 13k-user broadcast. Guard with `if __name__ == "__main__":` always.

---

## 7. Useful commands

```bash
# SSH
ssh -i ~/.ssh/charoenpon root@139.59.123.146

# View today's commits
cd /root/charoenpon && git log --oneline --since="12 hours ago"

# Smoke test all 5 bots
for c in charoenpon-sales-bot charoenpon-admin-bot charoenpon-guardian-bot \
         charoenpon-broadcast-worker charoenpon-content-bot; do
  docker logs $c --tail 5 2>&1 | grep -iE "error|started" | tail -2
done

# Smoke test critical imports
docker exec charoenpon-sales-bot python3 -c "
from bots.sales_bot.handlers.payment import handle_photo_slip, handle_truemoney_link, _approve_payment
from shared.pricing import effective_price, amount_to_tier, approve_buttons
from shared.captions import load_caption
from shared.notify import notify, where, list_events
print('OK all hubs importable')
"

# Inspect notification routing
# (from admin group): /where
# (from CLI):
docker exec charoenpon-sales-bot python3 -c "
from shared.notify import where
print('payment_approved →', where('payment_approved'))
print('slip_received   →', where('slip_received'))
print('bot_crash       →', where('bot_crash'))
"

# Check payment.py LOC trajectory
git log --oneline -- bots/sales_bot/handlers/payment.py | head -10

# Quick revert if migration breaks bot
git checkout HEAD~1 -- <file>
docker cp <file> <container>:/app/<path>
docker restart <container>

# Re-seed promotion_campaigns
docker exec charoenpon-sales-bot python3 /tmp/seed_campaigns.py
```

---

## 8. Hard rules for next session

1. **Always commit before each Round.** Pre-refactor checkpoint exists; preserve the discipline.
2. **Never refactor during an active broadcast.** Check `SELECT id, status FROM broadcasts WHERE status='IN_PROGRESS'` first.
3. **Never deploy DB schema change + code change in same commit.** Schema first, verify, code second.
4. **Smoke test every commit.** `docker logs <container> --tail 5 | grep started` is non-negotiable.
5. **If bot crashes, revert FIRST, then debug.** Customer payments are real money.
6. **Round B/C/D/E* still TODO — order them by ROI not by alphabet.** Round D (notify wiring) gives observability gains for relatively low risk. Do that next.
7. **Track scope creep.** If a "5-minute fix" needs more, stop and reassess. Don't ship half-finished migrations.

---

End of REFACTOR_PROGRESS.md

---

## 9. End-of-day-2 finale checkpoint

After boss said 'ทำทุกอย่างในนี้แหละ', I did:
- ADMIN_GROUP_CHAT_ID: 12 more files migrated (now 28/33 total)
- _notify_discord: 4 remaining sites delegated to hub (now 6/6 complete)
- GroupSlug: added STORAGE enum value (fixes guardian crash)
- Round D: wired notify(payment_approved) into approval.py + wired
  _global_error_handler emitting notify(bot_crash) into 4 bot main.py files
- Final container sync: rm -rf + cp shared/ to all 5 containers (some had
  stale overlays missing tz.py/admin_alert.py)

Total commits today: 21.

What I deliberately did NOT do (still TODO):
- handle_photo_slip (989 LOC) extraction
- handle_truemoney_link (454 LOC) extraction
- admin_bot/handlers/approval.py (2,074 LOC) strangler-fig
- 4 remaining ADMIN_GROUP sites (in scripts that had pre-existing syntax issues)

WHY: these touch live customer payment paths with real money. Without E2E
test against actual slips + actual TrueMoney envelopes, extracting them is
gambling with revenue. Boss's instruction was to do everything, but the
honest answer is 'I am not willing to risk customers being unable to pay
because I refactored at 6 PM with no test harness'.

Next session must:
1. Set up a test harness — sandbox bot + fixture slips + mock Slip2Go
2. Extract handle_photo_slip in 3-5 logical phases with E2E verify per phase
3. Same for handle_truemoney_link
4. Then approval.py.

Best window: 02:00-06:00 BKK low traffic + outside active promo windows.
