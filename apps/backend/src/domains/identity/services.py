"""
Identity domain — Business logic.

Handles registration, authentication, password management,
OAuth flows, preferences, and account deletion lifecycle.
"""

import logging
import secrets
from datetime import UTC, datetime, timedelta

from src.domains.identity.db_models import User

from src.config import get_settings
from src.shared.auth import (
    create_access_token,
    create_refresh_token,
    decode_access_token,
    generate_otp,
    get_password_hash,
    verify_password,
)
from src.shared.exceptions import (
    ForbiddenError,
    NotFoundError,
    UnauthorizedError,
    ValidationError,
)

from .events import (
    emit_user_deletion_cancelled,
    emit_user_deletion_requested,
    emit_user_registered,
    emit_user_verified,
)
from .models import OAuthUserInfo, TokenResponse
from .repository import identity_repo

logger = logging.getLogger(__name__)

# Account deletion cooling-off period
_DELETION_DAYS = 90


# ===========================================================================
# Registration
# ===========================================================================


async def signup(*, email: str, password: str, name: str) -> User:
    """Register a new user with email/password. Returns inactive user pending OTP."""
    existing = await identity_repo.find_by_email(email)
    if existing:
        raise ValidationError("Email already registered")

    hashed = get_password_hash(password)
    otp = generate_otp()
    otp_expires = datetime.now(UTC) + timedelta(minutes=15)

    user = await identity_repo.create_user(
        email=email,
        password_hash=hashed,
        name=name,
        provider="email",
        is_active=False,
        verification_code=otp,
        verification_code_expires_at=otp_expires,
    )

    await emit_user_registered(user.id, email, "email")

    # Send verification email (fire-and-forget)
    try:
        from src.integrations.brevo import send_verification_email

        await send_verification_email(email, otp)
    except Exception as e:
        logger.error(f"Failed to send verification email: {e}")

    return user


async def verify_email(*, email: str, code: str) -> None:
    """Verify OTP code and activate user account."""
    user = await identity_repo.find_by_email(email)
    if not user:
        raise NotFoundError("User", email)

    if user.isActive:
        return  # Already verified, idempotent

    now = datetime.now(UTC)
    if not user.verificationCode or user.verificationCode != code:
        raise ValidationError("Invalid verification code")
    if user.verificationCodeExpiresAt and user.verificationCodeExpiresAt < now:
        raise ValidationError("Verification code expired")

    await identity_repo.activate_user(user.id)
    await emit_user_verified(user.id, email)

    # Send welcome email (fire-and-forget)
    try:
        from src.integrations.brevo import send_welcome_email

        await send_welcome_email(email, user.name)
    except Exception as e:
        logger.error(f"Failed to send welcome email: {e}")


async def resend_otp(*, email: str) -> None:
    """Generate and send a new OTP. Rate-limited to 1 per minute."""
    user = await identity_repo.find_by_email(email)
    if not user:
        return  # Don't reveal whether email exists

    if user.isActive:
        raise ValidationError("Account is already verified")

    # Rate limit: if OTP was sent less than 1 minute ago
    now = datetime.now(UTC)
    if user.verificationCodeExpiresAt:
        remaining = user.verificationCodeExpiresAt - now
        if remaining > timedelta(minutes=14):
            wait = int(remaining.total_seconds() - 14 * 60)
            raise ValidationError(f"Please wait {wait} seconds before resending")

    new_otp = generate_otp()
    new_expiry = now + timedelta(minutes=15)
    await identity_repo.set_verification_code(user.id, new_otp, new_expiry)

    try:
        from src.integrations.brevo import send_verification_email

        await send_verification_email(email, new_otp)
    except Exception as e:
        logger.error(f"Failed to send OTP: {e}")
        raise ValidationError("Error sending email")


# ===========================================================================
# Authentication
# ===========================================================================


