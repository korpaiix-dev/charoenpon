"""Database connection pool using asyncpg."""
import asyncpg
from .config import DB_DSN

_pool: asyncpg.Pool = None

async def init_db():
    global _pool
    dsn = DB_DSN
    if "postgresql+asyncpg://" in dsn:
        dsn = dsn.replace("postgresql+asyncpg://", "postgresql://")
    _pool = await asyncpg.create_pool(dsn, min_size=2, max_size=10)

async def close_db():
    global _pool
    if _pool:
        await _pool.close()

def get_pool() -> asyncpg.Pool:
    return _pool

class _PoolProxy:
    """Proxy that always delegates to the current global pool."""
    def __getattr__(self, name):
        if _pool is None:
            raise RuntimeError("Database pool not initialized")
        return getattr(_pool, name)

pool = _PoolProxy()
