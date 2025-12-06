"""
Authentication models (Pydantic schemas).

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

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_serializer, field_validator

# --- Token Schemas ---


class Token(BaseModel):
    """Token response schema."""

    access_token: str
    token_type: str = "bearer"


class TokenData(BaseModel):
    """Schema for data embedded in the token."""

    email: str | None = None  # <--- THIS WAS MISSING


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

    model_config = ConfigDict(from_attributes=True)


class UserResponse(BaseModel):
    """User response schema."""

    id: str
    email: EmailStr
    name: str | None = None
    tier: str
    isActive: bool  # noqa: N815
    preferences: UserPreferencesResponse | None = None

    model_config = ConfigDict(from_attributes=True)

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
