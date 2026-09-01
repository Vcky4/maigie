"""Canonical notification types, actions, and policy metadata.

This module is deliberately pure: it knows no repositories, ORM models, routes,
or provider adapters. Producers and clients can depend on the contract without
creating an import cycle into notification delivery.
"""

# Python 3.11 is still supported, so these aliases cannot use Python 3.12's
# `type` statement even though Ruff normally prefers it.
# ruff: noqa: UP040

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Annotated, Literal, TypeAlias, cast, get_args

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

NotificationCategory: TypeAlias = Literal[
    "SECURITY",
    "ACCOUNT",
    "BILLING",
    "MEMBERSHIP",
    "SOCIAL",
    "CLASSROOM",
    "LEARNING",
    "PROGRESS",
    "SUPPORT",
    "OPERATIONS",
]
NotificationUrgency: TypeAlias = Literal["CRITICAL", "HIGH", "NORMAL", "LOW"]
NotificationChannel: TypeAlias = Literal["IN_APP", "MOBILE_PUSH", "WEB_PUSH", "EMAIL"]
IntelligenceScope: TypeAlias = Literal[
    "NONE",
    "TIMING_CHANNEL",
    "RANK_GROUP_TIME_CHANNEL",
    "BOUNDED_COPY",
]
ActionKind: TypeAlias = Literal[
    "NONE",
    "OPEN_HOME",
    "OPEN_GOAL",
    "OPEN_STUDY_PLAN",
    "OPEN_PREPARATION",
    "OPEN_REVIEW",
    "OPEN_RESOURCE",
    "OPEN_CLASSROOM",
    "OPEN_SESSION",
    "OPEN_ASSIGNMENT",
    "OPEN_CONVERSATION",
    "OPEN_INVITE",
    "OPEN_PROGRESS",
    "OPEN_BILLING",
    "OPEN_ACCOUNT_SECURITY",
    "OPEN_INCIDENT",
]


