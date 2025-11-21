"""Authentication models (Pydantic schemas)."""

from typing import Optional

from pydantic import BaseModel, EmailStr


class UserRegister(BaseModel):
    """User registration schema."""

    email: EmailStr
    password: str
    full_name: Optional[str] = None


class UserLogin(BaseModel):
    """User login schema."""

    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    """Token response schema."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


class RefreshTokenRequest(BaseModel):
    """Refresh token request schema."""

    refresh_token: str


class UserResponse(BaseModel):
    """User response schema."""

    id: str
    email: str
    full_name: Optional[str] = None

