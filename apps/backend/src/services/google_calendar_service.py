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

    async def create_maigie_calendar(self, user_id: str) -> str | None:
        """
        Create a dedicated Maigie calendar for the user.

        Args:
            user_id: User ID

        Returns:
            Calendar ID of the created Maigie calendar, or None if creation failed
        """
        try:
            access_token = await self.get_valid_access_token(user_id)
            if not access_token:
                logger.warning(f"No valid access token for user {user_id}")
                return None

            # Create calendar
            calendar_data = {
                "summary": "Maigie Schedule",
                "description": "Automated schedule management by Maigie",
                "timeZone": "UTC",
            }

            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{GOOGLE_CALENDAR_API_BASE}/calendars",
                    headers={
                        "Authorization": f"Bearer {access_token}",
                        "Content-Type": "application/json",
                    },
                    json=calendar_data,
                    timeout=30.0,  # Add timeout to prevent hanging
                )

                # Google Calendar API returns 201 Created for successful calendar creation
                if response.status_code not in (200, 201):
                    logger.error(
                        f"Failed to create Maigie calendar: {response.status_code} - {response.text}",
                        extra={
                            "user_id": user_id,
                            "status_code": response.status_code,
                            "response_text": response.text[:500],  # Limit response text length
                        },
                    )
                    return None

                try:
                    calendar = response.json()
                except Exception as json_error:
                    logger.error(
                        f"Failed to parse calendar creation response as JSON: {json_error}",
                        extra={
                            "user_id": user_id,
                            "response_text": response.text[:500],
                            "status_code": response.status_code,
                        },
                        exc_info=True,
                    )
                    return None

                calendar_id = calendar.get("id")

                if not calendar_id:
                    logger.error(
                        f"Calendar created but no ID returned: {response.text}",
                        extra={
                            "user_id": user_id,
                            "response_data": calendar,
                        },
                    )
                    return None

                # Update user with the calendar ID
                try:
                    await db.user.update(
                        where={"id": user_id},
                        data={"googleCalendarId": calendar_id},
                    )
                except Exception as db_error:
                    logger.error(
                        f"Failed to update user with calendar ID: {db_error}",
                        extra={
                            "user_id": user_id,
                            "calendar_id": calendar_id,
                        },
                        exc_info=True,
                    )
                    return None

                logger.info(f"Created Maigie calendar {calendar_id} for user {user_id}")
                return calendar_id

        except httpx.HTTPError as http_error:
            logger.error(
                f"HTTP error creating Maigie calendar for user {user_id}: {http_error}",
                extra={"user_id": user_id},
                exc_info=True,
            )
            return None
        except Exception as e:
            logger.error(
                f"Error creating Maigie calendar for user {user_id}: {type(e).__name__}: {str(e)}",
                extra={"user_id": user_id, "exception_type": type(e).__name__},
                exc_info=True,
            )
            return None

    async def check_freebusy(
        self,
        user_id: str,
        time_min: datetime,
        time_max: datetime,
        calendar_ids: list[str] | None = None,
    ) -> dict[str, Any] | None:
        """
        Check free/busy information for calendars.

        Args:
            user_id: User ID
            time_min: Start of time range
            time_max: End of time range
            calendar_ids: List of calendar IDs to check (defaults to primary)

        Returns:
            Free/busy information or None if check failed
        """
        try:
            access_token = await self.get_valid_access_token(user_id)
            if not access_token:
                logger.warning(f"No valid access token for user {user_id}")
                return None

            if not calendar_ids:
                calendar_ids = ["primary"]

            # Build request body
            request_body = {
                "timeMin": time_min.isoformat(),
                "timeMax": time_max.isoformat(),
                "items": [{"id": cal_id} for cal_id in calendar_ids],
            }

            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{GOOGLE_CALENDAR_API_BASE}/freeBusy",
                    headers={
                        "Authorization": f"Bearer {access_token}",
                        "Content-Type": "application/json",
                    },
                    json=request_body,
                )

                if response.status_code != 200:
                    logger.error(
                        f"Failed to check free/busy: {response.status_code} - {response.text}"
                    )
                    return None

                return response.json()

        except Exception as e:
            logger.error(f"Error checking free/busy for user {user_id}: {e}")
            return None

    async def has_conflict(
        self,
        user_id: str,
        start_time: datetime,
        end_time: datetime,
    ) -> dict[str, Any]:
        """
        Check if a time slot conflicts with existing calendar events.

        Args:
            user_id: User ID
            start_time: Proposed start time
            end_time: Proposed end time

        Returns:
            Dictionary with 'has_conflict' boolean and 'busy_periods' list
        """
        try:
            # Check if user has calendar sync enabled
            user = await db.user.find_unique(where={"id": user_id})
            if not user or not user.googleCalendarSyncEnabled:
                # No calendar connected, no conflict
                return {"has_conflict": False, "busy_periods": []}

            # Check free/busy for the primary calendar
            freebusy_data = await self.check_freebusy(
                user_id=user_id,
                time_min=start_time,
                time_max=end_time,
                calendar_ids=["primary"],
            )

            if not freebusy_data:
                # Failed to check, assume no conflict (fail open)
                logger.warning(
                    f"Failed to check free/busy for user {user_id}, assuming no conflict"
                )
                return {"has_conflict": False, "busy_periods": []}

            # Extract busy periods from the response
            calendars = freebusy_data.get("calendars", {})
            primary_calendar = calendars.get("primary", {})
            busy_periods = primary_calendar.get("busy", [])

            # Check if any busy period overlaps with our proposed time
            has_conflict = False
            for busy_period in busy_periods:
                busy_start = datetime.fromisoformat(busy_period["start"].replace("Z", "+00:00"))
                busy_end = datetime.fromisoformat(busy_period["end"].replace("Z", "+00:00"))

                # Check for overlap: (start_time < busy_end) and (end_time > busy_start)
                if start_time < busy_end and end_time > busy_start:
                    has_conflict = True
                    break

            return {
                "has_conflict": has_conflict,
                "busy_periods": busy_periods,
            }

        except Exception as e:
            logger.error(f"Error checking for conflicts for user {user_id}: {e}")
            # Fail open - don't block schedule creation on errors
            return {"has_conflict": False, "busy_periods": []}

    async def sync_existing_schedules(self, user_id: str) -> dict[str, Any]:
        """
        Sync all existing schedule blocks to Google Calendar.

        Args:
            user_id: User ID

        Returns:
            Dictionary with sync results (success_count, error_count)
        """
        try:
            # Get all schedules for the user that haven't been synced yet
            schedules = await db.scheduleblock.find_many(
                where={
                    "userId": user_id,
                    "googleCalendarEventId": None,  # Not yet synced
                }
            )

            success_count = 0
            error_count = 0

            for schedule in schedules:
                try:
                    event_id = await self.create_event(
                        user_id=user_id,
                        schedule_id=schedule.id,
                        title=schedule.title,
                        description=schedule.description,
                        start_at=schedule.startAt,
                        end_at=schedule.endAt,
                        recurring_rule=schedule.recurringRule,
                    )
                    if event_id:
                        success_count += 1
                    else:
                        error_count += 1
                except Exception as e:
                    logger.error(f"Error syncing schedule {schedule.id}: {e}")
                    error_count += 1

            logger.info(
                f"Synced schedules for user {user_id}: "
                f"{success_count} successful, {error_count} errors"
            )

            return {
                "success_count": success_count,
                "error_count": error_count,
                "total": len(schedules),
            }

        except Exception as e:
            logger.error(f"Error syncing schedules for user {user_id}: {e}")
            return {"success_count": 0, "error_count": 0, "total": 0}

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

    def _build_schedule_link(
        self, schedule_id: str, course_id: str | None = None, goal_id: str | None = None
    ) -> str:
        """
        Build a link to the schedule item in the frontend.

        Args:
            schedule_id: Schedule block ID
            course_id: Optional course ID (takes priority)
            goal_id: Optional goal ID (fallback if no course)

        Returns:
            Full URL to the schedule item or related resource
        """
        from src.config import get_settings

        settings = get_settings()
        frontend_url = settings.FRONTEND_URL.rstrip("/")

        # Prioritize course link, then goal, then schedule page
        if course_id:
            return f"{frontend_url}/courses/{course_id}"
        elif goal_id:
            return f"{frontend_url}/goals"
        else:
            return f"{frontend_url}/schedule"

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
            calendar_id = user.googleCalendarId

            # Validate calendar ID - we need the Maigie calendar ID, not primary
            if not calendar_id:
                logger.warning(
                    f"No calendar ID found for user {user_id}. "
                    f"Calendar sync may not be properly configured. "
                    f"User has sync enabled: {user.googleCalendarSyncEnabled if user else False}"
                )
                return None

            # Fetch schedule to get courseId/goalId for link generation
            schedule = await db.scheduleblock.find_unique(where={"id": schedule_id})
            course_id = getattr(schedule, "courseId", None) if schedule else None
            goal_id = getattr(schedule, "goalId", None) if schedule else None

            # Build link to schedule item
            schedule_link = self._build_schedule_link(schedule_id, course_id, goal_id)

            # Build description with link
            description_parts = []
            if description:
                description_parts.append(description)
            description_parts.append(f"\n\nView in Maigie: {schedule_link}")

            # Log which calendar we're using for debugging
            logger.debug(f"Creating event in calendar {calendar_id} for user {user_id}")

            # Build event data
            event_data: dict[str, Any] = {
                "summary": title,
                "description": "\n".join(description_parts),
                "start": {
                    "dateTime": start_at.isoformat(),
                    "timeZone": "UTC",
                },
                "end": {
                    "dateTime": end_at.isoformat(),
                    "timeZone": "UTC",
                },
                "source": {
                    "title": "Maigie",
                    "url": schedule_link,
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

                # Google Calendar API returns 201 Created for successful event creation
                if response.status_code not in (200, 201):
                    logger.error(
                        f"Failed to create Google Calendar event in calendar {calendar_id}: "
                        f"{response.status_code} - {response.text}"
                    )
                    # Log additional context for debugging
                    logger.error(
                        f"Event creation context - User: {user_id}, Calendar ID: {calendar_id}, "
                        f"Schedule ID: {schedule_id}, Title: {title}"
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

            # Fetch schedule to get courseId/goalId for link generation
            schedule = await db.scheduleblock.find_unique(where={"id": schedule_id})
            course_id = getattr(schedule, "courseId", None) if schedule else None
            goal_id = getattr(schedule, "goalId", None) if schedule else None

            # Build link to schedule item
            schedule_link = self._build_schedule_link(schedule_id, course_id, goal_id)

            # Build description with link
            description_parts = []
            if description:
                description_parts.append(description)
            description_parts.append(f"\n\nView in Maigie: {schedule_link}")

            # Build event data
            event_data: dict[str, Any] = {
                "summary": title,
                "description": "\n".join(description_parts),
                "start": {
                    "dateTime": start_at.isoformat(),
                    "timeZone": "UTC",
                },
                "end": {
                    "dateTime": end_at.isoformat(),
                    "timeZone": "UTC",
                },
                "source": {
                    "title": "Maigie",
                    "url": schedule_link,
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
