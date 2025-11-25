"""
Dependency injection system.

Copyright (C) 2024 Maigie Team

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

from typing import Annotated, Any

from fastapi import Depends, Header, HTTPException, status
from jose import JWTError

from .config import Settings, get_settings
from .core.security import decode_access_token

# Common dependencies
SettingsDep = Annotated[Settings, Depends(lambda: get_settings())]


async def verify_api_key(
    x_api_key: Annotated[str | None, Header()] = None,
    settings: SettingsDep = None,
) -> bool:
    """
    Verify API key header (placeholder for future implementation).

    For now, this is a placeholder that always returns True.
    In production, implement proper API key validation.
    """
    # TODO: Implement API key validation
    return True


async def get_current_user_token(
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    """
    Extract and verify JWT token from Authorization header.

    Args:
        authorization: Authorization header value (Bearer <token>)

    Returns:
        Decoded token payload

    Raises:
        HTTPException: If token is missing or invalid
    """
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization header missing",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        scheme, token = authorization.split()
        if scheme.lower() != "bearer":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid authentication scheme",
                headers={"WWW-Authenticate": "Bearer"},
            )
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authorization header format",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        payload = decode_access_token(token)
        return payload
    except JWTError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid token: {str(e)}",
            headers={"WWW-Authenticate": "Bearer"},
        )


async def get_current_user_id(
    token: Annotated[dict[str, Any], Depends(get_current_user_token)],
) -> str:
    """
    Get current user ID from verified JWT token.

    Args:
        token: Decoded JWT token payload

    Returns:
        User ID from token
    """
    user_id = token.get("sub") or token.get("user_id")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token missing user identifier",
        )
    return str(user_id)


async def get_current_user_email(
    token: Annotated[dict[str, Any], Depends(get_current_user_token)],
) -> str:
    """
    Get current user email from verified JWT token.

    Args:
        token: Decoded JWT token payload

    Returns:
        User email from token
    """
    email = token.get("email")
    if not email:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token missing email",
        )
    return str(email)


# Dependencies for authenticated endpoints
CurrentUserTokenDep = Annotated[dict[str, Any], Depends(get_current_user_token)]
CurrentUserIdDep = Annotated[str, Depends(get_current_user_id)]
CurrentUserEmailDep = Annotated[str, Depends(get_current_user_email)]
