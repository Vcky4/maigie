"""Basic API endpoint smoke tests."""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_health_check(client: AsyncClient):
    response = await client.get("/health")
    assert response.status_code in (200, 503)
    data = response.json()
    # Accepts any of: healthy (all services up), degraded (some down), unhealthy (503)
    detail = data.get("detail", data)
    status = detail.get("status", "")
    assert status in {"healthy", "degraded", "unhealthy"}
    assert "services" in detail or "database" in detail


@pytest.mark.asyncio
async def test_openapi_available(client: AsyncClient):
    """OpenAPI schema is available."""
    response = await client.get("/openapi.json")
    assert response.status_code == 200
    schema = response.json()
    assert "openapi" in schema
    assert "paths" in schema
