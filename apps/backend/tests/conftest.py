import os
from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient

from src.core.database import connect_db, disconnect_db, db
from src.main import app


# Manage Database Lifecycle
# NOTE: This fixture is NOT autouse - tests that need DB should explicitly request it
# or use the @pytest.mark.db_required marker
@pytest.fixture(scope="function")
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


# Create Client
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
