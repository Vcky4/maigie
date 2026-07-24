"""
Domain event type constants.

Centralizes event names to avoid typos and enable discoverability.
Domains reference these constants rather than raw strings.
"""


class IdentityEvents:
    USER_REGISTERED = "user.registered"
    USER_ONBOARDED = "user.onboarded"
    USER_DELETED = "user.deleted"
    USER_TIER_CHANGED = "user.tier_changed"


class KnowledgeEvents:
    COURSE_CREATED = "course.created"
    COURSE_COMPLETED = "course.completed"
    TOPIC_COMPLETED = "topic.completed"
    RESOURCE_ADDED = "resource.added"


class LearningSpaceEvents:
    SPACE_CREATED = "space.created"
    MEMBER_JOINED = "space.member_joined"
    MEMBER_LEFT = "space.member_left"
    ROLE_CHANGED = "space.role_changed"


class ClassroomEvents:
    SESSION_STARTED = "classroom.session_started"
    SESSION_ENDED = "classroom.session_ended"
    DISCUSSION_CREATED = "classroom.discussion_created"


class IntelligenceEvents:
    CONVERSATION_STARTED = "intelligence.conversation_started"
    MESSAGE_SENT = "intelligence.message_sent"
    RECOMMENDATION_MADE = "intelligence.recommendation_made"


class ProgressEvents:
    STREAK_UPDATED = "progress.streak_updated"
    ACHIEVEMENT_UNLOCKED = "progress.achievement_unlocked"
    REVIEW_DUE = "progress.review_due"
    STUDY_SESSION_COMPLETED = "progress.study_session_completed"


class BillingEvents:
    SUBSCRIPTION_CREATED = "billing.subscription_created"
    SUBSCRIPTION_CANCELLED = "billing.subscription_cancelled"
    CREDITS_PURCHASED = "billing.credits_purchased"
    CREDITS_DEPLETED = "billing.credits_depleted"
