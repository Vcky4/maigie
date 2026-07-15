"""
Async database session management.

Provides:
- async_engine: The SQLAlchemy async engine (connection pool)
- async_session_factory: Session factory for creating async sessions
- get_session(): FastAPI dependency that yields a session per request
- connect_db() / disconnect_db(): Lifecycle hooks for app startup/shutdown

Usage in routes:
    from src.shared.database import get_session
    from sqlalchemy.ext.asyncio import AsyncSession

    @router.get("/users")
    async def list_users(session: AsyncSession = Depends(get_session)):
        result = await session.execute(select(User))
        return result.scalars().all()

Usage in services/repositories:
    from src.shared.database import async_session_factory

    async with async_session_factory() as session:
        result = await session.execute(select(User).where(User.id == user_id))
        return result.scalar_one_or_none()
"""

import logging
from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from src.config import get_settings

logger = logging.getLogger(__name__)

# These are initialized on app startup via connect_db()
_engine = None
_session_factory = None


def _get_async_url(database_url: str) -> str:
    """Convert a standard PostgreSQL URL to asyncpg format.

    postgresql://user:pass@host/db → postgresql+asyncpg://user:pass@host/db
    """
    if database_url.startswith("postgresql://"):
        return database_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if database_url.startswith("postgres://"):
        return database_url.replace("postgres://", "postgresql+asyncpg://", 1)
    return database_url


async def connect_db() -> None:
    """Create the async engine and session factory. Call on app startup."""
    global _engine, _session_factory

    settings = get_settings()
    url = _get_async_url(settings.DATABASE_URL)

    # Remove pgbouncer param if present (asyncpg doesn't support it as URL param)
    # Keep the connection but handle pgbouncer at the pool level
    if "?pgbouncer=true" in url:
        url = url.replace("?pgbouncer=true", "")
    elif "&pgbouncer=true" in url:
        url = url.replace("&pgbouncer=true", "")

    _engine = create_async_engine(
        url,
        echo=settings.DEBUG,
        pool_size=20,
        max_overflow=10,
        pool_pre_ping=True,
        pool_recycle=300,
    )

    _session_factory = async_sessionmaker(
        _engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    logger.info("SQLAlchemy async engine connected")


async def disconnect_db() -> None:
    """Dispose the engine. Call on app shutdown."""
    global _engine, _session_factory

    if _engine:
        await _engine.dispose()
        logger.info("SQLAlchemy engine disposed")

    _engine = None
    _session_factory = None


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Get the session factory (for use in services/repositories)."""
    if _session_factory is None:
        raise RuntimeError("Database not initialized. Call connect_db() first.")
    return _session_factory


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency that provides a database session per request.

    Usage:
        @router.get("/items")
        async def get_items(session: AsyncSession = Depends(get_session)):
            ...
    """
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def check_db_health() -> dict:
    """Health check — execute a simple query."""
    if _engine is None:
        return {"status": "disconnected", "type": "postgresql"}
    try:
        async with _engine.connect() as conn:
            await conn.execute(
                __import__("sqlalchemy").text("SELECT 1")
            )
        return {"status": "healthy", "type": "postgresql", "engine": "sqlalchemy"}
    except Exception as e:
        logger.error(f"DB health check failed: {e}")
        return {"status": "unhealthy", "error": str(e), "type": "postgresql"}
