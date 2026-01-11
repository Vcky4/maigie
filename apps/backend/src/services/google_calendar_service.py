"""
Google Calendar Service.
Handles syncing schedule blocks with Google Calendar.

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from src.core.database import db

logger = logging.getLogger(__name__)

# Google Calendar API endpoints
GOOGLE_CALENDAR_API_BASE = "https://www.googleapis.com/calendar/v3"
GOOGLE_TOKEN_REFRESH_URL = "https://oauth2.googleapis.com/token"


class GoogleCalendarService:
    """Service for syncing schedules with Google Calendar."""

    async def get_valid_access_token(self, user_id: str) -> str | None:
        """
        Get a valid access token for the user, refreshing if necessary.

        Args:
            user_id: User ID

        Returns:
            Valid access token or None if unavailable
        """
        try:
            user = await db.user.find_unique(where={"id": user_id})

            if not user or not user.googleCalendarRefreshToken:
                return None

            # Check if token needs refresh
            if (
                user.googleCalendarTokenExpiresAt
                and user.googleCalendarTokenExpiresAt > datetime.now(timezone.utc)
            ):
                # Token is still valid
                return user.googleCalendarAccessToken

            # Token expired or about to expire, refresh it
            return await self._refresh_access_token(user_id, user.googleCalendarRefreshToken)

        except Exception as e:
            logger.error(f"Error getting access token for user {user_id}: {e}")
            return None

    async def _refresh_access_token(self, user_id: str, refresh_token: str) -> str | None:
        """
        Refresh Google Calendar access token.

        Args:
            user_id: User ID
            refresh_token: Google refresh token

        Returns:
            New access token or None if refresh failed
        """
        try:
            from src.config import get_settings

            settings = get_settings()

            async with httpx.AsyncClient() as client:
                response = await client.post(
                    GOOGLE_TOKEN_REFRESH_URL,
                    data={
                        "client_id": settings.OAUTH_GOOGLE_CLIENT_ID,
                        "client_secret": settings.OAUTH_GOOGLE_CLIENT_SECRET,
                        "refresh_token": refresh_token,
                        "grant_type": "refresh_token",
                    },
                )

                if response.status_code != 200:
                    logger.error(f"Failed to refresh token for user {user_id}: {response.text}")
                    return None

                token_data = response.json()
                new_access_token = token_data.get("access_token")
                expires_in = token_data.get("expires_in", 3600)  # Default 1 hour

                # Calculate expiration time
                expires_at = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(
                    seconds=expires_in
                )

                # Update user with new token
                await db.user.update(
                    where={"id": user_id},
                    data={
                        "googleCalendarAccessToken": new_access_token,
                        "googleCalendarTokenExpiresAt": expires_at,
                    },
                )

                return new_access_token

        except Exception as e:
            logger.error(f"Error refreshing access token for user {user_id}: {e}")
            return None

    async def create_event(
        self,
        user_id: str,
        schedule_id: str,
        title: str,
        description: str | None,
        start_at: datetime,
        end_at: datetime,
        recurring_rule: str | None = None,
    ) -> str | None:
        """
        Create an event in Google Calendar.

        Args:
            user_id: User ID
            schedule_id: Schedule block ID
            title: Event title
            description: Event description
            start_at: Event start time
            end_at: Event end time
            recurring_rule: Recurring rule (e.g., "DAILY", "WEEKLY", or RRULE format)

        Returns:
            Google Calendar event ID or None if creation failed
        """
        try:
            access_token = await self.get_valid_access_token(user_id)
            if not access_token:
                logger.warning(f"No valid access token for user {user_id}")
                return None

            user = await db.user.find_unique(where={"id": user_id})
            calendar_id = user.googleCalendarId or "primary"

            # Build event data
            event_data: dict[str, Any] = {
                "summary": title,
                "description": description or "",
                "start": {
                    "dateTime": start_at.isoformat(),
                    "timeZone": "UTC",
                },
                "end": {
                    "dateTime": end_at.isoformat(),
                    "timeZone": "UTC",
                },
            }

            # Add recurrence if provided
            if recurring_rule:
                rrule = self._convert_recurring_rule_to_rrule(recurring_rule, start_at)
                if rrule:
                    event_data["recurrence"] = [rrule]

            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{GOOGLE_CALENDAR_API_BASE}/calendars/{calendar_id}/events",
                    headers={
                        "Authorization": f"Bearer {access_token}",
                        "Content-Type": "application/json",
                    },
                    json=event_data,
                )

                if response.status_code != 200:
                    logger.error(
                        f"Failed to create Google Calendar event: {response.status_code} - {response.text}"
                    )
                    return None

                event = response.json()
                event_id = event.get("id")

                # Update schedule with Google Calendar event ID
                await db.scheduleblock.update(
                    where={"id": schedule_id},
                    data={
                        "googleCalendarEventId": event_id,
                        "googleCalendarSyncedAt": datetime.now(timezone.utc),
                    },
                )

                logger.info(f"Created Google Calendar event {event_id} for schedule {schedule_id}")
                return event_id

        except Exception as e:
            logger.error(f"Error creating Google Calendar event: {e}")
            return None

    async def update_event(
        self,
        user_id: str,
        schedule_id: str,
        event_id: str,
        title: str,
        description: str | None,
        start_at: datetime,
        end_at: datetime,
        recurring_rule: str | None = None,
    ) -> bool:
        """
        Update an event in Google Calendar.

        Args:
            user_id: User ID
            schedule_id: Schedule block ID
            event_id: Google Calendar event ID
            title: Event title
            description: Event description
            start_at: Event start time
            end_at: Event end time
            recurring_rule: Recurring rule

        Returns:
            True if update succeeded, False otherwise
        """
        try:
            access_token = await self.get_valid_access_token(user_id)
            if not access_token:
                logger.warning(f"No valid access token for user {user_id}")
                return False

            user = await db.user.find_unique(where={"id": user_id})
            calendar_id = user.googleCalendarId or "primary"

            # Build event data
            event_data: dict[str, Any] = {
                "summary": title,
                "description": description or "",
                "start": {
                    "dateTime": start_at.isoformat(),
                    "timeZone": "UTC",
                },
                "end": {
                    "dateTime": end_at.isoformat(),
                    "timeZone": "UTC",
                },
            }

            # Add recurrence if provided
            if recurring_rule:
                rrule = self._convert_recurring_rule_to_rrule(recurring_rule, start_at)
                if rrule:
                    event_data["recurrence"] = [rrule]

            async with httpx.AsyncClient() as client:
                response = await client.put(
                    f"{GOOGLE_CALENDAR_API_BASE}/calendars/{calendar_id}/events/{event_id}",
                    headers={
                        "Authorization": f"Bearer {access_token}",
                        "Content-Type": "application/json",
                    },
                    json=event_data,
                )

                if response.status_code != 200:
                    logger.error(
                        f"Failed to update Google Calendar event: {response.status_code} - {response.text}"
                    )
                    return False

                # Update sync timestamp
                await db.scheduleblock.update(
                    where={"id": schedule_id},
                    data={"googleCalendarSyncedAt": datetime.now(timezone.utc)},
                )

                logger.info(f"Updated Google Calendar event {event_id} for schedule {schedule_id}")
                return True

        except Exception as e:
            logger.error(f"Error updating Google Calendar event: {e}")
            return False

    async def delete_event(self, user_id: str, schedule_id: str, event_id: str) -> bool:
        """
        Delete an event from Google Calendar.

        Args:
            user_id: User ID
            schedule_id: Schedule block ID
            event_id: Google Calendar event ID

        Returns:
            True if deletion succeeded, False otherwise
        """
        try:
            access_token = await self.get_valid_access_token(user_id)
            if not access_token:
                logger.warning(f"No valid access token for user {user_id}")
                return False

            user = await db.user.find_unique(where={"id": user_id})
            calendar_id = user.googleCalendarId or "primary"

            async with httpx.AsyncClient() as client:
                response = await client.delete(
                    f"{GOOGLE_CALENDAR_API_BASE}/calendars/{calendar_id}/events/{event_id}",
                    headers={"Authorization": f"Bearer {access_token}"},
                )

                if response.status_code not in (200, 204):
                    logger.error(
                        f"Failed to delete Google Calendar event: {response.status_code} - {response.text}"
                    )
                    return False

                # Clear Google Calendar fields
                await db.scheduleblock.update(
                    where={"id": schedule_id},
                    data={
                        "googleCalendarEventId": None,
                        "googleCalendarSyncedAt": None,
                    },
                )

                logger.info(f"Deleted Google Calendar event {event_id} for schedule {schedule_id}")
                return True

        except Exception as e:
            logger.error(f"Error deleting Google Calendar event: {e}")
            return False

    def _convert_recurring_rule_to_rrule(self, rule: str, start_date: datetime) -> str | None:
        """
        Convert a simple recurring rule to RRULE format.

        Args:
            rule: Simple rule (e.g., "DAILY", "WEEKLY") or RRULE format
            start_date: Start date for the event

        Returns:
            RRULE string or None
        """
        rule_upper = rule.upper().strip()

        # If already in RRULE format, return as-is
        if rule_upper.startswith("RRULE:"):
            return rule_upper

        # Convert simple rules to RRULE
        if rule_upper == "DAILY":
            return "RRULE:FREQ=DAILY"
        elif rule_upper == "WEEKLY":
            # Get day of week from start_date
            day_of_week = start_date.strftime("%A").upper()[:2]  # MO, TU, WE, etc.
            return f"RRULE:FREQ=WEEKLY;BYDAY={day_of_week}"
        elif rule_upper == "MONTHLY":
            return "RRULE:FREQ=MONTHLY"
        elif rule_upper == "YEARLY":
            return "RRULE:FREQ=YEARLY"

        # If we can't parse it, return None (no recurrence)
        logger.warning(f"Unknown recurring rule format: {rule}")
        return None


# Global instance
google_calendar_service = GoogleCalendarService()
