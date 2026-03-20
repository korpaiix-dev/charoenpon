"""Dashboard configuration."""
import os

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@postgres:5432/charoenpon")
# Convert asyncpg URL for raw asyncpg usage
DB_DSN = DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://").replace("postgresql://", "postgresql://")

JWT_SECRET = os.getenv("DASHBOARD_JWT_SECRET", "charoenpon-dashboard-secret-2026-kx9m")
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_HOURS = 24

SALES_BOT_TOKEN = os.getenv("SALES_BOT_TOKEN", "")
GUARDIAN_BOT_TOKEN = os.getenv("GUARDIAN_BOT_TOKEN", "")
ADMIN_BOT_TOKEN = os.getenv("ADMIN_BOT_TOKEN", "")
CONTENT_BOT_TOKEN = os.getenv("CONTENT_BOT_TOKEN", "")
ANNOUNCE_BOT_TOKEN = os.getenv("ANNOUNCE_BOT_TOKEN", "")

ADMIN_TELEGRAM_IDS = [int(x) for x in os.getenv("ADMIN_TELEGRAM_IDS", "").split(",") if x.strip()]

# Role levels
ROLE_LEVELS = {"owner": 100, "super_admin": 75, "admin": 50, "moderator": 10}
