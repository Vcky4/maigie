"""
Google Calendar integration — schedule sync and calendar management.

Wraps the existing google_calendar_service during migration.
"""

import logging

logger = logging.getLogger(__name__)


async def create_maigie_calendar(user_id: str) -> str | None:
    """Create a dedicated Maigie calendar for the user."""
    from src.integrations.google_calendar.service import google_calendar_service

    return await google_calendar_service.create_maigie_calendar(user_id)


async def sync_existing_schedules(user_id: str) -> dict:
    """Sync existing study blocks to Google Calendar."""
    from src.integrations.google_calendar.service import google_calendar_service

    return await google_calendar_service.sync_existing_schedules(user_id)


async def sync_schedule_block(user_id: str, block_id: str) -> None:
    """Sync a single study block to Google Calendar."""
    from src.integrations.google_calendar.service import google_calendar_service

    await google_calendar_service.sync_schedule_block(user_id, block_id)
