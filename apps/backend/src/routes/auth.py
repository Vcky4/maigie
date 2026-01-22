"""
Authentication routes (JWT Signup/Login + OAuth + OTP Verification).

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

import base64
import json
import logging
import secrets
from datetime import UTC, datetime, timedelta, timezone
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, EmailStr

from src.config import settings
from src.core.database import db
from src.core.oauth import OAuthProviderFactory
from src.core.security import (
    create_access_token,
    create_refresh_token,
    decode_access_token,
    generate_otp,
    get_password_hash,
    verify_password,
)
from src.dependencies import CurrentUser, DBDep
from src.exceptions import AuthenticationError
from src.models.auth import (
    OAuthAuthorizeResponse,
    RefreshTokenRequest,
    Token,
    UserLogin,
    UserResponse,
    UserSignup,
)
from src.services.email import (
    send_password_reset_email,
    send_verification_email,
    send_welcome_email,
)
from src.services.referral_service import track_referral_signup
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
    # Note: Referral code linking is now handled separately via /link-referral endpoint


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


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


class LinkReferralRequest(BaseModel):
    referralCode: str


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
    otp_expires = datetime.now(UTC) + timedelta(minutes=15)

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

    # Note: Referral code linking is now handled separately via /link-referral endpoint
    # after user completes signup and email verification

    # 6. Send Verification Email (Safe Mode)
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
    Also registers referral code if provided (only if user doesn't already have one).
    """
    # 1. Find user
    user = await db.user.find_unique(where={"email": data.email})

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if user.isActive:
        return {"message": "Email already verified"}

    # 2. Validation Logic
    now = datetime.now(UTC)

    if not user.verificationCode or user.verificationCode != data.code:
        raise HTTPException(status_code=400, detail="Invalid verification code")

    if user.verificationCodeExpiresAt and user.verificationCodeExpiresAt < now:
        raise HTTPException(status_code=400, detail="Verification code expired")

    # 3. Prepare update data
    update_data = {
        "isActive": True,
        "verificationCode": None,
        "verificationCodeExpiresAt": None,
    }

    # Note: Referral code linking is now handled separately via /link-referral endpoint
    # after user completes email verification

    # 5. Activate user and register referral
    updated_user = await db.user.update(
        where={"id": user.id},
        data=update_data,
    )

    # 6. Send Welcome Email (Fire and Forget)
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
    now = datetime.now(UTC)
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

    # 4. Generate Tokens
    access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(data={"sub": user.email}, expires_delta=access_token_expires)
    refresh_token = create_refresh_token(data={"sub": user.email})

    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
    }


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

    # 4. Generate Tokens
    access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(data={"sub": user.email}, expires_delta=access_token_expires)
    refresh_token = create_refresh_token(data={"sub": user.email})

    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
    }


@router.post("/refresh", response_model=Token)
async def refresh_access_token(token_request: RefreshTokenRequest):
    """
    Refresh access token using a valid refresh token.
    """
    try:
        # Decode the refresh token
        payload = decode_access_token(token_request.refresh_token)

        # Verify it's a refresh token
        if payload.get("type") != "refresh":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token type",
            )

        # Get the user email
        email: str = payload.get("sub")
        if email is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token payload",
            )

        # Verify user still exists and is active
        user = await db.user.find_unique(where={"email": email})
        if not user or not user.isActive:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User not found or inactive",
            )

        # Generate new tokens
        access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
        new_access_token = create_access_token(
            data={"sub": user.email}, expires_delta=access_token_expires
        )
        new_refresh_token = create_refresh_token(data={"sub": user.email})

        return {
            "access_token": new_access_token,
            "refresh_token": new_refresh_token,
            "token_type": "bearer",
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error refreshing token: {e}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate refresh token",
        )


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
        expiry = datetime.now(UTC) + timedelta(minutes=15)

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

    now = datetime.now(UTC)

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
    now = datetime.now(UTC)
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