class _Action(BaseModel):
    """Base for versioned actions stored as camel-case JSON."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True, serialize_by_alias=True)


class NoAction(_Action):
    version: Literal[1] = 1
    kind: Literal["NONE"] = "NONE"


class OpenHomeAction(_Action):
    version: Literal[1] = 1
    kind: Literal["OPEN_HOME"] = "OPEN_HOME"


class OpenGoalAction(_Action):
    version: Literal[1] = 1
    kind: Literal["OPEN_GOAL"] = "OPEN_GOAL"
    entity_id: str = Field(alias="entityId", min_length=1)


class OpenStudyPlanAction(_Action):
    version: Literal[1] = 1
    kind: Literal["OPEN_STUDY_PLAN"] = "OPEN_STUDY_PLAN"
    entity_id: str = Field(alias="entityId", min_length=1)


class OpenPreparationAction(_Action):
    version: Literal[1] = 1
    kind: Literal["OPEN_PREPARATION"] = "OPEN_PREPARATION"
    entity_id: str = Field(alias="entityId", min_length=1)


class OpenReviewAction(_Action):
    version: Literal[1] = 1
    kind: Literal["OPEN_REVIEW"] = "OPEN_REVIEW"
    entity_id: str | None = Field(default=None, alias="entityId")


class OpenResourceAction(_Action):
    version: Literal[1] = 1
    kind: Literal["OPEN_RESOURCE"] = "OPEN_RESOURCE"
    entity_id: str = Field(alias="entityId", min_length=1)
    resource_type: str = Field(alias="resourceType", min_length=1)


class OpenClassroomAction(_Action):
    version: Literal[1] = 1
    kind: Literal["OPEN_CLASSROOM"] = "OPEN_CLASSROOM"
    entity_id: str = Field(alias="entityId", min_length=1)


class OpenSessionAction(_Action):
    version: Literal[1] = 1
    kind: Literal["OPEN_SESSION"] = "OPEN_SESSION"
    entity_id: str = Field(alias="entityId", min_length=1)


class OpenAssignmentAction(_Action):
    version: Literal[1] = 1
    kind: Literal["OPEN_ASSIGNMENT"] = "OPEN_ASSIGNMENT"
    entity_id: str = Field(alias="entityId", min_length=1)


class OpenConversationAction(_Action):
    version: Literal[1] = 1
    kind: Literal["OPEN_CONVERSATION"] = "OPEN_CONVERSATION"
    entity_id: str = Field(alias="entityId", min_length=1)


class OpenInviteAction(_Action):
    version: Literal[1] = 1
    kind: Literal["OPEN_INVITE"] = "OPEN_INVITE"
    entity_id: str = Field(alias="entityId", min_length=1)
    invite_type: Literal["SPACE", "CLASSROOM"] = Field(alias="inviteType")


class OpenProgressAction(_Action):
    version: Literal[1] = 1
    kind: Literal["OPEN_PROGRESS"] = "OPEN_PROGRESS"
    entity_id: str | None = Field(default=None, alias="entityId")


class OpenBillingAction(_Action):
    version: Literal[1] = 1
    kind: Literal["OPEN_BILLING"] = "OPEN_BILLING"
    entity_id: str | None = Field(default=None, alias="entityId")


class OpenAccountSecurityAction(_Action):
    version: Literal[1] = 1
    kind: Literal["OPEN_ACCOUNT_SECURITY"] = "OPEN_ACCOUNT_SECURITY"


class OpenIncidentAction(_Action):
    version: Literal[1] = 1
    kind: Literal["OPEN_INCIDENT"] = "OPEN_INCIDENT"
    entity_id: str | None = Field(default=None, alias="entityId")


NotificationAction: TypeAlias = Annotated[
    NoAction
    | OpenHomeAction
    | OpenGoalAction
    | OpenStudyPlanAction
    | OpenPreparationAction
    | OpenReviewAction
    | OpenResourceAction
    | OpenClassroomAction
    | OpenSessionAction
    | OpenAssignmentAction
    | OpenConversationAction
    | OpenInviteAction
    | OpenProgressAction
    | OpenBillingAction
    | OpenAccountSecurityAction
    | OpenIncidentAction,
    Field(discriminator="kind"),
]
ACTION_ADAPTER: TypeAdapter[NotificationAction] = TypeAdapter(NotificationAction)


@dataclass(frozen=True, slots=True)
class NotificationSpec:
    category: NotificationCategory
    urgency: NotificationUrgency
    default_channels: tuple[NotificationChannel, ...]
    allowed_channels: tuple[NotificationChannel, ...]
    action_kinds: tuple[ActionKind, ...]
    outcome: str
    ttl: timedelta | None
    dedupe_window: timedelta | None
    groupable: bool = False
    digestible: bool = False
    transactional: bool = False
    intelligence_scope: IntelligenceScope = "NONE"


IN_APP: tuple[NotificationChannel, ...] = ("IN_APP",)
IN_APP_PUSH: tuple[NotificationChannel, ...] = ("IN_APP", "MOBILE_PUSH", "WEB_PUSH")
ALL_CHANNELS: tuple[NotificationChannel, ...] = (
    "IN_APP",
    "MOBILE_PUSH",
    "WEB_PUSH",
    "EMAIL",
)
EMAIL_ONLY: tuple[NotificationChannel, ...] = ("EMAIL",)
IN_APP_EMAIL: tuple[NotificationChannel, ...] = ("IN_APP", "EMAIL")


def _spec(
    category: NotificationCategory,
    urgency: NotificationUrgency,
    default_channels: tuple[NotificationChannel, ...],
    action_kinds: tuple[ActionKind, ...],
    outcome: str,
    *,
    ttl: timedelta | None,
    dedupe: timedelta | None,
    allowed_channels: tuple[NotificationChannel, ...] = ALL_CHANNELS,
    groupable: bool = False,
    digestible: bool = False,
    transactional: bool = False,
    intelligence_scope: IntelligenceScope = "NONE",
) -> NotificationSpec:
    return NotificationSpec(
        category=category,
        urgency=urgency,
        default_channels=default_channels,
        allowed_channels=allowed_channels,
        action_kinds=action_kinds,
        outcome=outcome,
        ttl=ttl,
        dedupe_window=dedupe,
        groupable=groupable,
        digestible=digestible,
        transactional=transactional,
        intelligence_scope=intelligence_scope,
    )


# The registry is the single canonical vocabulary. It describes policy; it does
# not enable a producer or a channel by itself.
NOTIFICATION_SPECS: dict[str, NotificationSpec] = {
    "learning.study_session_reminder": _spec(
        "LEARNING",
        "HIGH",
        IN_APP_PUSH,
        ("OPEN_SESSION",),
        "start, complete, snooze, or reschedule the study session",
        ttl=timedelta(hours=2),
        dedupe=timedelta(hours=1),
        intelligence_scope="TIMING_CHANNEL",
    ),
    "learning.revision_reminder": _spec(
        "LEARNING",
        "NORMAL",
        IN_APP_PUSH,
        ("OPEN_REVIEW",),
        "start or complete the requested review",
        ttl=timedelta(days=1),
        dedupe=timedelta(hours=12),
        groupable=True,
        intelligence_scope="TIMING_CHANNEL",
    ),
    "learning.goal_checkin_reminder": _spec(
        "LEARNING",
        "NORMAL",
        IN_APP_PUSH,
        ("OPEN_GOAL",),
        "review the goal and choose its next step",
        ttl=timedelta(days=7),
        dedupe=timedelta(days=7),
        intelligence_scope="TIMING_CHANNEL",
    ),
    "learning.study_plan_checkin_reminder": _spec(
        "LEARNING",
        "NORMAL",
        IN_APP_PUSH,
        ("OPEN_STUDY_PLAN",),
        "review the study plan and record its next checkpoint",
        ttl=timedelta(days=7),
        dedupe=timedelta(days=7),
        intelligence_scope="TIMING_CHANNEL",
    ),
    "learning.morning_schedule": _spec(
        "LEARNING",
        "LOW",
        IN_APP,
        ("OPEN_HOME", "OPEN_STUDY_PLAN"),
        "review the local-day learning schedule",
        ttl=timedelta(days=1),
        dedupe=timedelta(days=1),
        allowed_channels=IN_APP_EMAIL,
        digestible=True,
        intelligence_scope="TIMING_CHANNEL",
    ),
    "learning.goal_deadline_changed": _spec(
        "LEARNING",
        "HIGH",
        IN_APP_PUSH,
        ("OPEN_GOAL",),
        "review the changed deadline and plan",
        ttl=timedelta(days=7),
        dedupe=timedelta(days=1),
    ),
    "learning.plan_redistributed": _spec(
        "LEARNING",
        "NORMAL",
        IN_APP,
        ("OPEN_STUDY_PLAN",),
        "review or edit the redistributed plan",
        ttl=timedelta(days=7),
        dedupe=timedelta(days=7),
        allowed_channels=IN_APP_PUSH,
    ),
    "learning.resource_ready": _spec(
        "LEARNING",
        "NORMAL",
        IN_APP,
        ("OPEN_RESOURCE",),
        "open and use the requested resource",
        ttl=timedelta(days=14),
        dedupe=None,
        allowed_channels=IN_APP_PUSH,
    ),
    "learning.resource_failed": _spec(
        "LEARNING",
        "NORMAL",
        IN_APP,
        ("OPEN_RESOURCE",),
        "retry or choose an alternative",
        ttl=timedelta(days=7),
        dedupe=None,
        allowed_channels=IN_APP_PUSH,
    ),
    "learning.next_best_action": _spec(
        "LEARNING",
        "NORMAL",
        IN_APP,
        ("OPEN_HOME", "OPEN_GOAL", "OPEN_REVIEW", "OPEN_STUDY_PLAN", "OPEN_PREPARATION"),
        "start a useful activity or choose an alternative",
        ttl=timedelta(days=1),
        dedupe=timedelta(days=1),
        intelligence_scope="RANK_GROUP_TIME_CHANNEL",
    ),
    "learning.review_due": _spec(
        "LEARNING",
        "NORMAL",
        IN_APP,
        ("OPEN_REVIEW",),
        "complete review and record its result",
        ttl=timedelta(days=3),
        dedupe=timedelta(days=1),
        allowed_channels=IN_APP_PUSH,
        groupable=True,
        digestible=True,
        intelligence_scope="RANK_GROUP_TIME_CHANNEL",
    ),
    "learning.unfinished_work_ready": _spec(
        "LEARNING",
        "LOW",
        IN_APP,
        ("OPEN_RESOURCE", "OPEN_HOME"),
        "resume, archive, or defer the work",
        ttl=timedelta(days=2),
        dedupe=timedelta(days=3),
        allowed_channels=IN_APP_PUSH,
        intelligence_scope="RANK_GROUP_TIME_CHANNEL",
    ),
    "learning.momentum_support": _spec(
        "LEARNING",
        "NORMAL",
        IN_APP,
        ("OPEN_HOME", "OPEN_STUDY_PLAN"),
        "resume, re-plan, ask for help, or choose a pause",
        ttl=timedelta(days=1),
        dedupe=timedelta(days=7),
        allowed_channels=ALL_CHANNELS,
        intelligence_scope="RANK_GROUP_TIME_CHANNEL",
    ),
    "learning.plan_adjustment_suggested": _spec(
        "LEARNING",
        "NORMAL",
        IN_APP,
        ("OPEN_STUDY_PLAN", "OPEN_GOAL"),
        "accept, edit, or reject the proposed adjustment",
        ttl=timedelta(days=7),
        dedupe=timedelta(days=7),
        allowed_channels=IN_APP_PUSH,
        intelligence_scope="RANK_GROUP_TIME_CHANNEL",
    ),
    "learning.reflection_opportunity": _spec(
        "LEARNING",
        "LOW",
        IN_APP,
        ("OPEN_PREPARATION", "OPEN_PROGRESS", "OPEN_HOME"),
        "reflect or explicitly skip",
        ttl=timedelta(days=3),
        dedupe=timedelta(days=1),
        digestible=True,
        intelligence_scope="BOUNDED_COPY",
    ),
    "learning.break_recommended": _spec(
        "LEARNING",
        "LOW",
        IN_APP,
        ("NONE",),
        "take a break, continue, or dismiss",
        ttl=timedelta(minutes=30),
        dedupe=timedelta(hours=2),
        allowed_channels=IN_APP,
        intelligence_scope="BOUNDED_COPY",
    ),
    "learning.preparation_opportunity": _spec(
        "LEARNING",
        "NORMAL",
        IN_APP,
        ("OPEN_PREPARATION",),
        "create or start a preparation plan",
        ttl=timedelta(days=7),
        dedupe=timedelta(days=3),
        allowed_channels=IN_APP_PUSH,
        intelligence_scope="RANK_GROUP_TIME_CHANNEL",
    ),
    "learning.resource_recommended": _spec(
        "LEARNING",
        "LOW",
        IN_APP,
        ("OPEN_RESOURCE",),
        "save, open, discuss, or dismiss the resource",
        ttl=timedelta(days=7),
        dedupe=timedelta(days=3),
        digestible=True,
        intelligence_scope="RANK_GROUP_TIME_CHANNEL",
    ),
    "learning.gentle_return": _spec(
        "LEARNING",
        "LOW",
        IN_APP,
        ("OPEN_HOME", "OPEN_STUDY_PLAN"),
        "resume, re-plan, pause, or disable return prompts",
        ttl=timedelta(days=2),
        dedupe=timedelta(days=14),
        allowed_channels=ALL_CHANNELS,
        intelligence_scope="RANK_GROUP_TIME_CHANNEL",
    ),
    "progress.goal_at_risk": _spec(
        "PROGRESS",
        "HIGH",
        IN_APP_PUSH,
        ("OPEN_GOAL",),
        "review the plan, begin work, or request support",
        ttl=timedelta(days=3),
        dedupe=timedelta(days=7),
        intelligence_scope="TIMING_CHANNEL",
    ),
    "progress.goal_decision_required": _spec(
        "PROGRESS",
        "HIGH",
        IN_APP_PUSH,
        ("OPEN_GOAL",),
        "complete, reschedule, deprioritize, or abandon the goal",
        ttl=timedelta(days=7),
        dedupe=timedelta(days=7),
        allowed_channels=ALL_CHANNELS,
        intelligence_scope="TIMING_CHANNEL",
    ),
    "progress.activity_milestone": _spec(
        "PROGRESS",
        "LOW",
        IN_APP,
        ("OPEN_PROGRESS",),
        "reflect or continue intended work",
        ttl=timedelta(days=7),
        dedupe=timedelta(days=1),
        allowed_channels=ALL_CHANNELS,
        groupable=True,
        digestible=True,
        intelligence_scope="BOUNDED_COPY",
    ),
    "progress.learning_improved": _spec(
        "PROGRESS",
        "LOW",
        IN_APP,
        ("OPEN_PROGRESS",),
        "reflect and choose the next action",
        ttl=timedelta(days=7),
        dedupe=timedelta(days=7),
        digestible=True,
        intelligence_scope="BOUNDED_COPY",
    ),
    "progress.weekly_summary": _spec(
        "PROGRESS",
        "LOW",
        IN_APP,
        ("OPEN_PROGRESS",),
        "review the week and choose a useful next action",
        ttl=timedelta(days=7),
        dedupe=timedelta(days=7),
        allowed_channels=IN_APP_EMAIL,
        digestible=True,
        intelligence_scope="BOUNDED_COPY",
    ),
    "progress.achievement_earned": _spec(
        "PROGRESS",
        "LOW",
        IN_APP,
        ("OPEN_PROGRESS",),
        "view, reflect on, or share the achievement",
        ttl=timedelta(days=30),
        dedupe=None,
        allowed_channels=ALL_CHANNELS,
        groupable=True,
        digestible=True,
        intelligence_scope="BOUNDED_COPY",
    ),
    "progress.contribution_milestone": _spec(
        "PROGRESS",
        "LOW",
        IN_APP,
        ("OPEN_PROGRESS",),
        "view impact or continue contributing",
        ttl=timedelta(days=7),
        dedupe=timedelta(days=7),
        digestible=True,
        intelligence_scope="BOUNDED_COPY",
    ),
    "classroom.assignment_due": _spec(
        "CLASSROOM",
        "HIGH",
        IN_APP_PUSH,
        ("OPEN_ASSIGNMENT",),
        "open, submit, or request allowed support",
        ttl=timedelta(days=1),
        dedupe=timedelta(hours=12),
        allowed_channels=ALL_CHANNELS,
        intelligence_scope="TIMING_CHANNEL",
    ),
    "classroom.assessment_upcoming": _spec(
        "CLASSROOM",
        "HIGH",
        IN_APP_PUSH,
        ("OPEN_PREPARATION", "OPEN_CLASSROOM"),
        "open or start preparation",
        ttl=timedelta(days=3),
        dedupe=timedelta(days=1),
        allowed_channels=ALL_CHANNELS,
        intelligence_scope="TIMING_CHANNEL",
    ),
    "classroom.session_starting": _spec(
        "CLASSROOM",
        "HIGH",
        IN_APP_PUSH,
        ("OPEN_SESSION",),
        "join or decline the session",
        ttl=timedelta(hours=2),
        dedupe=timedelta(hours=1),
        intelligence_scope="TIMING_CHANNEL",
    ),
    "classroom.announcement_important": _spec(
        "CLASSROOM",
        "NORMAL",
        IN_APP,
        ("OPEN_CLASSROOM",),
        "read or acknowledge the announcement",
        ttl=timedelta(days=7),
        dedupe=None,
        allowed_channels=ALL_CHANNELS,
        groupable=True,
        intelligence_scope="TIMING_CHANNEL",
    ),
    "classroom.educator_feedback_available": _spec(
        "CLASSROOM",
        "NORMAL",
        IN_APP_PUSH,
        ("OPEN_ASSIGNMENT", "OPEN_CLASSROOM"),
        "read, reflect, revise, or resubmit",
        ttl=timedelta(days=14),
        dedupe=None,
        allowed_channels=ALL_CHANNELS,
        intelligence_scope="TIMING_CHANNEL",
    ),
    "social.session_reminder": _spec(
        "SOCIAL",
        "HIGH",
        IN_APP_PUSH,
        ("OPEN_SESSION",),
        "join, decline, or reschedule",
        ttl=timedelta(hours=2),
        dedupe=timedelta(hours=1),
        intelligence_scope="TIMING_CHANNEL",
    ),
    "social.space_invite": _spec(
        "SOCIAL",
        "NORMAL",
        IN_APP_EMAIL,
        ("OPEN_INVITE",),
        "accept or decline the invitation",
        ttl=timedelta(days=7),
        dedupe=None,
        allowed_channels=ALL_CHANNELS,
    ),
    "social.classroom_invite": _spec(
        "SOCIAL",
        "NORMAL",
        IN_APP_EMAIL,
        ("OPEN_INVITE",),
        "accept or decline the invitation",
        ttl=timedelta(days=7),
        dedupe=None,
        allowed_channels=ALL_CHANNELS,
    ),
    "social.peer_response": _spec(
        "SOCIAL",
        "NORMAL",
        IN_APP_PUSH,
        ("OPEN_CONVERSATION",),
        "open, respond, or resolve the thread",
        ttl=timedelta(days=7),
        dedupe=timedelta(minutes=15),
        groupable=True,
        intelligence_scope="TIMING_CHANNEL",
    ),
    "social.mention_or_direct_message": _spec(
        "SOCIAL",
        "NORMAL",
        IN_APP_PUSH,
        ("OPEN_CONVERSATION",),
        "open or respond to the message",
        ttl=timedelta(days=7),
        dedupe=timedelta(minutes=15),
        groupable=True,
        intelligence_scope="TIMING_CHANNEL",
    ),
    "social.contribution_recognized": _spec(
        "SOCIAL",
        "LOW",
        IN_APP,
        ("OPEN_CONVERSATION", "OPEN_PROGRESS"),
        "view impact or continue contributing",
        ttl=timedelta(days=7),
        dedupe=timedelta(days=1),
        digestible=True,
        intelligence_scope="BOUNDED_COPY",
    ),
    "social.help_opportunity": _spec(
        "SOCIAL",
        "LOW",
        IN_APP,
        ("OPEN_CONVERSATION",),
        "offer help, answer, or decline",
        ttl=timedelta(days=2),
        dedupe=timedelta(days=3),
        digestible=True,
        intelligence_scope="RANK_GROUP_TIME_CHANNEL",
    ),
    "social.collaboration_suggested": _spec(
        "SOCIAL",
        "LOW",
        IN_APP,
        ("OPEN_CONVERSATION", "OPEN_CLASSROOM"),
        "invite, accept, or decline collaboration",
        ttl=timedelta(days=3),
        dedupe=timedelta(days=14),
        digestible=True,
        intelligence_scope="RANK_GROUP_TIME_CHANNEL",
    ),
    "educator.learner_support_needed": _spec(
        "SUPPORT",
        "HIGH",
        IN_APP,
        ("OPEN_CLASSROOM",),
        "review evidence and record a support action",
        ttl=timedelta(days=3),
        dedupe=timedelta(days=7),
        allowed_channels=ALL_CHANNELS,
        intelligence_scope="RANK_GROUP_TIME_CHANNEL",
    ),
    "educator.classroom_intervention_needed": _spec(
        "SUPPORT",
        "NORMAL",
        IN_APP,
        ("OPEN_CLASSROOM",),
        "create a teaching, resource, or session intervention",
        ttl=timedelta(days=7),
        dedupe=timedelta(days=7),
        digestible=True,
        intelligence_scope="RANK_GROUP_TIME_CHANNEL",
    ),
    "space.community_health_attention": _spec(
        "SUPPORT",
        "NORMAL",
        IN_APP,
        ("OPEN_CLASSROOM",),
        "start a support, discussion, or session action",
        ttl=timedelta(days=7),
        dedupe=timedelta(days=7),
        digestible=True,
        intelligence_scope="RANK_GROUP_TIME_CHANNEL",
    ),
    "support.encouragement_requested": _spec(
        "SUPPORT",
        "NORMAL",
        IN_APP_EMAIL,
        ("NONE",),
        "send consented encouragement",
        ttl=timedelta(days=2),
        dedupe=timedelta(days=7),
        allowed_channels=ALL_CHANNELS,
        intelligence_scope="TIMING_CHANNEL",
    ),
    "discovery.opportunity": _spec(
        "LEARNING",
        "LOW",
        IN_APP,
        ("OPEN_RESOURCE", "OPEN_CLASSROOM"),
        "read, practise, discuss, teach, reflect, or save",
        ttl=timedelta(days=7),
        dedupe=timedelta(days=7),
        digestible=True,
        intelligence_scope="RANK_GROUP_TIME_CHANNEL",
    ),
    "security.account_alert": _spec(
        "SECURITY",
        "CRITICAL",
        ALL_CHANNELS,
        ("OPEN_ACCOUNT_SECURITY",),
        "review and secure the account",
        ttl=timedelta(days=7),
        dedupe=None,
        transactional=True,
    ),
    "account.verification": _spec(
        "ACCOUNT",
        "HIGH",
        EMAIL_ONLY,
        ("NONE",),
        "verify the identity",
        ttl=timedelta(minutes=15),
        dedupe=timedelta(minutes=1),
        allowed_channels=EMAIL_ONLY,
        transactional=True,
    ),
    "account.password_reset": _spec(
        "ACCOUNT",
        "HIGH",
        EMAIL_ONLY,
        ("NONE",),
        "reset the password or ignore the request",
        ttl=timedelta(minutes=15),
        dedupe=timedelta(minutes=1),
        allowed_channels=EMAIL_ONLY,
        transactional=True,
    ),
    "account.identity_changed": _spec(
        "ACCOUNT",
        "HIGH",
        IN_APP_EMAIL,
        ("OPEN_ACCOUNT_SECURITY",),
        "confirm or report the identity change",
        ttl=timedelta(days=7),
        dedupe=None,
        transactional=True,
    ),
    "account.data_export_ready": _spec(
        "ACCOUNT",
        "NORMAL",
        IN_APP_EMAIL,
        ("OPEN_RESOURCE",),
        "download the export before expiry",
        ttl=timedelta(days=7),
        dedupe=None,
        transactional=True,
    ),
    "account.deletion_scheduled": _spec(
        "ACCOUNT",
        "HIGH",
        IN_APP_EMAIL,
        ("OPEN_ACCOUNT_SECURITY",),
        "confirm, cancel, or await deletion",
        ttl=timedelta(days=30),
        dedupe=None,
        transactional=True,
    ),
    "billing.receipt": _spec(
        "BILLING",
        "NORMAL",
        IN_APP_EMAIL,
        ("OPEN_BILLING",),
        "view the receipt",
        ttl=None,
        dedupe=None,
        transactional=True,
    ),
    "billing.payment_failed": _spec(
        "BILLING",
        "HIGH",
        IN_APP_EMAIL,
        ("OPEN_BILLING",),
        "repair the payment",
        ttl=timedelta(days=7),
        dedupe=timedelta(days=1),
        allowed_channels=ALL_CHANNELS,
        transactional=True,
    ),
    "billing.subscription_changed": _spec(
        "BILLING",
        "NORMAL",
        IN_APP_EMAIL,
        ("OPEN_BILLING",),
        "review the subscription state",
        ttl=None,
        dedupe=None,
        transactional=True,
    ),
    "billing.credit_balance_changed": _spec(
        "BILLING",
        "NORMAL",
        IN_APP,
        ("OPEN_BILLING",),
        "review the balance or receipt",
        ttl=timedelta(days=30),
        dedupe=None,
        allowed_channels=IN_APP_EMAIL,
        transactional=True,
    ),
    "membership.role_or_access_changed": _spec(
        "MEMBERSHIP",
        "NORMAL",
        IN_APP_EMAIL,
        ("OPEN_CLASSROOM", "OPEN_INVITE"),
        "review the new access or report an issue",
        ttl=timedelta(days=14),
        dedupe=None,
        allowed_channels=ALL_CHANNELS,
        transactional=True,
    ),
    "operations.incident_notice": _spec(
        "OPERATIONS",
        "HIGH",
        IN_APP_EMAIL,
        ("OPEN_INCIDENT",),
        "read status and instructions",
        ttl=timedelta(days=7),
        dedupe=None,
        allowed_channels=ALL_CHANNELS,
        transactional=True,
    ),
    # --- Digests -----------------------------------------------------------------
    #
    # One per settings category, because consent is expressed per category: a learner who asked
    # for a weekly Learning digest has said nothing about Progress, so a single cross-category
    # digest would either send them more than they agreed to or withhold what they asked for.
    #
    # `digestible=False` on all three: a digest must never become an item inside a later digest.
    # `action` is `NONE`, which every client resolves to the notification centre — the right
    # destination for "here is what accumulated", and honest rather than inventing a route.
    # No mobile push: the point of a digest is to stop interrupting.
    "learning.digest": _spec(
        "LEARNING",
        "LOW",
        IN_APP_EMAIL,
        ("NONE",),
        "review what accumulated and choose one thing to act on",
        ttl=timedelta(days=14),
        dedupe=None,
        allowed_channels=IN_APP_EMAIL,
    ),
    "progress.digest": _spec(
        "PROGRESS",
        "LOW",
        IN_APP_EMAIL,
        ("NONE",),
        "review the period's progress and choose a useful next action",
        ttl=timedelta(days=14),
        dedupe=None,
        allowed_channels=IN_APP_EMAIL,
    ),
    "social.digest": _spec(
        "SOCIAL",
        "LOW",
        IN_APP_EMAIL,
        ("NONE",),
        "review classroom and social activity and respond where useful",
        ttl=timedelta(days=14),
        dedupe=None,
        allowed_channels=IN_APP_EMAIL,
    ),
}


def validate_action(value: object) -> NotificationAction:
    """Validate an untrusted canonical action payload."""

    return cast(NotificationAction, ACTION_ADAPTER.validate_python(value))


def notification_spec(notification_type: str) -> NotificationSpec:
    """Return policy for a canonical type, rejecting ad-hoc producer strings."""

    try:
        return NOTIFICATION_SPECS[notification_type]
    except KeyError as exc:
        raise ValueError(f"Unknown notification type: {notification_type}") from exc


def validate_action_for_type(notification_type: str, value: object) -> NotificationAction:
    """Validate action shape and its eligibility for one notification type."""

    spec = notification_spec(notification_type)
    action = validate_action(value)
    if action.kind not in spec.action_kinds:
        raise ValueError(
            f"Action {action.kind} is not allowed for notification type {notification_type}"
        )
    return action


def canonical_action_payload(action: NotificationAction) -> dict[str, object]:
    """Serialize the wire/storage contract with canonical camel-case aliases."""

    return action.model_dump(mode="json", by_alias=True)


def _validate_registry() -> None:
    action_kinds = set(get_args(ActionKind))
    for notification_type, spec in NOTIFICATION_SPECS.items():
        if notification_type != notification_type.lower() or "." not in notification_type:
            raise RuntimeError(
                f"Notification type must be lowercase and namespaced: {notification_type}"
            )
        if not set(spec.default_channels).issubset(spec.allowed_channels):
            raise RuntimeError(f"Default channel is not allowed for {notification_type}")
        if not spec.action_kinds or not set(spec.action_kinds).issubset(action_kinds):
            raise RuntimeError(f"Invalid action contract for {notification_type}")
        if not spec.outcome.strip():
            raise RuntimeError(f"Missing outcome contract for {notification_type}")


_validate_registry()


_ANDROID_CHANNEL_BY_CATEGORY: dict[NotificationCategory, str] = {
    "SECURITY": "security",
    "ACCOUNT": "account",
    "BILLING": "billing",
    "MEMBERSHIP": "membership",
    "SOCIAL": "social",
    "CLASSROOM": "classroom",
    "LEARNING": "learning",
    "PROGRESS": "progress",
    "SUPPORT": "support",
    "OPERATIONS": "operations",
}


def android_channel_id(notification_type: str) -> str:
    """Return the stable client-configured Android channel for a canonical type."""

    return _ANDROID_CHANNEL_BY_CATEGORY[notification_spec(notification_type).category]
