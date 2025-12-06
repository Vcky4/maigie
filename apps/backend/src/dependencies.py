"""
Dependency injection system.

Copyright (C) 2025 Maigie

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU Affero General Public License as published
by the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU Affero General Public License for more details.

You should have received a copy of the GNU Affero General Public License
along with this program.  If not, see <https://www.gnu.org/licenses/>.
"""

from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials  # <--- CHANGED
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
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(security)]
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
