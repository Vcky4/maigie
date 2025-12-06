"""
Authentication routes (JWT Signup/Login + OAuth + OTP Verification).

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

import base64
import json
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, EmailStr

from src.config import settings
from src.core.database import db
from src.core.oauth import OAuthProviderFactory
from src.core.security import (
    create_access_token,
    get_password_hash,
    verify_password,
    generate_otp,
)
from src.dependencies import CurrentUser, DBDep
from src.exceptions import AuthenticationError
from src.models.auth import (
    OAuthAuthorizeResponse,
    Token,
    UserLogin,
    UserResponse,
    UserSignup,
)
from src.services.email import (
    send_verification_email,
    send_welcome_email,
    send_password_reset_email,
)
from src.services.user_service import OAuthUserInfo, get_or_create_oauth_user

# Get logger for this module
logger = logging.getLogger(__name__)

router = APIRouter(tags=["auth"])


def get_base_url_from_request(request: Request) -> str:
    """
    Get the base URL from request, respecting proxy headers.

    When behind a reverse proxy (Cloudflare Tunnel, Nginx, etc.), the proxy
    sets X-Forwarded-Proto and X-Forwarded-Host headers. This function
    uses those headers to construct the correct external URL (HTTPS) instead
    of the internal URL (HTTP).

    Args:
        request: FastAPI Request object

    Returns:
        Base URL string (e.g., "https://api.maigie.com" or "http://localhost:8000")
    """
    # Check for proxy headers (Cloudflare Tunnel, Nginx, etc.)
    forwarded_proto = request.headers.get("X-Forwarded-Proto", "http")
    forwarded_host = request.headers.get("X-Forwarded-Host")

    if forwarded_host:
        # Use forwarded host and protocol from proxy
        # Remove port if present (Cloudflare Tunnel doesn't include port)
        host = forwarded_host.split(":")[0] if ":" in forwarded_host else forwarded_host
        base_url = f"{forwarded_proto}://{host}".rstrip("/")
    else:
        # Fallback to request.base_url (for local development without proxy)
        base_url = str(request.base_url).rstrip("/")

    return base_url


# ==========================================
#  REQUEST MODELS
# ==========================================


class VerifyRequest(BaseModel):
    email: EmailStr
    code: str


class ResendOTPRequest(BaseModel):
    email: EmailStr


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class VerifyResetCodeRequest(BaseModel):
    email: EmailStr
    code: str


class ResetPasswordConfirm(BaseModel):
    email: EmailStr
    code: str
    new_password: str


# ==========================================
#  JWT & OTP AUTHENTICATION
# ==========================================


@router.post("/signup", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def signup(user_data: UserSignup):
    """
    Register a new user account and attempt to send verification OTP.
    """
    # 1. Check if email already exists
    existing_user = await db.user.find_unique(where={"email": user_data.email})
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered",
        )

    # 2. Hash the password
    hashed_password = get_password_hash(user_data.password)

    # 3. Generate OTP and Expiry (15 minutes)
    otp_code = generate_otp()
    otp_expires = datetime.now(timezone.utc) + timedelta(minutes=15)

    # 4. Create user (Inactive + OTP stored in DB)
    new_user = await db.user.create(
        data={
            "email": user_data.email,
            "passwordHash": hashed_password,
            "name": user_data.name,
            "provider": "email",
            "isActive": False,
            # Store the OTP and expiry directly in the user record
            "verificationCode": otp_code,
            "verificationCodeExpiresAt": otp_expires,
            "preferences": {
                "create": {
                    "theme": "light",
                    "language": "en",
                    "notifications": True,
                }
            },
        },
        include={"preferences": True},
    )

    # 5. Send Verification Email (Safe Mode)
    try:
        # Pass the 6-digit 'otp_code' directly
        await send_verification_email(new_user.email, otp_code)
    except Exception as e:
        # If email fails, print the error but DO NOT crash the request.
        logger.error(f"Email delivery failed during signup: {e}")

    return new_user


@router.post("/verify-email")
async def verify_email(data: VerifyRequest):
    """
    Verify the 6-digit OTP code to activate the account.
    """
    # 1. Find user
    user = await db.user.find_unique(where={"email": data.email})

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if user.isActive:
        return {"message": "Email already verified"}

    # 2. Validation Logic
    now = datetime.now(timezone.utc)

    if not user.verificationCode or user.verificationCode != data.code:
        raise HTTPException(status_code=400, detail="Invalid verification code")

    if user.verificationCodeExpiresAt and user.verificationCodeExpiresAt < now:
        raise HTTPException(status_code=400, detail="Verification code expired")

    # 3. Activate user
    updated_user = await db.user.update(
        where={"id": user.id},
        data={
            "isActive": True,
            "verificationCode": None,
            "verificationCodeExpiresAt": None,
        },
    )

    # 4. Send Welcome Email (Fire and Forget)
    try:
        await send_welcome_email(updated_user.email, updated_user.name)
    except Exception as e:
        logger.error(f"Failed to send welcome email: {e}")

    return {"message": "Email verified successfully"}


@router.post("/resend-otp")
async def resend_otp_code(data: ResendOTPRequest):
    """
    Generates a new OTP and sends it to the user.
    Includes a 1-minute cooldown to prevent spam.
    """
    # 1. Find User
    user = await db.user.find_unique(where={"email": data.email})

    if not user:
        # Security: Don't reveal if email exists or not.
        return {"message": "If this account exists, a new code has been sent."}

    if user.isActive:
        raise HTTPException(status_code=400, detail="Account is already verified.")

    # 2. Rate Limiting (Database Strategy)
    now = datetime.now(timezone.utc)
    if user.verificationCodeExpiresAt:
        time_remaining = user.verificationCodeExpiresAt - now
        # If more than 14 minutes remain on the 15-minute timer
        if time_remaining > timedelta(minutes=14):
            wait_seconds = int(time_remaining.total_seconds() - (14 * 60))
            raise HTTPException(
                status_code=429,
                detail=f"Please wait {wait_seconds} seconds before resending.",
            )

    # 3. Generate New OTP
    new_otp = generate_otp()
    new_expiry = now + timedelta(minutes=15)

    # 4. Update Database
    await db.user.update(
        where={"id": user.id},
        data={"verificationCode": new_otp, "verificationCodeExpiresAt": new_expiry},
    )

    # 5. Send Email
    try:
        await send_verification_email(user.email, new_otp)
    except Exception as e:
        logger.error(f"Failed to send email: {str(e)}")
        raise HTTPException(status_code=500, detail="Error sending email")

    return {"message": "New verification code sent."}


@router.post("/login", response_model=Token)
async def login_for_access_token(
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
):
    """
    OAuth2 compatible token login (Swagger UI).
    """
    # 1. Find User
    user = await db.user.find_unique(where={"email": form_data.username})

    # 2. Validate Credentials
    if (
        not user
        or not user.passwordHash
        or not verify_password(form_data.password, user.passwordHash)
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # 3. Check Activation Status
    if not user.isActive:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Account inactive. Please verify your email.",
        )

    # 4. Generate Token
    access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(data={"sub": user.email}, expires_delta=access_token_expires)

    return {"access_token": access_token, "token_type": "bearer"}


@router.post("/login/json", response_model=Token)
async def login_json(user_data: UserLogin):
    """
    Standard JSON login endpoint for frontend apps.
    """
    # 1. Find User
    user = await db.user.find_unique(where={"email": user_data.email})

    # 2. Validate Credentials
    if (
        not user
        or not user.passwordHash
        or not verify_password(user_data.password, user.passwordHash)
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # 3. Check Activation Status
    if not user.isActive:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Account inactive. Please verify your email.",
        )

    # 4. Generate Token
    access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(data={"sub": user.email}, expires_delta=access_token_expires)

    return {"access_token": access_token, "token_type": "bearer"}


@router.get("/me", response_model=UserResponse)
async def read_users_me(current_user: CurrentUser):
    """
    Get current user information.
    """
    return current_user


@router.post("/logout")
async def logout():
    """
    End user session.
    """
    return {"message": "Successfully logged out"}


# ==========================================
#  PASSWORD RESET FLOW
# ==========================================


@router.post("/forgot-password")
async def forgot_password(request: ForgotPasswordRequest):
    """
    Step 1: User provides email -> System sends OTP.
    """
    user = await db.user.find_unique(where={"email": request.email})

    if user:
        otp = generate_otp()
        expiry = datetime.now(timezone.utc) + timedelta(minutes=15)

        await db.user.update(
            where={"id": user.id},
            data={"passwordResetCode": otp, "passwordResetExpiresAt": expiry},
        )

        try:
            await send_password_reset_email(user.email, otp, user.name)
        except Exception as e:
            logger.error(f"Failed to send reset email: {e}")

    return {"message": "If an account exists, a reset code has been sent."}


@router.post("/verify-reset-code")
async def verify_reset_code(data: VerifyResetCodeRequest):
    """
    Step 2: Frontend sends code to check if it's valid BEFORE showing password inputs.
    """
    user = await db.user.find_unique(where={"email": data.email})

    if not user:
        raise HTTPException(status_code=400, detail="Invalid code or email")

    now = datetime.now(timezone.utc)

    if (
        not user.passwordResetCode
        or user.passwordResetCode != data.code
        or not user.passwordResetExpiresAt
        or user.passwordResetExpiresAt < now
    ):
        raise HTTPException(status_code=400, detail="Invalid or expired reset code")

    return {"message": "Code is valid"}


@router.post("/reset-password")
async def reset_password_confirm(data: ResetPasswordConfirm):
    """
    Step 3: User provides OTP + New Password -> System updates password.
    Note: We must verify the code AGAIN here for security reasons.
    """
    user = await db.user.find_unique(where={"email": data.email})
    if not user:
        raise HTTPException(status_code=400, detail="Invalid code or email")

    # Re-Verify Code (Security Requirement)
    now = datetime.now(timezone.utc)
    if (
        not user.passwordResetCode
        or user.passwordResetCode != data.code
        or not user.passwordResetExpiresAt
        or user.passwordResetExpiresAt < now
    ):
        raise HTTPException(status_code=400, detail="Invalid or expired reset code")

    # Hash and Update
    hashed_password = get_password_hash(data.new_password)

    await db.user.update(
        where={"id": user.id},
        data={
            "passwordHash": hashed_password,
            "passwordResetCode": None,  # Consume the code so it can't be reused
            "passwordResetExpiresAt": None,
        },
    )

    return {"message": "Password reset successfully. You can now login."}


# ==========================================
#  OAUTH AUTHENTICATION
# ==========================================


@router.get("/oauth/providers")
async def get_oauth_providers():
    """
    List available providers.
    """
    return {"providers": ["google"]}  # TODO: Add "github" when GitHub OAuth is enabled


@router.get("/oauth/{provider}/authorize", response_model=OAuthAuthorizeResponse)
async def oauth_authorize(
    provider: str,
    request: Request,
    redirect: bool = False,
    redirect_uri: str | None = None,
):
    """
    Initiate OAuth flow.

    Args:
        provider: OAuth provider name (e.g., "google")
        redirect: If True, perform server-side redirect instead of returning JSON
        redirect_uri: Optional custom redirect URI. If not provided, will be constructed
                     from OAUTH_BASE_URL or request.base_url
    """
    try:
        # Get OAuth provider instance (validates provider and credentials)
        oauth_provider = OAuthProviderFactory.get_provider(provider)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )

    # Normalize provider name to lowercase for consistent redirect URIs
    provider = provider.lower()

    # Build the callback redirect URI
    # Use provided redirect_uri, or configured OAUTH_BASE_URL, or request-based URL
    if redirect_uri:
        # Use the provided redirect_uri as-is
        redirect_uri = redirect_uri.rstrip("/")
    else:
        # Construct redirect URI from settings or request
        if settings.OAUTH_BASE_URL:
            base_url = settings.OAUTH_BASE_URL.rstrip("/")
        else:
            base_url = get_base_url_from_request(request)
        callback_path = f"/api/v1/auth/oauth/{provider}/callback"
        redirect_uri = f"{base_url}{callback_path}"

    # Generate a secure state token for CSRF protection
    # Encode the redirect_uri in the state so callback can use the same one
    state_data = {"redirect_uri": redirect_uri, "random": secrets.token_urlsafe(32)}
    state = base64.urlsafe_b64encode(json.dumps(state_data).encode()).decode().rstrip("=")

    # Log the redirect URI for debugging (helps verify Google Cloud Console config)
    logger.info(
        "OAuth authorization initiated",
        extra={
            "provider": provider,
            "redirect_uri": redirect_uri,
        },
    )

    try:
        # Get the authorization URL from the provider
        authorization_url = await oauth_provider.get_authorization_url(
            redirect_uri=redirect_uri, state=state
        )

        # If redirect=true, perform server-side redirect
        if redirect:
            return RedirectResponse(url=authorization_url)

        # Otherwise, return JSON for frontend to handle redirect
        return OAuthAuthorizeResponse(
            authorization_url=authorization_url,
            state=state,
            provider=provider,
        )

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to initiate OAuth flow: {str(e)}",
        )


@router.get("/oauth/{provider}/callback", response_model=Token)
async def oauth_callback(provider: str, code: str, state: str, request: Request, db: DBDep):
    """
    Handle OAuth callback.
    """
    try:
        # Get OAuth provider instance
        oauth_provider = OAuthProviderFactory.get_provider(provider)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )

    # Normalize provider name to lowercase (must match authorization request)
    provider = provider.lower()

    # Extract redirect_uri from state if it was encoded there, otherwise construct it
    redirect_uri = None
    try:
        # Try to decode state to get redirect_uri
        # Add padding if needed
        state_padded = state + "=" * (4 - len(state) % 4)
        state_decoded = base64.urlsafe_b64decode(state_padded).decode()
        state_data = json.loads(state_decoded)
        redirect_uri = state_data.get("redirect_uri")
    except Exception:
        # If state doesn't contain redirect_uri, construct it the same way as authorize
        pass

    # If redirect_uri wasn't in state, construct it from settings or request
    if not redirect_uri:
        if settings.OAUTH_BASE_URL:
            base_url = settings.OAUTH_BASE_URL.rstrip("/")
        else:
            base_url = get_base_url_from_request(request)
        callback_path = f"/api/v1/auth/oauth/{provider}/callback"
        redirect_uri = f"{base_url}{callback_path}"

    logger.info(
        "OAuth callback received",
        extra={
            "provider": provider,
            "redirect_uri": redirect_uri,
            "has_code": bool(code),
            "has_state": bool(state),
        },
    )

    try:
        # Exchange authorization code for access token
        token_response = await oauth_provider.get_access_token(code, redirect_uri)

        # Debug: Check token response structure
        if not isinstance(token_response, dict):
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Unexpected token response type: {type(token_response)}",
            )

        access_token = token_response.get("access_token")

        if not access_token:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Failed to obtain access token from OAuth provider. Response keys: {list(token_response.keys())}",
            )

        # Get user information from provider
        user_info = await oauth_provider.get_user_info(access_token)
        logger.info("User info retrieved from Google", extra={"user_info": user_info})

        # Extract user data
        email = user_info.get("email", "")
        user_id = user_info.get("id") or user_info.get("sub", "")
        full_name = user_info.get("name") or user_info.get("full_name")
        logger.info(
            "Extracted user data from OAuth response",
            extra={"email": email, "user_id": user_id, "full_name": full_name},
        )

        if not email:
            raise AuthenticationError("Email not provided by OAuth provider")

        # Construct the required Pydantic object
        oauth_user_info = OAuthUserInfo(
            email=email,
            full_name=full_name,
            provider=provider,
            provider_user_id=str(user_id),
        )

        # Get or Create the Maigie user record
        user = await get_or_create_oauth_user(oauth_user_info, db)
        logger.info(
            "User created/retrieved from database",
            extra={"user_id": user.id, "user_email": user.email},
        )

        # Update the token_data dictionary using the actual database user's info
        token_data = {
            "sub": user.email,
            "email": user.email,
            "user_id": str(user.id),
            "full_name": user.name,
            "is_onboarded": getattr(user, "isOnboarded", False),
        }

        # Generate JWT token
        access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
        jwt_token = create_access_token(data=token_data, expires_delta=access_token_expires)

        return {"access_token": jwt_token, "token_type": "bearer"}

    except HTTPException:
        raise
    except httpx.HTTPStatusError as e:
        # Handle HTTP errors from OAuth provider (e.g., Google)
        error_message = "Unknown OAuth error"
        error_data = {}

        try:
            # Try to extract error details from response
            if hasattr(e, "response") and e.response is not None:
                content_type = e.response.headers.get("content-type", "")
                if "application/json" in content_type:
                    try:
                        error_data = e.response.json()
                        error_message = (
                            error_data.get("error_description") or error_data.get("error") or str(e)
                        )
                    except Exception:
                        error_data = {"error": e.response.text}
                        error_message = e.response.text
                else:
                    error_data = {"error": e.response.text}
                    error_message = e.response.text

                logger.error(
                    "OAuth HTTP error from provider",
                    extra={
                        "status_code": e.response.status_code,
                        "error": error_data,
                        "redirect_uri": redirect_uri,
                        "provider": provider,
                    },
                )
        except Exception as parse_error:
            logger.error(
                "Failed to parse OAuth error response",
                extra={"error": str(parse_error), "original_error": str(e)},
            )
            error_message = str(e)

        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"OAuth provider error: {error_message}",
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    except Exception as e:
        import traceback

        logger.error(
            "OAuth callback unexpected error",
            extra={
                "error_type": type(e).__name__,
                "error": str(e),
            },
            exc_info=True,
        )

        error_detail = f"{type(e).__name__}: {str(e)}"
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"OAuth callback failed: {error_detail}",
        )
