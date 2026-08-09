"""
Identity domain — OAuth routes (authorize + callback).

Handles the OAuth web flow for social login (Google) and
Google Calendar integration. Separated from main routes.py
due to complexity (state encoding, redirect URI construction,
calendar token storage).

Mounted at: /api/v1/auth/oauth
"""

import base64
import json
import logging
import secrets
from datetime import UTC, datetime, timedelta

import google.auth.exceptions
import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse, RedirectResponse

from src.config import Settings, get_settings
from src.shared.auth import create_access_token, create_refresh_token
from src.shared.auth.oauth_providers import GoogleIdTokenVerifier, OAuthProviderFactory

from .models import (
    NativeGoogleCallbackRequest,
    OAuthAuthorizeResponse,
    OAuthUserInfo,
    TokenResponse,
)
from .services import get_or_create_oauth_user

logger = logging.getLogger(__name__)

oauth_router = APIRouter(tags=["auth"])


def _get_base_url_from_request(request: Request) -> str:
    """Get external-facing base URL, respecting proxy headers."""
    forwarded_proto = request.headers.get("X-Forwarded-Proto", "http")
    forwarded_host = request.headers.get("X-Forwarded-Host")
    if forwarded_host:
        host = forwarded_host.split(":")[0] if ":" in forwarded_host else forwarded_host
        return f"{forwarded_proto}://{host}"
    return str(request.base_url).rstrip("/")


@oauth_router.get("/oauth/providers")
async def get_oauth_providers():
    """List available OAuth providers."""
    return {"providers": ["google"]}


@oauth_router.post("/oauth/google/native-callback", response_model=TokenResponse)
async def google_native_callback(data: NativeGoogleCallbackRequest):
    """Accept a Google ID token from native mobile SDK and return Maigie tokens."""
    settings = get_settings()
    verifier = GoogleIdTokenVerifier(settings.OAUTH_GOOGLE_CLIENT_ID)

    try:
        claims = await verifier.verify(data.id_token)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid Google ID token format")
    except google.auth.exceptions.GoogleAuthError:
        raise HTTPException(status_code=401, detail="Invalid or expired Google ID token")
    except (httpx.ConnectError, httpx.TimeoutException):
        raise HTTPException(status_code=503, detail="Google authentication service unavailable")
    except Exception:
        logger.exception("Unexpected error during Google ID token verification")
        raise HTTPException(status_code=500, detail="Internal server error")

    email = claims.get("email")
    if not email:
        raise HTTPException(status_code=422, detail="Google ID token missing email claim")

    oauth_info = OAuthUserInfo(
        email=email,
        full_name=claims.get("name"),
        provider="google",
        provider_user_id=claims.get("sub", ""),
    )
    user = await get_or_create_oauth_user(oauth_info)

    settings = get_settings()
    access = create_access_token(
        data={"sub": user.email},
        expires_delta=timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
    )
    refresh = create_refresh_token(data={"sub": user.email})

    return TokenResponse(access_token=access, refresh_token=refresh)


@oauth_router.get("/oauth/{provider}/authorize", response_model=OAuthAuthorizeResponse)
async def oauth_authorize(
    provider: str,
    request: Request,
    redirect: bool = False,
    redirect_uri: str | None = None,
    referral_code: str | None = None,
):
    """Initiate OAuth flow — returns authorization URL."""
    settings = get_settings()

    try:
        oauth_provider = OAuthProviderFactory.get_provider(provider)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    provider = provider.lower()

    if redirect_uri:
        redirect_uri = redirect_uri.rstrip("/")
    else:
        if settings.OAUTH_BASE_URL:
            base_url = settings.OAUTH_BASE_URL.rstrip("/")
        else:
            base_url = _get_base_url_from_request(request)
        redirect_uri = f"{base_url}/api/v1/auth/oauth/{provider}/callback"

    # Encode state with redirect_uri and optional referral code
    state_data = {"redirect_uri": redirect_uri, "random": secrets.token_urlsafe(32)}
    if referral_code:
        state_data["referral_code"] = referral_code.upper().strip()
    state = base64.urlsafe_b64encode(json.dumps(state_data).encode()).decode().rstrip("=")

    authorization_url = await oauth_provider.get_authorization_url(
        redirect_uri=redirect_uri, state=state
    )

    if redirect:
        return RedirectResponse(url=authorization_url)

    return OAuthAuthorizeResponse(
        authorization_url=authorization_url, state=state, provider=provider
    )


