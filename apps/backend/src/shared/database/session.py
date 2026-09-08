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

import asyncio
import logging
from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from src.config import get_settings

logger = logging.getLogger(__name__)

# These are initialized on app startup via connect_db()
_engine = None
_session_factory = None
# The event loop the engine's connections belong to. asyncpg binds a connection to
# the loop that opened it, and Celery tasks run one loop per invocation, so this is
# how `ensure_db` tells "already connected" from "connected on a loop that is gone".
_engine_loop: asyncio.AbstractEventLoop | None = None


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
    """Create an unpooled async engine for Celery worker processes.

    A prefork worker runs one task at a time per fork, and each task runs on its own
    event loop, so a *pool* in a worker is all cost and no benefit: the connections it
    retains outlive the loop that created them and can never be reused. `NullPool`
    opens a connection when a task asks for one and closes it when the task is done,
    which is both the smallest possible claim on the shared pooler allowance and the
    only shape that cannot leak across loops.
    """
    await _connect_db(null_pool=True)


async def _connect_db(
    *,
    pool_size: int | None = None,
    max_overflow: int | None = None,
    null_pool: bool = False,
) -> None:
    """Internal: create the async engine and session factory.

    Either pass `pool_size`/`max_overflow` for a pooled engine, or `null_pool=True`
    for one that holds no connections between checkouts.
    """
    global _engine, _session_factory, _engine_loop

    settings = get_settings()
    url = _get_async_url(settings.DATABASE_URL)

    # Remove pgbouncer param if present (asyncpg doesn't support it as URL param)
    if "?pgbouncer=true" in url:
        url = url.replace("?pgbouncer=true", "")
    elif "&pgbouncer=true" in url:
        url = url.replace("&pgbouncer=true", "")

    if null_pool:
        # No `pool_size`/`max_overflow`: NullPool does not accept them. No pre-ping
        # either — every checkout is a brand-new connection, so the extra `SELECT 1`
        # would only guard against a failure that cannot happen.
        pool_kwargs: dict = {"poolclass": NullPool}
    else:
        pool_kwargs = {
            "pool_size": pool_size,
            "max_overflow": max_overflow,
            # Kept deliberately. It costs a `SELECT 1` per checkout — one of the three
            # round trips measured per repository call — but behind PgBouncer a pooled
            # connection can be closed server-side between uses, and without the ping
            # that surfaces as a request-failing error rather than a transparent
            # reconnect. Removing it is a latency win with an availability cost, so it
            # wants its own change and its own measurement, not a quiet flip here.
            "pool_pre_ping": True,
            "pool_recycle": settings.DB_POOL_RECYCLE_SECONDS,
        }

    _engine = create_async_engine(
        url,
        echo=settings.DEBUG,
        **pool_kwargs,
        # Disable prepared statement caching for pgbouncer compatibility, and pin the search_path.
        # Supabase's connection pooler hands out sessions with an empty search_path, which makes
        # unqualified table names — and `create_all` — fail with "no schema has been selected to
        # create in". Pinning it so every pooled connection resolves `public` first is required for
        # the app to work behind the pooler; `extensions` is included for Supabase-hosted extension
        # functions (gen_random_uuid, etc.), and both are harmless where those schemas don't exist.
        connect_args={
            "prepared_statement_cache_size": 0,
            "statement_cache_size": 0,
            "server_settings": {"search_path": "public, extensions"},
        },
    )

    _session_factory = async_sessionmaker(
        _engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    try:
        _engine_loop = asyncio.get_running_loop()
    except RuntimeError:  # pragma: no cover — _connect_db is always awaited
        _engine_loop = None

    if null_pool:
        logger.info("SQLAlchemy async engine connected (unpooled)")
    else:
        logger.info(
            "SQLAlchemy async engine connected (pool_size=%d, max_overflow=%d)",
            pool_size,
            max_overflow,
        )
        _warn_if_session_mode(url, pool_size or 0, max_overflow or 0)


def _warn_if_session_mode(url: str, pool_size: int, max_overflow: int) -> None:
    """Say so loudly when a pooled engine is pointed at Supabase's session-mode port.

    The two pooler ports have very different ceilings. Transaction mode (6543) multiplexes
    many clients onto the tenant's direct connections. Session mode (5432) gives every client
    a connection for its whole life, so the client cap *is* the tenant pool size — 15 here —
    and it is reported as `EMAXCONNSESSION: max clients reached in session mode`. Session mode
    is the right choice for DDL (see `scripts/db_direct.py`) and the wrong one for a server
    holding a pool, which is a mistake that otherwise only shows up under load, in the shape
    of unrelated queries failing.
    """
    if "pooler.supabase.com:5432" not in url:
        return
    logger.warning(
        "DATABASE_URL points at the session-mode pooler (:5432) while holding a pool of "
        "%d+%d connections. Session mode caps concurrent clients at the tenant pool size "
        "(15), so this will exhaust it. Use transaction mode (:6543) for the app and keep "
        ":5432 for migrations.",
        pool_size,
        max_overflow,
    )


async def disconnect_db() -> None:
    """Dispose the engine. Call on app shutdown."""
    global _engine, _session_factory, _engine_loop

    if _engine:
        await _engine.dispose()
        logger.info("SQLAlchemy engine disposed")

    _engine = None
    _session_factory = None
    _engine_loop = None


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Get the session factory (for use in services/repositories)."""
    if _session_factory is None:
        raise RuntimeError("Database not initialized. Call connect_db() first.")
    return _session_factory


async def ensure_db() -> None:
    """Ensure the database is initialized on the current event loop.

    Celery tasks create a new event loop per invocation, and asyncpg binds a connection to
    the loop that opened it, so an engine built on a previous task's loop is unusable here.

    Two things this deliberately does *not* do any more, both of which leaked pooler clients:

    - It does not probe the old engine with `SELECT 1`. The probe cost a round trip on every
      task, and its failure path was the leak: the stale engine was dropped by reassigning
      the module global, which frees the Python object but never closes the sockets. Those
      connections belong to a closed loop, so nothing ever sends a FIN, and the pooler goes
      on counting each one as a live client. In session mode, where the client cap is the
      tenant pool size (15), a few dozen task runs is enough to exhaust it — and it surfaces
      as `EMAXCONNSESSION` on an unrelated API request, which is what made it hard to place.
    - It does not build a pooled engine. Worker engines are `NullPool` (see
      `connect_db_worker`), so a connection is opened per checkout and closed at the end of
      the task, on the loop that opened it. There is then nothing for a discarded engine to
      hold, which is what makes discarding it safe rather than merely quiet.

    Call this at the start of every async Celery task coroutine.
    """
    global _engine, _session_factory, _engine_loop

    current_loop = asyncio.get_running_loop()

    if _engine is not None and _engine_loop is current_loop:
        return

    if _engine is not None:
        _discard_stale_engine()

    await connect_db_worker()


def _discard_stale_engine() -> None:
    """Drop an engine belonging to a dead event loop.

    `dispose()` cannot be awaited: its connections are bound to a loop that is already
    closed, and awaiting it there raises. Discarding the references is only correct
    because the engine is unpooled and therefore holds no open connection between
    tasks. If that ever stops being true, the connections would leak silently, so this
    says so instead of assuming.
    """
    global _engine, _session_factory, _engine_loop

    pool = getattr(_engine.sync_engine, "pool", None)
    if pool is not None and not isinstance(pool, NullPool):
        logger.warning(
            "Discarding a pooled engine from a closed event loop (%s); its connections "
            "cannot be closed from here and will be held by the pooler until the process "
            "exits. Worker engines should be unpooled — see connect_db_worker.",
            type(pool).__name__,
        )

    _engine = None
    _session_factory = None
    _engine_loop = None


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
