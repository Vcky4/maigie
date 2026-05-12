"""
OAuth provider base structure.

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

import asyncio
import logging
from typing import Any, Protocol

import google.auth.exceptions
import httpx
from authlib.integrations.httpx_client import AsyncOAuth2Client
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token

from ..config import get_settings

logger = logging.getLogger(__name__)

# Default httpx connect timeout (~5s) is tight for cloud → Google from some regions; OAuth is rare enough to wait longer.
_OAUTH_HTTP_TIMEOUT = httpx.Timeout(60.0, connect=20.0)


class GoogleIdTokenVerifier:
    """Verifies Google ID tokens issued by the native mobile SDK."""

    def __init__(self, client_id: str):
        """
        Initialize the verifier with the expected audience (client ID).

        Args:
            client_id: The Google OAuth 2.0 Web Client ID (expected audience)
        """
        self._client_id = client_id
        self._request = google_requests.Request()

    async def verify(self, token: str) -> dict[str, Any]:
        """
        Verify a Google ID token.

        Returns the decoded token claims on success.

        Args:
            token: The Google ID token JWT string

        Returns:
            dict containing the verified token claims (sub, email, name, etc.)

        Raises:
            ValueError: If the token is not a valid JWT format
            google.auth.exceptions.GoogleAuthError: If signature is invalid, token is expired,
                or audience does not match
            Network errors: If Google's servers are unreachable (httpx.ConnectError, etc.)
        """
        # google.oauth2.id_token.verify_oauth2_token is synchronous;
        # run in a thread pool to avoid blocking the event loop.
        loop = asyncio.get_event_loop()
        claims = await loop.run_in_executor(
            None,
            lambda: google_id_token.verify_oauth2_token(token, self._request, self._client_id),
        )
        return claims


class OAuthProvider(Protocol):
    """Protocol for OAuth providers."""

    name: str
    client_id: str
    client_secret: str
    authorize_url: str
    access_token_url: str
    user_info_url: str

    async def get_authorization_url(self, redirect_uri: str, state: str) -> str:
        """Get OAuth authorization URL."""
        ...

    async def get_access_token(self, code: str, redirect_uri: str) -> dict[str, Any]:
        """Exchange authorization code for access token."""
        ...

    async def get_user_info(self, access_token: str) -> dict[str, Any]:
        """Get user information from provider."""
        ...


class GoogleOAuthProvider:
    """Google OAuth provider."""

    name = "google"
    authorize_url = "https://accounts.google.com/o/oauth2/v2/auth"
    access_token_url = "https://oauth2.googleapis.com/token"
    user_info_url = "https://www.googleapis.com/oauth2/v2/userinfo"

    def __init__(self):
        settings = get_settings()
        self.client_id = settings.OAUTH_GOOGLE_CLIENT_ID
        self.client_secret = settings.OAUTH_GOOGLE_CLIENT_SECRET

    async def get_authorization_url(
        self, redirect_uri: str, state: str, include_calendar: bool = False
    ) -> str:
        """
        Get Google OAuth authorization URL.

        Args:
            redirect_uri: OAuth redirect URI
            state: OAuth state parameter
            include_calendar: If True, include Google Calendar scopes
        """
        client = AsyncOAuth2Client(
            client_id=self.client_id,
            client_secret=self.client_secret,
            redirect_uri=redirect_uri,
        )

        # Base scopes for authentication
        scope = "openid email profile"

        # Add Calendar scopes if requested
        if include_calendar:
            # Using minimal required scopes for calendar integration:
            # - calendar.app.created: Create Maigie calendar and ONLY manage events in calendars we create
            #   (more restrictive than calendar.events - doesn't access user's existing calendars)
            # - calendar.freebusy: Check availability across all user's calendars
            scope += (
                " https://www.googleapis.com/auth/calendar.app.created"
                " https://www.googleapis.com/auth/calendar.freebusy"
            )

        # create_authorization_url is synchronous and returns a tuple (url, state)
        authorization_url, _ = client.create_authorization_url(
            self.authorize_url,
            state=state,
            scope=scope,
            access_type="offline",  # Required to get refresh token
            prompt="consent",  # Force consent screen to get refresh token
        )
        return authorization_url

    async def get_access_token(self, code: str, redirect_uri: str) -> dict[str, Any]:
        """Exchange authorization code for access token."""
        # For Google OAuth, use manual token request to avoid authlib parsing issues
        # Google's token endpoint works fine, but authlib sometimes fails to parse the response
        async with httpx.AsyncClient() as http_client:
            token_data = {
                "code": code,
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            }

            logger.info(
                "Google OAuth token request",
                extra={
                    "redirect_uri": redirect_uri,
                    "has_code": bool(code),
                    "client_id": self.client_id[:10] + "..." if self.client_id else None,
                },
            )

            response = await http_client.post(
                self.access_token_url,
                data=token_data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )

            # Log error details before raising exception
            if response.status_code != 200:
                try:
                    error_response = response.json()
                    logger.error(
                        "Google OAuth token error",
                        extra={
                            "error": error_response,
                            "redirect_uri": redirect_uri,
                            "status_code": response.status_code,
                        },
                    )
                except Exception:
                    error_text = response.text
                    logger.error(
                        "Google OAuth token error (non-JSON)",
                        extra={
                            "error": error_text,
                            "redirect_uri": redirect_uri,
                            "status_code": response.status_code,
                        },
                    )

            response.raise_for_status()
            token_response = response.json()

            if "access_token" not in token_response:
                raise ValueError(
                    f"Token response missing access_token. Response keys: {list(token_response.keys())}"
                )
            return token_response

    async def get_user_info(self, access_token: str) -> dict[str, Any]:
        """Get user information from Google."""
        async with httpx.AsyncClient(timeout=_OAUTH_HTTP_TIMEOUT) as client:
            resp = await client.get(
                self.user_info_url,
                headers={"Authorization": f"Bearer {access_token}"},
            )
            resp.raise_for_status()
            return resp.json()


# TODO: Enable GitHub OAuth provider in the future
# class GitHubOAuthProvider:
#     """GitHub OAuth provider."""
#
#     name = "github"
#     authorize_url = "https://github.com/login/oauth/authorize"
#     access_token_url = "https://github.com/login/oauth/access_token"
#     user_info_url = "https://api.github.com/user"
#
#     def __init__(self):
#         settings = get_settings()
#         self.client_id = settings.OAUTH_GITHUB_CLIENT_ID
#         self.client_secret = settings.OAUTH_GITHUB_CLIENT_SECRET
#
#     async def get_authorization_url(self, redirect_uri: str, state: str) -> str:
#         """Get GitHub OAuth authorization URL."""
#         client = AsyncOAuth2Client(
#             client_id=self.client_id,
#             client_secret=self.client_secret,
#             redirect_uri=redirect_uri,
#         )
#         # create_authorization_url is synchronous and returns a tuple (url, state)
#         authorization_url, _ = client.create_authorization_url(
#             self.authorize_url,
#             state=state,
#             scope="user:email",
#         )
#         return authorization_url
#
#     async def get_access_token(self, code: str, redirect_uri: str) -> dict[str, Any]:
#         """Exchange authorization code for access token."""
#         client = AsyncOAuth2Client(
#             client_id=self.client_id,
#             client_secret=self.client_secret,
#         )
#         # Use context manager to ensure proper cleanup
#         async with client:
#             # redirect_uri must be passed to fetch_token to match the authorization request
#             token = await client.fetch_token(
#                 self.access_token_url,
#                 code=code,
#                 redirect_uri=redirect_uri,
#             )
#         return token
#
#     async def get_user_info(self, access_token: str) -> dict[str, Any]:
#         """Get user information from GitHub."""
#         client = AsyncOAuth2Client(
#             client_id=self.client_id,
#             client_secret=self.client_secret,
#         )
#         async with client:
#             resp = await client.get(
#                 self.user_info_url,
#                 headers={"Authorization": f"Bearer {access_token}"},
#             )
#             resp.raise_for_status()
#             user_data = resp.json()
#
#             # Get email if not in user data
#             if "email" not in user_data or not user_data["email"]:
#                 email_resp = await client.get(
#                     "https://api.github.com/user/emails",
#                     headers={"Authorization": f"Bearer {access_token}"},
#                 )
#                 email_resp.raise_for_status()
#                 emails = email_resp.json()
#                 if emails:
#                     user_data["email"] = emails[0].get("email", "")
#
#             return user_data


class OAuthProviderFactory:
    """Factory for creating OAuth providers."""

    _providers: dict[str, type[OAuthProvider]] = {
        "google": GoogleOAuthProvider,
        # "github": GitHubOAuthProvider,  # TODO: Enable GitHub OAuth provider in the future
    }

    @classmethod
    def get_provider(cls, provider_name: str) -> OAuthProvider:
        """
        Get OAuth provider by name.

        Args:
            provider_name: Name of the provider (google)

        Returns:
            OAuth provider instance

        Raises:
            ValueError: If provider is not supported
        """
        provider_class = cls._providers.get(provider_name.lower())
        if not provider_class:
            raise ValueError(f"Unsupported OAuth provider: {provider_name}")
        return provider_class()

    @classmethod
    def get_available_providers(cls) -> list[str]:
        """Get list of available OAuth providers."""
        return list(cls._providers.keys())
