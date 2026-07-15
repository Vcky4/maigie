"""
Identity domain — Domain events.

Events emitted when significant identity actions occur.
Other domains listen to these via the shared event bus.
"""

from src.shared.events import emit


async def emit_user_registered(user_id: str, email: str, provider: str = "email") -> None:
    """Emitted after a new user is created (email signup or OAuth)."""
    await emit("user.registered", {
        "user_id": user_id,
        "email": email,
        "provider": provider,
    })


async def emit_user_verified(user_id: str, email: str) -> None:
    """Emitted after email verification is complete."""
    await emit("user.verified", {
        "user_id": user_id,
        "email": email,
    })


async def emit_user_onboarded(user_id: str) -> None:
    """Emitted when a user completes onboarding."""
    await emit("user.onboarded", {"user_id": user_id})


async def emit_user_tier_changed(user_id: str, old_tier: str, new_tier: str) -> None:
    """Emitted when a user's subscription tier changes."""
    await emit("user.tier_changed", {
        "user_id": user_id,
        "old_tier": old_tier,
        "new_tier": new_tier,
    })


async def emit_user_deletion_requested(user_id: str, scheduled_for: str) -> None:
    """Emitted when account deletion countdown starts."""
    await emit("user.deletion_requested", {
        "user_id": user_id,
        "scheduled_for": scheduled_for,
    })


async def emit_user_deletion_cancelled(user_id: str) -> None:
    """Emitted when account deletion is cancelled."""
    await emit("user.deletion_cancelled", {"user_id": user_id})
