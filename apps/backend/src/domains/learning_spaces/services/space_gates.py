"""Plan-aware gates for space features.

Replaces a stub whose contract disagreed with its callers in every respect, so both gated
features raised `TypeError` instead of being gated. The stub exposed `SpaceGateState` as a
`StrEnum` while callers construct it with keyword fields, had no `CHAT_GROUP_CREATE`
member, and returned `ALLOWED` from an async `gate(space_id, feature)`.

The rules below are not invented. They are recovered from `tests/test_circle_gates.py`,
which specifies every feature, limit, error code and status code, and which is now
repointed at this module. Two consequences worth stating plainly, because an earlier pass
guessed at these and guessed wrong:

* The free group-session allowance is **3**, not 1.
* A space *with* a plan still has a chat-group ceiling of **10**, which is a different
  error (`409 CHAT_GROUP_LIMIT_REACHED`) from hitting the free limit
  (`402 CHAT_GROUPS_REQUIRE_CIRCLE_PLAN`). The distinction matters: one is "pay to
  continue", the other is "you cannot have more".

`gate()` returns `True` when allowed and raises `SpaceGateError` when not, which is what
`space_impl` is written against; it converts the error's `status_code`, `code` and
`message` into an `HTTPException`.

NOTE ON UNKNOWN FEATURES: an unrecognised feature is **allowed**. That is the recovered
behaviour, and it is deliberate in the original: plan gating is not authorization, and a
newly added feature should not be silently unavailable because nobody wrote a rule for it
yet. It does mean a feature intended to be paid is free until it appears below, so adding
a `SpaceFeature` member and adding its rule need to happen together.

The field is `space_plan_active` rather than the original `circle_plan_active`, matching
the circle-to-space rename and the current callers.
"""

from dataclasses import dataclass
from enum import StrEnum

from fastapi import status


class SpaceFeature(StrEnum):
    """Space features whose availability depends on the plan."""

    CHAT_GROUP_CREATE = "chat_group_create"
    GROUP_SESSION_START = "group_session_start"
    DM_OPEN = "dm_open"
    BANNER_THEME = "banner_theme"
    MODERATOR_ROLE = "moderator_role"
    AI_TUTOR = "ai_tutor"
    GROUP_AI = "group_ai"
    VERSION_HISTORY = "version_history"
    DETAILED_ANALYTICS = "detailed_analytics"
    FEATURED_ELIGIBILITY = "featured_eligibility"


# Limits, recovered from tests/test_space_gates.py.
FREE_CHAT_GROUP_LIMIT = 1
PLAN_CHAT_GROUP_LIMIT = 10
FREE_GROUP_SESSION_LIMIT = 3
FREE_PINNED_RESOURCE_LIMIT = 5

# Features that simply require a space plan, with the code each reports.
_PLAN_ONLY_FEATURES: dict[SpaceFeature, str] = {
    SpaceFeature.DM_OPEN: "DMS_REQUIRE_CIRCLE_PLAN",
    SpaceFeature.BANNER_THEME: "BANNER_THEME_REQUIRES_CIRCLE_PLAN",
    SpaceFeature.MODERATOR_ROLE: "MODERATOR_REQUIRES_CIRCLE_PLAN",
    SpaceFeature.VERSION_HISTORY: "VERSION_HISTORY_REQUIRES_CIRCLE_PLAN",
    SpaceFeature.DETAILED_ANALYTICS: "DETAILED_ANALYTICS_REQUIRES_CIRCLE_PLAN",
    SpaceFeature.FEATURED_ELIGIBILITY: "FEATURED_REQUIRES_CIRCLE_PLAN",
}

# AI features accept a seat add-on as an alternative to a full space plan.
_ADDON_ELIGIBLE_FEATURES = frozenset({SpaceFeature.AI_TUTOR, SpaceFeature.GROUP_AI})


@dataclass(frozen=True)
class SpaceGateState:
    """Everything a gate decision depends on, gathered by the caller."""

    space_plan_active: bool = False
    has_any_active_addon: bool = False
    chat_group_count: int = 0
    group_session_count: int = 0


class SpaceGateError(Exception):
    """Raised when a feature is not available to a space on its current plan."""

    def __init__(
        self,
        code: str,
        message: str,
        status_code: int = status.HTTP_402_PAYMENT_REQUIRED,
    ):
        self.code = code
        self.message = message
        self.status_code = status_code
        super().__init__(message)


def gate(feature: SpaceFeature | str, state: SpaceGateState) -> bool:
    """Return ``True`` if the feature is available, or raise ``SpaceGateError``.

    Raises:
        SpaceGateError: Carrying ``code``, ``message`` and ``status_code``, which the
            caller turns into an HTTP response.
    """
    if feature == SpaceFeature.CHAT_GROUP_CREATE:
        if not state.space_plan_active:
            if state.chat_group_count >= FREE_CHAT_GROUP_LIMIT:
                raise SpaceGateError(
                    code="CHAT_GROUPS_REQUIRE_CIRCLE_PLAN",
                    message="A space plan is required to create more chat groups.",
                )
            return True
        # On a plan there is still a ceiling, and it is not a payment problem.
        if state.chat_group_count >= PLAN_CHAT_GROUP_LIMIT:
            raise SpaceGateError(
                code="CHAT_GROUP_LIMIT_REACHED",
                message=f"A space can have at most {PLAN_CHAT_GROUP_LIMIT} chat groups.",
                status_code=status.HTTP_409_CONFLICT,
            )
        return True

    if feature == SpaceFeature.GROUP_SESSION_START:
        if not state.space_plan_active and state.group_session_count >= FREE_GROUP_SESSION_LIMIT:
            raise SpaceGateError(
                code="GROUP_SESSION_LIMIT_REACHED",
                message=(
                    f"Free spaces are limited to {FREE_GROUP_SESSION_LIMIT} group sessions. "
                    "A space plan removes the limit."
                ),
            )
        return True

    if feature in _ADDON_ELIGIBLE_FEATURES:
        if state.space_plan_active or state.has_any_active_addon:
            return True
        raise SpaceGateError(
            code="AI_REQUIRES_CIRCLE_PLAN_OR_ADDON",
            message="AI in a space requires a space plan or an active seat add-on.",
        )

    if feature in _PLAN_ONLY_FEATURES:
        if state.space_plan_active:
            return True
        raise SpaceGateError(
            code=_PLAN_ONLY_FEATURES[SpaceFeature(feature)],
            message="This feature requires a space plan.",
        )

    # Unknown features are allowed; see the note in the module docstring.
    return True


def check_pinned_resource_limit(state: SpaceGateState, pinned_count: int) -> bool:
    """Return ``True`` if another resource may be pinned, or raise.

    A space plan lifts the limit entirely.
    """
    if state.space_plan_active:
        return True
    if pinned_count >= FREE_PINNED_RESOURCE_LIMIT:
        raise SpaceGateError(
            code="PINNED_RESOURCE_LIMIT_REACHED",
            message=(
                f"Free spaces can pin up to {FREE_PINNED_RESOURCE_LIMIT} resources. "
                "A space plan removes the limit."
            ),
        )
    return True
