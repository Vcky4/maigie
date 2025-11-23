# apps/backend/tests/test_api.py
import pytest
from httpx import AsyncClient

@pytest.mark.asyncio
async def test_root_endpoint(client: AsyncClient):
    """Test that the root URL returns the correct version"""
    response = await client.get("/")
    assert response.status_code == 200
    # Checks if the JSON response matches what we expect
    assert response.json()["version"] == "0.1.0"

@pytest.mark.asyncio
async def test_health_check(client: AsyncClient):
    """Test that the health check returns 'healthy'"""
    response = await client.get("/health")
    assert response.status_code == 200
    
    data = response.json()
    assert data["status"] == "healthy"
    
    # Check if the database key exists (value depends on connection)
    assert "database" in data