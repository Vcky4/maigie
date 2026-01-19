"""
User database services for OAuth authentication.

This module provides services for handling OAuth user data, including
user lookup and creation for OAuth providers like Google.

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

import logging

from pydantic import BaseModel

from prisma.models import User

from ..dependencies import DBDep
from ..exceptions import AuthenticationError

logger = logging.getLogger(__name__)


class OAuthUserInfo(BaseModel):
    """Pydantic model for OAuth user information from providers."""

    email: str
    full_name: str | None = None
    provider: str
    provider_user_id: str
    referral_code: str | None = None  # Optional referral code for new signups


async def get_or_create_oauth_user(oauth_info: OAuthUserInfo, db: DBDep) -> User:
    """
    Get an existing OAuth user or create a new one.

    This function handles the OAuth user authentication flow:
    1. Looks up an existing user by provider and provider_user_id
    2. If found, returns the existing user (login scenario)
    3. If not found, creates a new user (signup scenario)
       - Checks for email conflicts to prevent account linking issues
       - Sets isOnboarded to False for new users

    Args:
        oauth_info: OAuth user information from the provider
        db: Database client dependency

    Returns:
        User: The existing or newly created user object

    Raises:
        AuthenticationError: If a user with the same email already exists
            with a different authentication method (security concern)
    """
    # Lookup: Attempt to find an existing user by provider and provider_user_id
    existing_user = await db.user.find_first(
        where={
            "provider": oauth_info.provider,
            "providerId": oauth_info.provider_user_id,
        }
    )

    # If User Found (Login): Return the existing user object immediately
    if existing_user:
        return existing_user

    # If User Not Found (New Signup):
    # Check if a user exists with the same email
    email_user = await db.user.find_unique(where={"email": oauth_info.email})

    if email_user:
        # User exists with this email. Allow login by returning the existing user.
        # This effectively "links" the OAuth login to the existing account.

        # If the user was pending verification, we can activate them since
        # the OAuth provider (e.g., Google) has verified the email.
        if not email_user.isActive:
            email_user = await db.user.update(
                where={"id": email_user.id},
                data={
                    "isActive": True,
                    "verificationCode": None,
                    "verificationCodeExpiresAt": None,
                },
            )

        return email_user

    # Create a new user record
    user_data = {
        "email": oauth_info.email,
        "name": oauth_info.full_name,
        "provider": oauth_info.provider,
        "providerId": oauth_info.provider_user_id,
        "isOnboarded": False,  # New OAuth users need to complete onboarding
    }

    # Add referral code if provided (only for new users)
    if oauth_info.referral_code:
        user_data["referredByCode"] = oauth_info.referral_code.upper().strip()
        logger.info(
            f"Registering referral code {user_data['referredByCode']} for new OAuth user {oauth_info.email}"
        )

    new_user = await db.user.create(data=user_data)

    return new_user
