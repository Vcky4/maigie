import os

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_health_check(client: AsyncClient):
    """Test health check endpoint - requires DATABASE_URL to be set."""
    if not os.getenv("DATABASE_URL"):
        pytest.skip("DATABASE_URL not set - skipping database-dependent test")
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "healthy"


@pytest.mark.asyncio
async def test_ready_check(client: AsyncClient):
    """Test ready check endpoint - requires DATABASE_URL to be set."""
    if not os.getenv("DATABASE_URL"):
        pytest.skip("DATABASE_URL not set - skipping database-dependent test")
    response = await client.get("/ready")
    assert response.status_code == 200
    # Check if database status is reported
    data = response.json()
    assert "database" in data
