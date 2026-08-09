"""Plan-aware gates for space features.

This replaces a stub whose contract disagreed with its callers in every respect, so
both gated features crashed rather than being gated. ``space_impl`` expects:

* ``SpaceGateState`` to be a data object carrying ``space_plan_active``,
  ``has_any_active_addon`` and a count, not a ``StrEnum``;
* ``gate(feature, state)`` to be synchronous and to take the feature first;
* a block to be signalled by raising ``SpaceGateError`` carrying ``status_code``,
  ``code`` and ``message``, which the caller turns into an ``HTTPException``.

The stub instead exposed a ``StrEnum`` named ``SpaceGateState``, an async
``gate(space_id, feature)`` returning ``ALLOWED``, and an error class with different
attributes. ``SpaceGateState(space_plan_active=...)`` therefore raised ``TypeError``
against the enum, and the enum had no ``CHAT_GROUP_CREATE`` or ``GROUP_SESSION_START``
member either.

The allowance rule follows from the fields the callers already assemble: a space with
an active plan or any active seat add-on is unrestricted, and a space with neither gets
a small free allowance before being asked to upgrade.

NOTE ON THE LIMITS: the two ``FREE_*_LIMIT`` values below are not documented anywhere
in the repository or the specs, so they are a conservative starting point rather than a
recovered policy. They are isolated here so the numbers can be set in one place once
confirmed. Nothing else in the module needs to change.
"""

from dataclasses import dataclass
from enum import StrEnum

from fastapi import status


class SpaceFeature(StrEnum):
    """Space features that are gated by plan."""

    CHAT_GROUP_CREATE = "chat_group_create"
    GROUP_SESSION_START = "group_session_start"


# Provisional. See the note in the module docstring.
FREE_CHAT_GROUP_LIMIT = 1
FREE_GROUP_SESSION_LIMIT = 1


@dataclass(frozen=True)
class SpaceGateState:
    """Everything a gate decision depends on, gathered by the caller."""

    space_plan_active: bool = False
    has_any_active_addon: bool = False
    chat_group_count: int = 0
    group_session_count: int = 0

    @property
    def has_paid_entitlement(self) -> bool:
        return self.space_plan_active or self.has_any_active_addon


class SpaceGateError(Exception):
    """Raised when a feature is not available for a space on its current plan."""

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


def gate(feature: SpaceFeature, state: SpaceGateState) -> None:
    """Allow the feature, or raise ``SpaceGateError`` explaining why not.

    Returns ``None`` when allowed. Raising rather than returning a status is what the
    callers are written against, and it means a gate that is accidentally not consulted
    cannot read as permission.
    """
    if state.has_paid_entitlement:
        return

    if feature is SpaceFeature.CHAT_GROUP_CREATE:
        if state.chat_group_count >= FREE_CHAT_GROUP_LIMIT:
            raise SpaceGateError(
                code="SPACE_PLAN_REQUIRED",
                message=(
                    f"A space plan or seat add-on is required for more than "
                    f"{FREE_CHAT_GROUP_LIMIT} chat group"
                    f"{'s' if FREE_CHAT_GROUP_LIMIT != 1 else ''}."
                ),
            )
        return

    if feature is SpaceFeature.GROUP_SESSION_START:
        if state.group_session_count >= FREE_GROUP_SESSION_LIMIT:
            raise SpaceGateError(
                code="SPACE_PLAN_REQUIRED",
                message=(
                    f"A space plan or seat add-on is required for more than "
                    f"{FREE_GROUP_SESSION_LIMIT} group session"
                    f"{'s' if FREE_GROUP_SESSION_LIMIT != 1 else ''}."
                ),
            )
        return

    # An unrecognised feature must not be silently permitted.
    raise SpaceGateError(
        code="SPACE_FEATURE_UNKNOWN",
        message=f"Unknown space feature: {feature!r}",
        status_code=status.HTTP_403_FORBIDDEN,
    )
