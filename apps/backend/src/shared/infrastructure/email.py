"""Stub — implementation pending migration from services/email."""

from typing import Any


async def send_space_invite_email(
    to_email: str, space_name: str, inviter_name: str, invite_url: str, **kwargs
) -> None:
    """Send a space invite email."""
    pass  # TODO: migrate implementation


async def send_limit_reached_email(
    email: str | None = None,
    name: str | None = None,
    user_id: str | None = None,
    **kwargs,
) -> None:
    """Send an email when the user has reached their credit limit."""
    pass  # TODO: migrate implementation


async def send_weekly_summaries() -> None:
    """Send weekly learning summary emails to all eligible users."""
    pass  # TODO: migrate implementation


async def send_bulk_email(
    email: str, name: str | None = None, subject: str = "", content: str = "", **kwargs
) -> None:
    """Send a bulk/transactional email to a user."""
    pass  # TODO: migrate implementation