@oauth_router.get("/oauth/{provider}/callback")
async def oauth_callback(provider: str, code: str, state: str, request: Request):
    """Handle OAuth callback — exchange code for tokens."""
    settings = get_settings()

    try:
        oauth_provider = OAuthProviderFactory.get_provider(provider)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    provider = provider.lower()

    # Decode state
    redirect_uri = None
    referral_code = None
    purpose = None
    calendar_user_id = None

    try:
        state_padded = state + "=" * (4 - len(state) % 4)
        state_data = json.loads(base64.urlsafe_b64decode(state_padded).decode())
        redirect_uri = state_data.get("redirect_uri")
        referral_code = state_data.get("referral_code")
        purpose = state_data.get("purpose")
        calendar_user_id = state_data.get("user_id")
    except Exception:
        pass

    if not redirect_uri:
        if settings.OAUTH_BASE_URL:
            base_url = settings.OAUTH_BASE_URL.rstrip("/")
        else:
            base_url = _get_base_url_from_request(request)
        redirect_uri = f"{base_url}/api/v1/auth/oauth/{provider}/callback"

    try:
        # Exchange code for token
        token_response = await oauth_provider.get_access_token(code, redirect_uri)
        access_token = token_response.get("access_token")
        refresh_token_oauth = token_response.get("refresh_token")
        expires_in = token_response.get("expires_in", 3600)

        if not access_token:
            raise HTTPException(status_code=400, detail="Failed to obtain access token")

        # Calendar sync flow
        if purpose == "calendar_sync" and calendar_user_id:
            from .repository import identity_repo

            user = await identity_repo.find_by_id(calendar_user_id)
            if not user:
                raise HTTPException(status_code=404, detail="User not found")

            expires_at = datetime.now(UTC) + timedelta(seconds=expires_in)
            await identity_repo.update(
                calendar_user_id,
                {
                    "googleCalendarAccessToken": access_token,
                    "googleCalendarRefreshToken": refresh_token_oauth,
                    "googleCalendarTokenExpiresAt": expires_at,
                    "googleCalendarSyncEnabled": True,
                },
            )

            # Create Maigie calendar
            from src.integrations.google_calendar import (
                create_maigie_calendar,
                sync_existing_schedules,
            )

            calendar_id = await create_maigie_calendar(calendar_user_id)

            # Sync existing schedules
            sync_results = {"success_count": 0, "total": 0}
            try:
                sync_results = await sync_existing_schedules(calendar_user_id)
            except Exception as e:
                logger.warning(f"Schedule sync failed: {e}")

            return JSONResponse(
                content={
                    "status": "success",
                    "message": "Google Calendar connected successfully",
                    "sync_enabled": True,
                    "calendar_id": calendar_id,
                    "synced_schedules": sync_results.get("success_count", 0),
                }
            )

        # Regular OAuth login flow
        user_info = await oauth_provider.get_user_info(access_token)
        email = user_info.get("email", "")
        if not email:
            raise HTTPException(status_code=400, detail="Email not provided by OAuth provider")

        oauth_info = OAuthUserInfo(
            email=email,
            full_name=user_info.get("name"),
            provider=provider,
            provider_user_id=str(user_info.get("id") or user_info.get("sub", "")),
            referral_code=referral_code,
        )
        user = await get_or_create_oauth_user(oauth_info)

        # Generate Maigie tokens
        token_data = {"sub": user.email, "email": user.email, "user_id": str(user.id)}
        jwt_token = create_access_token(
            data=token_data,
            expires_delta=timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
        )
        refresh = create_refresh_token(data={"sub": user.email})

        return {"access_token": jwt_token, "refresh_token": refresh, "token_type": "bearer"}

    except HTTPException:
        raise
    except (httpx.TimeoutException, httpx.ConnectError):
        raise HTTPException(status_code=503, detail="OAuth provider unavailable")
    except Exception as e:
        if "DataError" in type(e).__name__:
            raise HTTPException(status_code=503, detail="Service temporarily unavailable")
        logger.error(f"OAuth callback error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"OAuth callback failed: {type(e).__name__}")
