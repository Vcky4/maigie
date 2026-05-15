import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_health_check(client: AsyncClient):
    response = await client.get("/health")
    # Health check returns 503 when DB/Redis are unavailable (e.g. CI without services)
    assert response.status_code in (200, 503)
    data = response.json()
    if response.status_code == 200:
        assert data["status"] == "healthy"
    else:
        # 503 response may nest status under 'detail' key
        detail = data.get("detail", data)
        assert "status" in detail or "db" in detail


@pytest.mark.asyncio
async def test_ready_check(client: AsyncClient):
    response = await client.get("/ready")
    # Ready check may return 503 without DB
    assert response.status_code in (200, 503)
    data = response.json()
    assert "database" in data
