"""
Prisma database client lifecycle management.

Provides a singleton Prisma client with connect/disconnect/health_check
for use across all domains. Domains access the database through their
own repository layer, never directly through this module.

Usage:
    from src.shared.database import db, connect_db, disconnect_db

    # In app lifespan:
    await connect_db()
    ...
    await disconnect_db()

    # In repositories:
    from src.shared.database import db
    result = await db.user.find_unique(where={"id": user_id})
"""

import logging

from prisma import Prisma

logger = logging.getLogger(__name__)

# Global database instance — Prisma manages connection pooling internally.
db = Prisma()


async def connect_db() -> None:
    """Connect to the database. Call during application startup."""
    if not db.is_connected():
        await db.connect()
        logger.info("Database connected")


async def disconnect_db() -> None:
    """Disconnect from the database. Call during application shutdown."""
    if db.is_connected():
        await db.disconnect()
        logger.info("Database disconnected")


async def check_db_health() -> dict:
    """Check database connectivity with a real query.

    Returns:
        Health status dictionary with status, type, and optional error.
    """
    try:
        if not db.is_connected():
            return {"status": "disconnected", "type": "postgresql"}

        await db.query_raw("SELECT 1")
        return {"status": "healthy", "type": "postgresql", "engine": "prisma"}
    except Exception as e:
        logger.error(f"Database health check failed: {e}")
        return {"status": "unhealthy", "error": str(e), "type": "postgresql"}
