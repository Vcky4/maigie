"""
Plan-aware feature gate predicate for Circles.

A single ``gate(feature, state) -> bool`` predicate that determines whether
a Circle feature is available based on the Circle's plan state and add-on
presence. Each gate failure produces a specific error code with the
appropriate HTTP status.

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Gate state — snapshot of a Circle's plan/add-on/usage state
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CircleGateState:
    """Snapshot of a Circle's plan and usage state for gate evaluation.

    Callers build this from the Circle row and pass it to ``gate()``.
    """

    circle_plan_active: bool = False
    has_any_active_addon: bool = False
    chat_group_count: int = 0
    group_session_count: int = 0


# ---------------------------------------------------------------------------
# Feature enum and gate definitions
# ---------------------------------------------------------------------------


class CircleFeature:
    """Feature identifiers for gate checks."""

    CHAT_GROUP_CREATE = "chat_group_create"
    GROUP_SESSION_START = "group_session_start"
    DM_OPEN = "dm_open"
    PIN_RESOURCE = "pin_resource"
    BANNER_THEME = "banner_theme"
    MODERATOR_ROLE = "moderator_role"
    AI_TUTOR = "ai_tutor"
    GROUP_AI = "group_ai"
    VERSION_HISTORY = "version_history"
    DETAILED_ANALYTICS = "detailed_analytics"
    FEATURED_ELIGIBILITY = "featured_eligibility"


# Free-tier limits
_FREE_CHAT_GROUP_LIMIT = 1
_PLAN_CHAT_GROUP_LIMIT = 10
_FREE_GROUP_SESSION_LIMIT = 3
_FREE_PINNED_RESOURCE_LIMIT = 5


class CircleGateError(Exception):
    """Raised when a feature gate check fails."""

    def __init__(self, code: str, message: str, status_code: int = 402) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


# ---------------------------------------------------------------------------
# Gate predicate
# ---------------------------------------------------------------------------


def gate(feature: str, state: CircleGateState) -> bool:
    """Evaluate whether a Circle feature is available.

    Returns True if the feature is allowed. Raises ``CircleGateError``
    with the appropriate error code and HTTP status if denied.

    Gate logic per feature:
        - chat_group_create: ≤1 free / ≤10 plan
        - group_session_start: ≤3 free / unbounded plan
        - dm_open: plan-only
        - pin_resource: ≤5 free / unbounded plan (checked at call site)
        - banner_theme: plan-only
        - moderator_role: plan-only
        - ai_tutor: plan or any active add-on
        - group_ai: plan or any active add-on
        - version_history: plan-only
        - detailed_analytics: plan-only
        - featured_eligibility: plan-only
    """
    has_plan = state.circle_plan_active
    has_addon = state.has_any_active_addon

    if feature == CircleFeature.CHAT_GROUP_CREATE:
        limit = _PLAN_CHAT_GROUP_LIMIT if has_plan else _FREE_CHAT_GROUP_LIMIT
        if state.chat_group_count >= limit:
            if not has_plan and state.chat_group_count >= _FREE_CHAT_GROUP_LIMIT:
                raise CircleGateError(
                    code="CHAT_GROUPS_REQUIRE_CIRCLE_PLAN",
                    message="Upgrade to Circle Plan to create more chat groups.",
                    status_code=402,
                )
            raise CircleGateError(
                code="CHAT_GROUP_LIMIT_REACHED",
                message=f"Maximum of {limit} chat groups reached.",
                status_code=409,
            )
        return True

    if feature == CircleFeature.GROUP_SESSION_START:
        if not has_plan and state.group_session_count >= _FREE_GROUP_SESSION_LIMIT:
            raise CircleGateError(
                code="GROUP_SESSION_LIMIT_REACHED",
                message="Upgrade to Circle Plan for unlimited group sessions.",
                status_code=402,
            )
        return True

    if feature == CircleFeature.DM_OPEN:
        if not has_plan:
            raise CircleGateError(
                code="DMS_REQUIRE_CIRCLE_PLAN",
                message="Direct messages require an active Circle Plan.",
                status_code=402,
            )
        return True

    if feature == CircleFeature.PIN_RESOURCE:
        # Pinned resource limit is checked at the call site with the count;
        # this gate only checks plan-based unbounded access.
        if not has_plan:
            raise CircleGateError(
                code="PINNED_RESOURCES_REQUIRE_CIRCLE_PLAN",
                message="Upgrade to Circle Plan for unlimited pinned resources.",
                status_code=402,
            )
        return True

    if feature == CircleFeature.BANNER_THEME:
        if not has_plan:
            raise CircleGateError(
                code="BANNER_THEME_REQUIRES_CIRCLE_PLAN",
                message="Custom banner and theme require an active Circle Plan.",
                status_code=402,
            )
        return True

    if feature == CircleFeature.MODERATOR_ROLE:
        if not has_plan:
            raise CircleGateError(
                code="MODERATOR_REQUIRES_CIRCLE_PLAN",
                message="The Moderator role requires an active Circle Plan.",
                status_code=402,
            )
        return True

    if feature in (CircleFeature.AI_TUTOR, CircleFeature.GROUP_AI):
        if not has_plan and not has_addon:
            raise CircleGateError(
                code="AI_REQUIRES_CIRCLE_PLAN_OR_ADDON",
                message="AI features require a Circle Plan or an active Plus Seat add-on.",
                status_code=402,
            )
        return True

    if feature == CircleFeature.VERSION_HISTORY:
        if not has_plan:
            raise CircleGateError(
                code="VERSION_HISTORY_REQUIRES_CIRCLE_PLAN",
                message="Version history requires an active Circle Plan.",
                status_code=402,
            )
        return True

    if feature == CircleFeature.DETAILED_ANALYTICS:
        if not has_plan:
            raise CircleGateError(
                code="DETAILED_ANALYTICS_REQUIRES_CIRCLE_PLAN",
                message="Detailed analytics require an active Circle Plan.",
                status_code=402,
            )
        return True

    if feature == CircleFeature.FEATURED_ELIGIBILITY:
        if not has_plan:
            raise CircleGateError(
                code="FEATURED_REQUIRES_CIRCLE_PLAN",
                message="Featured eligibility requires an active Circle Plan.",
                status_code=402,
            )
        return True

    # Unknown feature — allow by default (fail open for forward compat)
    logger.warning("circle_gates: unknown feature %r, allowing by default", feature)
    return True


# ---------------------------------------------------------------------------
# Convenience: check pinned resource count against free limit
# ---------------------------------------------------------------------------


def check_pinned_resource_limit(state: CircleGateState, current_pinned_count: int) -> bool:
    """Check if a new pinned resource can be added.

    Free Circles: ≤5 pinned resources. Plan Circles: unbounded.
    Raises CircleGateError if the limit is reached.
    """
    if state.circle_plan_active:
        return True
    if current_pinned_count >= _FREE_PINNED_RESOURCE_LIMIT:
        raise CircleGateError(
            code="PINNED_RESOURCE_LIMIT_REACHED",
            message=(
                f"Free Circles can pin up to {_FREE_PINNED_RESOURCE_LIMIT} resources. "
                "Upgrade to Circle Plan for unlimited pins."
            ),
            status_code=402,
        )
    return True
