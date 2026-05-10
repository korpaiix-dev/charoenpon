# เจริญพร — Operations Manual

> Single source of truth สำหรับโปรเจกต์เจริญพร. Cowork session ไหนแตะหยิบไฟล์นี้มาอ่านได้ทันที.

## Active repos

| Repo | Role | Default branch | Auto-deploy |
|------|------|---------------|-------------|
| `korpaiix-dev/charoenpon` | Main code (this) | `master` | Yes |
| `korpaiix-dev/jarern4-auto-poster` | Content broadcast bot | `main` | Yes |
| `korpaiix-dev/jaroenporn-bot-main` | VIP subscription (namwan) | `main` | No (Vercel) |

## VPS

- Host: `139.59.123.146`
- User: `root`
- SSH port: 22
- Web shell (ttyd): port 7683 (basic auth, bookmarked in browser)
- Deploy SSH key: `/root/.ssh/github_deploy`

## Project structure (this repo)

```
charoenpon/
├── agents/      # dev_agent, growth_agent, marketing_analyzer
├── bots/        # admin_bot, sales_bot, content_bot, guardian_bot
├── dashboard/   # FastAPI backend + frontend
├── shared/      # database, api_cost_tracker, promos
├── sheets/      # Google Sheets integration
├── scripts/     # one-off scripts
├── assets/      # promo images
├── fb-manager/  # legacy (mostly pruned)
├── discord_bot/
├── docker-compose.yml
└── .env         # secrets (gitignored)
```

## Running services on VPS

Charoenpon stack runs in **docker compose** (`/root/charoenpon/docker-compose.yml`):

| Service | Container | Command |
|---------|-----------|---------|
| postgres | charoenpon-postgres | postgres:15 |
| sales-bot | charoenpon-sales-bot | python -m bots.sales_bot |
| admin-bot | charoenpon-admin-bot | python -m bots.admin_bot |
| content-bot | charoenpon-content-bot | python -m bots.content_bot |
| guardian-bot | charoenpon-guardian-bot | python -m bots.guardian_bot |
| discord-bot | charoenpon-discord-bot | python -m discord_bot.main |
| finance-scheduler | charoenpon-finance-scheduler | python -m agents.finance_agent |
| monitor | charoenpon-monitor | python -m agents.dev_agent monitor |
| backup-cron | charoenpon-backup-cron | python -m agents.dev_agent backup |
| manager-agent | charoenpon-manager-agent | python -m agents.manager_agent |
| dashboard | charoenpon-dashboard | uvicorn (port 8010) |

Restart commands:
- All: `cd /root/charoenpon && docker compose up -d`
- Rebuild + restart: `cd /root/charoenpon && docker compose up -d --build`
- One service: `docker compose restart <service>` (e.g. `sales-bot`)
- Logs: `docker compose logs -f <service>`

Non-docker:
| Service | Type | Restart |
|---------|------|---------|
| jarern4-poster | systemd timer | `systemctl restart jarern4-poster.timer` |
| Redis (host) | systemd | `systemctl restart redis-server` |
| Docker daemon | systemd | `systemctl restart docker` |

## Schedules

- `jarern4-poster` timer: **09:00 + 18:00 Asia/Bangkok** daily (`Persistent=true`, catches up missed runs)
- Vercel cron `/cron/check_expiry` (jaroenporn-bot-main): 05:00 daily

## Telegram broadcast targets

Source: `/root/charoenpon/.env` (vars `TG_GROUP_*`)

| Var | Chat ID | Title | @jarern4_bot status |
|-----|---------|-------|---------------------|
| TG_GROUP_ANNOUNCE_1 | -1003981084328 | เจริญพรรรรร | admin |
| TG_GROUP_ANNOUNCE_3 | -1003805660760 | น้ำหมัก เจ๊หอย | admin |
| TG_GROUP_MAIN_2 | -1003723154612 | โห่เฮียโห่ซ้อ | admin |
| TG_GROUP_ANNOUNCE_2 | -1003899592492 | (bot not member) | — |
| TG_GROUP_MAIN_1 | -1003789621076 | (bot not member) | — |
| TG_GROUP_MAIN_3 | -1003888282439 | (bot not member) | — |
| TG_GROUP_ADMIN | -1003830920430 | namwan admin group | namwan only |

## Bots

| Bot | Username / ID | Role | Token location |
|-----|---------------|------|----------------|
| Content broadcast | `@jarern4_bot` (8428806723) | post promos to groups | `BOT_TOKEN` in `/root/jarern4-auto-poster/.env` |
| VIP subscription | namwan_bot | payment, group join | `NAMWAN_TOKEN` env var (Vercel) |
| Worker | worker_bot | side-tasks | `WORKER_TOKEN` env var (Vercel) |

## Auto-deploy flow

```
[push to GitHub]
   ↓
[GitHub Actions: deploy.yml]
   ↓
appleboy/ssh-action → SSH to VPS
   ↓
cd /root/<repo>
   ↓
git fetch + git reset --hard origin/<branch>
   ↓
restart systemd unit (if applicable)
```

Workflow file: `.github/workflows/deploy.yml` in each repo.

Required GitHub repo secrets:
- `VPS_HOST`, `VPS_USER`, `VPS_PORT`, `VPS_SSH_KEY`

## Where sensitive stuff lives (DO NOT commit)

- Bot tokens: `/root/<repo>/.env` on VPS
- GitHub PAT: `/root/.git-credentials` on VPS (chmod 600)
- SSH deploy key: `/root/.ssh/github_deploy` (private)
- DB password: `/root/charoenpon/.pg_password`
- Google credentials: `/root/charoenpon/credentials/`

## Snapshots / rollback points

- `snapshot-2026-05-10-pre-cleanup` (charoenpon) — before 9-commit migration to GitHub-first workflow

## Common ops

```bash
# Tail jarern4-poster log
tail -f /root/jarern4-auto-poster/logs/poster.log

# Manual broadcast (skip schedule)
cd /root/jarern4-auto-poster && ./run.sh

# Dry-run
cd /root/jarern4-auto-poster && ./run.sh --dry-run

# Caption preview
cd /root/jarern4-auto-poster && ./run.sh --caption-preview

# View next timer fire
systemctl list-timers jarern4-poster.timer --no-pager

# View deploy run history
# https://github.com/korpaiix-dev/charoenpon/actions
# https://github.com/korpaiix-dev/jarern4-auto-poster/actions

# Rollback charoenpon
cd /root/charoenpon && git reset --hard snapshot-2026-05-10-pre-cleanup
```

## Known issues / TODO

- bots/* not wired to systemd → deploy can pull but does not auto-restart bots
- 3 dead repos to archive: jaroenporn-bot, botAljerernVIP, JarernPROTECTION
- Discord bot env relies on legacy channel IDs (`DISCORD_LOG_CHANNEL_ID` etc.) — audit recommended

## Repos NOT in scope (different brand)

- `korpai-agents`, `korpai-landing`, `codex-exec-server` — KORP AI brand
- `loan-backoffice`, `nectec-event-platform`, `patafoods` — other projects
