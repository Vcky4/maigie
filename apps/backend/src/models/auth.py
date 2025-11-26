"""
Authentication routes.

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

"""
Authentication routes (JWT Signup/Login + OAuth Placeholders).

Copyright (C) 2024 Maigie Team
"""

"""
Authentication models (Pydantic schemas).
"""
from pydantic import BaseModel, EmailStr, Field, ConfigDict
from typing import Optional

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


class UserResponse(BaseModel):
    """User response schema."""

    id: str
    email: EmailStr
    name: str | None = None
    tier: str
    isActive: bool
    preferences: UserPreferencesResponse | None = None

    model_config = ConfigDict(from_attributes=True)
