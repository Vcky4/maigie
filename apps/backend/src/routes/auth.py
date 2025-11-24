"""Authentication routes."""

import secrets

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import RedirectResponse

from ..config import get_settings
from ..core.oauth import OAuthProviderFactory
from ..core.security import (
    create_access_token,
    create_refresh_token,
    decode_refresh_token,
    get_password_hash,
)
from ..dependencies import CurrentUserTokenDep, SettingsDep
from ..exceptions import AuthenticationError
from ..models.auth import (
    RefreshTokenRequest,
    TokenResponse,
    UserLogin,
    UserRegister,
    UserResponse,
)

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(user_data: UserRegister, settings: SettingsDep = None):
    """
    Register a new user.

    Note: This is a placeholder implementation. In production, you would:
    - Validate email uniqueness
    - Store user in database
    - Send verification email
    - Handle password strength requirements
    """
    # TODO: Implement actual user registration with database
    # For now, this demonstrates the authentication flow

    # Hash password
    hashed_password = get_password_hash(user_data.password)

    # In production, create user in database here
    # user = await create_user(email=user_data.email, password=hashed_password, ...)
    user_id = "user-placeholder-id"  # Replace with actual user.id

    # Create tokens
    token_data = {
        "sub": user_id,
        "email": user_data.email,
        "user_id": user_id,
    }
    access_token = create_access_token(token_data)
    refresh_token = create_refresh_token(token_data)

    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type="bearer",
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )


@router.post("/login", response_model=TokenResponse)
async def login(credentials: UserLogin):
    """
    Login user with email and password.

    Note: This is a placeholder implementation. In production, you would:
    - Verify user exists in database
    - Verify password hash
    - Check if user is active/verified
    - Implement rate limiting
    """
    # TODO: Implement actual user authentication with database
    # For now, this demonstrates the authentication flow

    # In production, fetch user from database
    # user = await get_user_by_email(credentials.email)
    # if not user or not verify_password(credentials.password, user.hashed_password):
    #     raise AuthenticationError("Invalid email or password")

    # Placeholder: For demonstration, accept any email/password
    # In production, remove this and use database lookup above
    user_id = "user-placeholder-id"  # Replace with actual user.id

    # Create tokens
    token_data = {
        "sub": user_id,
        "email": credentials.email,
        "user_id": user_id,
    }
    access_token = create_access_token(token_data)
    refresh_token = create_refresh_token(token_data)

    settings = get_settings()
    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type="bearer",
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(request: RefreshTokenRequest):
    """
    Refresh access token using refresh token.

    Note: In production, you should:
    - Verify refresh token is not revoked
    - Check token in cache/database for revocation status
    - Implement token rotation
    """
    try:
        # Decode refresh token
        payload = decode_refresh_token(request.refresh_token)

        # Extract user information
        user_id = payload.get("sub") or payload.get("user_id")
        email = payload.get("email")

        if not user_id:
            raise AuthenticationError("Invalid refresh token: missing user identifier")

        # Create new tokens
        token_data = {
            "sub": user_id,
            "email": email,
            "user_id": user_id,
        }
        access_token = create_access_token(token_data)
        refresh_token = create_refresh_token(token_data)

        settings = get_settings()
        return TokenResponse(
            access_token=access_token,
            refresh_token=refresh_token,
            token_type="bearer",
            expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        )
    except Exception as e:
        raise AuthenticationError(f"Invalid refresh token: {str(e)}")


@router.get("/me", response_model=UserResponse)
async def get_current_user(token: CurrentUserTokenDep):
    """
    Get current authenticated user information.

    Note: In production, you would fetch full user data from database.
    """
    user_id = token.get("sub") or token.get("user_id")
    email = token.get("email")

    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token missing user identifier",
        )

    # TODO: Fetch user from database
    # user = await get_user_by_id(user_id)

    return UserResponse(
        id=str(user_id),
        email=email or "",
        full_name=token.get("full_name"),
    )


@router.get("/oauth/{provider}/authorize")
async def oauth_authorize(provider: str, request: Request):
    """
    Initiate OAuth flow for a provider.

    Args:
        provider: OAuth provider name (google, github)
    """
    try:
        oauth_provider = OAuthProviderFactory.get_provider(provider)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )

    # Generate state for CSRF protection
    state = secrets.token_urlsafe(32)

    # Store state in session/cache (in production, use Redis or session storage)
    # For now, we'll include it in the redirect URL as a query parameter
    # In production, store it server-side and verify on callback

    settings = get_settings()
    redirect_uri = f"{request.base_url}{router.prefix}/oauth/{provider}/callback"

    authorization_url = await oauth_provider.get_authorization_url(
        redirect_uri=redirect_uri,
        state=state,
    )

    # In production, store state securely and redirect
    # For now, append state to URL
    return RedirectResponse(
        url=f"{authorization_url}&state={state}",
        status_code=status.HTTP_302_FOUND,
    )


@router.get("/oauth/{provider}/callback")
async def oauth_callback(
    provider: str,
    code: str,
    state: str,
    request: Request,
):
    """
    OAuth callback endpoint.

    Args:
        provider: OAuth provider name
        code: Authorization code from provider
        state: CSRF state token
    """
    try:
        oauth_provider = OAuthProviderFactory.get_provider(provider)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )

    # In production, verify state token from session/cache
    # For now, we'll skip state verification in this placeholder

    settings = get_settings()
    redirect_uri = f"{request.base_url}{router.prefix}/oauth/{provider}/callback"

    try:
        # Exchange code for access token
        token_response = await oauth_provider.get_access_token(
            code=code,
            redirect_uri=redirect_uri,
        )

        access_token = token_response.get("access_token")
        if not access_token:
            raise AuthenticationError("Failed to obtain access token")

        # Get user information from provider
        user_info = await oauth_provider.get_user_info(access_token)

        # Extract user data
        email = user_info.get("email", "")
        user_id = user_info.get("id") or user_info.get("sub", "")
        full_name = user_info.get("name") or user_info.get("full_name")

        if not email:
            raise AuthenticationError("Email not provided by OAuth provider")

        # TODO: Create or get user from database
        # user = await get_or_create_user_from_oauth(
        #     provider=provider,
        #     provider_user_id=str(user_id),
        #     email=email,
        #     full_name=full_name,
        # )

        # Create application tokens
        token_data = {
            "sub": f"oauth_{provider}_{user_id}",
            "email": email,
            "user_id": f"oauth_{provider}_{user_id}",
            "full_name": full_name,
        }
        app_access_token = create_access_token(token_data)
        app_refresh_token = create_refresh_token(token_data)

        # In production, redirect to frontend with tokens
        # For now, return token response
        return TokenResponse(
            access_token=app_access_token,
            refresh_token=app_refresh_token,
            token_type="bearer",
            expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        )

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"OAuth authentication failed: {str(e)}",
        )


@router.get("/oauth/providers")
async def get_oauth_providers():
    """Get list of available OAuth providers."""
    providers = OAuthProviderFactory.get_available_providers()
    return {
        "providers": providers,
        "endpoints": {
            provider: f"/api/v1/auth/oauth/{provider}/authorize" for provider in providers
        },
    }
