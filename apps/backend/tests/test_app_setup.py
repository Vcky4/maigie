"""Tests for application setup."""

from collections.abc import AsyncGenerator

import pytest
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

from src.main import app
from src.utils.dependencies import get_db_client, get_redis_client


# Mock database client for testing
class MockPrismaClient:
    """Mock Prisma client for testing."""

    async def query_raw(self, query: str):
        """Mock query that returns a valid result."""
        return [{"test": 1}]


# Mock Redis client for testing
class MockRedisClient:
    """Mock Redis client for testing."""

    async def ping(self):
        """Mock ping that succeeds."""
        return True


async def override_get_db_client() -> AsyncGenerator:
    """Override database dependency for testing."""
    mock_client = MockPrismaClient()
    yield mock_client


async def override_get_redis_client() -> AsyncGenerator:
    """Override Redis dependency for testing."""
    mock_client = MockRedisClient()
    yield mock_client


@pytest.fixture
def client():
    """Create synchronous test client for non-async endpoints."""
    return TestClient(app)


@pytest.fixture
async def async_client():
    """Create async test client with overridden dependencies."""
    # Override dependencies for health check endpoints
    app.dependency_overrides[get_db_client] = override_get_db_client
    app.dependency_overrides[get_redis_client] = override_get_redis_client

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    # Clean up overrides after test
    app.dependency_overrides.clear()


def test_app_creation():
    """Test that FastAPI app is created successfully."""
    assert app is not None
    assert app.title == "Maigie API"


def test_root_endpoint(client):
    """Test root endpoint."""
    response = client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert "message" in data
    assert "version" in data
    assert data["message"] == "Maigie API"


@pytest.mark.asyncio
async def test_health_endpoint(async_client):
    """Test health check endpoint."""
    response = await async_client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"


def test_ready_endpoint(client):
    """Test readiness check endpoint."""
    response = client.get("/ready")
    assert response.status_code == 200
    data = response.json()
    assert "status" in data
    assert "database" in data
    assert "cache" in data
    assert data["status"] == "ready"


def test_cors_headers(client):
    """Test CORS headers are present."""
    response = client.options(
        "/",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "GET",
        },
    )
    # CORS preflight should be handled
    assert response.status_code in [200, 204]


def test_security_headers(client):
    """Test security headers are present."""
    response = client.get("/")
    assert "X-Content-Type-Options" in response.headers
    assert "X-Frame-Options" in response.headers
    assert "X-XSS-Protection" in response.headers
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"


def test_process_time_header(client):
    """Test X-Process-Time header is present."""
    response = client.get("/")
    assert "X-Process-Time" in response.headers


def test_api_documentation(client):
    """Test API documentation is accessible."""
    response = client.get("/docs")
    assert response.status_code == 200


def test_openapi_schema(client):
    """Test OpenAPI schema is accessible."""
    response = client.get("/openapi.json")
    assert response.status_code == 200
    schema = response.json()
    assert "openapi" in schema
    assert "info" in schema
    assert schema["info"]["title"] == "Maigie API"
