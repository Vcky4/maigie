"""
Authentication routes (JWT Signup/Login + OAuth Placeholders).

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
import logging
import secrets
from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, EmailStr  # <--- ADDED THIS IMPORT

from src.config import settings
from src.core.database import db
from src.core.oauth import OAuthProviderFactory
from src.core.security import (
    create_access_token,
    get_password_hash,
    verify_password,
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
from src.services.user_service import OAuthUserInfo, get_or_create_oauth_user

# Get logger for this module
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

# ==========================================
#  JWT AUTHENTICATION (Your Task)
# ==========================================

@router.post("/signup", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def signup(user_data: UserSignup):
    """
    Register a new user account.
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

    # 3. Create user and default preferences
    new_user = await db.user.create(
        data={
            "email": user_data.email,
            "passwordHash": hashed_password,
            "name": user_data.name,
            "provider": "email",
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

    return new_user


@router.post("/login", response_model=Token)
async def login_for_access_token(
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
):
    """
    OAuth2 compatible token login (Swagger UI).
    """
    user = await db.user.find_unique(where={"email": form_data.username})

    if not user or not user.passwordHash:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not verify_password(form_data.password, user.passwordHash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": user.email}, expires_delta=access_token_expires
    )

    return {"access_token": access_token, "token_type": "bearer"}


@router.post("/login/json", response_model=Token)
async def login_json(user_data: UserLogin):
    """
    Standard JSON login endpoint for frontend apps.
    """
    user = await db.user.find_unique(where={"email": user_data.email})

    if not user or not user.passwordHash or not verify_password(user_data.password, user.passwordHash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": user.email}, expires_delta=access_token_expires
    )

    return {"access_token": access_token, "token_type": "bearer"}


@router.get("/me", response_model=UserResponse)
async def read_users_me(current_user: CurrentUser):
    """
    Get current user information.
    """
    return current_user


# ==========================================
#  OAUTH AUTHENTICATION (Teammate Task)
# ==========================================

@router.get("/oauth/providers")
async def get_oauth_providers():
    """
    TODO: [Teammate Name] Implement listing available providers.
    """
    return {"providers": ["google", "github"]}


@router.get("/oauth/{provider}/authorize", response_model=OAuthAuthorizeResponse)
async def oauth_authorize(
    provider: str,
    request: Request,
    redirect: bool = False,
):
    """
    Initiate OAuth flow.
    - Validate provider
    - Generate state token for CSRF protection
    - Build callback redirect URI
    - Returns authorization URL in JSON (or redirects if redirect=true)

    Args:
        provider: OAuth provider name (google, github)
        redirect: If True, redirects directly to provider. If False (default), returns JSON.

    Returns:
        OAuthAuthorizeResponse with authorization_url, state, and provider
    """
    try:
        # Get OAuth provider instance (validates provider and credentials)
        oauth_provider = OAuthProviderFactory.get_provider(provider)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )

    # Generate a secure state token for CSRF protection
    state = secrets.token_urlsafe(32)

    # Build the callback redirect URI
    # The callback URL should match what's registered with the OAuth provider
    base_url = str(request.base_url).rstrip("/")
    callback_path = f"/api/v1/auth/oauth/{provider}/callback"
    redirect_uri = f"{base_url}{callback_path}"

    try:
        # Get the authorization URL from the provider
        authorization_url = await oauth_provider.get_authorization_url(
            redirect_uri=redirect_uri, state=state
        )

        # If redirect=true, perform server-side redirect (for backward compatibility)
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
async def oauth_callback(
    provider: str, code: str, state: str, request: Request, db: DBDep
):
    """
    Handle OAuth callback.
    - Exchange code for token
    - Get user info
    - Create/Update user in DB
    - Return JWT
    """
    try:
        # Get OAuth provider instance
        oauth_provider = OAuthProviderFactory.get_provider(provider)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )

    # Build redirect URI - must match exactly what was used in authorization request
    base_url = str(request.base_url).rstrip("/")
    callback_path = f"/api/v1/auth/oauth/{provider}/callback"
    redirect_uri = f"{base_url}{callback_path}"

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
        # Note: 'sub' must be the email to match get_current_user expectation
        token_data = {
            "sub": user.email,  # Must be email for get_current_user to work
            "email": user.email,
            "user_id": str(user.id),
            "full_name": user.name,  # User model uses 'name' field, mapped to 'full_name' in token
            # This flag is important for the frontend to know where to redirect
            "is_onboarded": getattr(user, "isOnboarded", False),  # Prisma uses camelCase 'isOnboarded'
        }

        # Generate JWT token
        access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
        jwt_token = create_access_token(
            data=token_data, expires_delta=access_token_expires
        )

        return {"access_token": jwt_token, "token_type": "bearer"}

    except HTTPException:
        raise
    except ValueError as e:
        # Re-raise ValueError from OAuth provider with better context
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    except Exception as e:
        # Include exception type and message for better debugging
        import traceback
        error_detail = f"{type(e).__name__}: {str(e)}"
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"OAuth callback failed: {error_detail}",
    )


# ==========================================
#  SESSION & PASSWORD MANAGEMENT
# ==========================================

@router.post("/logout")
async def logout():
    """
    End user session.
    """
    return {"message": "Successfully logged out"}

# --- Password & Email Management (Stubs) ---

class PasswordResetRequest(BaseModel):
    email: EmailStr

class EmailConfirmation(BaseModel):
    token: str

@router.post("/reset-password")
async def reset_password(request: PasswordResetRequest):
    """
    Request a password reset email.
    """
    # 1. Check if user exists
    user = await db.user.find_unique(where={"email": request.email})
    if user:
        # TODO: Generate reset token and send email via SendGrid/AWS
        print(f" MOCK EMAIL: Sending password reset link to {request.email}")

    # Always return 200 to prevent email enumeration attacks
    return {"message": "If an account exists, a reset email has been sent."}

@router.post("/confirm-email")
async def confirm_email(data: EmailConfirmation):
    """
    Verify email address using a token.
    """
    # TODO: Validate token and update user.isActive = True
    return {"message": "Email successfully confirmed"}

