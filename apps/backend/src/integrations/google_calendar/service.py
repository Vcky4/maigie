"""Stub — implementation pending migration from services/google_calendar_service."""

from typing import Any


class GoogleCalendarService:
    """Google Calendar integration service."""

    async def create_maigie_calendar(self, user_id: str) -> str | None:
        """Create a dedicated Maigie calendar for the user."""
        return None  # TODO: migrate implementation

    async def sync_existing_schedules(self, user_id: str) -> dict[str, Any]:
        """Sync existing study blocks to Google Calendar."""
        return {}  # TODO: migrate implementation

    async def sync_schedule_block(self, user_id: str, block_id: str) -> None:
        """Sync a single study block to Google Calendar."""
        pass  # TODO: migrate implementation


google_calendar_service = GoogleCalendarService()
