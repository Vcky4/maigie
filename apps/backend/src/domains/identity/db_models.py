"""
Identity domain — SQLAlchemy models.

Maps to existing PostgreSQL tables created by Prisma.
Column names use camelCase to match the existing schema exactly.
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.shared.database.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class User(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """User model — the central identity entity."""

    __tablename__ = "User"

    email: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    password_hash: Mapped[str | None] = mapped_column("passwordHash", String, nullable=True)
    name: Mapped[str | None] = mapped_column(String, nullable=True)
    provider: Mapped[str | None] = mapped_column(String, nullable=True)
    provider_id: Mapped[str | None] = mapped_column("providerId", String, nullable=True)
    tier: Mapped[str] = mapped_column(String, default="FREE", server_default="FREE")
    role: Mapped[str] = mapped_column(String, default="USER", server_default="USER")
    admin_staff_role: Mapped[str | None] = mapped_column("adminStaffRole", String, nullable=True)
    is_active: Mapped[bool] = mapped_column(
        "isActive", Boolean, default=True, server_default="true"
    )
    is_onboarded: Mapped[bool] = mapped_column(
        "isOnboarded", Boolean, default=False, server_default="false"
    )

    # Verification
    verification_code: Mapped[str | None] = mapped_column("verificationCode", String, nullable=True)
    verification_code_expires_at: Mapped[datetime | None] = mapped_column(
        "verificationCodeExpiresAt", DateTime(timezone=True), nullable=True
    )
    password_reset_code: Mapped[str | None] = mapped_column(
        "passwordResetCode", String, nullable=True
    )
    password_reset_expires_at: Mapped[datetime | None] = mapped_column(
        "passwordResetExpiresAt", DateTime(timezone=True), nullable=True
    )

    # Billing (Stripe)
    stripe_customer_id: Mapped[str | None] = mapped_column(
        "stripeCustomerId", String, unique=True, nullable=True
    )
    stripe_subscription_id: Mapped[str | None] = mapped_column(
        "stripeSubscriptionId", String, unique=True, nullable=True
    )
    stripe_subscription_status: Mapped[str | None] = mapped_column(
        "stripeSubscriptionStatus", String, nullable=True
    )
    stripe_price_id: Mapped[str | None] = mapped_column("stripePriceId", String, nullable=True)
    subscription_current_period_start: Mapped[datetime | None] = mapped_column(
        "subscriptionCurrentPeriodStart", DateTime(timezone=True), nullable=True
    )
    subscription_current_period_end: Mapped[datetime | None] = mapped_column(
        "subscriptionCurrentPeriodEnd", DateTime(timezone=True), nullable=True
    )

    # Billing (Paystack)
    paystack_customer_code: Mapped[str | None] = mapped_column(
        "paystackCustomerCode", String, unique=True, nullable=True
    )
    paystack_subscription_code: Mapped[str | None] = mapped_column(
        "paystackSubscriptionCode", String, unique=True, nullable=True
    )
    payment_provider: Mapped[str | None] = mapped_column("paymentProvider", String, nullable=True)

    # Billing (Google Play)
    google_play_purchase_token: Mapped[str | None] = mapped_column(
        "googlePlayPurchaseToken", String, unique=True, nullable=True
    )
    google_play_product_id: Mapped[str | None] = mapped_column(
        "googlePlayProductId", String, nullable=True
    )

    # Usage meter (MAIGIE_PLUS_COMMERCIAL_PLAN.md §6.2)
    #
    # Four columns replaced nine. The nine described a monthly period, a daily sub-period, a soft
    # cap, a hard cap and a purchased balance — five interacting quantities for one question ("may
    # this operation run?"). These two pairs answer it: a rolling 5-hour window that the product is
    # built around, and a calendar month that exists only as an abuse backstop.
    #
    # `usageWindowStartedAt` is null for a learner who has never run a billable operation, and the
    # meter treats null as "elapsed", so the first operation opens the window. There is no
    # initialisation step and therefore no user who can be missing one.
    usage_window_started_at: Mapped[datetime | None] = mapped_column(
        "usageWindowStartedAt", DateTime(timezone=True), nullable=True
    )
    usage_window_units_used: Mapped[int] = mapped_column(
        "usageWindowUnitsUsed", Integer, default=0, server_default="0"
    )
    usage_month_started_at: Mapped[datetime | None] = mapped_column(
        "usageMonthStartedAt", DateTime(timezone=True), nullable=True
    )
    usage_month_units_used: Mapped[int] = mapped_column(
        "usageMonthUnitsUsed", Integer, default=0, server_default="0"
    )

    # Live voice, on its own balance rather than on the window above (§6.3). A voice minute is 40× a
    # Flash-Lite chat turn, so one allowance covering both had to be priced for the voice case and was
    # spent almost entirely on the text case.
    #
    # `voice_seconds_remaining` is *granted* and belongs to `voice_allowance_source_id`. It is
    # re-derived on read: when the source the entitlement names stops matching the stored one, the
    # balance resets to that source's allowance. A renewal therefore re-grants and a pass expiring
    # takes its minutes with it, with no sweep job in between — see `billing.services.voice_service`.
    #
    # `voice_seconds_purchased` is *bought*, and is the one quantity here that never resets: a learner
    # who paid for 30 minutes owns them across a period boundary. Spending takes granted seconds
    # first, because those are the ones that expire.
    voice_seconds_remaining: Mapped[int] = mapped_column(
        "voiceSecondsRemaining", Integer, default=0, server_default="0"
    )
    voice_seconds_purchased: Mapped[int] = mapped_column(
        "voiceSecondsPurchased", Integer, default=0, server_default="0"
    )
    voice_allowance_source_id: Mapped[str | None] = mapped_column(
        "voiceAllowanceSourceId", String, nullable=True
    )

    # Feature usage (FREE tier)
    file_uploads_count: Mapped[int] = mapped_column(
        "fileUploadsCount", Integer, default=0, server_default="0"
    )
    file_uploads_period_start: Mapped[datetime | None] = mapped_column(
        "fileUploadsPeriodStart", DateTime(timezone=True), nullable=True
    )
    summary_generations_count: Mapped[int] = mapped_column(
        "summaryGenerationsCount", Integer, default=0, server_default="0"
    )
    summary_generations_period_start: Mapped[datetime | None] = mapped_column(
        "summaryGenerationsPeriodStart", DateTime(timezone=True), nullable=True
    )

    # Google Calendar
    google_calendar_access_token: Mapped[str | None] = mapped_column(
        "googleCalendarAccessToken", String, nullable=True
    )
    google_calendar_refresh_token: Mapped[str | None] = mapped_column(
        "googleCalendarRefreshToken", String, nullable=True
    )
    google_calendar_token_expires_at: Mapped[datetime | None] = mapped_column(
        "googleCalendarTokenExpiresAt", DateTime(timezone=True), nullable=True
    )
    google_calendar_sync_enabled: Mapped[bool] = mapped_column(
        "googleCalendarSyncEnabled", Boolean, default=False, server_default="false"
    )
    google_calendar_id: Mapped[str | None] = mapped_column(
        "googleCalendarId", String, nullable=True
    )

    # Referral
    referred_by_code: Mapped[str | None] = mapped_column("referredByCode", String, nullable=True)
    referral_code: Mapped[str | None] = mapped_column(
        "referralCode", String, unique=True, nullable=True
    )

    # Account deletion
    account_deletion_requested_at: Mapped[datetime | None] = mapped_column(
        "accountDeletionRequestedAt", DateTime(timezone=True), nullable=True
    )
    account_deletion_scheduled_for: Mapped[datetime | None] = mapped_column(
        "accountDeletionScheduledFor", DateTime(timezone=True), nullable=True
    )
    account_deletion_cancel_token: Mapped[str | None] = mapped_column(
        "accountDeletionCancelToken", String, nullable=True
    )
    account_deletion_reminder_30_sent_at: Mapped[datetime | None] = mapped_column(
        "accountDeletionReminder30SentAt", DateTime(timezone=True), nullable=True
    )
    account_deletion_reminder_7_sent_at: Mapped[datetime | None] = mapped_column(
        "accountDeletionReminder7SentAt", DateTime(timezone=True), nullable=True
    )
    account_deletion_last_cancelled_at: Mapped[datetime | None] = mapped_column(
        "accountDeletionLastCancelledAt", DateTime(timezone=True), nullable=True
    )

    # Profile
    profile_image_url: Mapped[str | None] = mapped_column("profileImageUrl", String, nullable=True)
    profile_image_status: Mapped[str | None] = mapped_column(
        "profileImageStatus", String, nullable=True
    )

    # Activity tracking
    last_seen_at: Mapped[datetime | None] = mapped_column(
        "lastSeenAt", DateTime(timezone=True), nullable=True, index=True
    )
    last_seen_platform: Mapped[str | None] = mapped_column(
        "lastSeenPlatform", String, nullable=True
    )

    # --- Relationships ---
    preferences: Mapped[Optional["UserPreferences"]] = relationship(
        "UserPreferences", back_populates="user", uselist=False, lazy="selectin"
    )

    def __repr__(self) -> str:
        return f"<User id={self.id} email={self.email}>"


class UserPreferences(Base):
    """User preferences."""

    __tablename__ = "UserPreferences"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: __import__("uuid").uuid4().hex
    )
    user_id: Mapped[str] = mapped_column(
        "userId", String, ForeignKey("User.id", ondelete="CASCADE"), unique=True
    )

    theme: Mapped[str] = mapped_column(String, default="light", server_default="light")
    language: Mapped[str] = mapped_column(String, default="en", server_default="en")
    notifications: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    study_goals: Mapped[dict | None] = mapped_column("studyGoals", JSON, nullable=True)

    # Email notification preferences
    #
    # `timezone` is NOT NULL with a "UTC" default and predates anything asking for
    # it, so the value alone cannot distinguish "this learner is in UTC" from "we
    # never asked". `timezone_source` is what makes that readable: NULL means the
    # column holds a default rather than an observation. Use
    # `services.timezone_resolver` rather than reading these three directly.
    timezone: Mapped[str] = mapped_column(String, default="UTC", server_default="UTC")
    # DEVICE | MANUAL, or NULL for never captured. A learner's stated zone
    # outranks a device-reported one, which is only expressible with the source.
    timezone_source: Mapped[str | None] = mapped_column("timezoneSource", String, nullable=True)
    timezone_captured_at: Mapped[datetime | None] = mapped_column(
        "timezoneCapturedAt", DateTime(timezone=True), nullable=True
    )
    email_morning_schedule: Mapped[bool] = mapped_column(
        "emailMorningSchedule", Boolean, default=True, server_default="true"
    )
    email_schedule_reminder: Mapped[bool] = mapped_column(
        "emailScheduleReminder", Boolean, default=True, server_default="true"
    )
    email_weekly_tips: Mapped[bool] = mapped_column(
        "emailWeeklyTips", Boolean, default=True, server_default="true"
    )

    # Push notification preferences
    push_schedule_reminder: Mapped[bool] = mapped_column(
        "pushScheduleReminder", Boolean, default=True, server_default="true"
    )
    push_study_tips: Mapped[bool] = mapped_column(
        "pushStudyTips", Boolean, default=True, server_default="true"
    )

    # Relationship
    user: Mapped["User"] = relationship("User", back_populates="preferences")


class OAuthClient(Base, TimestampMixin):
    """OAuth 2.1 client registration."""

    __tablename__ = "OAuthClient"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: __import__("uuid").uuid4().hex
    )
    client_id: Mapped[str] = mapped_column(
        "clientId", String, unique=True, nullable=False, index=True
    )
    client_secret: Mapped[str | None] = mapped_column("clientSecret", String, nullable=True)
    client_name: Mapped[str] = mapped_column("clientName", String, nullable=False)
    redirect_uris: Mapped[list] = mapped_column("redirectUris", JSON, default=list)
    client_uri: Mapped[str | None] = mapped_column("clientUri", String, nullable=True)
    logo_uri: Mapped[str | None] = mapped_column("logoUri", String, nullable=True)
    token_endpoint_auth_method: Mapped[str] = mapped_column(
        "tokenEndpointAuthMethod", String, default="none"
    )
    grant_types: Mapped[list] = mapped_column(
        "grantTypes", JSON, default=lambda: ["authorization_code", "refresh_token"]
    )
    response_types: Mapped[list] = mapped_column("responseTypes", JSON, default=lambda: ["code"])
    user_id: Mapped[str | None] = mapped_column(
        "userId", String, ForeignKey("User.id", ondelete="SET NULL"), nullable=True
    )


class OAuthCode(Base):
    """OAuth authorization code."""

    __tablename__ = "OAuthCode"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: __import__("uuid").uuid4().hex
    )
    code: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)
    client_id: Mapped[str] = mapped_column("clientId", String, nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(
        "userId", String, ForeignKey("User.id", ondelete="CASCADE"), index=True
    )
    redirect_uri: Mapped[str] = mapped_column("redirectUri", String, nullable=False)
    scope: Mapped[str | None] = mapped_column(String, nullable=True)
    code_challenge: Mapped[str | None] = mapped_column("codeChallenge", String, nullable=True)
    code_challenge_method: Mapped[str | None] = mapped_column(
        "codeChallengeMethod", String, nullable=True
    )
    expires_at: Mapped[datetime] = mapped_column(
        "expiresAt", DateTime(timezone=True), nullable=False
    )
    is_used: Mapped[bool] = mapped_column("isUsed", Boolean, default=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(
        "createdAt",
        DateTime(timezone=True),
        default=lambda: __import__("datetime").datetime.now(__import__("datetime").timezone.utc),
    )


class OAuthToken(Base):
    """OAuth access/refresh token."""

    __tablename__ = "OAuthToken"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: __import__("uuid").uuid4().hex
    )
    access_token: Mapped[str] = mapped_column(
        "accessToken", String, unique=True, nullable=False, index=True
    )
    refresh_token: Mapped[str | None] = mapped_column(
        "refreshToken", String, unique=True, nullable=True, index=True
    )
    client_id: Mapped[str] = mapped_column("clientId", String, nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(
        "userId", String, ForeignKey("User.id", ondelete="CASCADE"), index=True
    )
    scope: Mapped[str | None] = mapped_column(String, nullable=True)
    access_token_expires_at: Mapped[datetime] = mapped_column(
        "accessTokenExpiresAt", DateTime(timezone=True), nullable=False
    )
    refresh_token_expires_at: Mapped[datetime | None] = mapped_column(
        "refreshTokenExpiresAt", DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        "createdAt",
        DateTime(timezone=True),
        default=lambda: __import__("datetime").datetime.now(__import__("datetime").timezone.utc),
    )


class DeviceToken(Base, TimestampMixin):
    """FCM push notification device tokens."""

    __tablename__ = "DeviceToken"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: __import__("uuid").uuid4().hex[:25]
    )
    user_id: Mapped[str] = mapped_column(
        "userId", String, ForeignKey("User.id", ondelete="CASCADE"), index=True
    )
    token: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)
    platform: Mapped[str] = mapped_column(String, nullable=False)  # ANDROID, IOS, WEB


class ModelPreference(Base, TimestampMixin):
    """Per-user AI model preference."""

    __tablename__ = "ModelPreference"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: __import__("uuid").uuid4().hex[:25]
    )
    user_id: Mapped[str] = mapped_column(
        "userId", String, ForeignKey("User.id", ondelete="CASCADE"), index=True
    )
    capability: Mapped[str] = mapped_column(
        String, nullable=False
    )  # chat, vision, structured_output, embedding
    provider: Mapped[str] = mapped_column(String, nullable=False)  # gemini, openai, anthropic
    model_id: Mapped[str] = mapped_column("modelId", String, nullable=False)

    __table_args__ = (
        Index("ModelPreference_userId_capability_key", "userId", "capability", unique=True),
    )


class LimitReachedEmailLog(Base):
    """Tracks limit-reached emails to avoid spam.

    Deduped by **day**, not by usage window. A 5-hour window permits 4.8 windows a day, so keying
    this on the window would mail a heavy free learner up to five times daily where the old monthly
    period mailed them once. The in-app refusal carries the reset time and is the right surface for
    "your allowance is back at 3:40 PM"; the email exists for the learner who is not looking at the
    app, and they need telling once.
    """

    __tablename__ = "LimitReachedEmailLog"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: __import__("uuid").uuid4().hex[:25]
    )
    user_id: Mapped[str] = mapped_column(
        "userId", String, ForeignKey("User.id", ondelete="CASCADE"), index=True
    )
    window_day: Mapped[datetime] = mapped_column(
        "windowDay", DateTime(timezone=True), nullable=False
    )
    sent_at: Mapped[datetime] = mapped_column(
        "sentAt",
        DateTime(timezone=True),
        default=lambda: __import__("datetime").datetime.now(__import__("datetime").timezone.utc),
    )

    __table_args__ = (
        Index(
            "LimitReachedEmailLog_userId_windowDay_key",
            "userId",
            "windowDay",
            unique=True,
        ),
    )
