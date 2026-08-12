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
    """Create the async engine and session factory. Call on app startup.

    Sizing comes from settings rather than being hardcoded, because the right
    number depends on how many processes share the database's connection
    allowance — see `DB_POOL_SIZE` for the arithmetic. It was previously 20+10 per
    process against a tenant allowance of 15, so one process could claim double the
    whole budget and two workers could claim four times it.
    """
    settings = get_settings()
    await _connect_db(
        pool_size=settings.DB_POOL_SIZE,
        max_overflow=settings.DB_MAX_OVERFLOW,
    )


async def connect_db_worker() -> None:
    """Create a minimal async engine for Celery worker processes.

    Workers run tasks sequentially (concurrency=1 per fork) so they only need
    a single reusable connection. This prevents exhausting PgBouncer's session
    pool which is shared with the FastAPI app.
    """
    await _connect_db(pool_size=1, max_overflow=1)


async def _connect_db(*, pool_size: int, max_overflow: int) -> None:
    """Internal: create the async engine and session factory."""
    global _engine, _session_factory

    settings = get_settings()
    url = _get_async_url(settings.DATABASE_URL)

    # Remove pgbouncer param if present (asyncpg doesn't support it as URL param)
    if "?pgbouncer=true" in url:
        url = url.replace("?pgbouncer=true", "")
    elif "&pgbouncer=true" in url:
        url = url.replace("&pgbouncer=true", "")

    _engine = create_async_engine(
        url,
        echo=settings.DEBUG,
        pool_size=pool_size,
        max_overflow=max_overflow,
        # Kept deliberately. It costs a `SELECT 1` per checkout — one of the three
        # round trips measured per repository call — but behind PgBouncer a pooled
        # connection can be closed server-side between uses, and without the ping
        # that surfaces as a request-failing error rather than a transparent
        # reconnect. Removing it is a latency win with an availability cost, so it
        # wants its own change and its own measurement, not a quiet flip here.
        pool_pre_ping=True,
        pool_recycle=settings.DB_POOL_RECYCLE_SECONDS,
        # Disable prepared statement caching for pgbouncer compatibility
        connect_args={"prepared_statement_cache_size": 0, "statement_cache_size": 0},
    )

    _session_factory = async_sessionmaker(
        _engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    logger.info("SQLAlchemy async engine connected (pool_size=%d)", pool_size)


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


async def ensure_db() -> None:
    """Ensure the database is initialized on the current event loop.

    Celery tasks create a new event loop per invocation. asyncpg connections are
    bound to the loop they were created on, so we must re-initialize the engine
    if the loop has changed or if we haven't connected yet.

    Call this at the start of every async Celery task coroutine.
    """
    global _engine, _session_factory
    import asyncio

    current_loop = asyncio.get_running_loop()

    if _engine is not None:
        # Check if the existing engine's pool connections are on a different loop
        # by checking if the engine was created on this loop. SQLAlchemy doesn't
        # expose this directly, so we dispose and recreate on any mismatch.
        # Simple heuristic: if _session_factory exists and works, keep it.
        try:
            async with _session_factory() as session:
                await session.execute(__import__("sqlalchemy").text("SELECT 1"))
            return  # Engine is healthy on this loop
        except Exception:
            # Engine is stale (different loop or closed connection).
            # Don't try to dispose — the old engine's connections are bound to a
            # closed loop and disposing them triggers noisy RuntimeError logs.
            # Just discard the references and create fresh ones.
            _engine = None
            _session_factory = None

    # (Re-)initialize with a minimal pool for worker use
    await _connect_db(pool_size=1, max_overflow=1)


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
            await conn.execute(__import__("sqlalchemy").text("SELECT 1"))
        return {"status": "healthy", "type": "postgresql", "engine": "sqlalchemy"}
    except Exception as e:
        logger.error(f"DB health check failed: {e}")
        return {"status": "unhealthy", "error": str(e), "type": "postgresql"}
