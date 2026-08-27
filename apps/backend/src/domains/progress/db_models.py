"""
Progress domain — SQLAlchemy models.

Goal, GoalMilestone, GoalProgressSnapshot, ScheduleBlock, StudySession,
UserStreak, Achievement, ReviewItem, ScheduleBehaviourLog.

Maps to existing PostgreSQL tables created by Prisma.
Column names use camelCase to match the existing schema exactly.
"""

from datetime import date, datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.shared.database.base import Base, TimestampMixin

# ---------------------------------------------------------------------------
# Goal
# ---------------------------------------------------------------------------


class Goal(Base, TimestampMixin):
    __tablename__ = "Goal"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: __import__("uuid").uuid4().hex[:25]
    )
    user_id: Mapped[str] = mapped_column(
        "userId", String, ForeignKey("User.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str | None] = mapped_column(String, nullable=True)
    target_date: Mapped[datetime | None] = mapped_column(
        "targetDate", DateTime(timezone=True), nullable=True
    )
    status: Mapped[str] = mapped_column(String, default="ACTIVE", server_default="ACTIVE")
    progress: Mapped[float] = mapped_column(Float, default=0.0, server_default="0")

    # --- What the goal measures, and against what ---
    #
    # `metricKind` is the column that makes `currentValue` honest, and it is why these four
    # arrived together. "Study 300 focused minutes" is measurable from `StudySession` and must
    # never be a number the learner typed; "Reach 80% interview readiness" maps to prep readiness
    # over a linked preparation; a goal with no measurable source is `manual` and says so.
    # Without the discriminator, `currentValue` would be a column that is sometimes measured and
    # sometimes asserted with no way to tell which — the fabricated-metrics defect this
    # programme exists to close, one table over.
    metric_kind: Mapped[str] = mapped_column(
        "metricKind", String, nullable=False, default="manual", server_default="manual"
    )
    # What the learner is aiming at. Null when they never set a figure, which is why `pace` and
    # `projectedOutcome` are null for such goals rather than computed against an invented target.
    target_value: Mapped[float | None] = mapped_column("targetValue", Float, nullable=True)
    # The unit the target is in — "minutes", "topics", "%". Stored rather than derived from
    # `metricKind`, so a `manual` goal can name a unit only the learner knows.
    unit: Mapped[str | None] = mapped_column(String, nullable=True)
    # **Only written when `metricKind` is `manual`.** For every other kind it is derived on read
    # from the measured source, because storing a copy would create a second version of a figure
    # that already exists and it would start disagreeing the moment the source moved.
    current_value: Mapped[float | None] = mapped_column("currentValue", Float, nullable=True)

    # Optional links.
    #
    # All four are `ON DELETE SET NULL`: a goal outlives the thing it was attached to. Deleting a
    # course should detach the goal, not delete the learner's stated intention along with it — which
    # is what `CASCADE` would do, and it is why `userId` above is the only cascading link on this row.
    #
    # These three were **already constrained in the database** and simply undeclared here, inherited
    # from the Prisma schema (`Goal_courseId_fkey`, `Goal_topicId_fkey`, and `Goal_circleId_fkey` —
    # still carrying its pre-rename name — all `SET NULL`). Declaring them changes no DDL; it stops
    # the model claiming a looseness the database does not have, which is the kind of disagreement
    # that gets discovered by a migration autogenerate trying to "add" what already exists.
    course_id: Mapped[str | None] = mapped_column(
        "courseId", String, ForeignKey("Course.id", ondelete="SET NULL"), nullable=True, index=True
    )
    topic_id: Mapped[str | None] = mapped_column(
        "topicId", String, ForeignKey("Topic.id", ondelete="SET NULL"), nullable=True, index=True
    )
    space_id: Mapped[str | None] = mapped_column(
        "spaceId", String, ForeignKey("Space.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # The preparation a `prep_readiness` goal measures. Added because without it that `metricKind`
    # was unreachable: the value was in the CHECK constraint and in the design's wording — "Reach
    # 80% interview readiness" maps to readiness over a linked preparation — while nothing on the
    # row said *which* preparation, so `currentValue` would have derived null forever. A metric kind
    # a learner can choose and the server can never measure is worse than not offering it.
    #
    # This is the one link that was genuinely unconstrained, because migration `043` added it as a
    # plain String on the stated grounds that its three siblings were plain too. They were not; the
    # constraints were in the database and missing only from this file. Migration `044` closes it.
    prep_id: Mapped[str | None] = mapped_column(
        "prepId", String, ForeignKey("ExamPrep.id", ondelete="SET NULL"), nullable=True, index=True
    )

    # Relationships
    schedules: Mapped[list["ScheduleBlock"]] = relationship(
        "ScheduleBlock", back_populates="goal", lazy="selectin"
    )
    # `delete-orphan`, unlike decks and their cards. A milestone is part of the goal's definition
    # rather than work the learner produced, so it has no meaning once the goal is gone — there
    # is nothing to detach it to. The deck rule went the other way precisely because cards are
    # authored content with review history attached.
    milestones: Mapped[list["GoalMilestone"]] = relationship(
        "GoalMilestone",
        back_populates="goal",
        lazy="selectin",
        cascade="all, delete-orphan",
        order_by="GoalMilestone.order_index",
    )

    __table_args__ = (
        Index("Goal_userId_status_idx", "userId", "status"),
        Index("Goal_targetDate_idx", "targetDate"),
        # A closed set, enforced in the database as well as in Pydantic. `Reflection.type` is the
        # precedent, and it is there because an unconstrained String let the Celery task write
        # `"WEEKLY"` while the service branched on `"weekly"` for months.
        CheckConstraint(
            "\"metricKind\" IN ('focused_minutes', 'topics_mastered', 'cards_reviewed', "
            "'course_progress', 'prep_readiness', 'manual')",
            name="Goal_metricKind_check",
        ),
    )

    def __repr__(self) -> str:
        return f"<Goal id={self.id} title={self.title}>"


# ---------------------------------------------------------------------------
# GoalMilestone
# ---------------------------------------------------------------------------


class GoalMilestone(Base, TimestampMixin):
    """A checkpoint on the way to a goal.

    `ReflectGoalDetailPage` has always rendered a milestone list; there was no table, so it came
    from a fixture. Milestones are the learner's own breakdown of a goal, which is why they are
    rows rather than something derived: nothing in the data can infer that "finish the syllabus"
    divides into four stages, and inventing a division would be the surface asserting structure
    the learner never described.

    Not to be confused with `Achievement`, which Reflect reads for *milestones reached* (Decision
    Q). An `Achievement` is unlocked by the system for something the learner did; a
    `GoalMilestone` is a step they planned. Merging them would mean a planned step appearing in a
    list of things accomplished.
    """

    __tablename__ = "GoalMilestone"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: __import__("uuid").uuid4().hex[:25]
    )
    # No `index=True`: the composite below leads with `goalId` and serves those lookups. The
    # older tables in this module do carry both, but they are Prisma-era and their ORM-declared
    # `ix_*` names do not match the `Table_col_idx` names actually in the database.
    goal_id: Mapped[str] = mapped_column(
        "goalId", String, ForeignKey("Goal.id", ondelete="CASCADE")
    )
    title: Mapped[str] = mapped_column(String, nullable=False)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    # The value of the goal's metric this milestone represents — 100 of 300 minutes. Null for a
    # milestone that is a step rather than a threshold, which is most of them.
    target_value: Mapped[float | None] = mapped_column("targetValue", Float, nullable=True)
    # Explicit ordering, because milestones are a sequence the learner chose and `createdAt`
    # records only the order they happened to type them in.
    order_index: Mapped[int] = mapped_column(
        "orderIndex", Integer, nullable=False, default=0, server_default="0"
    )
    # Null until reached. A timestamp rather than a boolean, so "when" is answerable — the goal
    # trend needs it, and a boolean would have to be replaced by this column later anyway.
    achieved_at: Mapped[datetime | None] = mapped_column(
        "achievedAt", DateTime(timezone=True), nullable=True
    )

    goal: Mapped["Goal"] = relationship("Goal", back_populates="milestones")

    __table_args__ = (Index("GoalMilestone_goalId_orderIndex_idx", "goalId", "orderIndex"),)

    def __repr__(self) -> str:
        return f"<GoalMilestone id={self.id} goal={self.goal_id}>"


# ---------------------------------------------------------------------------
# GoalScheduleChange
# ---------------------------------------------------------------------------


class GoalScheduleChange(Base):
    """Every time a goal's deadline moved, and why.

    **Without this, a goal that has been rewritten three times looks healthy.** `elapsed_percent`
    measures the window as `createdAt → targetDate`, so pushing `targetDate` forward enlarges the
    denominator, shrinks elapsed percent, shrinks the lag `is_at_risk` tests, and the goal marks itself
    on track by moving its own goalposts. The predicates cannot see the difference between a goal that
    was always due in December and one that was due in August and quietly moved twice. This table is
    that difference, and `previousDate` on the oldest row is the window the goal actually started with.

    A log, not a mutation of the goal: rows accumulate and nothing is ever updated in place. That is why
    it carries an explicit `createdAt` and no `updatedAt` (`Base` without `TimestampMixin`) — the same
    shape `ScheduleBehaviourLog` uses one table over, and for the same reason. An entry describes a
    moment that has already passed.

    `dateAuthority` is **snapshotted** rather than derived on read, even though `date_authority()`
    derives it from `Goal.prepId` everywhere else. `prepId` is `ON DELETE SET NULL`, so deleting a
    preparation silently reclassifies every past change on its goal from `external` to `learner` — and
    the whole point of an entry is what was true when the date moved. This is the same argument
    `PrepOutcome` makes for snapshotting readiness instead of joining it back later.

    `ON DELETE CASCADE` on the goal, matching `GoalMilestone`. The history exists to be shown beside a
    goal, so it has no reader once the goal is gone; `SET NULL` would leave rows nothing can attribute.
    """

    __tablename__ = "GoalScheduleChange"

    #: Why the date moved. A closed set enforced in the database as well as here, following
    #: `Goal_metricKind_check` — an unconstrained String is how `Reflection.type` ended up with the
    #: Celery task writing `"WEEKLY"` while the service branched on `"weekly"`.
    #:
    #: Both tokens have a writer today. There is deliberately **no** `system_extended` token yet: the
    #: nightly ladder that would extend a deadline on the learner's behalf is not built, and a value the
    #: schema offers and nothing can produce is the accept-and-ignore defect this codebase keeps
    #: closing. It arrives with its writer.
    REASONS = ("learner_edited", "plan_regenerated")

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: __import__("uuid").uuid4().hex[:25]
    )
    goal_id: Mapped[str] = mapped_column(
        "goalId", String, ForeignKey("Goal.id", ondelete="CASCADE"), nullable=False
    )
    #: Denormalised from the goal so the log can be read per learner without a join. The goal is
    #: already scoped to its owner by every caller; this is what lets a future portfolio-wide question
    #: ("how many of this learner's deadlines have moved") stay one query.
    #: No `index=True`: it would generate `ix_GoalScheduleChange_userId` while the migration creates
    #: `GoalScheduleChange_userId_idx`, and an ORM that names an index differently from the database is
    #: how autogenerate ends up proposing to "add" something that already exists. Declared in
    #: `__table_args__` below with the name the migration actually uses.
    user_id: Mapped[str] = mapped_column(
        "userId", String, ForeignKey("User.id", ondelete="CASCADE"), nullable=False
    )

    #: The deadline before this change. **Null when the goal had none** — setting a first deadline is
    #: recorded because it is a schedule change, but it is not an extension, and the extension count
    #: excludes it rather than treating "no date" as an infinitely early one.
    previous_date: Mapped[datetime | None] = mapped_column(
        "previousDate", DateTime(timezone=True), nullable=True
    )
    #: The deadline after this change. Null records a deadline being cleared, which is a real edit.
    new_date: Mapped[datetime | None] = mapped_column(
        "newDate", DateTime(timezone=True), nullable=True
    )
    reason: Mapped[str] = mapped_column(String, nullable=False)
    #: What `date_authority()` returned at the moment of the change. See the class docstring.
    date_authority: Mapped[str] = mapped_column("dateAuthority", String, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        "createdAt",
        DateTime(timezone=True),
        nullable=False,
        default=lambda: __import__("datetime").datetime.now(__import__("datetime").timezone.utc),
    )

    __table_args__ = (
        # Leads with `goalId` because the only read today is "this goal's history", and the count the
        # response publishes is grouped by it.
        Index("GoalScheduleChange_goalId_createdAt_idx", "goalId", "createdAt"),
        Index("GoalScheduleChange_userId_idx", "userId"),
        CheckConstraint(
            "reason IN ('learner_edited', 'plan_regenerated')",
            name="GoalScheduleChange_reason_check",
        ),
        CheckConstraint(
            "\"dateAuthority\" IN ('external', 'learner')",
            name="GoalScheduleChange_dateAuthority_check",
        ),
    )

    def __repr__(self) -> str:
        return f"<GoalScheduleChange goal={self.goal_id} reason={self.reason}>"


# ---------------------------------------------------------------------------
# ScheduleBlock
# ---------------------------------------------------------------------------


class ScheduleBlock(Base, TimestampMixin):
    __tablename__ = "ScheduleBlock"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: __import__("uuid").uuid4().hex[:25]
    )
    user_id: Mapped[str] = mapped_column(
        "userId", String, ForeignKey("User.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str | None] = mapped_column(String, nullable=True)
    start_at: Mapped[datetime] = mapped_column("startAt", DateTime(timezone=True), nullable=False)
    end_at: Mapped[datetime] = mapped_column("endAt", DateTime(timezone=True), nullable=False)
    recurring_rule: Mapped[str | None] = mapped_column("recurringRule", String, nullable=True)

    # Google Calendar sync
    google_calendar_event_id: Mapped[str | None] = mapped_column(
        "googleCalendarEventId", String, nullable=True
    )
    google_calendar_synced_at: Mapped[datetime | None] = mapped_column(
        "googleCalendarSyncedAt", DateTime(timezone=True), nullable=True
    )

    # Optional links
    course_id: Mapped[str | None] = mapped_column("courseId", String, nullable=True, index=True)
    topic_id: Mapped[str | None] = mapped_column("topicId", String, nullable=True, index=True)
    goal_id: Mapped[str | None] = mapped_column(
        "goalId", String, ForeignKey("Goal.id", ondelete="SET NULL"), nullable=True, index=True
    )

    # Spaced repetition link
    review_item_id: Mapped[str | None] = mapped_column(
        "reviewItemId",
        String,
        ForeignKey("ReviewItem.id", ondelete="SET NULL"),
        unique=True,
        nullable=True,
    )

    # Exam prep link
    exam_prep_id: Mapped[str | None] = mapped_column(
        "examPrepId", String, nullable=True, index=True
    )

    #: When the learner recorded this block as done. `None` means not done — **not** "we do not know",
    #: because a block is only ever completed by an explicit action.
    #:
    #: Added because nothing on this row recorded whether a planned session happened, which made
    #: "planned versus completed" — a chart the goal pages have always drawn — unanswerable. The
    #: alternatives were both worse: infer completion from a `StudySession` overlapping the block's
    #: window, which is a time coincidence wearing the word "completed" and would credit a learner who
    #: studied something else entirely; or read `ScheduleBehaviourLog`, which has exactly the right
    #: planned-versus-actual shape and which nothing in the application has ever written.
    #:
    #: A timestamp rather than a boolean, so a Tuesday session marked done on Thursday keeps Tuesday's
    #: date, and so un-completing is expressible by setting it back to null. The same reasoning
    #: `GoalMilestone.achievedAt` already follows.
    completed_at: Mapped[datetime | None] = mapped_column(
        "completedAt", DateTime(timezone=True), nullable=True
    )

    # Relationships
    goal: Mapped[Optional["Goal"]] = relationship("Goal", back_populates="schedules")
    review_item: Mapped[Optional["ReviewItem"]] = relationship(
        "ReviewItem", back_populates="schedule_block"
    )

    __table_args__ = (
        Index("ScheduleBlock_userId_startAt_idx", "userId", "startAt"),
        Index("ScheduleBlock_startAt_endAt_idx", "startAt", "endAt"),
        # The goal momentum read: every block for one goal, bucketed by the week it was planned for.
        Index("ScheduleBlock_goalId_startAt_idx", "goalId", "startAt"),
    )

    def __repr__(self) -> str:
        return f"<ScheduleBlock id={self.id} title={self.title}>"


# ---------------------------------------------------------------------------
# ReviewItem (Spaced Repetition)
# ---------------------------------------------------------------------------


class ReviewItem(Base, TimestampMixin):
    __tablename__ = "ReviewItem"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: __import__("uuid").uuid4().hex[:25]
    )
    user_id: Mapped[str] = mapped_column(
        "userId", String, ForeignKey("User.id", ondelete="CASCADE"), index=True
    )
    topic_id: Mapped[str] = mapped_column(
        "topicId", String, ForeignKey("Topic.id", ondelete="CASCADE"), index=True
    )

    # SM-2 fields
    next_review_at: Mapped[datetime] = mapped_column(
        "nextReviewAt", DateTime(timezone=True), nullable=False
    )
    interval_days: Mapped[int] = mapped_column(
        "intervalDays", Integer, default=1, server_default="1"
    )
    repetition_count: Mapped[int] = mapped_column(
        "repetitionCount", Integer, default=0, server_default="0"
    )
    ease_factor: Mapped[float] = mapped_column(
        "easeFactor", Float, default=2.5, server_default="2.5"
    )
    last_quality: Mapped[int] = mapped_column(
        "lastQuality", Integer, default=-1, server_default="-1"
    )
    lapse_count: Mapped[int] = mapped_column("lapseCount", Integer, default=0, server_default="0")
    last_reviewed_at: Mapped[datetime | None] = mapped_column(
        "lastReviewedAt", DateTime(timezone=True), nullable=True
    )

    # Relationships
    topic: Mapped[Optional["Topic"]] = relationship("Topic", lazy="selectin")
    schedule_block: Mapped[Optional["ScheduleBlock"]] = relationship(
        "ScheduleBlock", back_populates="review_item", uselist=False
    )

    __table_args__ = (Index("ReviewItem_userId_nextReviewAt_idx", "userId", "nextReviewAt"),)

    def __repr__(self) -> str:
        return f"<ReviewItem id={self.id} topicId={self.topic_id}>"


# ---------------------------------------------------------------------------
# ScheduleBehaviourLog
# ---------------------------------------------------------------------------


class ScheduleBehaviourLog(Base):
    __tablename__ = "ScheduleBehaviourLog"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: __import__("uuid").uuid4().hex[:25]
    )
    user_id: Mapped[str] = mapped_column(
        "userId", String, ForeignKey("User.id", ondelete="CASCADE"), index=True
    )
    behaviour_type: Mapped[str] = mapped_column("behaviourType", String, nullable=False)
    entity_type: Mapped[str] = mapped_column("entityType", String, nullable=False)
    entity_id: Mapped[str | None] = mapped_column("entityId", String, nullable=True)

    scheduled_at: Mapped[datetime | None] = mapped_column(
        "scheduledAt", DateTime(timezone=True), nullable=True
    )
    actual_at: Mapped[datetime | None] = mapped_column(
        "actualAt", DateTime(timezone=True), nullable=True
    )
    metadata_json: Mapped[dict | None] = mapped_column("metadata", JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        "createdAt",
        DateTime(timezone=True),
        default=lambda: __import__("datetime").datetime.now(__import__("datetime").timezone.utc),
    )

    __table_args__ = (
        Index("ScheduleBehaviourLog_userId_createdAt_idx", "userId", "createdAt"),
        Index("ScheduleBehaviourLog_behaviourType_idx", "behaviourType"),
    )


# ---------------------------------------------------------------------------
# StudySession
# ---------------------------------------------------------------------------


class StudySession(Base, TimestampMixin):
    __tablename__ = "StudySession"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: __import__("uuid").uuid4().hex[:25]
    )
    user_id: Mapped[str] = mapped_column(
        "userId", String, ForeignKey("User.id", ondelete="CASCADE"), index=True
    )

    start_time: Mapped[datetime] = mapped_column(
        "startTime", DateTime(timezone=True), nullable=False
    )
    end_time: Mapped[datetime | None] = mapped_column(
        "endTime", DateTime(timezone=True), nullable=True
    )
    duration: Mapped[float] = mapped_column(Float, default=0.0, server_default="0")

    # Context
    course_id: Mapped[str | None] = mapped_column("courseId", String, nullable=True, index=True)
    topic_id: Mapped[str | None] = mapped_column("topicId", String, nullable=True, index=True)
    # No space column. A sitting is not space-scoped work yet: nothing wrote the field, and
    # `StartSessionRequest` accepts only `courseId` and `topicId`, so it was a claim the schema made and
    # the product did not support — see migration 032. Space attribution stays derivable through
    # `Course.spaceId` until a feature needs it directly, at which point it arrives with a writer.
    metadata_json: Mapped[dict | None] = mapped_column("metadata", JSON, nullable=True)

    __table_args__ = (
        Index("StudySession_userId_startTime_idx", "userId", "startTime"),
        Index("StudySession_startTime_idx", "startTime"),
    )

    def __repr__(self) -> str:
        return f"<StudySession id={self.id} userId={self.user_id}>"