@router.post("/change-password")
async def change_password(
    data: ChangePasswordRequest,
    current_user: CurrentUser,
):
    """
    Change password for logged-in users.
    """
    # Verify current password
    if not current_user.passwordHash or not verify_password(
        data.current_password, current_user.passwordHash
    ):
        raise HTTPException(status_code=400, detail="Incorrect current password")

    # Update password
    hashed_password = get_password_hash(data.new_password)
    await db.user.update(
        where={"id": current_user.id},
        data={"passwordHash": hashed_password},
    )

    return {"message": "Password changed successfully"}


@router.post("/link-referral")
async def link_referral(
    data: LinkReferralRequest,
    current_user: CurrentUser,
):
    """
    Link a referral code to the current user after signup/login.
    Skips if the user was already referred.
    """
    # Check if user already has a referral code (immutable once set)
    if current_user.referredByCode:
        logger.info(
            f"User {current_user.id} already has referral code {current_user.referredByCode}, "
            f"skipping link for code {data.referralCode}"
        )
        return {
            "message": "User already has a referral code",
            "alreadyReferred": True,
            "existingCode": current_user.referredByCode,
        }

    # Normalize referral code
    referral_code = data.referralCode.upper().strip()

    # Track referral signup (this will validate the code, check for self-referral, etc.)
    try:
        # Get fresh user data from database
        user = await db.user.find_unique(where={"id": current_user.id})
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        referrer = await track_referral_signup(user, referral_code)
        if referrer:
            logger.info(f"Successfully linked referral code {referral_code} for user {user.id}")
            return {
                "message": "Referral code linked successfully",
                "alreadyReferred": False,
                "referralCode": referral_code,
            }
        else:
            # Referral code not found or invalid
            logger.warning(f"Referral code {referral_code} not found or invalid")
            raise HTTPException(
                status_code=400,
                detail="Invalid referral code",
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error linking referral code: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Failed to link referral code",
        )


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
    referral_code: str | None = None,
):
    """
    Initiate OAuth flow.

    Args:
        provider: OAuth provider name (e.g., "google")
        redirect: If True, perform server-side redirect instead of returning JSON
        redirect_uri: Optional custom redirect URI. If not provided, will be constructed
                     from OAUTH_BASE_URL or request.base_url
        referral_code: Optional referral code to register after OAuth signup
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
    # Encode the redirect_uri and referral_code in the state so callback can use them
    state_data = {"redirect_uri": redirect_uri, "random": secrets.token_urlsafe(32)}
    if referral_code:
        state_data["referral_code"] = referral_code.upper().strip()
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


@router.get("/oauth/{provider}/callback")
async def oauth_callback(provider: str, code: str, state: str, request: Request, db: DBDep):
    """
    Handle OAuth callback.
    """
    logger.info(
        "OAuth callback initiated",
        extra={
            "provider": provider,
            "has_code": bool(code),
            "has_state": bool(state),
            "state_length": len(state) if state else 0,
            "code_length": len(code) if code else 0,
            "request_url": str(request.url),
            "request_method": request.method,
        },
    )

    try:
        # Get OAuth provider instance
        oauth_provider = OAuthProviderFactory.get_provider(provider)
        logger.debug(f"OAuth provider instance created for {provider}")
    except ValueError as e:
        logger.error(
            f"Invalid OAuth provider: {provider}",
            extra={"provider": provider, "error": str(e)},
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )

    # Normalize provider name to lowercase (must match authorization request)
    provider = provider.lower()

    # Extract redirect_uri, purpose, and callback info from state if it was encoded there, otherwise construct it
    redirect_uri = None
    purpose = None
    calendar_user_id = None
    backend_callback_uri = None
    frontend_redirect_uri = None
    state_decoded_successfully = False

    try:
        # Try to decode state to get redirect_uri, purpose, and callback info
        # Add padding if needed
        state_padded = state + "=" * (4 - len(state) % 4)
        state_decoded = base64.urlsafe_b64decode(state_padded).decode()
        state_data = json.loads(state_decoded)
        redirect_uri = state_data.get("redirect_uri")  # The redirect URI used for Google OAuth
        purpose = state_data.get("purpose")  # "calendar_sync" for Calendar integration
        calendar_user_id = state_data.get("user_id")  # User ID for Calendar sync
        backend_callback_uri = state_data.get("backend_callback_uri")  # Backend callback endpoint
        frontend_redirect_uri = state_data.get(
            "frontend_redirect_uri"
        )  # Frontend redirect URI (optional)
        referral_code = state_data.get("referral_code")  # Referral code from signup URL
        state_decoded_successfully = True

        logger.info(
            "State decoded successfully",
            extra={
                "provider": provider,
                "purpose": purpose,
                "has_calendar_user_id": bool(calendar_user_id),
                "has_backend_callback_uri": bool(backend_callback_uri),
                "has_frontend_redirect_uri": bool(frontend_redirect_uri),
                "redirect_uri": redirect_uri,
            },
        )
    except Exception as state_error:
        # If state doesn't contain redirect_uri, construct it the same way as authorize
        logger.debug(
            "State decoding failed, will construct redirect_uri",
            extra={
                "provider": provider,
                "error_type": type(state_error).__name__,
                "error": str(state_error),
            },
        )

    # If redirect_uri wasn't in state, construct it from settings or request
    if not redirect_uri:
        if settings.OAUTH_BASE_URL:
            base_url = settings.OAUTH_BASE_URL.rstrip("/")
            logger.debug(f"Using OAUTH_BASE_URL from settings: {base_url}")
        else:
            base_url = get_base_url_from_request(request)
            logger.debug(f"Constructed base_url from request: {base_url}")
        callback_path = f"/api/v1/auth/oauth/{provider}/callback"
        redirect_uri = f"{base_url}{callback_path}"
        logger.info(f"Constructed redirect_uri: {redirect_uri}")

    logger.info(
        "OAuth callback received",
        extra={
            "provider": provider,
            "redirect_uri": redirect_uri,
            "has_code": bool(code),
            "has_state": bool(state),
            "state_decoded": state_decoded_successfully,
            "purpose": purpose,
            "calendar_user_id": calendar_user_id,
        },
    )

    try:
        # Exchange authorization code for access token
        logger.info(
            "Exchanging authorization code for access token",
            extra={
                "provider": provider,
                "redirect_uri": redirect_uri,
                "code_length": len(code) if code else 0,
            },
        )

        token_response = await oauth_provider.get_access_token(code, redirect_uri)

        logger.debug(
            "Token exchange completed",
            extra={
                "provider": provider,
                "response_type": type(token_response).__name__,
                "response_keys": (
                    list(token_response.keys()) if isinstance(token_response, dict) else None
                ),
            },
        )

        # Debug: Check token response structure
        if not isinstance(token_response, dict):
            logger.error(
                f"Unexpected token response type: {type(token_response)}",
                extra={
                    "provider": provider,
                    "response_type": str(type(token_response)),
                    "response_value": str(token_response)[:200],  # Limit length
                },
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Unexpected token response type: {type(token_response)}",
            )

        access_token = token_response.get("access_token")
        refresh_token = token_response.get("refresh_token")
        expires_in = token_response.get("expires_in", 3600)
        granted_scopes = token_response.get("scope", "")
        token_type = token_response.get("token_type", "Bearer")

        logger.info(
            "Token response parsed",
            extra={
                "provider": provider,
                "has_access_token": bool(access_token),
                "has_refresh_token": bool(refresh_token),
                "expires_in": expires_in,
                "token_type": token_type,
                "granted_scopes": granted_scopes,
                "access_token_length": len(access_token) if access_token else 0,
                "refresh_token_length": len(refresh_token) if refresh_token else 0,
            },
        )

        if not access_token:
            logger.error(
                "No access token in response",
                extra={
                    "provider": provider,
                    "response_keys": list(token_response.keys()),
                    "response_values": {
                        k: "***" if "token" in k.lower() else v for k, v in token_response.items()
                    },
                },
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Failed to obtain access token from OAuth provider. Response keys: {list(token_response.keys())}",
            )

        # If this is for Calendar sync, handle it differently
        if purpose == "calendar_sync" and calendar_user_id:
            logger.info(
                "Processing calendar sync OAuth callback",
                extra={
                    "user_id": calendar_user_id,
                    "granted_scopes": granted_scopes,
                    "has_refresh_token": bool(refresh_token),
                    "expires_in": expires_in,
                },
            )
            try:
                # Verify user exists
                logger.debug(f"Looking up user {calendar_user_id}")
                user = await db.user.find_unique(where={"id": calendar_user_id})
                if not user:
                    logger.error(
                        "User not found for calendar sync",
                        extra={"user_id": calendar_user_id},
                    )
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail="User not found",
                    )

                logger.info(
                    "User found for calendar sync",
                    extra={
                        "user_id": user.id,
                        "user_email": user.email,
                        "existing_calendar_id": user.googleCalendarId,
                        "sync_already_enabled": user.googleCalendarSyncEnabled,
                    },
                )

                # Calculate expiration time
                expires_at = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(
                    seconds=expires_in
                )
                logger.debug(
                    "Calculated token expiration",
                    extra={
                        "expires_at": expires_at.isoformat(),
                        "expires_in_seconds": expires_in,
                    },
                )

                # Store Calendar tokens in user record (without calendar ID yet)
                logger.info(f"Storing calendar tokens for user {calendar_user_id}")
                await db.user.update(
                    where={"id": calendar_user_id},
                    data={
                        "googleCalendarAccessToken": access_token,
                        "googleCalendarRefreshToken": refresh_token,
                        "googleCalendarTokenExpiresAt": expires_at,
                        "googleCalendarSyncEnabled": True,
                    },
                )
                logger.info(f"Calendar tokens stored successfully for user {calendar_user_id}")

                # Create dedicated Maigie calendar
                logger.info(f"Creating Maigie calendar for user {calendar_user_id}")
                from src.services.google_calendar_service import google_calendar_service

                calendar_id = await google_calendar_service.create_maigie_calendar(calendar_user_id)
                if not calendar_id:
                    logger.error(
                        "Failed to create Maigie calendar",
                        extra={
                            "user_id": calendar_user_id,
                            "granted_scopes": granted_scopes,
                        },
                    )
                    raise HTTPException(
                        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                        detail="Failed to create Maigie calendar",
                    )

                logger.info(
                    "Maigie calendar created successfully",
                    extra={
                        "user_id": calendar_user_id,
                        "calendar_id": calendar_id,
                    },
                )

                # Sync existing schedules to the new calendar (don't fail connection if sync fails)
                sync_results = {"success_count": 0, "error_count": 0, "total": 0}
                logger.info(f"Syncing existing schedules for user {calendar_user_id}")
                try:
                    sync_results = await google_calendar_service.sync_existing_schedules(
                        calendar_user_id
                    )
                    logger.info(
                        "Schedule sync completed",
                        extra={
                            "user_id": calendar_user_id,
                            "success_count": sync_results.get("success_count", 0),
                            "error_count": sync_results.get("error_count", 0),
                            "total": sync_results.get("total", 0),
                        },
                    )
                except Exception as sync_error:
                    # Log sync errors but don't fail the connection
                    logger.warning(
                        f"Failed to sync existing schedules for user {calendar_user_id}",
                        extra={
                            "user_id": calendar_user_id,
                            "error_type": type(sync_error).__name__,
                            "error": str(sync_error),
                        },
                        exc_info=True,
                    )

                logger.info(
                    "Google Calendar connected and synced",
                    extra={
                        "user_id": calendar_user_id,
                        "user_email": user.email,
                        "calendar_id": calendar_id,
                        "synced_schedules": sync_results.get("success_count", 0),
                    },
                )

                # Always return JSON response (frontend will handle redirect if needed)
                # When frontend provides redirect_uri, Google redirects to frontend first,
                # then frontend calls this backend endpoint to get the JSON response
                return JSONResponse(
                    status_code=200,
                    content={
                        "status": "success",
                        "message": "Google Calendar connected successfully",
                        "sync_enabled": True,
                        "calendar_id": calendar_id,
                        "synced_schedules": sync_results.get("success_count", 0),
                        "total_schedules": sync_results.get("total", 0),
                    },
                )
            except HTTPException:
                # Re-raise HTTP exceptions
                raise
            except Exception as e:
                # Catch any other exceptions and log them properly
                logger.error(
                    f"Unexpected error during calendar sync for user {calendar_user_id}: {e}",
                    exc_info=True,
                )
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f"Failed to complete calendar connection: {str(e)}",
                )

        # Get user information from provider (for regular OAuth login)
        logger.info(
            "Fetching user info from OAuth provider",
            extra={"provider": provider},
        )
        user_info = await oauth_provider.get_user_info(access_token)
        logger.info(
            "User info retrieved from OAuth provider",
            extra={
                "provider": provider,
                "user_info_keys": list(user_info.keys()) if isinstance(user_info, dict) else None,
                "has_email": "email" in user_info if isinstance(user_info, dict) else False,
            },
        )

        # Extract user data
        email = user_info.get("email", "")
        user_id = user_info.get("id") or user_info.get("sub", "")
        full_name = user_info.get("name") or user_info.get("full_name")
        logger.info(
            "Extracted user data from OAuth response",
            extra={
                "provider": provider,
                "email": email,
                "user_id": user_id,
                "full_name": full_name,
                "has_email": bool(email),
                "has_user_id": bool(user_id),
            },
        )

        if not email:
            logger.error(
                "Email not provided by OAuth provider",
                extra={
                    "provider": provider,
                    "user_info": user_info,
                },
            )
            raise AuthenticationError("Email not provided by OAuth provider")

        # Construct the required Pydantic object
        oauth_user_info = OAuthUserInfo(
            email=email,
            full_name=full_name,
            provider=provider,
            provider_user_id=str(user_id),
            referral_code=referral_code if state_decoded_successfully else None,
        )

        # Get or Create the Maigie user record
        logger.info(
            "Getting or creating OAuth user",
            extra={
                "provider": provider,
                "email": email,
                "provider_user_id": oauth_user_info.provider_user_id,
            },
        )
        user = await get_or_create_oauth_user(oauth_user_info, db)
        logger.info(
            "User created/retrieved from database",
            extra={
                "user_id": user.id,
                "user_email": user.email,
                "provider": provider,
                "is_new_user": not hasattr(user, "_created") or getattr(user, "_created", False),
            },
        )

        # Update the token_data dictionary using the actual database user's info
        token_data = {
            "sub": user.email,
            "email": user.email,
            "user_id": str(user.id),
            "full_name": user.name,
            "is_onboarded": getattr(user, "isOnboarded", False),
        }

        # Generate JWT tokens
        logger.info(
            "Generating JWT tokens",
            extra={
                "user_id": user.id,
                "user_email": user.email,
                "expires_minutes": settings.ACCESS_TOKEN_EXPIRE_MINUTES,
            },
        )
        access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
        jwt_token = create_access_token(data=token_data, expires_delta=access_token_expires)
        refresh_token = create_refresh_token(data={"sub": user.email})

        logger.info(
            "OAuth callback completed successfully",
            extra={
                "provider": provider,
                "user_id": user.id,
                "user_email": user.email,
                "has_jwt_token": bool(jwt_token),
                "has_refresh_token": bool(refresh_token),
            },
        )

        return {
            "access_token": jwt_token,
            "refresh_token": refresh_token,
            "token_type": "bearer",
        }

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
