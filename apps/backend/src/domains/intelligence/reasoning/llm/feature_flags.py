"""Feature flags for the LLM layer.

The pre-migration implementation was a 24k module backed by stored flag definitions and
per-scope overrides; it has not been migrated.

``is_enabled`` previously returned ``True`` for every flag, which turned an absent flag
service into a blanket "yes" and could switch on unfinished code paths. It now fails
closed. That is the safe direction for a flag check: an unknown flag reads as off, so
behaviour stays at the default rather than at whatever the newest half-built path does.
Each call logs once so the missing subsystem is visible rather than silently shaping
behaviour.
"""

import logging

logger = logging.getLogger(__name__)

PERSONAL_SCOPE = "personal"

_warned: set[str] = set()


def circle_scope(circle_id: str) -> str:
    """Return a scope string for a space."""
    return f"circle:{circle_id}"


def _warn_once(flag: str) -> None:
    if flag in _warned:
        return
    _warned.add(flag)
    logger.warning(
        "LLM feature flag service is not migrated; treating flag %r as disabled. "
        'Recoverable from git show "4953972^:apps/backend/src/services/llm/feature_flags.py".',
        flag,
    )


class FeatureFlagService:
    """Fails closed until the flag service is migrated."""

    async def is_enabled(self, flag: str, scope: str = PERSONAL_SCOPE) -> bool:
        _warn_once(flag)
        return False

    async def get_variant(self, flag: str, scope: str = PERSONAL_SCOPE) -> str | None:
        _warn_once(flag)
        return None
