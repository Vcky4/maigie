"""Google Calendar integration — schedule sync."""

from .client import (
    create_maigie_calendar,
    delete_schedule_block_event,
    disconnect_calendar,
    get_calendar_status,
    sync_existing_schedules,
    sync_schedule_block,
)

__all__ = [
    "create_maigie_calendar",
    "delete_schedule_block_event",
    "disconnect_calendar",
    "get_calendar_status",
    "sync_existing_schedules",
    "sync_schedule_block",
]
