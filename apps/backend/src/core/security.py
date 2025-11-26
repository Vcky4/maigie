"""
Security utilities (JWT, password hashing, etc.).

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

import hashlib
from datetime import datetime, timedelta, timezone
from typing import Any

import bcrypt
from jose import JWTError, jwt
from passlib.context import CryptContext

from ..config import get_settings

# Password hashing context (used for verification of existing hashes)
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# bcrypt has a 72-byte limit for passwords
BCRYPT_MAX_PASSWORD_LENGTH = 72


def get_password_hash(password: str) -> str:
    """
    Hash a password using bcrypt directly.

    bcrypt has a 72-byte limit, so passwords longer than that are pre-hashed
    with SHA256 before being hashed with bcrypt.

    We use bcrypt directly instead of passlib to avoid initialization issues
    with passlib's bug detection mechanism.

    Args:
        password: Plain text password to hash

    Returns:
        Hashed password (bcrypt hash string)

    Raises:
        ValueError: If password hashing fails
    """
    password_bytes = password.encode("utf-8")

    # If password exceeds bcrypt's 72-byte limit, pre-hash with SHA256
    if len(password_bytes) > BCRYPT_MAX_PASSWORD_LENGTH:
        sha256_hash = hashlib.sha256(password_bytes).hexdigest()
        password_to_hash = sha256_hash.encode("utf-8")
    else:
        password_to_hash = password_bytes

    # Use bcrypt directly to avoid passlib initialization issues
    salt = bcrypt.gensalt()
    hashed = bcrypt.hashpw(password_to_hash, salt)
    return hashed.decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """
    Verify a password against a hash.

    If the password exceeds bcrypt's 72-byte limit, it will be pre-hashed
    with SHA256 before verification (matching how it was hashed).

    Args:
        plain_password: Plain text password
        hashed_password: Hashed password to verify against

    Returns:
        True if password matches, False otherwise
    """
    password_bytes = plain_password.encode("utf-8")
    hashed_bytes = hashed_password.encode("utf-8")

    # If password exceeds bcrypt's 72-byte limit, pre-hash with SHA256
    if len(password_bytes) > BCRYPT_MAX_PASSWORD_LENGTH:
        sha256_hash = hashlib.sha256(password_bytes).hexdigest()
        password_to_verify = sha256_hash.encode("utf-8")
    else:
        password_to_verify = password_bytes

    # Use bcrypt directly to verify
    try:
        return bcrypt.checkpw(password_to_verify, hashed_bytes)
    except (ValueError, TypeError):
        # Fallback to passlib for compatibility with old hashes
        # This handles edge cases and different hash formats
        if len(password_bytes) > BCRYPT_MAX_PASSWORD_LENGTH:
            sha256_hash = hashlib.sha256(password_bytes).hexdigest()
            return pwd_context.verify(sha256_hash, hashed_password)
        return pwd_context.verify(plain_password, hashed_password)


def create_access_token(data: dict[str, Any], expires_delta: timedelta | None = None) -> str:
    """
    Create a JWT access token.

    Args:
        data: Data to encode in the token
        expires_delta: Optional expiration time delta

    Returns:
        Encoded JWT token
    """
    settings = get_settings()
    to_encode = data.copy()

    if expires_delta:
        expire = datetime.now(datetime.UTC) + expires_delta
    else:
        expire = datetime.now(datetime.UTC) + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)

    to_encode.update({"exp": expire, "type": "access"})
    encoded_jwt = jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
    return encoded_jwt


def create_refresh_token(data: dict[str, Any]) -> str:
    """
    Create a JWT refresh token.

    Args:
        data: Data to encode in the token

    Returns:
        Encoded JWT refresh token
    """
    settings = get_settings()
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)

    to_encode.update({"exp": expire, "type": "refresh"})
    encoded_jwt = jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
    return encoded_jwt


def decode_access_token(token: str) -> dict[str, Any]:
    """
    Decode and verify a JWT access token.

    Args:
        token: JWT token to decode

    Returns:
        Decoded token payload

    Raises:
        JWTError: If token is invalid or expired
    """
    settings = get_settings()
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        # Verify token type
        if payload.get("type") != "access":
            raise JWTError("Invalid token type")
        return payload
    except JWTError as e:
        raise JWTError(f"Invalid token: {e}")


def decode_refresh_token(token: str) -> dict[str, Any]:
    """
    Decode and verify a JWT refresh token.

    Args:
        token: JWT refresh token to decode

    Returns:
        Decoded token payload

    Raises:
        JWTError: If token is invalid or expired
    """
    settings = get_settings()
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        # Verify token type
        if payload.get("type") != "refresh":
            raise JWTError("Invalid token type")
        return payload
    except JWTError as e:
        raise JWTError(f"Invalid refresh token: {e}")
