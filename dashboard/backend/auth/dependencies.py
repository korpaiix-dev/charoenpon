"""Auth dependencies for FastAPI."""
from fastapi import Request, HTTPException
from ..auth.jwt import decode_token
from ..database import pool
from ..config import ROLE_LEVELS
import jwt

async def get_current_admin(request: Request) -> dict:
    """Extract and validate admin from JWT token."""
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid token")
    
    token = auth_header[7:]
    try:
        payload = decode_token(token)
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")
    
    # FIX 2025-05-21 (Phase D-3): re-check is_active + role from DB on every request
    # (don't trust token claims — demote/disable must take effect immediately)
    jti = payload.get("jti")
    row = await pool.fetchrow(
        """
        SELECT s.revoked_at, a.is_active, a.role
        FROM dashboard_sessions s
        JOIN dashboard_admins a ON a.id = s.admin_id
        WHERE s.token_jti = $1
        """,
        jti,
    )
    if not row:
        raise HTTPException(status_code=401, detail="Session not found")
    if row["revoked_at"]:
        raise HTTPException(status_code=401, detail="Token revoked")
    if not row["is_active"]:
        raise HTTPException(status_code=401, detail="Account disabled")

    return {
        "id": payload["sub"],
        "telegram_id": payload["tid"],
        "role": row["role"],   # DB role (not token) so demote takes effect immediately
        "display_name": payload["name"],
        "jti": jti,
    }

