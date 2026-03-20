"""Charoenpon Dashboard — FastAPI Application."""
from fastapi import FastAPI
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
from .routers.promotions import router as promotions_router
from .routers.content import router as content_router
from .routers.groups import router as groups_router
from .routers.team import router as team_router
from .routers.settings import router as settings_router
from .routers.marketing import router as marketing_router

@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield
    await close_db()

app = FastAPI(title="เจริญพร Dashboard", version="1.0", lifespan=lifespan)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
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

# Serve frontend static files
frontend_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "frontend")
if os.path.isdir(frontend_dir):
    app.mount("/assets", StaticFiles(directory=os.path.join(frontend_dir, "assets")), name="assets")
    app.mount("/css", StaticFiles(directory=os.path.join(frontend_dir, "css")), name="css")
    app.mount("/js", StaticFiles(directory=os.path.join(frontend_dir, "js")), name="js")

@app.get("/health")
async def health():
    return {"status": "ok", "service": "charoenpon-dashboard"}

# SPA fallback — serve index.html for all non-API routes
@app.get("/{full_path:path}")
async def spa_fallback(full_path: str):
    if full_path.startswith("api/"):
        return {"error": "Not found"}
    index = os.path.join(frontend_dir, "index.html")
    if os.path.isfile(index):
        return FileResponse(index)
    return {"error": "Frontend not built", "hint": "Place index.html in dashboard/frontend/"}
