from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient

from src.core.database import connect_db, disconnect_db
from src.main import app


# Manage Database Lifecycle
@pytest.fixture(scope="function", autouse=True)
async def db_lifecycle():
    """
    Connect to DB once for the entire session.
    """
    await connect_db()
    yield
    await disconnect_db()


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
