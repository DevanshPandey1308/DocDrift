"""Async PostgreSQL engine and session factory helpers."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.ext.asyncio.engine import AsyncEngine


def normalize_async_database_url(database_url: str) -> str:
    """Ensure common PostgreSQL URLs select the asyncpg SQLAlchemy driver."""
    if database_url.startswith("postgresql://"):
        return database_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if database_url.startswith("postgres://"):
        return database_url.replace("postgres://", "postgresql+asyncpg://", 1)
    if database_url.startswith("postgresql+asyncpg://"):
        return database_url
    raise ValueError("DOCDRIFT_DATABASE_URL must be a PostgreSQL URL using asyncpg.")


def create_session_factory(
    database_url: str,
) -> tuple[async_sessionmaker[AsyncSession], AsyncEngine]:
    """Create one reusable async engine and its session factory."""
    engine = create_async_engine(normalize_async_database_url(database_url), pool_pre_ping=True)
    return async_sessionmaker(engine, expire_on_commit=False), engine


async def dispose_engine(engine: AsyncEngine) -> None:
    """Dispose an engine created by ``create_session_factory``."""
    await engine.dispose()
