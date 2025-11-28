import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_health_check(client: AsyncClient, db_lifecycle):
    """Test health check endpoint - requires DATABASE_URL to be set."""
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "healthy"


@pytest.mark.asyncio
async def test_ready_check(client: AsyncClient, db_lifecycle):
    """Test ready check endpoint - requires DATABASE_URL to be set."""
    response = await client.get("/ready")
    assert response.status_code == 200
    # Check if database status is reported
    data = response.json()
    assert "database" in data