async def login(*, email: str, password: str) -> TokenResponse:
    """Authenticate with email/password and return token pair."""
    user = await identity_repo.find_by_email(email)

    if not user or not user.passwordHash or not verify_password(password, user.passwordHash):
        raise UnauthorizedError("Incorrect email or password")

    if not user.isActive:
        raise ValidationError("Account inactive. Please verify your email.")

    return _create_tokens(user.email)


async def refresh_token(*, refresh_token_str: str) -> TokenResponse:
    """Exchange a valid refresh token for a new token pair."""
    try:
        payload = decode_access_token(refresh_token_str)
    except Exception:
        raise UnauthorizedError("Could not validate refresh token")

    if payload.get("type") != "refresh":
        raise UnauthorizedError("Invalid token type")

    email = payload.get("sub")
    if not email:
        raise UnauthorizedError("Invalid token payload")

    user = await identity_repo.find_by_email(email)
    if not user or not user.isActive:
        raise UnauthorizedError("User not found or inactive")

    return _create_tokens(user.email)


def _create_tokens(email: str) -> TokenResponse:
    """Generate access + refresh token pair."""
    settings = get_settings()
    access = create_access_token(
        data={"sub": email},
        expires_delta=timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
    )
    refresh = create_refresh_token(data={"sub": email})
    return TokenResponse(access_token=access, refresh_token=refresh)


# ===========================================================================
# OAuth
# ===========================================================================


async def get_or_create_oauth_user(info: OAuthUserInfo) -> User:
    """Find existing user by OAuth provider or create a new one.

    Handles:
    - Returning users (matched by provider + provider_id)
    - Email-linked accounts (matched by email, activates if pending)
    - Brand new users (creates fresh record)
    """
    # Try provider match first
    existing = await identity_repo.find_by_oauth(info.provider, info.provider_user_id)
    if existing:
        return existing

    # Try email match (link accounts)
    email_user = await identity_repo.find_by_email(info.email)
    if email_user:
        if not email_user.isActive:
            await identity_repo.activate_user(email_user.id)
        return email_user

    # Create new
    user = await identity_repo.create_oauth_user(
        email=info.email,
        name=info.full_name,
        provider=info.provider,
        provider_id=info.provider_user_id,
    )
    await emit_user_registered(user.id, info.email, info.provider)
    return user


# ===========================================================================
# Password Management
# ===========================================================================


async def forgot_password(*, email: str) -> None:
    """Send a password reset OTP (silent if email not found)."""
    user = await identity_repo.find_by_email(email)
    if not user:
        return  # Don't reveal whether email exists

    otp = generate_otp()
    expiry = datetime.now(UTC) + timedelta(minutes=15)
    await identity_repo.set_password_reset_code(user.id, otp, expiry)

    try:
        from src.integrations.brevo import send_password_reset_email

        await send_password_reset_email(email, otp, user.name)
    except Exception as e:
        logger.error(f"Failed to send reset email: {e}")


async def verify_reset_code(*, email: str, code: str) -> None:
    """Validate a password reset OTP."""
    user = await identity_repo.find_by_email(email)
    if not user:
        raise ValidationError("Invalid code or email")

    now = datetime.now(UTC)
    if (
        not user.passwordResetCode
        or user.passwordResetCode != code
        or not user.passwordResetExpiresAt
        or user.passwordResetExpiresAt < now
    ):
        raise ValidationError("Invalid or expired reset code")


async def reset_password(*, email: str, code: str, new_password: str) -> None:
    """Complete password reset (re-validates OTP for security)."""
    user = await identity_repo.find_by_email(email)
    if not user:
        raise ValidationError("Invalid code or email")

    now = datetime.now(UTC)
    if (
        not user.passwordResetCode
        or user.passwordResetCode != code
        or not user.passwordResetExpiresAt
        or user.passwordResetExpiresAt < now
    ):
        raise ValidationError("Invalid or expired reset code")

    hashed = get_password_hash(new_password)
    await identity_repo.clear_password_reset(user.id, hashed)


async def change_password(*, user: User, current_password: str, new_password: str) -> None:
    """Change password for an authenticated user."""
    if not user.passwordHash or not verify_password(current_password, user.passwordHash):
        raise ValidationError("Incorrect current password")

    hashed = get_password_hash(new_password)
    await identity_repo.update_password(user.id, hashed)


