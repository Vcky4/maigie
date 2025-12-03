import pytest
from httpx import AsyncClient
from uuid import uuid4
from src.core.database import db
from src.core.security import get_password_hash


@pytest.mark.asyncio
async def test_password_reset_flow(client: AsyncClient):
    """
    Test the full password reset flow:
    1. Create verified user
    2. Request reset code
    3. Verify code
    4. Reset password
    5. Login with NEW password
    6. Login with OLD password (should fail)
    """
    # Setup: Create a user who is already active
    email = f"reset_{uuid4()}@example.com"
    old_password = "OldPassword123!"
    new_password = "NewStrongPassword99!"

    await db.user.create(
        data={
            "email": email,
            "name": "Reset Tester",
            "passwordHash": get_password_hash(old_password),
            "provider": "email",
            "isActive": True,  # User must be active to use the system
            "preferences": {"create": {}},
        }
    )

    # 1. Request Password Reset (Forgot Password)
    response = await client.post("/api/v1/auth/forgot-password", json={"email": email})
    assert response.status_code == 200
    assert response.json()["message"] == "If an account exists, a reset code has been sent."

    # 2. Fetch the OTP from Database (Simulate checking email)
    user = await db.user.find_unique(where={"email": email})
    reset_code = user.passwordResetCode
    assert reset_code is not None
    assert len(reset_code) == 6

    # 3. Verify Code (Frontend Step)
    verify_res = await client.post(
        "/api/v1/auth/verify-reset-code", json={"email": email, "code": reset_code}
    )
    assert verify_res.status_code == 200
    assert verify_res.json()["message"] == "Code is valid"

    # 4. Reset Password
    reset_res = await client.post(
        "/api/v1/auth/reset-password",
        json={"email": email, "code": reset_code, "new_password": new_password},
    )
    assert reset_res.status_code == 200
    assert "successfully" in reset_res.json()["message"]

    # 5. Login with NEW password (Should Success)
    login_new = await client.post(
        "/api/v1/auth/login",
        data={"username": email, "password": new_password},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert login_new.status_code == 200
    assert "access_token" in login_new.json()

    # 6. Login with OLD password (Should Fail)
    login_old = await client.post(
        "/api/v1/auth/login",
        data={"username": email, "password": old_password},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert login_old.status_code == 401
