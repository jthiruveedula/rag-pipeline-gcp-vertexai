"""Async SQLAlchemy engine and session factory for Cloud SQL PostgreSQL."""

import os
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from google.cloud import secretmanager


def _get_db_url() -> str:
    """Resolve DB connection string from Secret Manager or env var."""
    # In Cloud Run: use unix socket via Cloud SQL Auth Proxy
    # DATABASE_URL=postgresql+asyncpg://user:pass@/dbname?host=/cloudsql/PROJECT:REGION:INSTANCE
    db_url = os.getenv("DATABASE_URL")
    if db_url:
        return db_url

    # Fallback: construct from individual env vars
    user = os.getenv("DB_USER", "platform")
    password = os.getenv("DB_PASSWORD", "")
    host = os.getenv("DB_HOST", "localhost")
    port = os.getenv("DB_PORT", "5432")
    dbname = os.getenv("DB_NAME", "platform_db")
    return f"postgresql+asyncpg://{user}:{password}@{host}:{port}/{dbname}"


engine = create_async_engine(
    _get_db_url(),
    echo=os.getenv("DB_ECHO", "false").lower() == "true",
    pool_size=5,
    max_overflow=10,
    pool_pre_ping=True,
)

AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency: yields an async DB session per request."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def create_all_tables() -> None:
    """Create all tables (dev/test only - use Alembic in production)."""
    from .models import Base
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
