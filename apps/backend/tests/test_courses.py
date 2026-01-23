import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_get_courses_requires_login(client: AsyncClient):
    """Test that fetching courses without a token fails."""
    response = await client.get("/api/v1/courses")
    assert response.status_code in [401, 403]  # <--- Accept either code
