"""Security utilities (JWT, password hashing, etc.)."""

import hashlib
from datetime import datetime, timedelta
from typing import Any

from jose import JWTError, jwt
from passlib.context import CryptContext

from ..config import get_settings

# Password hashing context
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# bcrypt has a 72-byte limit for passwords
BCRYPT_MAX_PASSWORD_LENGTH = 72

# Flag to track if backend is initialized
_backend_initialized = False


def _ensure_backend_initialized():
    """Ensure bcrypt backend is initialized with a short password."""
    global _backend_initialized
    if not _backend_initialized:
        try:
            # Initialize with a short password to avoid detection issues
            _ = pwd_context.hash("init")
            _backend_initialized = True
        except (ValueError, AttributeError):
            # Backend initialization may fail during detection phase
            # It will be retried on first actual use
            pass


def get_password_hash(password: str) -> str:
    """
    Hash a password.
    
    bcrypt has a 72-byte limit, so passwords longer than that are pre-hashed
    with SHA256 before being hashed with bcrypt.
    
    Args:
        password: Plain text password to hash
        
    Returns:
        Hashed password
        
    Raises:
        ValueError: If password hashing fails
    """
    # Ensure backend is initialized
    _ensure_backend_initialized()
    
    password_bytes = password.encode('utf-8')
    
    # If password exceeds bcrypt's 72-byte limit, pre-hash with SHA256
    if len(password_bytes) > BCRYPT_MAX_PASSWORD_LENGTH:
        sha256_hash = hashlib.sha256(password_bytes).hexdigest()
        return pwd_context.hash(sha256_hash)
    
    return pwd_context.hash(password)


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
    password_bytes = plain_password.encode('utf-8')
    
    # If password exceeds bcrypt's 72-byte limit, pre-hash with SHA256
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
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(
            minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES
        )

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
        payload = jwt.decode(
            token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM]
        )
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
        payload = jwt.decode(
            token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM]
        )
        # Verify token type
        if payload.get("type") != "refresh":
            raise JWTError("Invalid token type")
        return payload
    except JWTError as e:
        raise JWTError(f"Invalid refresh token: {e}")

