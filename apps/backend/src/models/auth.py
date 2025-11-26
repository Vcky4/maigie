"""
Authentication models (Pydantic schemas).

Copyright (C) 2024 Maigie Team
"""

from typing import Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field

# --- Token Schemas ---


class Token(BaseModel):
    """Token response schema."""

    access_token: str
    token_type: str = "bearer"


class TokenData(BaseModel):
    """Schema for data embedded in the token."""

    email: str | None = None


# --- Request Models (Input) ---


class UserSignup(BaseModel):
    """User registration schema."""

    email: EmailStr
    password: str = Field(min_length=8, description="Password must be at least 8 characters")
    name: str = Field(..., description="Full Name")


class UserLogin(BaseModel):
    """User login schema."""

    email: EmailStr
    password: str


# --- Response Models (Output) ---


class UserPreferencesResponse(BaseModel):
    """Schema for user preferences."""

    theme: str
    language: str
    notifications: bool


class UserResponse(BaseModel):
    """User response schema."""

    id: str
    email: EmailStr
    name: str | None = None
    tier: str
    isActive: bool
    preferences: UserPreferencesResponse | None = None

    model_config = ConfigDict(from_attributes=True)