# ---------------------------------------------------------------------------
# UserStreak
# ---------------------------------------------------------------------------


class UserStreak(Base, TimestampMixin):
    __tablename__ = "UserStreak"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: __import__("uuid").uuid4().hex[:25]
    )
    user_id: Mapped[str] = mapped_column(
        "userId", String, ForeignKey("User.id", ondelete="CASCADE"), unique=True, index=True
    )

    current_streak: Mapped[int] = mapped_column(
        "currentStreak", Integer, default=0, server_default="0"
    )
    longest_streak: Mapped[int] = mapped_column(
        "longestStreak", Integer, default=0, server_default="0"
    )
    last_study_date: Mapped[datetime | None] = mapped_column(
        "lastStudyDate", DateTime(timezone=True), nullable=True
    )

    def __repr__(self) -> str:
        return f"<UserStreak userId={self.user_id} current={self.current_streak}>"


# ---------------------------------------------------------------------------
# Achievement
# ---------------------------------------------------------------------------


class Achievement(Base):
    __tablename__ = "Achievement"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: __import__("uuid").uuid4().hex[:25]
    )
    user_id: Mapped[str] = mapped_column(
        "userId", String, ForeignKey("User.id", ondelete="CASCADE"), index=True
    )

    achievement_type: Mapped[str] = mapped_column("achievementType", String, nullable=False)
    title: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str | None] = mapped_column(String, nullable=True)
    icon: Mapped[str | None] = mapped_column(String, nullable=True)
    metadata_json: Mapped[dict | None] = mapped_column("metadata", JSON, nullable=True)

    unlocked_at: Mapped[datetime] = mapped_column(
        "unlockedAt",
        DateTime(timezone=True),
        default=lambda: __import__("datetime").datetime.now(__import__("datetime").timezone.utc),
    )

    __table_args__ = (Index("Achievement_userId_achievementType_idx", "userId", "achievementType"),)

    def __repr__(self) -> str:
        return f"<Achievement id={self.id} type={self.achievement_type}>"


