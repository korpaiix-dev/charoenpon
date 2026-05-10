"""Async SQLAlchemy engine, session factory, and DB init - บริษัทเจริญพร."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from shared.models import Base

DATABASE_URL: str = os.environ.get(
    "DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/charoenpon",
)

# Convert postgres:// to postgresql+asyncpg:// if needed (e.g. from Heroku/Railway)
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+asyncpg://", 1)
elif DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)

engine: AsyncEngine = create_async_engine(
    DATABASE_URL,
    echo=os.environ.get("SQL_ECHO", "").lower() in ("1", "true"),
    pool_size=int(os.environ.get("DB_POOL_SIZE", "10")),
    max_overflow=int(os.environ.get("DB_MAX_OVERFLOW", "20")),
    pool_pre_ping=True,
    pool_recycle=300,
)

async_session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Provide a transactional async session scope.

    Usage::

        async with get_session() as session:
            result = await session.execute(select(User))
    """
    session = async_session_factory()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


CONTENT_PREVIEWS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS content_previews (
    id SERIAL PRIMARY KEY,
    content_id INTEGER NOT NULL REFERENCES content_queue(id),
    preview_file_id TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
)
"""

CONTENT_PREVIEWS_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS ix_content_previews_content_id ON content_previews(content_id)
"""


async def init_db() -> None:
    """Create all tables and compatibility tables if they don't exist."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text(CONTENT_PREVIEWS_TABLE_SQL))
        await conn.execute(text(CONTENT_PREVIEWS_INDEX_SQL))


async def drop_db() -> None:
    """Drop all tables — use only for testing."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


async def close_db() -> None:
    """Dispose of the engine connection pool."""
    await engine.dispose()
