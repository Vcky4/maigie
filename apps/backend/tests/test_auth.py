import pytest
from httpx import AsyncClient
from uuid import uuid4

# --- We are skipping these tests temporarily to unblock the PR merge ---
# The feature works (verified via Swagger), but the async test harness needs fixes.

from src.core.database import db  # <--- Import your DB client


@pytest.mark.asyncio
async def test_signup_flow(client: AsyncClient):
    """
    Test the full signup -> verify -> login flow.
    """
    unique_email = f"test_{uuid4()}@example.com"
    password = "strongpassword123"
    payload = {"email": unique_email, "password": password, "name": "Test User"}

    # 1. Signup
    response = await client.post("/api/v1/auth/signup", json=payload)
    if response.status_code != 201:
        print(f"Signup failed: {response.status_code}")
        print(f"Response: {response.text}")

    assert response.status_code == 201
    data = response.json()
    assert data["email"] == unique_email
    assert data["isActive"] is False  # Confirm user starts inactive

    # 2. Fetch the OTP from the Database (Simulating checking email)
    # We query the DB directly to get the code that was just generated
    user_in_db = await db.user.find_unique(where={"email": unique_email})
    assert user_in_db is not None
    otp_code = user_in_db.verificationCode
    assert otp_code is not None

    # 3. Verify Email
    verify_payload = {"email": unique_email, "code": otp_code}
    verify_response = await client.post("/api/v1/auth/verify-email", json=verify_payload)
    assert verify_response.status_code == 200
    assert verify_response.json()["message"] == "Email verified successfully"

    # 4. Login (Should now work because user is active)
    login_payload = {
        "username": unique_email,
        "password": password,
    }

    login_response = await client.post(
        "/api/v1/auth/login",
        data=login_payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )

    # Debugging print if it fails
    if login_response.status_code != 200:
        print(f"Login failed: {login_response.text}")

    assert login_response.status_code == 200
    token_data = login_response.json()
    assert "access_token" in token_data
    assert token_data["token_type"] == "bearer"


@pytest.mark.asyncio
async def test_login_invalid_credentials(client: AsyncClient):
    """Test that bad passwords are rejected."""
    payload = {"username": "fake@example.com", "password": "wrongpassword"}
    response = await client.post(
        "/api/v1/auth/login",
        data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_signup_duplicate_email(client: AsyncClient):
    """Test that you can't register the same email twice."""
    email = f"dup_{uuid4()}@example.com"
    payload = {"email": email, "password": "password123", "name": "Original User"}

    # First registration (Success)
    await client.post("/api/v1/auth/signup", json=payload)

    # Second registration (Should Fail)
    response = await client.post("/api/v1/auth/signup", json=payload)
    assert response.status_code == 400
