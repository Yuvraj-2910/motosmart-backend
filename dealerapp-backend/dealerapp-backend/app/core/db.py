"""Async SQLAlchemy engine + session dependency.

The schema is owned by Alembic; we never call `create_all()` in app code.
"""

from __future__ import annotations

import ssl
from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import settings

_connect_args: dict = {}
if settings.DB_SSL_CA_BUNDLE:
    # asyncpg takes an SSLContext, not libpq's sslmode/sslrootcert query params.
    ctx = ssl.create_default_context(cafile=settings.DB_SSL_CA_BUNDLE)
    ctx.check_hostname = True
    ctx.verify_mode = ssl.CERT_REQUIRED
    _connect_args["ssl"] = ctx

engine: AsyncEngine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.DB_ECHO,
    pool_pre_ping=True,
    pool_size=settings.DB_POOL_SIZE,
    max_overflow=settings.DB_MAX_OVERFLOW,
    connect_args=_connect_args,
)

SessionLocal: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding a session, rolling back on error."""
    async with SessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


async def dispose_engine() -> None:
    await engine.dispose()
