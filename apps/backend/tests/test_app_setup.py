"""Application setup tests.

These cover the wiring in ``src/app.py``: metadata, middleware, health
reporting, the OpenAPI schema, and which domain routers are mounted.

None of these tests need a database or cache. The health endpoint reports
service status rather than requiring it, so it is asserted against both the
connected and disconnected cases.
"""

import pytest
from httpx import AsyncClient

from src.config import get_settings

# API_PREFIX must match the prefix used by ``_register_domains``.
API_PREFIX = "/api/v1"


@pytest.fixture(autouse=True)
def db_lifecycle():
    """Override the session-wide database fixture.

    Application setup is verified without a live database, so these tests must
    not be skipped when ``DATABASE_URL`` is unset.
    """
    yield


@pytest.mark.asyncio
async def test_app_metadata_matches_settings():
    """App title and version come from settings, not hard-coded strings."""
    from src.app import app

    settings = get_settings()
    assert app.title == settings.APP_NAME
    assert app.version == settings.APP_VERSION


@pytest.mark.asyncio
async def test_health_endpoint_reports_service_status(client: AsyncClient):
    """Health check responds with per-service status instead of failing."""
    response = await client.get("/health")
    assert response.status_code == 200

    body = response.json()
    settings = get_settings()
    # "healthy" only when the database is reachable; "degraded" otherwise.
    assert body["status"] in {"healthy", "degraded"}
    assert body["version"] == settings.APP_VERSION
    assert body["environment"] == settings.ENVIRONMENT
    assert "database" in body["services"]
    assert "cache" in body["services"]
    assert "status" in body["services"]["database"]


@pytest.mark.asyncio
async def test_security_headers_are_applied(client: AsyncClient):
    """SecurityHeadersMiddleware sets its headers on every response."""
    response = await client.get("/health")

    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["X-XSS-Protection"] == "1; mode=block"
    assert response.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"


@pytest.mark.asyncio
async def test_hsts_header_only_outside_production(client: AsyncClient):
    """HSTS is added only when running in production."""
    response = await client.get("/health")

    is_production = get_settings().ENVIRONMENT == "production"
    assert ("Strict-Transport-Security" in response.headers) is is_production


@pytest.mark.asyncio
async def test_process_time_header_is_added(client: AsyncClient):
    """LoggingMiddleware reports how long the request took."""
    response = await client.get("/health")

    assert "X-Process-Time" in response.headers
    assert float(response.headers["X-Process-Time"]) >= 0


@pytest.mark.asyncio
async def test_cors_preflight_allows_a_configured_origin(client: AsyncClient):
    """A configured origin passes the CORS preflight."""
    settings = get_settings()
    origin = settings.CORS_ORIGINS[0]

    response = await client.options(
        "/health",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "GET",
        },
    )

    assert response.status_code in (200, 204)
    assert response.headers["access-control-allow-origin"] == origin


@pytest.mark.asyncio
async def test_cors_preflight_rejects_an_unknown_origin(client: AsyncClient):
    """An unlisted origin is not granted access."""
    response = await client.options(
        "/health",
        headers={
            "Origin": "https://not-a-maigie-origin.example",
            "Access-Control-Request-Method": "GET",
        },
    )

    assert "access-control-allow-origin" not in response.headers


@pytest.mark.asyncio
async def test_openapi_schema_is_available(client: AsyncClient):
    """The OpenAPI schema is served and describes this application."""
    response = await client.get("/openapi.json")
    assert response.status_code == 200

    schema = response.json()
    settings = get_settings()
    assert schema["info"]["title"] == settings.APP_NAME
    assert schema["info"]["version"] == settings.APP_VERSION
    assert schema["paths"]


@pytest.mark.asyncio
async def test_openapi_operation_ids_are_unique():
    """Duplicate operation ids break generated client types.

    Client repositories generate TypeScript from this schema and key their
    operations off the operation id, so a collision silently makes one
    endpoint unreachable in generated clients.
    """
    from src.app import create_app

    schema = create_app().openapi()

    operation_ids = [
        operation["operationId"]
        for path_item in schema["paths"].values()
        for method, operation in path_item.items()
        if method in {"get", "post", "put", "patch", "delete"} and "operationId" in operation
    ]

    duplicates = {
        operation_id for operation_id in operation_ids if operation_ids.count(operation_id) > 1
    }
    assert not duplicates, f"Duplicate OpenAPI operation ids: {sorted(duplicates)}"


@pytest.mark.asyncio
async def test_mounted_domain_routers(client: AsyncClient):
    """The domains wired up in ``_register_domains`` are reachable.

    Guards against a router being dropped during refactoring. Only domains
    that are actually mounted are asserted; the rest stay commented out in
    ``src/app.py`` until their contracts are normalized.
    """
    response = await client.get("/openapi.json")
    paths = response.json()["paths"]

    for expected in (
        f"{API_PREFIX}/auth/login/json",
        f"{API_PREFIX}/auth/oauth/providers",
        f"{API_PREFIX}/users/me",
        f"{API_PREFIX}/learning/home",
        f"{API_PREFIX}/learning/dashboard",
        f"{API_PREFIX}/knowledge/courses",
        f"{API_PREFIX}/spaces",
        f"{API_PREFIX}/progress/streaks",
    ):
        assert expected in paths, f"{expected} is not mounted"


@pytest.mark.asyncio
async def test_unmounted_domains_are_absent():
    """Domains still awaiting migration must not appear mounted.

    ``src/app.py`` intentionally leaves admin and classrooms commented out. Asserting
    their absence keeps the documented state and the wiring honest.

    Billing has left this list: it is mounted, and while it was not the meter ran with no
    reachable way to pay it. What is and is not served *inside* that domain is asserted in
    `test_billing_routes_mounted.py`, endpoint by endpoint — several of its endpoints are
    absent for several different reasons, and a prefix check cannot tell them apart.
    """
    from src.app import create_app

    paths = create_app().openapi()["paths"]

    for prefix in (
        f"{API_PREFIX}/admin",
        f"{API_PREFIX}/classrooms",
    ):
        assert not any(
            path.startswith(prefix) for path in paths
        ), f"{prefix} is mounted but src/app.py documents it as pending"


@pytest.mark.asyncio
async def test_redoc_is_available(client: AsyncClient):
    """ReDoc is the public API reference and is always served."""
    response = await client.get("/redoc")
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_swagger_visibility_follows_debug_setting(client: AsyncClient):
    """Swagger UI is exposed in debug builds and hidden otherwise."""
    response = await client.get("/docs")

    if get_settings().DEBUG:
        assert response.status_code == 200
    else:
        assert response.status_code == 404
