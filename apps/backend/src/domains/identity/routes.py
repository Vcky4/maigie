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

from src.domains.identity.db_models import User
from src.shared.auth import CurrentUser

from . import services
from .models import (
    AccountDeletionStatusResponse,
    CancelDeletionRequest,
    ChangePasswordRequest,
    DeviceTimezoneRequest,
    DeviceTokenRequest,
    DeviceTokenResponse,
    ForgotPasswordRequest,
    LinkReferralRequest,
    LoginRequest,
    MessageResponse,
    PreferencesUpdateRequest,
    RefreshTokenRequest,
    ResendOtpRequest,
    ResetPasswordRequest,
    SignupRequest,
    TimezoneResponse,
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

# `GET /oauth/providers` lives in `oauth_routes.py` with the rest of the OAuth
# surface. A duplicate handler here registered the same path twice under the
# same `/auth` prefix, which produced a duplicate OpenAPI operation id and made
# generated client operation names ambiguous. The duplicate was unreachable at
# runtime, so removing it does not change behaviour.


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


@users_router.get("/me/timezone", response_model=TimezoneResponse)
async def get_timezone(current_user: CurrentUser):
    """The learner's timezone and whether it is known rather than assumed.

    `isKnown` is `false` until a timezone has actually been captured. The
    `timezone` field still carries a usable value in that case, but it is a
    default, so nothing should tell the learner anything about their local time
    based on it.
    """
    return await services.get_timezone(user_id=current_user.id)


@users_router.put("/me/timezone", response_model=TimezoneResponse)
async def record_device_timezone(data: DeviceTimezoneRequest, current_user: CurrentUser):
    """Record the timezone reported by this device.

    Idempotent, and safe to call on every app load — which is how it gets
    populated, since nothing has ever asked the learner directly.

    A timezone the learner set themselves is **not** overwritten: this returns the
    stored value unchanged in that case rather than reverting a deliberate choice.
    """
    return await services.record_device_timezone(user_id=current_user.id, timezone=data.timezone)


# --- Push notification devices ---


@users_router.put("/me/device-tokens", response_model=DeviceTokenResponse)
async def register_device_token(data: DeviceTokenRequest, current_user: CurrentUser):
    """Register this device to receive push notifications.

    **Nothing has ever written a `DeviceToken`.** The sender, the payload builders, the dead-token pruning
    and every notification that says "we will tell you" were all built and complete, and every push
    returned `no_tokens` — so the entire notification path has been undeliverable for its whole life.
    This is the missing half.

    `PUT` rather than `POST`, because registering the same device twice is not creating a second one. Safe
    to call on every app launch, which is how these rows will actually appear — the same pattern
    `PUT /me/timezone` relies on for the same reason.

    **Re-registering a token that belonged to someone else reassigns it.** FCM issues one token per app
    install, so a second learner signing in on the same phone presents the same token. Leaving it on the
    first learner would deliver their private notifications to the second learner's device, so the token
    always follows whoever is signed in.
    """
    from .repository import identity_repo

    await identity_repo.upsert_device_token(
        user_id=current_user.id, token=data.token, platform=data.platform
    )
    return DeviceTokenResponse(
        platform=data.platform,
        deviceCount=await identity_repo.count_device_tokens(current_user.id),
    )


@users_router.delete("/me/device-tokens", status_code=status.HTTP_204_NO_CONTENT)
async def unregister_device_token(data: DeviceTokenRequest, current_user: CurrentUser):
    """Stop sending push to this device. **Clients must call this on sign-out.**

    A token left registered keeps the device attached to the learner who signed out, so the next person to
    use that phone receives their notifications. Dead-token pruning does not help: the token is still
    valid, it is the *attribution* that is wrong.

    `204` whether or not a row was removed. The outcome a client needs is "this device is not registered",
    and that is true either way — reporting `404` for an already-unregistered token would make a
    correctly-idempotent sign-out look like a failure.
    """
    from .repository import identity_repo

    await identity_repo.delete_device_token(user_id=current_user.id, token=data.token)


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
