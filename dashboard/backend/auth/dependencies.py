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
    
    # Check if session is revoked
    jti = payload.get("jti")
    row = await pool.fetchrow(
        "SELECT revoked_at FROM dashboard_sessions WHERE token_jti = $1", jti
    )
    if row and row["revoked_at"]:
        raise HTTPException(status_code=401, detail="Token revoked")
    
    return {
        "id": payload["sub"],
        "telegram_id": payload["tid"],
        "role": payload["role"],
        "display_name": payload["name"],
        "jti": jti,
    }

def require_role(min_role: str):
    """Dependency factory for role-based access."""
    min_level = ROLE_LEVELS.get(min_role, 0)
    
    async def checker(request: Request):
        admin = await get_current_admin(request)
        admin_level = ROLE_LEVELS.get(admin["role"], 0)
        if admin_level < min_level:
            raise HTTPException(status_code=403, detail=f"Requires {min_role} role or higher")
        request.state.admin = admin
        return admin
    
    return checker
