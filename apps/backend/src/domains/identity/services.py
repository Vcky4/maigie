"""
Identity domain — Business logic.

Handles registration, authentication, password management,
OAuth flows, preferences, and account deletion lifecycle.
"""

import logging
import secrets
from datetime import UTC, datetime, timedelta

from src.config import get_settings
from src.domains.identity.db_models import User
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
from .exceptions import EmailVerificationRequiredError
from .models import OAuthUserInfo, TokenResponse
from .repository import identity_repo

logger = logging.getLogger(__name__)


def _tz_safe(dt: datetime | None) -> datetime | None:
    """Coerce naive datetimes to UTC for safe comparison."""
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


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

    if user.is_active:
        return  # Already verified, idempotent

    now = datetime.now(UTC)
    if not user.verification_code or user.verification_code != code:
        raise ValidationError("Invalid verification code")
    if (
        _tz_safe(user.verification_code_expires_at)
        and _tz_safe(user.verification_code_expires_at) < now
    ):
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

    if user.is_active:
        raise ValidationError("Account is already verified")

    # Rate limit: if OTP was sent less than 1 minute ago
    now = datetime.now(UTC)
    if _tz_safe(user.verification_code_expires_at):
        remaining = _tz_safe(user.verification_code_expires_at) - now
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


# ===========================================================================
# Authentication
# ===========================================================================


async def login(*, email: str, password: str) -> TokenResponse:
    """Authenticate with email/password and return token pair."""
    user = await identity_repo.find_by_email(email)

    if not user or not user.password_hash or not verify_password(password, user.password_hash):
        raise UnauthorizedError("Incorrect email or password")

    if not user.is_active:
        raise EmailVerificationRequiredError()

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
    if not user or not user.is_active:
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
        if not email_user.is_active:
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
        not user.password_reset_code
        or user.password_reset_code != code
        or not user.password_reset_expires_at
        or _tz_safe(user.password_reset_expires_at) < now
    ):
        raise ValidationError("Invalid or expired reset code")


async def reset_password(*, email: str, code: str, new_password: str) -> None:
    """Complete password reset (re-validates OTP for security)."""
    user = await identity_repo.find_by_email(email)
    if not user:
        raise ValidationError("Invalid code or email")

    now = datetime.now(UTC)
    if (
        not user.password_reset_code
        or user.password_reset_code != code
        or not user.password_reset_expires_at
        or _tz_safe(user.password_reset_expires_at) < now
    ):
        raise ValidationError("Invalid or expired reset code")

    hashed = get_password_hash(new_password)
    await identity_repo.clear_password_reset(user.id, hashed)


async def change_password(*, user: User, current_password: str, new_password: str) -> None:
    """Change password for an authenticated user."""
    if not user.password_hash or not verify_password(current_password, user.password_hash):
        raise ValidationError("Incorrect current password")

    hashed = get_password_hash(new_password)
    await identity_repo.update_password(user.id, hashed)


# ===========================================================================
# Referrals
# ===========================================================================


async def link_referral(*, user: User, referral_code: str) -> dict:
    """Link a referral code to user (immutable once set)."""
    if user.referred_by_code:
        return {
            "message": "User already has a referral code",
            "alreadyReferred": True,
            "existingCode": user.referred_by_code,
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

    # A timezone the learner set themselves is an observation, and outranks a
    # device report. Stamped here rather than in the route so every path that
    # writes a preference records provenance the same way.
    if "timezone" in update_data:
        _validate_iana_timezone(update_data["timezone"])
        update_data["timezoneSource"] = "MANUAL"
        update_data["timezoneCapturedAt"] = datetime.now(UTC)

    return await identity_repo.upsert_preferences(user_id, update_data)


def _validate_iana_timezone(name: str) -> None:
    """Reject anything that is not a real IANA zone.

    Stored unvalidated, a typo becomes a permanent silent fallback to UTC at every
    read site, which is indistinguishable from never having been captured. Better
    to refuse the write.
    """
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValidationError(f"'{name}' is not a recognised timezone")


async def _read_timezone_state(user_id: str) -> dict | None:
    """The three timezone columns, read directly.

    Not via `find_by_id(include_preferences=True)`: that flag is accepted and
    ignored — the query never eager-loads the relationship — so touching
    `user.preferences` afterwards either lazy-loads on a closed async session or
    silently yields nothing. Selecting the columns is both correct and cheaper.

    Returns `None` when the learner has no preferences row at all, which is
    distinct from having one that was never populated.
    """
    from sqlalchemy import select

    from src.domains.identity.db_models import UserPreferences
    from src.shared.database import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        stmt = select(
            UserPreferences.timezone,
            UserPreferences.timezone_source,
            UserPreferences.timezone_captured_at,
        ).where(UserPreferences.user_id == user_id)
        result = await session.execute(stmt)
        row = result.one_or_none()

    if row is None:
        return None
    return {"timezone": row[0], "source": row[1], "capturedAt": row[2]}


async def record_device_timezone(*, user_id: str, timezone: str) -> dict:
    """Record a device-reported timezone, without overriding a stated one.

    Called on sign-in and app load, so it fires often and unattended. That is
    exactly why it must not clobber `MANUAL`: a learner who deliberately set their
    zone while travelling would otherwise have it silently reverted by their own
    device on the next page load.

    Returns the resolved state rather than the user, because the caller only needs
    to know what is now stored and whether it is trustworthy.
    """
    _validate_iana_timezone(timezone)

    state = await _read_timezone_state(user_id)

    # The learner's own choice wins. Reported unchanged rather than as an error:
    # the device did nothing wrong, its report is simply not the better source.
    if state and state["source"] == "MANUAL":
        return {**state, "isKnown": True}

    now = datetime.now(UTC)
    await identity_repo.upsert_preferences(
        user_id,
        {
            "timezone": timezone,
            "timezoneSource": "DEVICE",
            "timezoneCapturedAt": now,
        },
    )
    return {
        "timezone": timezone,
        "source": "DEVICE",
        "capturedAt": now,
        "isKnown": True,
    }


async def get_timezone(*, user_id: str) -> dict:
    """The learner's timezone and whether it is actually known.

    `isKnown` false means `timezone` is the column default rather than an
    observation, so nothing should assert a local time from it.
    """
    state = await _read_timezone_state(user_id)
    if state is None:
        return {"timezone": "UTC", "source": None, "capturedAt": None, "isKnown": False}
    return {**state, "isKnown": state["source"] in ("DEVICE", "MANUAL")}


# ===========================================================================
# Account Deletion
# ===========================================================================


def _pending_deletion_payload(user: User) -> dict | None:
    """Compute pending deletion info from user fields."""
    requested = getattr(user, "account_deletion_requested_at", None)
    scheduled = getattr(user, "account_deletion_scheduled_for", None)
    if not requested or not scheduled:
        return None

    now = datetime.now(UTC)
    scheduled_aware = _tz_safe(scheduled)
    if scheduled_aware <= now:
        return None  # Already past — worker will handle actual deletion

    days = (scheduled_aware - now).days
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
        db_token = getattr(user, "account_deletion_cancel_token", None)
        if not db_token or not secrets.compare_digest(str(token), str(db_token)):
            raise ValidationError("Invalid cancellation token")

    now = datetime.now(UTC)
    await identity_repo.cancel_deletion(user.id, now)
    await emit_user_deletion_cancelled(user.id)
    logger.info(f"User {user.id} cancelled pending account deletion")
