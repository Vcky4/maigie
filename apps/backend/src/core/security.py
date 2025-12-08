"""
Security utilities (JWT, password hashing, etc.).

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

import hashlib
import base64
import bcrypt
from datetime import datetime, timedelta, timezone
from typing import Any, Union
from jose import jwt, JWTError
from passlib.context import CryptContext
from src.config import settings
import secrets
import string

# --- FIX START: Monkey patch for bcrypt 4.1.0+ compatibility ---
# Passlib relies on an __about__ attribute that was removed in newer bcrypt versions.
# This injects it back to prevent the AttributeError in your logs.
if not hasattr(bcrypt, "__about__"):
    bcrypt.__about__ = type("about", (object,), {"__version__": bcrypt.__version__})
# --- FIX END ---

# Setup Password Hashing (Bcrypt)
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def _get_safe_password(password: str) -> str:
    """
    Internal helper: Pre-hashes the password using SHA-256 to ensure it
    never exceeds the bcrypt 72-byte limit.
    """
    if not password:
        raise ValueError("Password cannot be empty")

    # 1. Hash with SHA-256 (produces 32 bytes)
    password_bytes = password.encode("utf-8")
    digest = hashlib.sha256(password_bytes).digest()

    # 2. Encode to Base64 (produces ~44 characters, well within 72 limit)
    return base64.b64encode(digest).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Check if the plain password matches the hash."""
    # We must pre-hash the plain password exactly the same way before verifying
    safe_password = _get_safe_password(plain_password)
    return pwd_context.verify(safe_password, hashed_password)


def get_password_hash(password: str) -> str:
    """Hash a password before saving to the database."""
    # Pre-hash to ensure safety/length compliance
    safe_password = _get_safe_password(password)
    return pwd_context.hash(safe_password)


def create_access_token(data: dict[str, Any], expires_delta: timedelta | None = None) -> str:
    """
    Generate a JWT access token.
    """
    to_encode = data.copy()

    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(
            minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES
        )

    # Standard JWT claims
    to_encode.update({"exp": expire, "type": "access"})
    encoded_jwt = jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
    return encoded_jwt


def create_verification_token(email: str) -> str:
    """
    Generate a short-lived token for email verification.
    """
    expire = datetime.now(timezone.utc) + timedelta(hours=24)
    # We use the same secret key but a specific 'type' claim to differentiate it from login tokens
    to_encode = {"exp": expire, "sub": email, "type": "verification"}
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def decode_access_token(token: str) -> dict[str, Any]:
    """
    Decode and verify a JWT token.
    """
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        return payload
    except JWTError as e:
        raise e


def generate_otp(length: int = 6) -> str:
    """Generate a random numeric OTP."""
    return "".join(secrets.choice(string.digits) for _ in range(length))
