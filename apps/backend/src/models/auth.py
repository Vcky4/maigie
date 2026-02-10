"""
Authentication models (Pydantic schemas).

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_serializer, field_validator

# --- Token Schemas ---


class Token(BaseModel):
    """Token response schema."""

    access_token: str
    token_type: str = "bearer"
    refresh_token: str | None = None


class RefreshTokenRequest(BaseModel):
    """Request schema for token refresh."""

    refresh_token: str


class TokenData(BaseModel):
    """Schema for data embedded in the token."""

    email: str | None = None  # <--- THIS WAS MISSING


# --- Request Models (Input) ---


class UserSignup(BaseModel):
    """User registration schema."""

    email: EmailStr
    password: str = Field(min_length=8, description="Password must be at least 8 characters")
    name: str = Field(..., description="Full Name")
    referralCode: str | None = Field(None, description="Optional referral code from URL parameter")


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
    study_goals: dict | None = Field(
        None, validation_alias="studyGoals", serialization_alias="studyGoals"
    )

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class UserResponse(BaseModel):
    """User response schema."""

    id: str
    email: EmailStr
    name: str | None = None
    tier: str
    role: str
    isActive: bool  # noqa: N815
    isOnboarded: bool = False  # noqa: N815
    preferences: UserPreferencesResponse | None = None

    model_config = ConfigDict(from_attributes=True)

    @field_serializer("role")
    def serialize_role(self, v, _info):
        """Serialize UserRole enum to string."""
        if v is None:
            return "USER"
        if isinstance(v, str):
            return v
        # Handle Prisma enum objects - they might have .value or be directly string-like
        if hasattr(v, "value"):
            return str(v.value)
        if hasattr(v, "name"):
            return str(v.name)
        # Fallback: convert to string
        return str(v)

    @field_serializer("tier")
    def serialize_tier(self, v, _info):
        """Serialize Tier enum to string."""
        if v is None:
            return "FREE"
        if isinstance(v, str):
            return v
        # Handle Prisma enum objects - they might have .value or be directly string-like
        if hasattr(v, "value"):
            return str(v.value)
        if hasattr(v, "name"):
            return str(v.name)
        # Fallback: convert to string
        return str(v)


class OAuthAuthorizeResponse(BaseModel):
    """OAuth authorization URL response schema."""

    authorization_url: str
    state: str
    provider: str

    model_config = ConfigDict(from_attributes=True)
