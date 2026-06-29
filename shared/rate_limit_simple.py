"""Simple Redis-backed rate limiter for FastAPI endpoints.

Usage:
    from shared.rate_limit_simple import rate_limit_check
    
    @router.get("/...")
    async def endpoint(request: Request):
        await rate_limit_check(request, key="myendpoint", limit=60, window=60)
"""
from __future__ import annotations
import logging
import time
from fastapi import Request, HTTPException

logger = logging.getLogger(__name__)


async def rate_limit_check(
    request: Request, *, key: str, limit: int = 60, window: int = 60
):
    """Allow `limit` requests per `window` seconds per (client_ip, key).
    
    Throws 429 if exceeded. If Redis down → fail-open (allow).
    """
    try:
        from shared.cache import _get_redis
        r = await _get_redis()
        if r is None:
            return  # fail-open
        
        # AUDIT FIX M6: CF-Connecting-IP/X-Real-IP ตั้งโดย proxy (client ปลอมไม่ได้); XFF ดิบใช้เป็น last resort
        client_ip = (
            request.headers.get("cf-connecting-ip")
            or request.headers.get("x-real-ip")
            or (request.client.host if request.client else None)
            or (request.headers.get("x-forwarded-for", "").split(",")[0].strip() or "unknown")
        )
        
        bucket_key = f"ratelimit:{key}:{client_ip}:{int(time.time() // window)}"
        count = await r.incr(bucket_key)
        if count == 1:
            await r.expire(bucket_key, window + 5)
        
        if count > limit:
            raise HTTPException(
                status_code=429,
                detail=f"Rate limit: {limit}/{window}s exceeded",
                headers={"Retry-After": str(window)},
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.warning("rate_limit_check failed (fail-open): %s", e)


__all__ = ["rate_limit_check"]
