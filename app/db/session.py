from __future__ import annotations

from collections.abc import AsyncIterator

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import settings

engine: AsyncEngine = create_async_engine(
    settings.DATABASE_URL,
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_pool_size * 2,
    pool_pre_ping=True,
    pool_recycle=3600,
    echo=False,
)

AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

_redis: Redis | None = None


async def get_redis() -> Redis:
    """Return a lazily-initialised, process-wide Redis client."""
    global _redis
    if _redis is None:
        _redis = Redis.from_url(settings.REDIS_URL, decode_responses=True)
    return _redis


async def get_db() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency — yields a session that is closed afterwards."""
    async with AsyncSessionLocal() as session:
        yield session


async def init_db() -> None:
    """Create all tables directly from models. Used in tests; production uses Alembic."""
    from app.db.models import Base

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
