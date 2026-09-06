"""
JWT token creation and validation.

Handles access tokens, refresh tokens, and verification tokens.
Password hashing utilities are co-located here since they are
part of the authentication concern.
"""

import base64
import hashlib
import secrets
import string
from datetime import UTC, datetime, timedelta
from typing import Any

import bcrypt
from jose import JWTError, jwt
from passlib.context import CryptContext

from src.config import get_settings

# --- Bcrypt 4.1.0+ compatibility patch ---
# Passlib relies on bcrypt.__about__ which was removed in newer versions.
if not hasattr(bcrypt, "__about__"):
    bcrypt.__about__ = type("about", (object,), {"__version__": bcrypt.__version__})

# Password hashing context
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


# ---------------------------------------------------------------------------
# Password Utilities
# ---------------------------------------------------------------------------


def _get_safe_password(password: str) -> str:
    """Pre-hash password with SHA-256 to stay within bcrypt's 72-byte limit."""
    if not password:
        raise ValueError("Password cannot be empty")
    digest = hashlib.sha256(password.encode("utf-8")).digest()
    return base64.b64encode(digest).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a plain password against a stored hash."""
    safe_password = _get_safe_password(plain_password)
    return pwd_context.verify(safe_password, hashed_password)


def get_password_hash(password: str) -> str:
    """Hash a password for storage."""
    safe_password = _get_safe_password(password)
    return pwd_context.hash(safe_password)


# ---------------------------------------------------------------------------
# Token Creation
# ---------------------------------------------------------------------------


def create_access_token(data: dict[str, Any], expires_delta: timedelta | None = None) -> str:
    """Generate a JWT access token.

    Args:
        data: Claims to encode (must include "sub" with user email).
        expires_delta: Optional custom expiration. Defaults to settings value.

    Returns:
        Encoded JWT string.
    """
    settings = get_settings()
    to_encode = data.copy()

    expire = datetime.now(UTC) + (
        expires_delta or timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    to_encode.update({"exp": expire, "type": "access"})

    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def create_refresh_token(data: dict[str, Any]) -> str:
    """Generate a JWT refresh token (longer-lived)."""
    settings = get_settings()
    to_encode = data.copy()

    expire = datetime.now(UTC) + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
    to_encode.update({"exp": expire, "type": "refresh"})

    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def create_verification_token(email: str) -> str:
    """Generate a short-lived token for email verification."""
    settings = get_settings()
    expire = datetime.now(UTC) + timedelta(hours=24)
    to_encode = {"exp": expire, "sub": email, "type": "verification"}
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


# ---------------------------------------------------------------------------
# Token Validation
# ---------------------------------------------------------------------------


def decode_access_token(token: str) -> dict[str, Any]:
    """Decode and verify a JWT token.

    Args:
        token: The JWT string to decode.

    Returns:
        Decoded payload dictionary.

    Raises:
        JWTError: If the token is invalid or expired.
    """
    settings = get_settings()
    return jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])


# ---------------------------------------------------------------------------
# OTP
# ---------------------------------------------------------------------------


def generate_otp(length: int = 6) -> str:
    """Generate a cryptographically random numeric OTP."""
    return "".join(secrets.choice(string.digits) for _ in range(length))
