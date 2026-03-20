"""JWT token creation and verification."""
import jwt
import uuid
from datetime import datetime, timedelta, timezone
from ..config import JWT_SECRET, JWT_ALGORITHM, JWT_EXPIRE_HOURS

def create_token(admin_id: int, telegram_id: int, role: str, display_name: str) -> tuple[str, str, datetime]:
    """Create JWT token. Returns (token, jti, expires_at)."""
    jti = uuid.uuid4().hex
    expires_at = datetime.utcnow() + timedelta(hours=JWT_EXPIRE_HOURS)
    payload = {
        "sub": admin_id,
        "tid": telegram_id,
        "role": role,
        "name": display_name,
        "jti": jti,
        "exp": expires_at,
        "iat": datetime.utcnow(),
    }
    token = jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)
    return token, jti, expires_at

def decode_token(token: str) -> dict:
    """Decode and verify JWT token."""
    return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
