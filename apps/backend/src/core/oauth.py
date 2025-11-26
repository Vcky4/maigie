"""
OAuth provider base structure.

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

from typing import Any, Protocol

from authlib.integrations.httpx_client import AsyncOAuth2Client

from ..config import get_settings


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

    async def get_authorization_url(self, redirect_uri: str, state: str) -> str:
        """Get Google OAuth authorization URL."""
        client = AsyncOAuth2Client(
            client_id=self.client_id,
            client_secret=self.client_secret,
            redirect_uri=redirect_uri,
        )
        authorization_url, _ = await client.create_authorization_url(
            self.authorize_url,
            state=state,
            scope="openid email profile",
        )
        return authorization_url

    async def get_access_token(self, code: str, redirect_uri: str) -> dict[str, Any]:
        """Exchange authorization code for access token."""
        client = AsyncOAuth2Client(
            client_id=self.client_id,
            client_secret=self.client_secret,
            redirect_uri=redirect_uri,
        )
        token = await client.fetch_token(
            self.access_token_url,
            code=code,
        )
        return token

    async def get_user_info(self, access_token: str) -> dict[str, Any]:
        """Get user information from Google."""
        client = AsyncOAuth2Client(
            client_id=self.client_id,
            client_secret=self.client_secret,
        )
        async with client:
            resp = await client.get(
                self.user_info_url,
                headers={"Authorization": f"Bearer {access_token}"},
            )
            resp.raise_for_status()
            return resp.json()


class GitHubOAuthProvider:
    """GitHub OAuth provider."""

    name = "github"
    authorize_url = "https://github.com/login/oauth/authorize"
    access_token_url = "https://github.com/login/oauth/access_token"
    user_info_url = "https://api.github.com/user"

    def __init__(self):
        settings = get_settings()
        self.client_id = settings.OAUTH_GITHUB_CLIENT_ID
        self.client_secret = settings.OAUTH_GITHUB_CLIENT_SECRET

    async def get_authorization_url(self, redirect_uri: str, state: str) -> str:
        """Get GitHub OAuth authorization URL."""
        client = AsyncOAuth2Client(
            client_id=self.client_id,
            client_secret=self.client_secret,
            redirect_uri=redirect_uri,
        )
        authorization_url, _ = await client.create_authorization_url(
            self.authorize_url,
            state=state,
            scope="user:email",
        )
        return authorization_url

    async def get_access_token(self, code: str, redirect_uri: str) -> dict[str, Any]:
        """Exchange authorization code for access token."""
        client = AsyncOAuth2Client(
            client_id=self.client_id,
            client_secret=self.client_secret,
            redirect_uri=redirect_uri,
        )
        token = await client.fetch_token(
            self.access_token_url,
            code=code,
        )
        return token

    async def get_user_info(self, access_token: str) -> dict[str, Any]:
        """Get user information from GitHub."""
        client = AsyncOAuth2Client(
            client_id=self.client_id,
            client_secret=self.client_secret,
        )
        async with client:
            resp = await client.get(
                self.user_info_url,
                headers={"Authorization": f"Bearer {access_token}"},
            )
            resp.raise_for_status()
            user_data = resp.json()

            # Get email if not in user data
            if "email" not in user_data or not user_data["email"]:
                email_resp = await client.get(
                    "https://api.github.com/user/emails",
                    headers={"Authorization": f"Bearer {access_token}"},
                )
                email_resp.raise_for_status()
                emails = email_resp.json()
                if emails:
                    user_data["email"] = emails[0].get("email", "")

            return user_data


class OAuthProviderFactory:
    """Factory for creating OAuth providers."""

    _providers: dict[str, type[OAuthProvider]] = {
        "google": GoogleOAuthProvider,
        "github": GitHubOAuthProvider,
    }

    @classmethod
    def get_provider(cls, provider_name: str) -> OAuthProvider:
        """
        Get OAuth provider by name.

        Args:
            provider_name: Name of the provider (google, github)

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
