# apps/backend/tests/conftest.py
import pytest
from httpx import AsyncClient, ASGITransport
from typing import AsyncGenerator
from src.main import app  # Importing your FastAPI app

@pytest.fixture(scope="function")
async def client() -> AsyncGenerator[AsyncClient, None]:
    """
    Creates an asynchronous HTTP client for testing.
    This 'mock' client sends requests directly to your FastAPI app.
    """
    # We use ASGITransport so we don't need a running server
    transport = ASGITransport(app=app)
    
    # Base URL is just a placeholder, it doesn't matter for local tests
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac