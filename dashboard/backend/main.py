"""Charoenpon Dashboard — FastAPI Application."""
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from contextlib import asynccontextmanager
import os

from .database import init_db, close_db
from .auth.router import router as auth_router
from .routers.dashboard import router as dashboard_router
from .routers.customers import router as customers_router
from .routers.payments import router as payments_router
from .routers.promotions import router as promotions_router, ensure_promo_campaign_tables
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

@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    await ensure_promo_campaign_tables()
    yield
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

# API Routers
app.include_router(auth_router)
app.include_router(dashboard_router)
app.include_router(customers_router)
app.include_router(payments_router)
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