# ---------------------------------------------------------------------------
# GoalProgressSnapshot
# ---------------------------------------------------------------------------


class GoalProgressSnapshot(Base, TimestampMixin):
    """One day's progress for one goal. The only history behind a goal's trajectory.

    `ReflectGoalDetailPage` renders a progress trajectory and nothing recorded one. `Goal.progress`
    is mutated in place, so yesterday's value is gone the moment it changes — the same problem
    `PrepReadinessSnapshot` solved for Prepare and `DailyLearningSnapshot` for Reflect, and this
    follows those rather than inventing a third approach.

    **It cannot be backfilled, and that is the difference from its two siblings.** Decision P
    reconstructed historical mastery from `Topic.completedAt`, because completion leaves a dated
    trail. A goal's progress leaves none: it is a float the learner or a service overwrites, with no
    per-event source to replay. So this table starts empty and fills from the day it ships, the chart
    says it is building rather than drawing a flat line at today's value, and no row is ever invented
    for a day nobody observed (Decision Y).

    **The day is the learner's calendar day**, from `to_learner_local`, matching
    `DailyLearningSnapshot`. `PrepReadinessSnapshot` truncates to a UTC date and its own docstring
    records that as a bug; this does not repeat it.

    **The previous local day, matching `DailyLearningSnapshot`.** Progress is pure state, so reading
    it shortly after a learner's day ends gives that day's *closing* value — which is what a daily
    point should mean. Dating it today instead would produce a newest point whose meaning changed with
    every run ("as of whenever the task last fired"), and would put this table's x-axis half a day out
    from the one Reflect's other charts use. The cost is that work done today appears on the chart
    tomorrow, which is the same lag the learning snapshot already accepts.

    **`currentValue` is stored alongside `progress`.** They answer different questions — `progress` is
    the learner's percentage, `currentValue` the measured figure behind it — and `currentValueMeasured`
    records which of the two kinds it was, because a `manual` goal's value is asserted and every other
    kind's is derived. Recomputing the measurement on read is impossible for the same reason the table
    exists.
    """

    __tablename__ = "GoalProgressSnapshot"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: __import__("uuid").uuid4().hex[:25]
    )
    goal_id: Mapped[str] = mapped_column(
        "goalId", String, ForeignKey("Goal.id", ondelete="CASCADE"), index=True
    )
    #: Denormalised from `Goal.userId` so a learner's whole history is one predicate and
    #: authorisation does not need a join. `DailyLearningSnapshot` carries `userId` for the same
    #: reason. `CASCADE` because a snapshot of a deleted learner's goal is not a record of anything.
    user_id: Mapped[str] = mapped_column(
        "userId", String, ForeignKey("User.id", ondelete="CASCADE"), index=True
    )
    #: A day, not an instant — the unit of the trend, and what makes the writer idempotent through
    #: the unique index below.
    captured_on: Mapped[date] = mapped_column("capturedOn", Date, nullable=False)

    #: The learner's percentage, as `Goal.progress` stood on that day.
    progress: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    #: The measured figure behind the percentage. Null when the goal's kind has no source to measure
    #: — a `course_progress` goal with no `courseId` has nothing, and null says so where `0` would
    #: claim no progress (Decision I).
    current_value: Mapped[float | None] = mapped_column("currentValue", Float, nullable=True)
    #: `True` when `currentValue` came from event rows rather than from the learner. Stored because a
    #: reader cannot infer it later: `metricKind` can be edited after the fact.
    current_value_measured: Mapped[bool] = mapped_column(
        "currentValueMeasured", Boolean, nullable=False, default=False
    )
    #: The goal's lifecycle value on that day, so a chart can show where it was completed or
    #: abandoned rather than just stopping.
    status: Mapped[str] = mapped_column(String, nullable=False, default="ACTIVE")

    __table_args__ = (
        Index("GoalProgressSnapshot_goalId_capturedOn_key", "goalId", "capturedOn", unique=True),
        Index("GoalProgressSnapshot_userId_capturedOn_idx", "userId", "capturedOn"),
    )

    def __repr__(self) -> str:
        return f"<GoalProgressSnapshot goal={self.goal_id} on={self.captured_on}>"


# Import Topic for relationship resolution (avoid circular at module level)
from src.domains.knowledge.db_models import Topic  # noqa: E402, F401
