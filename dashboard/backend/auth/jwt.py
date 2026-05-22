"""JWT token creation and verification."""
import jwt
import uuid
from datetime import datetime, timedelta, timezone
from ..config import JWT_SECRET, JWT_ALGORITHM, JWT_EXPIRE_HOURS

def create_token(admin_id: int, telegram_id: int, role: str, display_name: str) -> tuple[str, str, datetime]:
    """Create JWT token. Returns (token, jti, expires_at)."""
    jti = uuid.uuid4().hex
    # FIX 2025-05-21 (Phase D-2): use timezone-aware UTC internally (datetime.utcnow is deprecated)
    now_utc = datetime.now(timezone.utc)
    expires_at_aware = now_utc + timedelta(hours=JWT_EXPIRE_HOURS)
    payload = {
        "sub": admin_id,
        "tid": telegram_id,
        "role": role,
        "name": display_name,
        "jti": jti,
        "exp": expires_at_aware,
        "iat": now_utc,
    }
    token = jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)
    # FIX 2025-05-21 (Phase D-fix4): DB schema dashboard_sessions.expires_at is
    # `timestamp without time zone` — drop tz info to avoid asyncpg DataError
    expires_at_naive = expires_at_aware.astimezone(timezone.utc).replace(tzinfo=None)
    return token, jti, expires_at_naive

def decode_token(token: str) -> dict:
    """Decode and verify JWT token."""
    return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
