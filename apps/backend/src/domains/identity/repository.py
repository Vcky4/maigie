"""
Identity domain — Data access layer (SQLAlchemy).

All queries related to User and UserPreferences are encapsulated here.
No other domain should query these tables directly.
"""

import logging
from datetime import datetime
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.domains.notifications.db_models import NotificationPolicy
from src.shared.database import get_session_factory

from .db_models import DeviceToken, User, UserPreferences

logger = logging.getLogger(__name__)


class IdentityRepository:
    """Data access for User and UserPreferences."""

    async def _get_session(self) -> AsyncSession:
        factory = get_session_factory()
        return factory()

    # -----------------------------------------------------------------------
    # Lookups
    # -----------------------------------------------------------------------

    async def find_by_id(self, user_id: str, *, include_preferences: bool = False) -> User | None:
        async with await self._get_session() as session:
            stmt = select(User).where(User.id == user_id)
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def find_by_email(self, email: str, *, include_preferences: bool = False) -> User | None:
        async with await self._get_session() as session:
            stmt = select(User).where(User.email == email)
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def find_by_oauth(self, provider: str, provider_id: str) -> User | None:
        async with await self._get_session() as session:
            stmt = select(User).where(User.provider == provider, User.provider_id == provider_id)
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def find_by_referral_code(self, code: str) -> User | None:
        async with await self._get_session() as session:
            stmt = select(User).where(User.referral_code == code)
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

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
        async with await self._get_session() as session:
            user = User(
                email=email,
                password_hash=password_hash,
                name=name,
                provider=provider,
                provider_id=provider_id,
                is_active=is_active,
                verification_code=verification_code,
                verification_code_expires_at=verification_code_expires_at,
            )
            session.add(user)
            await session.flush()

            # Create default preferences
            prefs = UserPreferences(
                user_id=user.id,
                theme="light",
                language="en",
                notifications=True,
            )
            session.add(prefs)
            session.add(NotificationPolicy(user_id=user.id, engagement_enabled=True))
            await session.commit()
            await session.refresh(user)
            return user

    async def create_oauth_user(
        self,
        *,
        email: str,
        name: str | None,
        provider: str,
        provider_id: str,
    ) -> User:
        """Create a new user from OAuth flow (active, not onboarded)."""
        async with await self._get_session() as session:
            user = User(
                email=email,
                name=name,
                provider=provider,
                provider_id=provider_id,
                is_active=True,
                is_onboarded=False,
            )
            session.add(user)
            await session.flush()
            session.add(NotificationPolicy(user_id=user.id, engagement_enabled=False))
            await session.commit()
            await session.refresh(user)
            return user

    # -----------------------------------------------------------------------
    # Device tokens (push notifications)
    # -----------------------------------------------------------------------

    async def upsert_device_token(self, *, user_id: str, token: str, platform: str) -> DeviceToken:
        """Register a device for push, keyed on the **token** rather than the learner.

        Until this existed nothing wrote `DeviceToken` at all, so every push in the application returned
        `no_tokens` — the notification path was complete and delivered to nobody.

        **Keyed on the token because a device changes hands.** FCM issues one token per app install, so
        when a second learner signs in on the same phone the same token arrives with a different
        `userId`. `token` is `UNIQUE`, so inserting blindly raises; and leaving the row on the first
        learner is worse than an error — the row decides who a message is *sent for*, so the first
        learner's private notifications would be delivered to the second learner's phone. Reassigning is
        the only behaviour that is both correct and safe.

        Idempotent, so a client may call it on every launch, which is how these rows will actually appear
        — the same pattern `record_device_timezone` relies on.
        """
        async with await self._get_session() as session:
            existing = (
                await session.execute(select(DeviceToken).where(DeviceToken.token == token))
            ).scalar_one_or_none()

            if existing is not None:
                existing.user_id = user_id
                existing.platform = platform
                await session.commit()
                await session.refresh(existing)
                return existing

            row = DeviceToken(user_id=user_id, token=token, platform=platform)
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return row

    async def delete_device_token(self, *, user_id: str, token: str) -> bool:
        """Unregister one device. Returns whether a row was removed.

        Scoped to the owner, so a token cannot be unregistered by whoever happens to know the string.

        **Clients must call this on sign-out.** A token left behind keeps the device registered to the
        learner who signed out, so the next person to use that phone receives their notifications. That
        is the same hole `upsert_device_token` closes from the other direction, and it is the reason this
        is an endpoint rather than something only the FCM error path prunes.
        """
        async with await self._get_session() as session:
            result = await session.execute(
                delete(DeviceToken).where(
                    DeviceToken.token == token, DeviceToken.user_id == user_id
                )
            )
            await session.commit()
            return bool(result.rowcount)

    async def count_device_tokens(self, user_id: str) -> int:
        """How many devices this learner can be reached on.

        Published so a client can tell "push is off" apart from "push is on and quiet", and so the
        notification settings surface can stop implying a channel that reaches nothing.
        """
        async with await self._get_session() as session:
            rows = await session.execute(
                select(DeviceToken.id).where(DeviceToken.user_id == user_id)
            )
            return len(list(rows.scalars().all()))

    # -----------------------------------------------------------------------
    # Updates
    # -----------------------------------------------------------------------

    async def update(self, user_id: str, data: dict[str, Any]) -> User:
        """Generic update by user ID."""
        async with await self._get_session() as session:
            # Map API field names to model attribute names
            mapped = self._map_fields(data)
            stmt = update(User).where(User.id == user_id).values(**mapped)
            await session.execute(stmt)
            await session.commit()
            return await self.find_by_id(user_id)

    async def activate_user(self, user_id: str) -> User:
        """Activate user and clear verification codes."""
        return await self.update(
            user_id,
            {
                "isActive": True,
                "verificationCode": None,
                "verificationCodeExpiresAt": None,
            },
        )

    async def set_verification_code(self, user_id: str, code: str, expires_at: datetime) -> User:
        return await self.update(
            user_id,
            {
                "verificationCode": code,
                "verificationCodeExpiresAt": expires_at,
            },
        )

    async def set_password_reset_code(self, user_id: str, code: str, expires_at: datetime) -> User:
        return await self.update(
            user_id,
            {
                "passwordResetCode": code,
                "passwordResetExpiresAt": expires_at,
            },
        )

    async def clear_password_reset(self, user_id: str, new_password_hash: str) -> User:
        return await self.update(
            user_id,
            {
                "passwordHash": new_password_hash,
                "passwordResetCode": None,
                "passwordResetExpiresAt": None,
            },
        )

    async def update_password(self, user_id: str, new_password_hash: str) -> User:
        return await self.update(user_id, {"passwordHash": new_password_hash})

    async def set_onboarded(self, user_id: str) -> User:
        return await self.update(user_id, {"isOnboarded": True})

    async def set_referred_by(self, user_id: str, referral_code: str) -> User:
        return await self.update(user_id, {"referredByCode": referral_code})

    # -----------------------------------------------------------------------
    # Preferences
    # -----------------------------------------------------------------------

    async def upsert_preferences(self, user_id: str, data: dict[str, Any]) -> User:
        """Upsert user preferences."""
        async with await self._get_session() as session:
            # Check if preferences exist
            stmt = select(UserPreferences).where(UserPreferences.user_id == user_id)
            result = await session.execute(stmt)
            prefs = result.scalar_one_or_none()

            mapped = self._map_pref_fields(data)

            if prefs:
                for key, value in mapped.items():
                    setattr(prefs, key, value)
            else:
                prefs = UserPreferences(user_id=user_id, **mapped)
                session.add(prefs)

            await session.commit()
            return await self.find_by_id(user_id)

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
        return await self.update(
            user_id,
            {
                "accountDeletionRequestedAt": requested_at,
                "accountDeletionScheduledFor": scheduled_for,
                "accountDeletionCancelToken": cancel_token,
                "accountDeletionReminder30SentAt": None,
                "accountDeletionReminder7SentAt": None,
            },
        )

    async def cancel_deletion(self, user_id: str, cancelled_at: datetime) -> User:
        return await self.update(
            user_id,
            {
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

    async def update_last_seen(self, user_id: str, last_seen_at: datetime, platform: str) -> None:
        """Update lastSeenAt (called from auth dependency, throttled)."""
        try:
            async with await self._get_session() as session:
                stmt = (
                    update(User)
                    .where(User.id == user_id)
                    .values(last_seen_at=last_seen_at, last_seen_platform=platform)
                )
                await session.execute(stmt)
                await session.commit()
        except Exception:
            pass  # Never fail a request for activity tracking

    # -----------------------------------------------------------------------
    # Field mapping helpers
    # -----------------------------------------------------------------------

    def _map_fields(self, data: dict[str, Any]) -> dict[str, Any]:
        """Map camelCase API field names to SQLAlchemy model attributes."""
        field_map = {
            "isActive": "is_active",
            "isOnboarded": "is_onboarded",
            "verificationCode": "verification_code",
            "verificationCodeExpiresAt": "verification_code_expires_at",
            "passwordHash": "password_hash",
            "passwordResetCode": "password_reset_code",
            "passwordResetExpiresAt": "password_reset_expires_at",
            "referredByCode": "referred_by_code",
            "lastSeenAt": "last_seen_at",
            "lastSeenPlatform": "last_seen_platform",
            "accountDeletionRequestedAt": "account_deletion_requested_at",
            "accountDeletionScheduledFor": "account_deletion_scheduled_for",
            "accountDeletionCancelToken": "account_deletion_cancel_token",
            "accountDeletionReminder30SentAt": "account_deletion_reminder_30_sent_at",
            "accountDeletionReminder7SentAt": "account_deletion_reminder_7_sent_at",
            "accountDeletionLastCancelledAt": "account_deletion_last_cancelled_at",
        }
        result = {}
        for key, value in data.items():
            attr_name = field_map.get(key, key)
            # If attr_name is still camelCase (not in map), try snake_case conversion
            if attr_name == key and "_" not in key and len(key) > 1:
                # Simple camelCase → snake_case
                import re

                attr_name = re.sub(r"(?<!^)(?=[A-Z])", "_", key).lower()
            result[attr_name] = value
        return result

    def _map_pref_fields(self, data: dict[str, Any]) -> dict[str, Any]:
        """Map preference field names to model attributes."""
        field_map = {
            "studyGoals": "study_goals",
            "emailMorningSchedule": "email_morning_schedule",
            "emailScheduleReminder": "email_schedule_reminder",
            "emailWeeklyTips": "email_weekly_tips",
            "pushScheduleReminder": "push_schedule_reminder",
            "pushStudyTips": "push_study_tips",
            "timezoneSource": "timezone_source",
            "timezoneCapturedAt": "timezone_captured_at",
        }
        result = {}
        for key, value in data.items():
            if value is None:
                continue
            attr_name = field_map.get(key, key)
            result[attr_name] = value
        return result


# Singleton instance
identity_repo = IdentityRepository()
