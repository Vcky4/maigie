"""
Dependency injection system.

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer  # <--- CHANGED
from jose import JWTError

from prisma import Prisma
from prisma.models import User

from .config import Settings, get_settings
from .core.database import db
from .core.security import decode_access_token
from .models.auth import TokenData

# Common dependencies
SettingsDep = Annotated[Settings, Depends(get_settings)]


def get_settings_dependency() -> Settings:
    """
    Get settings dependency (for verification/testing).
    This is a convenience function for non-FastAPI contexts.
    """
    return get_settings()


# Database dependency
async def get_db() -> Prisma:
    """Get database client dependency."""
    return db


DBDep = Annotated[Prisma, Depends(get_db)]

# --- CHANGED: Use HTTPBearer ---
# This tells Swagger UI: "Just ask for a Bearer Token string"
security = HTTPBearer()


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(security)],
) -> User:
    """
    Validate JWT and retrieve the current user from the database.
    This is the main dependency for protecting routes.
    """
    # Extract the token string from the Bearer object
    token = credentials.credentials

    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    try:
        # 1. Decode the token using your security util
        payload = decode_access_token(token)

        # 2. Extract the subject (email)
        email: str = payload.get("sub")
        if email is None:
            raise credentials_exception

        token_data = TokenData(email=email)

    except JWTError:
        raise credentials_exception

    # 3. Fetch User from Database
    # We include preferences so we don't need a second query later
    user = await db.user.find_unique(
        where={"email": token_data.email}, include={"preferences": True}
    )

    if user is None:
        raise credentials_exception

    # Optional: Check if user is active
    if not user.isActive:
        raise HTTPException(status_code=400, detail="Inactive user")

    return user


# Create a reusable type shortcut
CurrentUser = Annotated[User, Depends(get_current_user)]


async def get_admin_user(current_user: CurrentUser) -> User:
    """
    Dependency to ensure the current user is an admin.
    Must be used after get_current_user dependency.
    """
    if current_user.role != "ADMIN":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    return current_user


# Create a reusable type shortcut for admin users
AdminUser = Annotated[User, Depends(get_admin_user)]


async def require_premium(current_user: CurrentUser) -> User:
    """
    Dependency to ensure the current user has Premium (Plus) subscription.
    Smart AI Tutor, Exam Prep, and 11labs voice require Premium.
    """
    if str(current_user.tier) not in ("PREMIUM_MONTHLY", "PREMIUM_YEARLY"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This feature requires Maigie Plus. Upgrade to unlock Smart AI Tutor and Exam Prep.",
        )
    return current_user


PremiumUser = Annotated[User, Depends(require_premium)]
