"""Auth router — login, logout, me, password change."""
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
import bcrypt
from ..database import pool
from ..auth.jwt import create_token
from ..auth.dependencies import get_current_admin

router = APIRouter(prefix="/api/auth", tags=["auth"])

class LoginRequest(BaseModel):
    telegram_id: int
    password: str

class PasswordChangeRequest(BaseModel):
    old_password: str
    new_password: str

async def _log_activity(admin_id: int, action: str, entity_type: str = None, entity_id: int = None, details: dict = None, ip: str = None):
    await pool.execute(
        """INSERT INTO dashboard_activity_log (admin_id, action, entity_type, entity_id, details, ip_address)
           VALUES ($1, $2, $3, $4, $5::jsonb, $6)""",
        admin_id, action, entity_type, entity_id,
        __import__('json').dumps(details) if details else None, ip
    )

@router.post("/login")
async def login(req: LoginRequest, request: Request):
    row = await pool.fetchrow(
        "SELECT * FROM dashboard_admins WHERE telegram_id = $1 AND is_active = TRUE",
        req.telegram_id
    )
    if not row:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    
    if not bcrypt.checkpw(req.password.encode(), row["password_hash"].encode()):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    
    token, jti, expires_at = create_token(row["id"], row["telegram_id"], row["role"], row["display_name"])
    
    ip = request.client.host if request.client else None
    ua = request.headers.get("user-agent", "")
    
    # Store session
    await pool.execute(
        """INSERT INTO dashboard_sessions (admin_id, token_jti, ip_address, user_agent, expires_at)
           VALUES ($1, $2, $3, $4, $5)""",
        row["id"], jti, ip, ua, expires_at
    )
    
    # Update last login
    await pool.execute(
        "UPDATE dashboard_admins SET last_login_at = NOW(), last_login_ip = $1 WHERE id = $2",
        ip, row["id"]
    )
    
    await _log_activity(row["id"], "login", "session", None, {"ip": ip}, ip)
    
    return {
        "token": token,
        "admin": {
            "id": row["id"],
            "telegram_id": row["telegram_id"],
            "display_name": row["display_name"],
            "role": row["role"],
        }
    }

@router.post("/logout")
async def logout(request: Request):
    admin = await get_current_admin(request)
    await pool.execute(
        "UPDATE dashboard_sessions SET revoked_at = NOW() WHERE token_jti = $1",
        admin["jti"]
    )
    await _log_activity(admin["id"], "logout", "session", None, None, request.client.host if request.client else None)
    return {"ok": True}

@router.get("/me")
async def me(request: Request):
    admin = await get_current_admin(request)
    row = await pool.fetchrow("SELECT * FROM dashboard_admins WHERE id = $1", admin["id"])
    return {
        "id": row["id"],
        "telegram_id": row["telegram_id"],
        "display_name": row["display_name"],
        "username": row["username"],
        "role": row["role"],
        "last_login_at": str(row["last_login_at"]) if row["last_login_at"] else None,
    }

@router.put("/password")
async def change_password(req: PasswordChangeRequest, request: Request):
    admin = await get_current_admin(request)
    row = await pool.fetchrow("SELECT password_hash FROM dashboard_admins WHERE id = $1", admin["id"])
    if not bcrypt.checkpw(req.old_password.encode(), row["password_hash"].encode()):
        raise HTTPException(status_code=400, detail="Old password incorrect")
    
    new_hash = bcrypt.hashpw(req.new_password.encode(), bcrypt.gensalt()).decode()
    await pool.execute("UPDATE dashboard_admins SET password_hash = $1, updated_at = NOW() WHERE id = $2", new_hash, admin["id"])
    await _log_activity(admin["id"], "change_password", "admin", admin["id"], None, request.client.host if request.client else None)
    return {"ok": True}
