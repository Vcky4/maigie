"""
Identity domain — Pydantic request/response schemas.

All schemas for auth, user profile, preferences, account deletion,
and OAuth flows live here.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_serializer

# ===========================================================================
# Auth — Requests
# ===========================================================================


class SignupRequest(BaseModel):
    """User registration."""

    email: EmailStr
    password: str = Field(min_length=8, description="Minimum 8 characters")
    name: str = Field(..., description="Full name")
    referral_code: str | None = Field(
        None, alias="referralCode", description="Optional referral code"
    )

    model_config = ConfigDict(populate_by_name=True)


class LoginRequest(BaseModel):
    """JSON login."""

    email: EmailStr
    password: str


class VerifyEmailRequest(BaseModel):
    """OTP email verification."""

    email: EmailStr
    code: str


class ResendOtpRequest(BaseModel):
    """Resend verification OTP."""

    email: EmailStr


class RefreshTokenRequest(BaseModel):
    """Token refresh."""

    refresh_token: str


class ForgotPasswordRequest(BaseModel):
    """Initiate password reset."""

    email: EmailStr


class VerifyResetCodeRequest(BaseModel):
    """Validate reset OTP before showing new password form."""

    email: EmailStr
    code: str


class ResetPasswordRequest(BaseModel):
    """Complete password reset with OTP + new password."""

    email: EmailStr
    code: str
    new_password: str = Field(min_length=8)


class ChangePasswordRequest(BaseModel):
    """Change password for authenticated users."""

    current_password: str
    new_password: str = Field(min_length=8)


class LinkReferralRequest(BaseModel):
    """Link a referral code post-signup."""

    referral_code: str = Field(..., alias="referralCode")

    model_config = ConfigDict(populate_by_name=True)


class NativeGoogleCallbackRequest(BaseModel):
    """Google ID token from native mobile SDK."""

    id_token: str


# ===========================================================================
# Auth — Responses
# ===========================================================================


class TokenResponse(BaseModel):
    """JWT token pair."""

    access_token: str
    refresh_token: str | None = None
    token_type: str = "bearer"


class OAuthAuthorizeResponse(BaseModel):
    """OAuth authorization URL for frontend redirect."""

    authorization_url: str
    state: str
    provider: str


class MessageResponse(BaseModel):
    """Generic success message."""

    message: str


# ===========================================================================
# User Profile — Responses
# ===========================================================================


class UserPreferencesResponse(BaseModel):
    """User preferences."""

    theme: str = "light"
    language: str = "en"
    notifications: bool = True
    study_goals: dict | None = Field(
        None, validation_alias="studyGoals", serialization_alias="studyGoals"
    )
    timezone: str = "UTC"
    email_morning_schedule: bool = Field(
        True, validation_alias="emailMorningSchedule", serialization_alias="emailMorningSchedule"
    )
    email_schedule_reminder: bool = Field(
        True,
        validation_alias="emailScheduleReminder",
        serialization_alias="emailScheduleReminder",
    )
    email_weekly_tips: bool = Field(
        True, validation_alias="emailWeeklyTips", serialization_alias="emailWeeklyTips"
    )

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class PendingDeletionResponse(BaseModel):
    """Account deletion countdown info."""

    requestedAt: datetime
    scheduledFor: datetime
    daysUntilDeletion: int


class UserResponse(BaseModel):
    """Authenticated user profile."""

    id: str
    email: EmailStr
    name: str | None = None
    tier: str
    role: str
    isActive: bool = Field(validation_alias="is_active")
    isOnboarded: bool = Field(default=False, validation_alias="is_onboarded")
    adminStaffRole: str | None = Field(default=None, validation_alias="admin_staff_role")
    preferences: UserPreferencesResponse | None = None
    paymentProvider: str | None = Field(default=None, validation_alias="payment_provider")
    subscriptionCurrentPeriodEnd: datetime | None = Field(
        default=None, validation_alias="subscription_current_period_end"
    )
    pendingDeletion: PendingDeletionResponse | None = None

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    @field_serializer("adminStaffRole")
    def serialize_admin_staff_role(self, v, _info):
        if v is None:
            return None
        if isinstance(v, str):
            return v
        return str(getattr(v, "value", None) or getattr(v, "name", None) or v)

    @field_serializer("role")
    def serialize_role(self, v, _info):
        if v is None:
            return "USER"
        if isinstance(v, str):
            return v
        return str(getattr(v, "value", None) or getattr(v, "name", None) or v)

    @field_serializer("tier")
    def serialize_tier(self, v, _info):
        if v is None:
            return "FREE"
        if isinstance(v, str):
            return v
        return str(getattr(v, "value", None) or getattr(v, "name", None) or v)


# ===========================================================================
# User Profile — Requests
# ===========================================================================


class PreferencesUpdateRequest(BaseModel):
    """Update user preferences (all fields optional).

    A `timezone` set through here is recorded as `MANUAL`: it came from the
    learner, so it outranks anything a device reports later.
    """

    theme: str | None = None
    language: str | None = None
    notifications: bool | None = None
    studyGoals: dict | None = None
    timezone: str | None = None
    emailMorningSchedule: bool | None = None
    emailScheduleReminder: bool | None = None
    emailWeeklyTips: bool | None = None


class DeviceTimezoneRequest(BaseModel):
    """A timezone reported by the learner's device.

    An IANA name (`Europe/London`), not an offset: offsets are ambiguous across
    daylight saving, so storing one would make a summer reading wrong in winter.
    Clients read it from `Intl.DateTimeFormat().resolvedOptions().timeZone`.
    """

    timezone: str = Field(min_length=1, max_length=64)


class DeviceTokenRequest(BaseModel):
    """A push token reported by the learner's device.

    Until this existed nothing wrote `DeviceToken`, so every push the application sent returned
    `no_tokens`: the whole notification path was built, correct, and delivered to nobody.

    `platform` is a closed set rather than free text. The column has no CHECK constraint, and the sender
    builds per-platform payloads — an Android config block and an APNs one — so a value it does not
    recognise is a device that silently receives nothing.
    """

    token: str = Field(min_length=1, max_length=512)
    platform: Literal["ANDROID", "IOS", "WEB"]


class DeviceTokenResponse(BaseModel):
    """Confirmation that a device is registered, and how many are.

    `deviceCount` is returned so a client can distinguish "push is off for this learner" from "push is on
    and nothing has been sent", which is otherwise indistinguishable from the outside — and the reason the
    notification path went unnoticed as undeliverable for so long.
    """

    platform: str
    deviceCount: int = 0


class TimezoneResponse(BaseModel):
    """The learner's timezone and where it came from.

    `source` is `null` when the timezone has never been captured, in which case
    `timezone` is a default rather than an observation and nothing should claim a
    local time from it.
    """

    timezone: str
    source: str | None = None
    capturedAt: datetime | None = None
    isKnown: bool = False


# ===========================================================================
# Account Deletion
# ===========================================================================


class AccountDeletionStatusResponse(BaseModel):
    """Account deletion state."""

    pending: bool
    requestedAt: datetime | None = None
    scheduledFor: datetime | None = None
    daysUntilDeletion: int | None = None


class CancelDeletionRequest(BaseModel):
    """Cancel pending account deletion."""

    token: str | None = None


# ===========================================================================
# Internal (service-to-service)
# ===========================================================================


class OAuthUserInfo(BaseModel):
    """Normalized user info from any OAuth provider."""

    email: str
    full_name: str | None = None
    provider: str
    provider_user_id: str
    referral_code: str | None = None
