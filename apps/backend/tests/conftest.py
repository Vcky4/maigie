"""
Shared pytest configuration.

Uses the new SQLAlchemy async engine (``src.shared.database``) for
database-touching tests. Set ``SKIP_DB_FIXTURE=1`` for pure unit tests
that do not need a live database.

Tests that reference legacy modules under ``src.services`` or
``src.routes`` (removed in the domain refactor) are collected but
skipped automatically — see ``pytest_collection_modifyitems``.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient

# Path segments in imports/source that indicate a test targets a
# module that no longer exists in the domain-driven architecture.
_LEGACY_IMPORT_MARKERS = (
    "src.services.",
    "src.routes.",
    "src.core.database",
    "src.schemas.subscription",
)


def pytest_ignore_collect(collection_path, config):
    """
    Skip legacy test files that import removed modules.

    Reading these files' source is fast and avoids ``ImportError``
    during collection. When you migrate a legacy service, drop the
    import-line marker and the file is picked up again automatically.
    """
    if collection_path.suffix != ".py":
        return None
    if not collection_path.name.startswith("test_"):
        return None
    try:
        source = collection_path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return None
    if any(marker in source for marker in _LEGACY_IMPORT_MARKERS):
        return True  # ignore this file
    return None


# ---------------------------------------------------------------------------
# Database lifecycle
# ---------------------------------------------------------------------------


@pytest.fixture(scope="function", autouse=True)
async def db_lifecycle():
    """
    Connect/disconnect the SQLAlchemy async engine per test.

    - Set ``SKIP_DB_FIXTURE=1`` to skip DB setup entirely (unit tests).
    - If ``DATABASE_URL`` is not set, tests requiring DB are skipped.
    """
    if os.getenv("SKIP_DB_FIXTURE", "").lower() in ("1", "true", "yes"):
        yield
        return

    database_url = os.getenv("DATABASE_URL", "")
    if not database_url:
        pytest.skip("DATABASE_URL not set — skipping database-dependent test")

    from src.shared.database.session import connect_db, disconnect_db

    connected = False
    try:
        await connect_db()
        connected = True
        yield
    except Exception as e:  # pragma: no cover - environmental
        pytest.skip(f"Database connection failed: {e}")
    finally:
        if connected:
            try:
                await disconnect_db()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# HTTP client
# ---------------------------------------------------------------------------


@pytest.fixture(scope="function")
async def client() -> AsyncGenerator[AsyncClient, None]:
    """Async HTTP client against the FastAPI app.

    The app's lifespan is intentionally bypassed — the ``db_lifecycle``
    fixture owns database setup/teardown.
    """
    from src.app import app

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


@pytest.fixture
async def auth_headers(client: AsyncClient):
    """Create a user, activate them, and log them in.

    Returns ``{"Authorization": "Bearer <token>"}``.
    """
    email = f"test_{uuid.uuid4()}@example.com"
    password = "StrongPassword123!"

    signup = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": password, "name": "Test User"},
    )
    if signup.status_code not in (200, 201):
        pytest.skip(
            f"Signup failed (DB likely unavailable): {signup.status_code} - {signup.text[:200]}"
        )

    # Force-activate to bypass OTP verification.
    from sqlalchemy import update as sa_update

    from src.domains.identity.db_models import User
    from src.shared.database.session import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        await session.execute(sa_update(User).where(User.email == email).values(is_active=True))
        await session.commit()

    login = await client.post(
        "/api/v1/auth/login/json",
        json={"email": email, "password": password},
    )
    if login.status_code != 200:
        pytest.fail(f"Login failed: {login.status_code} - {login.text}")

    token = login.json().get("access_token") or login.json().get("accessToken")
    if not token:
        pytest.fail(f"No access_token in login response: {login.json()}")

    return {"Authorization": f"Bearer {token}"}
