"""Database connection pool using asyncpg."""
import asyncpg
from .config import DB_DSN

_pool: asyncpg.Pool = None

async def _init_codecs(conn):
    """FIX 2026-06-26 (audit): register JSONB/JSON codecs so dict/list pass through native.

    Without this, asyncpg returns JSONB columns as strings — caused frontend bug
    \"c.delivery_channels.join is not a function\" because the column came back as
    JSON string instead of Python list. Setting codec means it Just Works."""
    import json as _json
    await conn.set_type_codec(
        "jsonb",
        encoder=_json.dumps,
        decoder=_json.loads,
        schema="pg_catalog",
    )
    await conn.set_type_codec(
        "json",
        encoder=_json.dumps,
        decoder=_json.loads,
        schema="pg_catalog",
    )


async def init_db():
    global _pool
    dsn = DB_DSN
    if "postgresql+asyncpg://" in dsn:
        dsn = dsn.replace("postgresql+asyncpg://", "postgresql://")
    _pool = await asyncpg.create_pool(
        dsn, min_size=2, max_size=10,
        init=_init_codecs,
    )

async def close_db():
    global _pool
    if _pool:
        await _pool.close()


class _PoolProxy:
    """Proxy that always delegates to the current global pool."""
    def __getattr__(self, name):
        if _pool is None:
            raise RuntimeError("Database pool not initialized")
        return getattr(_pool, name)

pool = _PoolProxy()
