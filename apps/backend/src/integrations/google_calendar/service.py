"""Google Calendar sync for study blocks.

Restored from the pre-migration `services/google_calendar_service`, ported from the Prisma
client to SQLAlchemy. The three methods here were `return None`, `return {}` and `pass`,
which made calendar sync a complete no-op: the OAuth flow in `identity/oauth_routes` stores
a user's Calendar tokens and sets `googleCalendarSyncEnabled`, so a user could connect their
calendar, be told it worked, and never see a single event appear.

The Calendar REST API is called directly with `httpx`, as the original did. No Google client
library is needed and none is installed beyond `google-auth`.

Nothing here raises. Calendar sync is a convenience layered on top of a schedule that is
already saved locally; a Google outage or a revoked grant must not fail creating a study
block. Failures are logged and reported in the return value.

Only the three methods with callers are implemented. The original also had `check_freebusy`
and `has_conflict`, which nothing calls, so they are deliberately not carried over rather
than restored as dead code.
"""

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import select

from src.domains.identity.db_models import User
from src.domains.progress.db_models import ScheduleBlock
from src.shared.database import get_session_factory

logger = logging.getLogger(__name__)

GOOGLE_CALENDAR_API_BASE = "https://www.googleapis.com/calendar/v3"
GOOGLE_TOKEN_REFRESH_URL = "https://oauth2.googleapis.com/token"

CALENDAR_SUMMARY = "Maigie Schedule"
CALENDAR_DESCRIPTION = "Automated schedule management by Maigie"

# Refresh slightly early so a token cannot expire mid-request.
TOKEN_EXPIRY_MARGIN_SECONDS = 60

# How far ahead an initial sync reaches. Past blocks are not worth writing to a calendar.
INITIAL_SYNC_DAYS_AHEAD = 60

_REQUEST_TIMEOUT = 30.0


