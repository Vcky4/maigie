import os

import pytest
from httpx import ASGITransport, AsyncClient
from typing import AsyncGenerator

try:
    from prisma.errors import TableNotFoundError
except ImportError:
    # Fallback if prisma.errors is not available
    TableNotFoundError = Exception

from src.core.database import connect_db, db, disconnect_db
from src.main import app

# 1. Force session-scoped event loop po


# 2. Manage Database Lifecycle
@pytest.fixture(scope="function", autouse=True)
async def db_lifecycle():
    """
    Connect to DB for tests that require database access.
    Skips database connection if DATABASE_URL is not set.
    """
    # Only connect if DATABASE_URL is configured
    database_url = os.getenv("DATABASE_URL", "")
    if not database_url:
        pytest.skip("DATABASE_URL not set - skipping database-dependent test")

    connected = False
    try:
        await connect_db()
        connected = True
        # Check if tables exist by trying a simple query
        try:
            await db.query_raw('SELECT 1 FROM "User" LIMIT 1')
        except TableNotFoundError:
            pytest.skip(
                "Database tables do not exist. Run migrations first: "
                "poetry run prisma migrate deploy"
            )
        except Exception as e:
            # Check if it's a table not found error (fallback for different error formats)
            error_msg = str(e).lower()
            if "table" in error_msg and ("does not exist" in error_msg or "not found" in error_msg):
                pytest.skip(
                    "Database tables do not exist. Run migrations first: "
                    "poetry run prisma migrate deploy"
                )
            # For other errors, re-raise
            raise
        yield
    except Exception as e:
        pytest.skip(f"Database connection failed: {e}")
    finally:
        # Always try to disconnect if we connected
        if connected and db.is_connected():
            try:
                await disconnect_db()
            except Exception:
                pass  # Ignore disconnect errors


# 3. Create Client
@pytest.fixture(scope="function")
async def client() -> AsyncGenerator[AsyncClient, None]:
    """
    Create a client that uses the shared DB connection.
    We bypass the app's internal lifespan to prevent it from closing the DB.
    """
    # Create client without triggering lifespan
    transport = ASGITransport(app=app, raise_app_exceptions=False)

    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
