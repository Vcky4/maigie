"""Domain event type constants.

Centralizes event names to avoid typos and enable discoverability. Domains reference these constants
rather than raw strings.

**That was the stated rule and nothing followed it.** All forty-seven `emit` and `@listen` sites passed
raw string literals, these classes were referenced by nothing at all, and the cost arrived exactly where
the docstring predicted: `personal_learning.events` listened for `knowledge.topic_completed` while the
knowledge domain emitted `topic.completed`. A handler that could never fire, because two strings written
months apart in different files disagreed by one word. Thirteen names in use had no constant here either,
so the list could not have been followed without extending it first.

Both halves are now enforced by `tests/test_event_bus.py`:

- every `emit` and `@listen` must name a constant from this module, so a name that does not exist here
  is an `AttributeError` at import rather than a handler that waits forever;
- every listener's event must be emitted somewhere, which is the check that catches a constant used on
  one side of a pair and not the other.

A few constants below have no call site. They are kept: a name is cheap and an aspirational one costs
nothing, unlike an *emitter* with no caller, which reads as working behaviour. Where that distinction
matters it is recorded in `EMITTERS_WITHOUT_A_LISTENER` in the test.
"""


class IdentityEvents:
    USER_REGISTERED = "user.registered"
    USER_VERIFIED = "user.verified"
    USER_ONBOARDED = "user.onboarded"
    #: No call site — nothing emits a hard delete. Deletion is the request/cancel pair below.
    USER_DELETED = "user.deleted"
    USER_TIER_CHANGED = "user.tier_changed"
    DELETION_REQUESTED = "user.deletion_requested"
    DELETION_CANCELLED = "user.deletion_cancelled"


class KnowledgeEvents:
    COURSE_CREATED = "course.created"
    COURSE_COMPLETED = "course.completed"
    TOPIC_COMPLETED = "topic.completed"
    TOPIC_UNCOMPLETED = "topic.uncompleted"
    RESOURCE_ADDED = "resource.added"


class LearningSpaceEvents:
    SPACE_CREATED = "space.created"
    MEMBER_JOINED = "space.member_joined"
    MEMBER_LEFT = "space.member_left"
    ROLE_CHANGED = "space.role_changed"


class ClassroomEvents:
    CLASSROOM_CREATED = "classroom.created"
    SESSION_STARTED = "classroom.session_started"
    SESSION_ENDED = "classroom.session_ended"
    DISCUSSION_CREATED = "classroom.discussion_created"


class IntelligenceEvents:
    #: No call sites. Intelligence observes other domains' events and publishes none of its own yet.
    CONVERSATION_STARTED = "intelligence.conversation_started"
    MESSAGE_SENT = "intelligence.message_sent"
    RECOMMENDATION_MADE = "intelligence.recommendation_made"


class ProgressEvents:
    STREAK_UPDATED = "progress.streak_updated"
    ACHIEVEMENT_UNLOCKED = "progress.achievement_unlocked"
    #: No call site. Due reviews are read from `ReviewItem.nextReviewAt` by the agenda rather than
    #: announced, which is the same compose-on-read choice that superseded materialising them.
    REVIEW_DUE = "progress.review_due"
    STUDY_SESSION_COMPLETED = "progress.study_session_completed"


class PersonalLearningEvents:
    """What personal learning publishes about itself.

    Every name here is emitted by a wrapper in `personal_learning/events.py`, and — apart from
    `MILESTONE_REACHED`, which the streak handler emits — **those wrappers have no callers**. The events
    are declared, the helpers are written, and nothing in the application reaches them. Listed in the
    test's emitter inventory with that reason rather than deleted, because each names a real moment in a
    learner's day that a future subscriber would want.
    """

    NOTE_CREATED = "personal_learning.note_created"
    TOPIC_STUDIED = "personal_learning.topic_studied"
    TOPIC_COMPLETED = "personal_learning.topic_completed"
    QUIZ_COMPLETED = "personal_learning.quiz_completed"
    STUDY_SESSION_ENDED = "personal_learning.study_session_ended"
    FLASHCARD_REVIEWED = "personal_learning.flashcard_reviewed"
    PREPARATION_COMPLETED = "personal_learning.preparation_completed"
    MILESTONE_REACHED = "personal_learning.milestone_reached"
    STUDY_PLAN_ITEM_COMPLETED = "personal_learning.study_plan_item_completed"


class BillingEvents:
    #: No call site — a new subscription is not announced, only its cancellation.
    SUBSCRIPTION_CREATED = "billing.subscription_created"
    SUBSCRIPTION_CANCELLED = "billing.subscription_cancelled"
    CREDITS_PURCHASED = "billing.credits_purchased"
    #: No call site.
    CREDITS_DEPLETED = "billing.credits_depleted"
    REFERRAL_LINKED = "billing.referral_linked"
