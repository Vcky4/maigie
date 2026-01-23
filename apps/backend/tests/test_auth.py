import os
import uuid
from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient

try:
    from prisma.errors import TableNotFoundError
except ImportError:
    # Fallback if prisma.errors is not available
    TableNotFoundError = Exception

from src.core.database import connect_db, db, disconnect_db
from src.main import app


# 1. Manage Database Lifecycle
@pytest.fixture(scope="function", autouse=True)
async def db_lifecycle():
    """
    Connect to DB for tests that require database access.
    Skips database connection if DATABASE_URL is not set.
    """
    database_url = os.getenv("DATABASE_URL", "")
    if not database_url:
        pytest.skip("DATABASE_URL not set - skipping database-dependent test")

    connected = False
    try:
        await connect_db()
        connected = True
        try:
            await db.query_raw('SELECT 1 FROM "User" LIMIT 1')
        except TableNotFoundError:
            pytest.skip("Database tables do not exist. Run migrations first.")
        except Exception as e:
            error_msg = str(e).lower()
            if "table" in error_msg and ("does not exist" in error_msg or "not found" in error_msg):
                pytest.skip("Database tables do not exist. Run migrations first.")
            raise
        yield
    except Exception as e:
        pytest.skip(f"Database connection failed: {e}")
    finally:
        if connected and db.is_connected():
            try:
                await disconnect_db()
            except Exception:
                pass


# 2. Create Client
@pytest.fixture(scope="function")
async def client() -> AsyncGenerator[AsyncClient, None]:
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# 3. Auth Headers Fixture (FIXED & FORMATTED)
@pytest.fixture
async def auth_headers(client: AsyncClient):
    """Creates a user, forces them active, and logs them in."""
    unique_email = f"test_{uuid.uuid4()}@example.com"
    password = "StrongPassword123!"

    user_data = {"email": unique_email, "password": password, "name": "Test User"}

    # 1. Signup
    signup_res = await client.post("/api/v1/auth/signup", json=user_data)
    if signup_res.status_code != 201:
        pytest.fail(f"Signup failed: {signup_res.status_code} - {signup_res.text}")

    # 2. FORCE ACTIVATE USER
    # Bypass OTP verification
    user = await db.user.update(where={"email": unique_email}, data={"isActive": True})
    if not user:
        pytest.fail("Database update failed: User not found after signup")

    # 3. Login
    login_data = {"username": unique_email, "password": password}
    headers = {"Content-Type": "application/x-www-form-urlencoded"}

    # Try standard URL
    response = await client.post("/api/v1/auth/login", data=login_data, headers=headers)

    # Fallback: Try with trailing slash (FastAPI sometimes requires this)
    if response.status_code == 404:
        response = await client.post("/api/v1/auth/login/", data=login_data, headers=headers)

    # Fallback: Try standard OAuth2 path /token
    if response.status_code == 404:
        response = await client.post("/api/v1/auth/token", data=login_data, headers=headers)

    if response.status_code != 200:
        pytest.fail(
            f"Login failed on all attempted URLs. Last Status: {response.status_code}, Body: {response.text}"
        )

    token = response.json().get("access_token")
    if not token:
        pytest.fail(f"No access_token in login response: {response.json()}")

    return {"Authorization": f"Bearer {token}"}
