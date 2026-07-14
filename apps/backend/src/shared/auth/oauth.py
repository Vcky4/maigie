"""
OAuth provider utilities (Google OAuth login flow).

Handles the server-side OAuth dance for social login.
The OAuth 2.1 provider implementation (for ChatGPT integration)
lives in the identity domain since it's a product feature, not
shared infrastructure.
"""

import logging
from typing import Any

import httpx
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token

from src.config import get_settings

logger = logging.getLogger(__name__)


async def verify_google_token(token: str) -> dict[str, Any] | None:
    """Verify a Google OAuth ID token and return user info.

    Args:
        token: The ID token received from the Google OAuth flow.

    Returns:
        Dictionary with user info (email, name, sub) or None if invalid.
    """
    settings = get_settings()
    try:
        idinfo = google_id_token.verify_oauth2_token(
            token,
            google_requests.Request(),
            settings.OAUTH_GOOGLE_CLIENT_ID,
        )

        if idinfo["iss"] not in ("accounts.google.com", "https://accounts.google.com"):
            logger.warning("Google token has invalid issuer: %s", idinfo.get("iss"))
            return None

        return {
            "email": idinfo["email"],
            "name": idinfo.get("name"),
            "provider_id": idinfo["sub"],
            "email_verified": idinfo.get("email_verified", False),
        }
    except ValueError as e:
        logger.warning("Google token verification failed: %s", e)
        return None
    except Exception as e:
        logger.error("Unexpected error verifying Google token: %s", e)
        return None
