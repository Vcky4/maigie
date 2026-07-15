"""
Identity domain — API routes.

Endpoints for authentication, user profile, preferences,
password management, OAuth, and account deletion.

Mounted at: /api/v1/auth (auth routes) and /api/v1/users (user routes)
"""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from prisma.models import User

from src.shared.auth import CurrentUser

from . import services
from .models import (
    AccountDeletionStatusResponse,
    CancelDeletionRequest,
    ChangePasswordRequest,
    ForgotPasswordRequest,
    LinkReferralRequest,
    LoginRequest,
    MessageResponse,
    PreferencesUpdateRequest,
    RefreshTokenRequest,
    ResendOtpRequest,
    ResetPasswordRequest,
    SignupRequest,
    TokenResponse,
    UserResponse,
    VerifyEmailRequest,
    VerifyResetCodeRequest,
)

logger = logging.getLogger(__name__)

# Two routers: auth (public) and users (authenticated)
auth_router = APIRouter(tags=["auth"])
users_router = APIRouter(tags=["users"])


# ===========================================================================
# AUTH ROUTES (mostly public)
# ===========================================================================


@auth_router.post("/signup", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def signup(data: SignupRequest):
    """Register a new user account."""
    user = await services.signup(email=data.email, password=data.password, name=data.name)
    return user


@auth_router.post("/verify-email", response_model=MessageResponse)
async def verify_email(data: VerifyEmailRequest):
    """Verify email with OTP code."""
    await services.verify_email(email=data.email, code=data.code)
    return MessageResponse(message="Email verified successfully")


@auth_router.post("/resend-otp", response_model=MessageResponse)
async def resend_otp(data: ResendOtpRequest):
    """Resend verification OTP."""
    await services.resend_otp(email=data.email)
    return MessageResponse(message="If this account exists, a new code has been sent.")


@auth_router.post("/login", response_model=TokenResponse)
async def login_form(form_data: Annotated[OAuth2PasswordRequestForm, Depends()]):
    """OAuth2-compatible form login (for Swagger UI)."""
    return await services.login(email=form_data.username, password=form_data.password)


@auth_router.post("/login/json", response_model=TokenResponse)
async def login_json(data: LoginRequest):
    """JSON login for frontend apps."""
    return await services.login(email=data.email, password=data.password)


@auth_router.post("/refresh", response_model=TokenResponse)
async def refresh(data: RefreshTokenRequest):
    """Refresh access token."""
    return await services.refresh_token(refresh_token_str=data.refresh_token)


@auth_router.get("/me", response_model=UserResponse)
async def get_me(current_user: CurrentUser):
    """Get current authenticated user."""
    from .repository import identity_repo

    user = await identity_repo.find_by_id(current_user.id, include_preferences=True)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    response = UserResponse.model_validate(user, from_attributes=True)
    payload = response.model_dump()

    # Inject pending deletion info
    deletion_info = await services.get_deletion_status(current_user.id)
    payload["pendingDeletion"] = deletion_info
    return payload


@auth_router.post("/logout", response_model=MessageResponse)
async def logout():
    """End user session (stateless — client discards tokens)."""
    return MessageResponse(message="Successfully logged out")


# --- Password Reset ---


@auth_router.post("/forgot-password", response_model=MessageResponse)
async def forgot_password(data: ForgotPasswordRequest):
    """Send password reset OTP."""
    await services.forgot_password(email=data.email)
    return MessageResponse(message="If an account exists, a reset code has been sent.")


@auth_router.post("/verify-reset-code", response_model=MessageResponse)
async def verify_reset_code(data: VerifyResetCodeRequest):
    """Validate reset code before showing password form."""
    await services.verify_reset_code(email=data.email, code=data.code)
    return MessageResponse(message="Code is valid")


@auth_router.post("/reset-password", response_model=MessageResponse)
async def reset_password(data: ResetPasswordRequest):
    """Complete password reset."""
    await services.reset_password(email=data.email, code=data.code, new_password=data.new_password)
    return MessageResponse(message="Password reset successfully. You can now login.")


@auth_router.post("/change-password", response_model=MessageResponse)
async def change_password(data: ChangePasswordRequest, current_user: CurrentUser):
    """Change password for authenticated user."""
    await services.change_password(
        user=current_user,
        current_password=data.current_password,
        new_password=data.new_password,
    )
    return MessageResponse(message="Password changed successfully")


# --- Referrals ---


@auth_router.post("/link-referral")
async def link_referral(data: LinkReferralRequest, current_user: CurrentUser):
    """Link a referral code to the current user."""
    return await services.link_referral(user=current_user, referral_code=data.referral_code)


# --- OAuth ---


@auth_router.get("/oauth/providers")
async def get_oauth_providers():
    """List available OAuth providers."""
    return {"providers": ["google"]}


# NOTE: OAuth authorize + callback endpoints require request context and
# provider-specific logic (state encoding, redirect URI construction).
# These will be migrated from routes/auth.py in a follow-up pass once
# the Google Calendar OAuth is extracted to the integrations layer.


# ===========================================================================
# USER ROUTES (authenticated)
# ===========================================================================


@users_router.put("/preferences", response_model=UserResponse)
async def update_preferences(data: PreferencesUpdateRequest, current_user: CurrentUser):
    """Update user preferences."""
    update_data = data.model_dump(exclude_unset=True)
    user = await services.update_preferences(user_id=current_user.id, data=update_data)
    return user


# --- Account Deletion ---


@users_router.get("/me/delete-request", response_model=AccountDeletionStatusResponse)
async def get_deletion_status(current_user: CurrentUser):
    """Get pending account deletion state."""
    info = await services.get_deletion_status(current_user.id)
    if not info:
        return AccountDeletionStatusResponse(pending=False)
    return AccountDeletionStatusResponse(pending=True, **info)


@users_router.post("/me/delete-request", response_model=AccountDeletionStatusResponse)
async def request_deletion(current_user: CurrentUser):
    """Start 90-day account deletion countdown."""
    from .repository import identity_repo

    user = await identity_repo.find_by_id(current_user.id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    info = await services.request_deletion(user)
    if not info:
        raise HTTPException(status_code=500, detail="Failed to start deletion process")
    return AccountDeletionStatusResponse(pending=True, **info)


@users_router.post("/me/delete-request/cancel", response_model=AccountDeletionStatusResponse)
async def cancel_deletion(body: CancelDeletionRequest, current_user: CurrentUser):
    """Cancel pending account deletion."""
    from .repository import identity_repo

    user = await identity_repo.find_by_id(current_user.id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    await services.cancel_deletion(user=user, token=body.token)
    return AccountDeletionStatusResponse(pending=False)


@users_router.delete("/me", status_code=status.HTTP_204_NO_CONTENT)
async def delete_account_alias(current_user: CurrentUser):
    """Backward-compatible: starts deletion schedule instead of immediate delete."""
    from .repository import identity_repo

    user = await identity_repo.find_by_id(current_user.id)
    if user:
        await services.request_deletion(user)
    return None