# ===========================================================================
# Referrals
# ===========================================================================


async def link_referral(*, user: User, referral_code: str) -> dict:
    """Link a referral code to user (immutable once set)."""
    if user.referredByCode:
        return {
            "message": "User already has a referral code",
            "alreadyReferred": True,
            "existingCode": user.referredByCode,
        }

    code = referral_code.upper().strip()

    # Validate referral code exists and isn't self-referral
    referrer = await identity_repo.find_by_referral_code(code)
    if not referrer or referrer.id == user.id:
        raise ValidationError("Invalid referral code")

    await identity_repo.set_referred_by(user.id, code)

    # Track referral reward via domain event (billing domain handles rewards)
    try:
        from src.shared.events import emit

        await emit(
            "billing.referral_linked",
            {
                "user_id": user.id,
                "referral_code": code,
                "referrer_id": referrer.id,
            },
        )
    except Exception as e:
        logger.error(f"Error tracking referral: {e}")

    return {
        "message": "Referral code linked successfully",
        "alreadyReferred": False,
        "referralCode": code,
    }


# ===========================================================================
# Preferences
# ===========================================================================


async def update_preferences(*, user_id: str, data: dict) -> User:
    """Update user preferences (upsert)."""
    update_data = {k: v for k, v in data.items() if v is not None}
    if not update_data:
        user = await identity_repo.find_by_id(user_id, include_preferences=True)
        if not user:
            raise NotFoundError("User", user_id)
        return user

    return await identity_repo.upsert_preferences(user_id, update_data)


# ===========================================================================
# Account Deletion
# ===========================================================================


def _pending_deletion_payload(user: User) -> dict | None:
    """Compute pending deletion info from user fields."""
    requested = getattr(user, "accountDeletionRequestedAt", None)
    scheduled = getattr(user, "accountDeletionScheduledFor", None)
    if not requested or not scheduled:
        return None

    now = datetime.now(UTC)
    if scheduled <= now:
        return None  # Already past — worker will handle actual deletion

    days = (scheduled - now).days
    return {
        "requestedAt": requested,
        "scheduledFor": scheduled,
        "daysUntilDeletion": max(days, 0),
    }


async def get_deletion_status(user_id: str) -> dict | None:
    """Get current deletion countdown state."""
    user = await identity_repo.find_by_id(user_id)
    if not user:
        raise NotFoundError("User", user_id)
    return _pending_deletion_payload(user)


async def request_deletion(user: User) -> dict:
    """Start 90-day account deletion countdown."""
    if str(user.role) == "ADMIN":
        raise ForbiddenError("Admin accounts cannot be deleted from this endpoint")

    existing = _pending_deletion_payload(user)
    if existing:
        return existing

    now = datetime.now(UTC)
    scheduled = now + timedelta(days=_DELETION_DAYS)
    cancel_token = secrets.token_urlsafe(32)

    updated = await identity_repo.request_deletion(
        user.id,
        requested_at=now,
        scheduled_for=scheduled,
        cancel_token=cancel_token,
    )

    await emit_user_deletion_requested(user.id, scheduled.isoformat())
    logger.info(f"User {user.id} requested deletion for {scheduled.isoformat()}")

    return _pending_deletion_payload(updated)  # type: ignore


async def cancel_deletion(*, user: User, token: str | None = None) -> None:
    """Cancel pending deletion. If token provided, must match."""
    pending = _pending_deletion_payload(user)
    if not pending:
        return  # Nothing to cancel

    if token:
        db_token = getattr(user, "accountDeletionCancelToken", None)
        if not db_token or not secrets.compare_digest(str(token), str(db_token)):
            raise ValidationError("Invalid cancellation token")

    now = datetime.now(UTC)
    await identity_repo.cancel_deletion(user.id, now)
    await emit_user_deletion_cancelled(user.id)
    logger.info(f"User {user.id} cancelled pending account deletion")
