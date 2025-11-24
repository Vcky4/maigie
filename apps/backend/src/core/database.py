"""
Database connection utilities using Prisma.
This manages the global database connection lifecycle.
"""

from prisma import Prisma

# 1. Global Database Instance
# We instantiate this ONCE. Prisma handles the connection pooling internally.
db = Prisma()


async def connect_db() -> None:
    """
    Connect to the database.
    This should be called during the FastAPI startup event.
    """
    if not db.is_connected():
        await db.connect()
        print(" Database Connected")


async def disconnect_db() -> None:
    """
    Disconnect from the database.
    This should be called during the FastAPI shutdown event.
    """
    if db.is_connected():
        await db.disconnect()
        print(" Database Disconnected")


async def check_db_health() -> dict:
    """
    Perform a real query to ensure the database is responsive.
    """
    try:
        if not db.is_connected():
            return {"status": "disconnected", "type": "postgresql"}

        # Run a simple raw query to check connectivity
        # This is better than just checking .is_connected()
        await db.query_raw("SELECT 1")

        return {"status": "healthy", "type": "postgresql", "engine": "prisma"}
    except Exception as e:
        return {"status": "unhealthy", "error": str(e), "type": "postgresql"}