class GoogleCalendarService:
    """Google Calendar integration."""

    # -- tokens ------------------------------------------------------------

    async def get_valid_access_token(self, user_id: str) -> str | None:
        """Return a usable access token, refreshing it first if it is close to expiry."""
        factory = get_session_factory()
        async with factory() as session:
            user = (
                await session.execute(select(User).where(User.id == user_id))
            ).scalar_one_or_none()
            if user is None:
                return None
            access_token = user.google_calendar_access_token
            refresh_token = user.google_calendar_refresh_token
            expires_at = user.google_calendar_token_expires_at

        if not access_token and not refresh_token:
            logger.info("User %s has not connected Google Calendar", user_id)
            return None

        if expires_at is not None:
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=UTC)
            margin = datetime.now(UTC) + timedelta(seconds=TOKEN_EXPIRY_MARGIN_SECONDS)
            if expires_at > margin:
                return access_token

        if not refresh_token:
            # Expired with no way to renew; the user must reconnect.
            logger.warning(
                "Google Calendar token for user %s has expired and there is no refresh token",
                user_id,
            )
            return None

        return await self._refresh_access_token(user_id, refresh_token)

    async def _refresh_access_token(self, user_id: str, refresh_token: str) -> str | None:
        """Exchange a refresh token for a new access token and persist it."""
        from src.config import get_settings

        settings = get_settings()
        if not settings.OAUTH_GOOGLE_CLIENT_ID or not settings.OAUTH_GOOGLE_CLIENT_SECRET:
            logger.warning(
                "Google OAuth client credentials are not configured; "
                "cannot refresh the Calendar token for user %s",
                user_id,
            )
            return None

        try:
            async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
                response = await client.post(
                    GOOGLE_TOKEN_REFRESH_URL,
                    data={
                        "client_id": settings.OAUTH_GOOGLE_CLIENT_ID,
                        "client_secret": settings.OAUTH_GOOGLE_CLIENT_SECRET,
                        "refresh_token": refresh_token,
                        "grant_type": "refresh_token",
                    },
                )
        except Exception:
            logger.warning("Google token refresh failed for user %s", user_id, exc_info=True)
            return None

        if response.status_code != 200:
            logger.error(
                "Google token refresh rejected for user %s: HTTP %s %s",
                user_id,
                response.status_code,
                response.text[:500],
            )
            return None

        token_data = response.json()
        new_access_token = token_data.get("access_token")
        if not new_access_token:
            logger.error("Google token refresh for user %s returned no access_token", user_id)
            return None

        expires_at = datetime.now(UTC) + timedelta(seconds=int(token_data.get("expires_in", 3600)))

        try:
            factory = get_session_factory()
            async with factory() as session:
                user = (
                    await session.execute(select(User).where(User.id == user_id))
                ).scalar_one_or_none()
                if user is not None:
                    user.google_calendar_access_token = new_access_token
                    user.google_calendar_token_expires_at = expires_at
                    await session.commit()
        except Exception:
            # The token is still usable for this request even if we could not store it.
            logger.warning(
                "Refreshed the Google token for user %s but could not persist it",
                user_id,
                exc_info=True,
            )

        return new_access_token

    # -- calendar ----------------------------------------------------------

    async def create_maigie_calendar(self, user_id: str) -> str | None:
        """Create the dedicated Maigie calendar and remember its id.

        Returns the existing id if one is already stored, so calling this twice does not
        leave the user with two calendars.
        """
        factory = get_session_factory()
        async with factory() as session:
            user = (
                await session.execute(select(User).where(User.id == user_id))
            ).scalar_one_or_none()
            if user is not None and user.google_calendar_id:
                return user.google_calendar_id

        access_token = await self.get_valid_access_token(user_id)
        if not access_token:
            logger.warning("No valid Google access token for user %s", user_id)
            return None

        try:
            async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
                response = await client.post(
                    f"{GOOGLE_CALENDAR_API_BASE}/calendars",
                    headers=self._headers(access_token),
                    json={
                        "summary": CALENDAR_SUMMARY,
                        "description": CALENDAR_DESCRIPTION,
                        "timeZone": "UTC",
                    },
                )
        except Exception:
            logger.warning(
                "Creating the Maigie calendar failed for user %s", user_id, exc_info=True
            )
            return None

        if response.status_code not in (200, 201):
            logger.error(
                "Creating the Maigie calendar for user %s failed: HTTP %s %s",
                user_id,
                response.status_code,
                response.text[:500],
            )
            return None

        calendar_id = response.json().get("id")
        if not calendar_id:
            logger.error("Google returned no calendar id for user %s", user_id)
            return None

        try:
            factory = get_session_factory()
            async with factory() as session:
                user = (
                    await session.execute(select(User).where(User.id == user_id))
                ).scalar_one_or_none()
                if user is not None:
                    user.google_calendar_id = calendar_id
                    await session.commit()
        except Exception:
            logger.warning(
                "Created calendar %s for user %s but could not store the id",
                calendar_id,
                user_id,
                exc_info=True,
            )

        logger.info("Created Maigie calendar %s for user %s", calendar_id, user_id)
        return calendar_id

    # -- events ------------------------------------------------------------

    async def sync_schedule_block(self, user_id: str, block_id: str) -> None:
        """Create or update the calendar event for one study block."""
        factory = get_session_factory()
        async with factory() as session:
            block = (
                await session.execute(select(ScheduleBlock).where(ScheduleBlock.id == block_id))
            ).scalar_one_or_none()
            user = (
                await session.execute(select(User).where(User.id == user_id))
            ).scalar_one_or_none()

        if block is None or user is None:
            return
        if not user.google_calendar_sync_enabled:
            return

        calendar_id = user.google_calendar_id or await self.create_maigie_calendar(user_id)
        if not calendar_id:
            return

        access_token = await self.get_valid_access_token(user_id)
        if not access_token:
            return

        body = self._event_body(block)
        existing_event_id = block.google_calendar_event_id

        try:
            async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
                if existing_event_id:
                    response = await client.put(
                        f"{GOOGLE_CALENDAR_API_BASE}/calendars/{calendar_id}"
                        f"/events/{existing_event_id}",
                        headers=self._headers(access_token),
                        json=body,
                    )
                    # The event may have been deleted in Google; fall back to creating it.
                    if response.status_code in (404, 410):
                        existing_event_id = None
                        response = await client.post(
                            f"{GOOGLE_CALENDAR_API_BASE}/calendars/{calendar_id}/events",
                            headers=self._headers(access_token),
                            json=body,
                        )
                else:
                    response = await client.post(
                        f"{GOOGLE_CALENDAR_API_BASE}/calendars/{calendar_id}/events",
                        headers=self._headers(access_token),
                        json=body,
                    )
        except Exception:
            logger.warning("Calendar sync failed for block %s", block_id, exc_info=True)
            return

        if response.status_code not in (200, 201):
            logger.error(
                "Calendar sync for block %s failed: HTTP %s %s",
                block_id,
                response.status_code,
                response.text[:500],
            )
            return

        event_id = response.json().get("id") or existing_event_id
        try:
            factory = get_session_factory()
            async with factory() as session:
                stored = (
                    await session.execute(select(ScheduleBlock).where(ScheduleBlock.id == block_id))
                ).scalar_one_or_none()
                if stored is not None:
                    stored.google_calendar_event_id = event_id
                    stored.google_calendar_synced_at = datetime.now(UTC)
                    await session.commit()
        except Exception:
            logger.warning(
                "Synced block %s to Google but could not record the event id",
                block_id,
                exc_info=True,
            )

    async def sync_existing_schedules(self, user_id: str) -> dict[str, Any]:
        """Push a user's upcoming study blocks to their calendar.

        Called right after the OAuth grant, so the calendar is not empty when the user
        first looks at it. Counts are returned so an empty result is distinguishable from
        a failure.
        """
        calendar_id = await self.create_maigie_calendar(user_id)
        if not calendar_id:
            return {"synced": 0, "failed": 0, "skipped": 0, "reason": "no_calendar"}

        now = datetime.now(UTC)
        horizon = now + timedelta(days=INITIAL_SYNC_DAYS_AHEAD)

        factory = get_session_factory()
        async with factory() as session:
            blocks = list(
                (
                    await session.execute(
                        select(ScheduleBlock)
                        .where(
                            ScheduleBlock.user_id == user_id,
                            ScheduleBlock.start_at >= now,
                            ScheduleBlock.start_at <= horizon,
                        )
                        .order_by(ScheduleBlock.start_at.asc())
                    )
                )
                .scalars()
                .all()
            )

        synced = 0
        failed = 0
        for block in blocks:
            try:
                await self.sync_schedule_block(user_id, block.id)
                synced += 1
            except Exception:
                failed += 1
                logger.exception("Failed to sync block %s for user %s", block.id, user_id)

        result = {"synced": synced, "failed": failed, "skipped": 0, "total": len(blocks)}
        logger.info("Initial calendar sync for user %s: %s", user_id, result)
        return result

    # -- helpers -----------------------------------------------------------

    @staticmethod
    def _headers(access_token: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }

    def _event_body(self, block: ScheduleBlock) -> dict[str, Any]:
        start_at = block.start_at
        end_at = block.end_at
        if start_at.tzinfo is None:
            start_at = start_at.replace(tzinfo=UTC)
        if end_at.tzinfo is None:
            end_at = end_at.replace(tzinfo=UTC)

        body: dict[str, Any] = {
            "summary": block.title,
            "description": block.description or "",
            "start": {"dateTime": start_at.isoformat(), "timeZone": "UTC"},
            "end": {"dateTime": end_at.isoformat(), "timeZone": "UTC"},
        }

        if block.recurring_rule:
            rrule = self._convert_recurring_rule_to_rrule(block.recurring_rule, start_at)
            if rrule:
                body["recurrence"] = [rrule]

        return body

    def _convert_recurring_rule_to_rrule(self, rule: str, start_date: datetime) -> str | None:
        """Turn a stored rule into an RRULE, passing through anything already in that form."""
        rule_upper = (rule or "").upper().strip()
        if not rule_upper:
            return None
        if rule_upper.startswith("RRULE:"):
            return rule_upper
        if rule_upper == "DAILY":
            return "RRULE:FREQ=DAILY"
        if rule_upper == "WEEKLY":
            # Anchor the weekly repeat to the day the block starts on.
            day_of_week = start_date.strftime("%A").upper()[:2]
            return f"RRULE:FREQ=WEEKLY;BYDAY={day_of_week}"
        if rule_upper == "MONTHLY":
            return "RRULE:FREQ=MONTHLY"
        if rule_upper == "YEARLY":
            return "RRULE:FREQ=YEARLY"
        logger.info("Unrecognised recurring rule %r; the event will not repeat", rule)
        return None


google_calendar_service = GoogleCalendarService()
