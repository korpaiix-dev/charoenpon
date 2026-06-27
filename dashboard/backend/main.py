"""Charoenpon Dashboard — FastAPI Application."""
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from contextlib import asynccontextmanager
from .test_mode import test_mode_middleware, TEST_MODE
import os

from .database import init_db, close_db
from .auth.router import router as auth_router
from .routers.dashboard import router as dashboard_router
from .routers.customers import router as customers_router
from .routers.payments import router as payments_router
from .routers.promotions import router as promotions_router, ensure_promo_campaign_tables
from .routers.promo_dayzero import router as promo_dayzero_router
from .routers.customer_miniapp import router as customer_miniapp_router
from .routers.content import router as content_router
from .routers.groups import router as groups_router
from .routers.team import router as team_router
from .routers.settings import router as settings_router
from .routers.marketing import router as marketing_router
from .routers.bots import router as bots_router
from .routers.webapp import router as webapp_router
from .routers.panda_monitor import router as panda_monitor_router
from .routers.panda_errors import router as panda_errors_router
from .routers.redirect import router as redirect_router
from .routers.receivers import router as receivers_router
from .routers.gacha_admin import router as gacha_admin_router
from .routers.exports import router as exports_router
from .routers.prae_logs import router as prae_logs_router
from .routers.daily_report import router as daily_report_router
from .routers.group_broadcast import router as group_broadcast_router
from .routers.ws import router as ws_router
from .routers.prae_prompt import router as prae_prompt_router
from .routers.feature_flags import router as feature_flags_router
from .routers.bot_messages_admin import router as bot_messages_admin_router
from .routers.promo_manager import router as promo_manager_router
from .routers.customer_notes import router as customer_notes_router

_snapshot_task = None


async def _periodic_snapshot_loop():
    """Background task: snapshot member counts every 30 minutes."""
    import asyncio as _aio
    import logging as _log
    _logger = _log.getLogger("snapshot-loop")
    await _aio.sleep(30)  # initial delay so app finishes startup
    while True:
        try:
            from .routers.bots import _snapshot_one_group
            from .database import pool
            import os as _os
            bot_token = _os.environ.get("GUARDIAN_BOT_TOKEN", "") or _os.environ.get("SALES_BOT_TOKEN", "")
            if bot_token:
                groups = await pool.fetch(
                    "SELECT chat_id FROM group_registry WHERE is_active = TRUE"
                )
                rows = []
                for g in groups:
                    cnt = await _snapshot_one_group(bot_token, int(g["chat_id"]))
                    if cnt is not None:
                        rows.append((int(g["chat_id"]), cnt))
                if rows:
                    await pool.executemany(
                        "INSERT INTO group_member_snapshots (chat_id, member_count, source) VALUES ($1, $2, 'auto')",
                        rows,
                    )
                    for cid, cnt in rows:
                        await pool.execute(
                            "UPDATE group_registry SET member_count=$1, updated_at=NOW() WHERE chat_id=$2",
                            cnt, cid,
                        )
                    _logger.info("auto-snapshotted %d groups", len(rows))
        except Exception as exc:
            _logger.warning("periodic snapshot failed: %s", exc)
        await _aio.sleep(1800)  # 30 minutes


@asynccontextmanager
async def lifespan(app: FastAPI):
    import asyncio as _aio
    await init_db()
    await ensure_promo_campaign_tables()
    # Start background snapshot loop
    global _snapshot_task
    _snapshot_task = _aio.create_task(_periodic_snapshot_loop())
    yield
    if _snapshot_task:
        _snapshot_task.cancel()
    await close_db()

app = FastAPI(title="เจริญพร Dashboard", version="1.0", lifespan=lifespan)

# FIX 2025-05-21 (Phase D-4): CORS — wildcard "*" cannot be combined with credentials.
# Use explicit allow-list from DASHBOARD_ALLOWED_ORIGINS env (comma-separated).
_origins = os.getenv("DASHBOARD_ALLOWED_ORIGINS", "").split(",")
_origins = [o.strip() for o in _origins if o.strip()] or ["http://localhost:8010"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

# Phase A.2 (2026-06-27): test mode middleware — blocks destructive methods
# when DASHBOARD_TEST_MODE env is true. Real production has it false/unset.
app.middleware("http")(test_mode_middleware)
if TEST_MODE:
    import logging as _log
    _log.getLogger(__name__).warning("🟡 DASHBOARD_TEST_MODE = ON — destructive HTTP methods will be blocked")

# API Routers
app.include_router(auth_router)
app.include_router(dashboard_router)
app.include_router(customers_router)
app.include_router(payments_router)
app.include_router(promo_dayzero_router)
app.include_router(customer_miniapp_router)
app.include_router(promotions_router)
app.include_router(content_router)
app.include_router(groups_router)
app.include_router(team_router)
app.include_router(settings_router)
app.include_router(marketing_router)
app.include_router(bots_router)
app.include_router(webapp_router)
app.include_router(panda_monitor_router)
app.include_router(panda_errors_router)
app.include_router(redirect_router)
app.include_router(receivers_router, prefix="/api")
app.include_router(gacha_admin_router, prefix="/api")
app.include_router(exports_router, prefix="/api")
app.include_router(prae_logs_router, prefix="/api")
app.include_router(daily_report_router, prefix="/api")
app.include_router(group_broadcast_router, prefix="/api")
app.include_router(ws_router)
app.include_router(prae_prompt_router, prefix="/api")
app.include_router(feature_flags_router, prefix="/api")
app.include_router(bot_messages_admin_router, prefix="/api")
app.include_router(promo_manager_router, prefix="/api")
app.include_router(customer_notes_router, prefix="/api")

# Serve frontend static files
frontend_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "frontend")
if os.path.isdir(frontend_dir):
    app.mount("/assets", StaticFiles(directory=os.path.join(frontend_dir, "assets")), name="assets")
    app.mount("/css", StaticFiles(directory=os.path.join(frontend_dir, "css")), name="css")
    app.mount("/js", StaticFiles(directory=os.path.join(frontend_dir, "js")), name="js")

@app.get("/health")
async def health():
    return {"status": "ok", "service": "charoenpon-dashboard"}

# SPA fallback — return index.html for any non-API path so the SPA can route client-side
# FIX 2025-05-21 (Phase D-5): /api/* paths that don't match a router return real 404
@app.get("/{full_path:path}")
async def spa_fallback(full_path: str):
    if full_path.startswith("api/"):
        raise HTTPException(status_code=404, detail="Not found")
    index = os.path.join(frontend_dir, "index.html")
    if os.path.isfile(index):
        return FileResponse(index)
    raise HTTPException(status_code=503, detail="Frontend not built")
# DAY0 touch 1782585829
