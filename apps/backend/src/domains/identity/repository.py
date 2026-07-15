"""
Identity domain — Data access layer.

All Prisma queries related to User and UserPreferences are encapsulated here.
No other domain should query these tables directly.
"""

import logging
from datetime import datetime
from typing import Any

from prisma import Json
from prisma.models import User

from src.shared.database import db

logger = logging.getLogger(__name__)


class IdentityRepository:
    """Data access for User and UserPreferences."""

    # -----------------------------------------------------------------------
    # Lookups
    # -----------------------------------------------------------------------

    async def find_by_id(self, user_id: str, *, include_preferences: bool = False) -> User | None:
        return await db.user.find_unique(
            where={"id": user_id},
            include={"preferences": True} if include_preferences else None,
        )

    async def find_by_email(
        self, email: str, *, include_preferences: bool = False
    ) -> User | None:
        return await db.user.find_unique(
            where={"email": email},
            include={"preferences": True} if include_preferences else None,
        )

    async def find_by_oauth(self, provider: str, provider_id: str) -> User | None:
        return await db.user.find_first(
            where={"provider": provider, "providerId": provider_id}
        )

    async def find_by_referral_code(self, code: str) -> User | None:
        return await db.user.find_first(where={"referralCode": code})

    # -----------------------------------------------------------------------
    # Creation
    # -----------------------------------------------------------------------

    async def create_user(
        self,
        *,
        email: str,
        password_hash: str | None = None,
        name: str | None = None,
        provider: str = "email",
        provider_id: str | None = None,
        is_active: bool = False,
        verification_code: str | None = None,
        verification_code_expires_at: datetime | None = None,
    ) -> User:
        """Create a new user with default preferences."""
        data: dict[str, Any] = {
            "email": email,
            "name": name,
            "provider": provider,
            "isActive": is_active,
            "preferences": {
                "create": {
                    "theme": "light",
                    "language": "en",
                    "notifications": True,
                }
            },
        }
        if password_hash:
            data["passwordHash"] = password_hash
        if provider_id:
            data["providerId"] = provider_id
        if verification_code:
            data["verificationCode"] = verification_code
            data["verificationCodeExpiresAt"] = verification_code_expires_at

        return await db.user.create(data=data, include={"preferences": True})

    async def create_oauth_user(
        self,
        *,
        email: str,
        name: str | None,
        provider: str,
        provider_id: str,
    ) -> User:
        """Create a new user from OAuth flow (active, not onboarded)."""
        return await db.user.create(
            data={
                "email": email,
                "name": name,
                "provider": provider,
                "providerId": provider_id,
                "isActive": True,
                "isOnboarded": False,
            }
        )

    # -----------------------------------------------------------------------
    # Updates
    # -----------------------------------------------------------------------

    async def update(self, user_id: str, data: dict[str, Any]) -> User:
        """Generic update by user ID."""
        return await db.user.update(where={"id": user_id}, data=data)

    async def activate_user(self, user_id: str) -> User:
        """Activate user and clear verification codes."""
        return await db.user.update(
            where={"id": user_id},
            data={
                "isActive": True,
                "verificationCode": None,
                "verificationCodeExpiresAt": None,
            },
        )

    async def set_verification_code(
        self, user_id: str, code: str, expires_at: datetime
    ) -> User:
        return await db.user.update(
            where={"id": user_id},
            data={
                "verificationCode": code,
                "verificationCodeExpiresAt": expires_at,
            },
        )

    async def set_password_reset_code(
        self, user_id: str, code: str, expires_at: datetime
    ) -> User:
        return await db.user.update(
            where={"id": user_id},
            data={
                "passwordResetCode": code,
                "passwordResetExpiresAt": expires_at,
            },
        )

    async def clear_password_reset(self, user_id: str, new_password_hash: str) -> User:
        return await db.user.update(
            where={"id": user_id},
            data={
                "passwordHash": new_password_hash,
                "passwordResetCode": None,
                "passwordResetExpiresAt": None,
            },
        )

    async def update_password(self, user_id: str, new_password_hash: str) -> User:
        return await db.user.update(
            where={"id": user_id},
            data={"passwordHash": new_password_hash},
        )

    async def set_onboarded(self, user_id: str) -> User:
        return await db.user.update(
            where={"id": user_id},
            data={"isOnboarded": True},
        )

    async def set_referred_by(self, user_id: str, referral_code: str) -> User:
        return await db.user.update(
            where={"id": user_id},
            data={"referredByCode": referral_code},
        )

    # -----------------------------------------------------------------------
    # Preferences
    # -----------------------------------------------------------------------

    async def upsert_preferences(self, user_id: str, data: dict[str, Any]) -> User:
        """Upsert user preferences (create if missing, update if exists)."""
        # Convert studyGoals dict to Prisma Json type
        if "studyGoals" in data and data["studyGoals"] is not None:
            data["studyGoals"] = Json(data["studyGoals"])

        create_defaults = {
            "theme": data.get("theme", "light"),
            "language": data.get("language", "en"),
            "notifications": data.get("notifications", True),
            "timezone": data.get("timezone", "UTC"),
            "emailMorningSchedule": data.get("emailMorningSchedule", True),
            "emailScheduleReminder": data.get("emailScheduleReminder", True),
            "emailWeeklyTips": data.get("emailWeeklyTips", True),
        }
        if "studyGoals" in data:
            create_defaults["studyGoals"] = data["studyGoals"]

        return await db.user.update(
            where={"id": user_id},
            data={
                "preferences": {
                    "upsert": {
                        "create": create_defaults,
                        "update": data,
                    }
                }
            },
            include={"preferences": True},
        )

    # -----------------------------------------------------------------------
    # Account Deletion
    # -----------------------------------------------------------------------

    async def request_deletion(
        self,
        user_id: str,
        *,
        requested_at: datetime,
        scheduled_for: datetime,
        cancel_token: str,
    ) -> User:
        return await db.user.update(
            where={"id": user_id},
            data={
                "accountDeletionRequestedAt": requested_at,
                "accountDeletionScheduledFor": scheduled_for,
                "accountDeletionCancelToken": cancel_token,
                "accountDeletionReminder30SentAt": None,
                "accountDeletionReminder7SentAt": None,
            },
        )

    async def cancel_deletion(self, user_id: str, cancelled_at: datetime) -> User:
        return await db.user.update(
            where={"id": user_id},
            data={
                "accountDeletionRequestedAt": None,
                "accountDeletionScheduledFor": None,
                "accountDeletionCancelToken": None,
                "accountDeletionReminder30SentAt": None,
                "accountDeletionReminder7SentAt": None,
                "accountDeletionLastCancelledAt": cancelled_at,
            },
        )

    # -----------------------------------------------------------------------
    # Activity tracking
    # -----------------------------------------------------------------------

    async def update_last_seen(
        self, user_id: str, last_seen_at: datetime, platform: str
    ) -> None:
        """Update lastSeenAt (called from auth dependency, throttled)."""
        try:
            await db.user.update(
                where={"id": user_id},
                data={"lastSeenAt": last_seen_at, "lastSeenPlatform": platform},
            )
        except Exception:
            pass  # Never fail a request for activity tracking


# Singleton instance
identity_repo = IdentityRepository()
