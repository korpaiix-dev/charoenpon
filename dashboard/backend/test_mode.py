"""Test mode middleware — blocks destructive HTTP methods when DASHBOARD_TEST_MODE=true.

Allows boss/staff to click anything in dashboard without affecting real customers.
Only POST/PATCH/DELETE/PUT are intercepted. GET requests pass through normally
(read real data, can see how it looks).

Auth endpoints are allowed (so boss can log in).
Cache-clear endpoints are allowed (harmless).
"""
from __future__ import annotations
import os
import logging
from fastapi import Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

TEST_MODE = os.environ.get("DASHBOARD_TEST_MODE", "").lower() in ("true", "1", "yes")
DESTRUCTIVE_METHODS = {"POST", "PATCH", "DELETE", "PUT"}

# These paths are allowed even in test mode (auth + harmless ops)
ALLOWED_PATH_PREFIXES = (
    "/api/auth/",        # login/logout
    "/api/promo-manager/cache-clear",  # harmless
)

async def test_mode_middleware(request: Request, call_next):
    """Intercept destructive HTTP methods in test mode and return mock success."""
    if not TEST_MODE:
        return await call_next(request)

    method = request.method.upper()
    path = request.url.path

    if method in DESTRUCTIVE_METHODS and not any(path.startswith(p) for p in ALLOWED_PATH_PREFIXES):
        logger.info("[TEST_MODE] blocked %s %s", method, path)
        return JSONResponse(
            status_code=200,
            content={
                "ok": True,
                "_test_mode": True,
                "_blocked_method": method,
                "_blocked_path": path,
                "_message": "Test mode — action NOT executed. Customer NOT affected.",
            },
            headers={"X-Dashboard-Test-Mode": "true"},
        )

    response = await call_next(request)
    if TEST_MODE:
        response.headers["X-Dashboard-Test-Mode"] = "true"
    return response
