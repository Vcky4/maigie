"""
Personal Learning domain — SQLAlchemy models.

Note, NoteTag, NoteAttachment, NoteHistory, ExamPrep, GeneratedDocument.

Maps to existing PostgreSQL tables created by Prisma.
Column names use camelCase to match the existing schema exactly.
"""

from datetime import date, datetime
from typing import Optional

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.shared.database.base import Base, TimestampMixin

# ---------------------------------------------------------------------------
# Note
# ---------------------------------------------------------------------------


class Note(Base, TimestampMixin):
    __tablename__ = "Note"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: __import__("uuid").uuid4().hex[:25]
    )
    user_id: Mapped[str] = mapped_column(
        "userId", String, ForeignKey("User.id", ondelete="CASCADE"), index=True
    )

    title: Mapped[str] = mapped_column(String, nullable=False)
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary: Mapped[str | None] = mapped_column(String, nullable=True)

    course_id: Mapped[str | None] = mapped_column(
        "courseId", String, ForeignKey("Course.id", ondelete="SET NULL"), nullable=True, index=True
    )
    topic_id: Mapped[str | None] = mapped_column(
        "topicId", String, ForeignKey("Topic.id", ondelete="SET NULL"), nullable=True, index=True
    )
    space_id: Mapped[str | None] = mapped_column("spaceId", String, nullable=True, index=True)

    last_edited_by_id: Mapped[str | None] = mapped_column(
        "lastEditedById", String, ForeignKey("User.id", ondelete="SET NULL"), nullable=True
    )
    archived: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    voice_recording_url: Mapped[str | None] = mapped_column(
        "voiceRecordingUrl", String, nullable=True
    )

    # Relationships
    tags: Mapped[list["NoteTag"]] = relationship(
        "NoteTag", back_populates="note", cascade="all, delete-orphan", lazy="selectin"
    )
    attachments: Mapped[list["NoteAttachment"]] = relationship(
        "NoteAttachment", back_populates="note", cascade="all, delete-orphan", lazy="selectin"
    )
    history: Mapped[list["NoteHistory"]] = relationship(
        "NoteHistory", back_populates="note", cascade="all, delete-orphan", lazy="noload"
    )

    __table_args__ = (Index("Note_userId_archived_idx", "userId", "archived"),)

    def __repr__(self) -> str:
        return f"<Note id={self.id} title={self.title}>"


# ---------------------------------------------------------------------------
# NoteTag
# ---------------------------------------------------------------------------


class NoteTag(Base):
    __tablename__ = "NoteTag"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: __import__("uuid").uuid4().hex[:25]
    )
    note_id: Mapped[str] = mapped_column(
        "noteId", String, ForeignKey("Note.id", ondelete="CASCADE"), index=True
    )
    tag: Mapped[str] = mapped_column(String, nullable=False, index=True)

    # Relationships
    note: Mapped["Note"] = relationship("Note", back_populates="tags")

    def __repr__(self) -> str:
        return f"<NoteTag id={self.id} tag={self.tag}>"


# ---------------------------------------------------------------------------
# NoteAttachment
# ---------------------------------------------------------------------------


class NoteAttachment(Base):
    __tablename__ = "NoteAttachment"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: __import__("uuid").uuid4().hex[:25]
    )
    note_id: Mapped[str] = mapped_column(
        "noteId", String, ForeignKey("Note.id", ondelete="CASCADE"), index=True
    )
    filename: Mapped[str] = mapped_column(String, nullable=False)
    url: Mapped[str] = mapped_column(String, nullable=False)
    size: Mapped[int | None] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        "createdAt",
        DateTime(timezone=True),
        default=lambda: __import__("datetime").datetime.now(__import__("datetime").timezone.utc),
    )

    # Relationships
    note: Mapped["Note"] = relationship("Note", back_populates="attachments")

    def __repr__(self) -> str:
        return f"<NoteAttachment id={self.id} filename={self.filename}>"


# ---------------------------------------------------------------------------
# NoteHistory
# ---------------------------------------------------------------------------


class NoteHistory(Base):
    """A snapshot of a note taken immediately before its content was replaced.

    Written by ``note_service`` on every content-changing write — a manual edit, an AI retake — and
    read by ``GET /notes/{id}/history``. Both halves arrived in migration 033; before that the table
    existed with no producer and no consumer.

    ``title`` is snapshotted with the content so a version stays self-describing after the note is
    renamed. ``content`` is nullable because ``Note.content`` is.
    """

    __tablename__ = "NoteHistory"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: __import__("uuid").uuid4().hex[:25]
    )
    note_id: Mapped[str] = mapped_column(
        "noteId", String, ForeignKey("Note.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[str] = mapped_column(
        "userId", String, ForeignKey("User.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        "createdAt",
        DateTime(timezone=True),
        default=lambda: __import__("datetime").datetime.now(__import__("datetime").timezone.utc),
    )

    # Relationships
    note: Mapped["Note"] = relationship("Note", back_populates="history")

    def __repr__(self) -> str:
        return f"<NoteHistory id={self.id} noteId={self.note_id}>"


# ---------------------------------------------------------------------------
# ExamPrep
# ---------------------------------------------------------------------------


class ExamPrep(Base, TimestampMixin):
    __tablename__ = "ExamPrep"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: __import__("uuid").uuid4().hex[:25]
    )
    user_id: Mapped[str] = mapped_column(
        "userId", String, ForeignKey("User.id", ondelete="CASCADE"), index=True
    )

    subject: Mapped[str] = mapped_column(String, nullable=False)
    # What is being prepared for: EXAM, CERTIFICATION, INTERVIEW, PRESENTATION,
    # ASSIGNMENT, PROJECT. Nullable because rows created before this column
    # existed have no value to backfill from.
    prep_type: Mapped[str | None] = mapped_column("type", String, nullable=True)
    # How the learner wants to work, captured by the create wizard.
    # STARTING | DEVELOPING | CONFIDENT — self-reported starting point.
    confidence: Mapped[str | None] = mapped_column(String, nullable=True)
    # LIGHT | BALANCED | INTENSIVE — how hard they want to push. The effort this
    # implies (sessions per week, weekly minutes) is derived in `prep_intent`
    # rather than stored, so there is one definition of what each pace means.
    pace: Mapped[str | None] = mapped_column(String, nullable=True)
    # Readiness the learner is aiming for, 0-100. Nullable because a target is an
    # intention rather than a measurement: it cannot be derived, and defaulting it
    # would invent a goal. When null, a surface shows readiness without a target.
    target_readiness: Mapped[int | None] = mapped_column("targetReadiness", Integer, nullable=True)
    exam_date: Mapped[datetime] = mapped_column("examDate", DateTime(timezone=True), nullable=False)
    description: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, default="SETUP", server_default="SETUP")

    space_id: Mapped[str | None] = mapped_column("spaceId", String, nullable=True, index=True)

    __table_args__ = (
        Index("ExamPrep_userId_examDate_idx", "userId", "examDate"),
        Index("ExamPrep_userId_status_idx", "userId", "status"),
        Index("ExamPrep_examDate_idx", "examDate"),
    )

    def __repr__(self) -> str:
        return f"<ExamPrep id={self.id} subject={self.subject}>"


# ---------------------------------------------------------------------------
# GeneratedDocument
# ---------------------------------------------------------------------------


class GeneratedDocument(Base, TimestampMixin):
    __tablename__ = "GeneratedDocument"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: __import__("uuid").uuid4().hex[:25]
    )
    user_id: Mapped[str] = mapped_column(
        "userId", String, ForeignKey("User.id", ondelete="CASCADE"), index=True
    )

    title: Mapped[str] = mapped_column(String, nullable=False)
    format: Mapped[str] = mapped_column(String, nullable=False)
    style: Mapped[str] = mapped_column(String, default="academic", server_default="academic")
    filename: Mapped[str] = mapped_column(String, nullable=False)
    file_url: Mapped[str] = mapped_column("fileUrl", String, nullable=False)
    preview_url: Mapped[str] = mapped_column("previewUrl", String, nullable=False)
    size: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    content_type: Mapped[str] = mapped_column("contentType", String, nullable=False)

    # Share settings.
    #
    # ``shareId`` is NOT NULL in the database and always has been. The model used to declare it
    # nullable, which is how a raw-SQL insert in the chat skill came to omit it and fail at the
    # constraint — silently, inside a `except Exception: log.warning`, so every document generated
    # from chat was rendered, uploaded and then never stored. A private document still has a share
    # id; ``isPublic`` is what decides whether the id resolves.
    is_public: Mapped[bool] = mapped_column(
        "isPublic", Boolean, default=False, server_default="false"
    )
    share_id: Mapped[str] = mapped_column("shareId", String, unique=True, nullable=False)

    def __repr__(self) -> str:
        return f"<GeneratedDocument id={self.id} title={self.title}>"


# ---------------------------------------------------------------------------
# FlashcardDeck
# ---------------------------------------------------------------------------


class FlashcardDeck(Base, TimestampMixin):
    __tablename__ = "FlashcardDeck"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: __import__("uuid").uuid4().hex[:25]
    )
    user_id: Mapped[str] = mapped_column(
        "userId", String, ForeignKey("User.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str | None] = mapped_column(String, nullable=True)
    # A grouping label the learner types ("Computer Science", "Language"). Their own
    # words, so it cannot be derived; null for decks created before the create form
    # had anywhere to send it.
    subject: Mapped[str | None] = mapped_column(String, nullable=True)
    # The colour the learner picked for this deck. Presentation, and the one place
    # the API carries it, because the server never chooses it — a null accent means
    # the client derives one. See migration 020 for the reasoning.
    accent: Mapped[str | None] = mapped_column(String, nullable=True)
    # Cards per day the learner is aiming for in this deck. An intention, not a
    # measurement: null means they never set one, and a default would invent a goal.
    daily_goal: Mapped[int | None] = mapped_column("dailyGoal", Integer, nullable=True)
    course_id: Mapped[str | None] = mapped_column("courseId", String, nullable=True, index=True)
    topic_id: Mapped[str | None] = mapped_column("topicId", String, nullable=True, index=True)
    prep_id: Mapped[str | None] = mapped_column(
        "prepId", String, ForeignKey("ExamPrep.id", ondelete="SET NULL"), nullable=True
    )

    # Relationships
    #
    # No `delete-orphan`. It used to be declared here, which contradicted the
    # `SET NULL` foreign key on `Flashcard.deckId`: the ORM would delete a deck's
    # cards while the database was configured to detach them, so the outcome
    # depended on which layer performed the delete. Deleting a deck now detaches
    # its cards, matching the FK — a deck is an organising container, and removing
    # a container should not destroy authored cards along with their review
    # history. Cards are deleted individually through `DELETE /flashcards/{id}`.
    flashcards: Mapped[list["Flashcard"]] = relationship("Flashcard", back_populates="deck")

    def __repr__(self) -> str:
        return f"<FlashcardDeck id={self.id} title={self.title}>"


# ---------------------------------------------------------------------------
# Flashcard
# ---------------------------------------------------------------------------


class Flashcard(Base, TimestampMixin):
    __tablename__ = "Flashcard"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: __import__("uuid").uuid4().hex[:25]
    )
    user_id: Mapped[str] = mapped_column(
        "userId", String, ForeignKey("User.id", ondelete="CASCADE"), index=True
    )
    deck_id: Mapped[str | None] = mapped_column(
        "deckId",
        String,
        ForeignKey("FlashcardDeck.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    front: Mapped[str] = mapped_column(Text, nullable=False)
    back: Mapped[str] = mapped_column(Text, nullable=False)

    # The three review aids. Each backs a piece of the review page that was deleted for having no
    # column: a hint revealed on demand, an explanation shown with the answer, and a mnemonic beside
    # it. Nullable, and the reader omits the control rather than offering a blank one — a card with no
    # hint is different from a card whose hint is empty.
    #
    # Three columns rather than one JSON blob: they are read and edited individually, and packing them
    # together would make a partial update a read-modify-write, so two concurrent edits lose one.
    hint: Mapped[str | None] = mapped_column(Text, nullable=True)
    explanation: Mapped[str | None] = mapped_column(Text, nullable=True)
    memory_hook: Mapped[str | None] = mapped_column("memoryHook", Text, nullable=True)

    # SM-2 spaced repetition fields
    interval_days: Mapped[int] = mapped_column("intervalDays", Integer, default=1)
    repetition_count: Mapped[int] = mapped_column("repetitionCount", Integer, default=0)
    ease_factor: Mapped[float] = mapped_column("easeFactor", Float, default=2.5)
    next_review_at: Mapped[datetime] = mapped_column(
        "nextReviewAt", DateTime(timezone=True), nullable=False
    )
    last_reviewed_at: Mapped[datetime | None] = mapped_column(
        "lastReviewedAt", DateTime(timezone=True), nullable=True
    )
    last_quality: Mapped[int] = mapped_column("lastQuality", Integer, default=-1)
    lapse_count: Mapped[int] = mapped_column("lapseCount", Integer, default=0)

    # Source tracking
    source_type: Mapped[str | None] = mapped_column("sourceType", String, nullable=True)
    source_id: Mapped[str | None] = mapped_column("sourceId", String, nullable=True)

    # Relationships
    deck: Mapped[Optional["FlashcardDeck"]] = relationship(
        "FlashcardDeck", back_populates="flashcards"
    )

    __table_args__ = (
        Index("Flashcard_userId_nextReviewAt_idx", "userId", "nextReviewAt"),
        Index("Flashcard_deckId_idx", "deckId"),
    )

    def __repr__(self) -> str:
        return f"<Flashcard id={self.id} front={self.front[:30]}>"


# ---------------------------------------------------------------------------
# FlashcardReview
# ---------------------------------------------------------------------------


class FlashcardReview(Base):
    """One row per grade a learner gave a card.

    `Flashcard` holds only the most recent review, which is everything the SM-2
    scheduler needs and nothing that a question about frequency needs. Deriving
    "days active this week" from `Flashcard.lastReviewedAt` gives one date per card,
    so re-reviewing a card overwrites the earlier date and the day it belonged to
    disappears — a learner's streak could be shortened by studying. This table is
    what makes streaks, weekly counts, recall trend, per-deck recall and mastery
    change answerable. See migration 020.

    `flashcard_id` and `deck_id` are nullable and detach on delete rather than
    cascading: the review happened, and deleting the card afterwards must not
    retract it.
    """

    __tablename__ = "FlashcardReview"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: __import__("uuid").uuid4().hex[:25]
    )
    user_id: Mapped[str] = mapped_column(
        "userId", String, ForeignKey("User.id", ondelete="CASCADE"), index=True
    )
    flashcard_id: Mapped[str | None] = mapped_column(
        "flashcardId",
        String,
        ForeignKey("Flashcard.id", ondelete="SET NULL"),
        nullable=True,
    )
    # The deck the card was graded in, snapshotted, because a card can be moved
    # later and per-deck recall must attribute the grade where it was earned.
    deck_id: Mapped[str | None] = mapped_column(
        "deckId",
        String,
        ForeignKey("FlashcardDeck.id", ondelete="SET NULL"),
        nullable=True,
    )
    quality: Mapped[int] = mapped_column(Integer, nullable=False)

    # SM-2 state *after* this review, so an earlier state can be replayed. Without
    # these, "was this card mature a week ago" is unanswerable.
    interval_days: Mapped[int] = mapped_column("intervalDays", Integer, nullable=False)
    ease_factor: Mapped[float] = mapped_column("easeFactor", Float, nullable=False)
    repetition_count: Mapped[int] = mapped_column("repetitionCount", Integer, nullable=False)
    # Stored rather than recomputed from `quality`, so tuning the lapse threshold
    # does not retroactively change what historic rows mean.
    was_lapse: Mapped[bool] = mapped_column(
        "wasLapse", Boolean, nullable=False, default=False, server_default="false"
    )

    reviewed_at: Mapped[datetime] = mapped_column(
        "reviewedAt", DateTime(timezone=True), nullable=False
    )

    __table_args__ = (
        Index("FlashcardReview_userId_reviewedAt_idx", "userId", "reviewedAt"),
        Index("FlashcardReview_deckId_reviewedAt_idx", "deckId", "reviewedAt"),
        Index("FlashcardReview_flashcardId_reviewedAt_idx", "flashcardId", "reviewedAt"),
    )

    def __repr__(self) -> str:
        return f"<FlashcardReview id={self.id} quality={self.quality}>"


# ---------------------------------------------------------------------------
# SavedResource
# ---------------------------------------------------------------------------


class SavedResource(Base, TimestampMixin):
    __tablename__ = "SavedResource"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: __import__("uuid").uuid4().hex[:25]
    )
    user_id: Mapped[str] = mapped_column(
        "userId", String, ForeignKey("User.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String, nullable=False)
    url: Mapped[str | None] = mapped_column(String, nullable=True)
    source_type: Mapped[str] = mapped_column("sourceType", String, nullable=False)
    source_id: Mapped[str | None] = mapped_column("sourceId", String, nullable=True)
    tags: Mapped[dict | None] = mapped_column("tags", JSON, nullable=True)
    last_accessed_at: Mapped[datetime | None] = mapped_column(
        "lastAccessedAt", DateTime(timezone=True), nullable=True
    )

    __table_args__ = (Index("SavedResource_userId_sourceType_idx", "userId", "sourceType"),)

    def __repr__(self) -> str:
        return f"<SavedResource id={self.id} title={self.title}>"


# ---------------------------------------------------------------------------
# LearningProfile
# ---------------------------------------------------------------------------


class LearningProfile(Base, TimestampMixin):
    __tablename__ = "LearningProfile"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: __import__("uuid").uuid4().hex[:25]
    )
    user_id: Mapped[str] = mapped_column(
        "userId", String, ForeignKey("User.id", ondelete="CASCADE"), unique=True, index=True
    )

    # Onboarding state machine
    onboarding_state: Mapped[str] = mapped_column(
        "onboardingState",
        String,
        nullable=False,
        default="not_started",
        server_default="not_started",
    )

    # Purpose and context
    purpose: Mapped[str | None] = mapped_column(String, nullable=True)
    subjects: Mapped[dict | None] = mapped_column("subjects", JSON, nullable=True)
    goals_text: Mapped[str | None] = mapped_column("goalsText", Text, nullable=True)

    # Exam prep specific fields
    exam_name: Mapped[str | None] = mapped_column("examName", String, nullable=True)
    exam_date: Mapped[date | None] = mapped_column("examDate", Date, nullable=True)

    # Skill building specific fields
    skill_name: Mapped[str | None] = mapped_column("skillName", String, nullable=True)
    current_level: Mapped[str | None] = mapped_column("currentLevel", String, nullable=True)
    preferred_explanation_style: Mapped[str | None] = mapped_column(
        "preferredExplanationStyle", String, nullable=True
    )
    proficiency_map: Mapped[dict | None] = mapped_column("proficiencyMap", JSON, nullable=True)
    onboarding_completed_at: Mapped[datetime | None] = mapped_column(
        "onboardingCompletedAt", DateTime(timezone=True), nullable=True
    )
    maturity_days: Mapped[int] = mapped_column("maturityDays", Integer, default=0)
    quiet_hours_start: Mapped[str | None] = mapped_column("quietHoursStart", String, nullable=True)
    quiet_hours_end: Mapped[str | None] = mapped_column("quietHoursEnd", String, nullable=True)
    max_daily_notifications: Mapped[int] = mapped_column(
        "maxDailyNotifications", Integer, default=5
    )

    # Behaviour cache (updated by background task)
    preferred_study_times: Mapped[dict | None] = mapped_column(
        "preferredStudyTimes", JSON, nullable=True
    )
    avg_session_minutes: Mapped[float | None] = mapped_column(
        "avgSessionMinutes", Float, nullable=True
    )
    consistency_score: Mapped[float | None] = mapped_column(
        "consistencyScore", Float, nullable=True
    )
    best_day_of_week: Mapped[str | None] = mapped_column("bestDayOfWeek", String, nullable=True)
    dropout_risk: Mapped[float | None] = mapped_column("dropoutRisk", Float, nullable=True)

    # LLM provider preference (gemini, openai, anthropic; null = system default)
    preferred_llm_provider: Mapped[str | None] = mapped_column(
        "preferredLlmProvider", String, nullable=True
    )

    # --- Commercial: Trial tracking ---
    trial_started_at: Mapped[datetime | None] = mapped_column(
        "trialStartedAt", DateTime(timezone=True), nullable=True
    )
    trial_ends_at: Mapped[datetime | None] = mapped_column(
        "trialEndsAt", DateTime(timezone=True), nullable=True
    )
    last_trial_ended_at: Mapped[datetime | None] = mapped_column(
        "lastTrialEndedAt", DateTime(timezone=True), nullable=True
    )

    # --- Commercial: Conversion trigger tracking ---
    last_trigger_shown_at: Mapped[datetime | None] = mapped_column(
        "lastTriggerShownAt", DateTime(timezone=True), nullable=True
    )
    trigger_dismissal_count: Mapped[int] = mapped_column(
        "triggerDismissalCount", Integer, default=0, server_default="0"
    )
    last_trigger_dismissed_at: Mapped[datetime | None] = mapped_column(
        "lastTriggerDismissedAt", DateTime(timezone=True), nullable=True
    )

    # --- Commercial: Educator transition tracking ---
    educator_readiness_met_at: Mapped[datetime | None] = mapped_column(
        "educatorReadinessMetAt", DateTime(timezone=True), nullable=True
    )
    educator_suggestion_shown_at: Mapped[datetime | None] = mapped_column(
        "educatorSuggestionShownAt", DateTime(timezone=True), nullable=True
    )
    space_trial_started_at: Mapped[datetime | None] = mapped_column(
        "spaceTrialStartedAt", DateTime(timezone=True), nullable=True
    )

    # --- Commercial: Value tracking ---
    last_value_summary_at: Mapped[datetime | None] = mapped_column(
        "lastValueSummaryAt", DateTime(timezone=True), nullable=True
    )
    plus_features_used_this_period: Mapped[dict | None] = mapped_column(
        "plusFeaturesUsedThisPeriod", JSON, nullable=True
    )

    __table_args__ = (Index("LearningProfile_userId_idx", "userId"),)

    def __repr__(self) -> str:
        return f"<LearningProfile id={self.id} userId={self.user_id}>"


# ---------------------------------------------------------------------------
# Notification
# ---------------------------------------------------------------------------


class Notification(Base, TimestampMixin):
    __tablename__ = "Notification"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: __import__("uuid").uuid4().hex[:25]
    )
    user_id: Mapped[str] = mapped_column(
        "userId", String, ForeignKey("User.id", ondelete="CASCADE"), index=True
    )
    type: Mapped[str] = mapped_column(String, nullable=False)
    title: Mapped[str] = mapped_column(String, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    priority: Mapped[int] = mapped_column(Integer, default=5)
    action_data: Mapped[dict | None] = mapped_column("actionData", JSON, nullable=True)
    scheduled_at: Mapped[datetime] = mapped_column(
        "scheduledAt", DateTime(timezone=True), nullable=False
    )
    delivered_at: Mapped[datetime | None] = mapped_column(
        "deliveredAt", DateTime(timezone=True), nullable=True
    )
    read_at: Mapped[datetime | None] = mapped_column(
        "readAt", DateTime(timezone=True), nullable=True
    )
    dismissed_at: Mapped[datetime | None] = mapped_column(
        "dismissedAt", DateTime(timezone=True), nullable=True
    )
    status: Mapped[str] = mapped_column(String, default="PENDING")

    __table_args__ = (
        Index("Notification_userId_status_idx", "userId", "status"),
        Index("Notification_scheduledAt_idx", "scheduledAt"),
    )

    def __repr__(self) -> str:
        return f"<Notification id={self.id} type={self.type}>"


# ---------------------------------------------------------------------------
# PrepTopic
# ---------------------------------------------------------------------------


class PrepTopic(Base, TimestampMixin):
    __tablename__ = "PrepTopic"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: __import__("uuid").uuid4().hex[:25]
    )
    prep_id: Mapped[str] = mapped_column(
        "prepId", String, ForeignKey("ExamPrep.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # A grouping label ("Foundations", "Statistical inference"). A property of the
    # topic rather than of its mastery, so it cannot be computed from anything
    # else. Null for topics extracted before this existed; they render ungrouped.
    category: Mapped[str | None] = mapped_column(String, nullable=True)
    estimated_minutes: Mapped[int] = mapped_column("estimatedMinutes", Integer, default=30)
    order_index: Mapped[int] = mapped_column("orderIndex", Integer, default=0)
    mastery_score: Mapped[float] = mapped_column("masteryScore", Float, default=0.0)
    # Mastery this topic is aiming for, 0-100. Falls back to the preparation's
    # target when null, so per-topic targets are an override rather than a
    # requirement.
    target_mastery: Mapped[float | None] = mapped_column("targetMastery", Float, nullable=True)
    status: Mapped[str] = mapped_column(String, default="NOT_STARTED")

    __table_args__ = (Index("PrepTopic_prepId_order_idx", "prepId", "orderIndex"),)

    def __repr__(self) -> str:
        return f"<PrepTopic id={self.id} title={self.title}>"


# ---------------------------------------------------------------------------
# PrepMaterial
# ---------------------------------------------------------------------------


class PrepMaterial(Base, TimestampMixin):
    __tablename__ = "PrepMaterial"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: __import__("uuid").uuid4().hex[:25]
    )
    prep_id: Mapped[str] = mapped_column(
        "prepId", String, ForeignKey("ExamPrep.id", ondelete="CASCADE"), index=True
    )
    filename: Mapped[str] = mapped_column(String, nullable=False)
    url: Mapped[str] = mapped_column(String, nullable=False)
    file_type: Mapped[str | None] = mapped_column("fileType", String, nullable=True)
    size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    extracted_text: Mapped[str | None] = mapped_column("extractedText", Text, nullable=True)
    category: Mapped[str] = mapped_column(String, default="OTHER")
    label: Mapped[str | None] = mapped_column(String, nullable=True)

    def __repr__(self) -> str:
        return f"<PrepMaterial id={self.id} filename={self.filename}>"


# ---------------------------------------------------------------------------
# QuizSession
# ---------------------------------------------------------------------------


class QuizSession(Base, TimestampMixin):
    __tablename__ = "QuizSession"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: __import__("uuid").uuid4().hex[:25]
    )
    user_id: Mapped[str] = mapped_column(
        "userId", String, ForeignKey("User.id", ondelete="CASCADE"), index=True
    )
    prep_id: Mapped[str] = mapped_column(
        "prepId", String, ForeignKey("ExamPrep.id", ondelete="CASCADE"), index=True
    )
    mode: Mapped[str] = mapped_column(String, nullable=False)
    topic_id: Mapped[str | None] = mapped_column(
        "topicId", String, ForeignKey("PrepTopic.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(String, default="IN_PROGRESS")
    total_questions: Mapped[int] = mapped_column("totalQuestions", Integer, default=0)
    correct_count: Mapped[int] = mapped_column("correctCount", Integer, default=0)
    score_percentage: Mapped[float | None] = mapped_column("scorePercentage", Float, nullable=True)
    duration_seconds: Mapped[int | None] = mapped_column("durationSeconds", Integer, nullable=True)
    # How long question selection and generation took, in milliseconds.
    #
    # Decision H says quiz start stays synchronous until p95 exceeds 10s. That was
    # unanswerable: the figure was emitted only as a log field, so unlike every
    # other measurement on this surface it could not be read with a script against
    # the database. Persisting it makes the trigger checkable — see
    # `scripts/check_generation_latency.py`.
    #
    # Nullable with no default, because sessions that predate this column have no
    # timing and zero would read as instantaneous.
    generation_ms: Mapped[int | None] = mapped_column("generationMs", Integer, nullable=True)
    # Which phase of generation the session is in while `status` is `GENERATING`.
    #
    # Exists so the wait screen can report a stage the server actually reached.
    # Phase 4e refused to show a staged progress bar driven by a timer, because the
    # client cannot observe the stages of a POST that does not return until it is
    # done — it would have read "Writing questions" for a request that had already
    # failed selecting them. Generation is now backgrounded and this is what the
    # client polls. See `quiz_engine.GenerationStage`.
    generation_stage: Mapped[str | None] = mapped_column("generationStage", String, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(
        "completedAt", DateTime(timezone=True), nullable=True
    )

    # Relationships
    answers: Mapped[list["QuizAnswer"]] = relationship(
        "QuizAnswer", back_populates="quiz_session", cascade="all, delete-orphan"
    )

    __table_args__ = (Index("QuizSession_userId_prepId_idx", "userId", "prepId"),)

    def __repr__(self) -> str:
        return f"<QuizSession id={self.id} mode={self.mode}>"


# ---------------------------------------------------------------------------
# QuizQuestion
# ---------------------------------------------------------------------------


class PrepQuestion(Base, TimestampMixin):
    """A question belonging to a *preparation*, not to a single quiz session.

    Replaces the old ``QuizQuestion``, whose rows were owned by a session. That
    ownership made "every question for this preparation" inexpressible, so the
    workspace could not offer a question bank, and it meant every session
    regenerated its questions from scratch even for material already covered.

    A session now references banked questions through ``QuizSessionQuestion``,
    which is what carries the per-session ordering.
    """

    __tablename__ = "PrepQuestion"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: __import__("uuid").uuid4().hex[:25]
    )
    prep_id: Mapped[str] = mapped_column(
        "prepId", String, ForeignKey("ExamPrep.id", ondelete="CASCADE"), index=True
    )
    prep_topic_id: Mapped[str | None] = mapped_column(
        "prepTopicId", String, ForeignKey("PrepTopic.id", ondelete="SET NULL"), nullable=True
    )
    question_text: Mapped[str] = mapped_column("questionText", Text, nullable=False)
    question_type: Mapped[str] = mapped_column("questionType", String, default="MULTIPLE_CHOICE")
    # A JSON array of answer strings. The original `dict` annotation never matched
    # what the generator writes.
    options: Mapped[list | None] = mapped_column(JSON, nullable=True)
    correct_answer: Mapped[str] = mapped_column("correctAnswer", String, nullable=False)
    explanation: Mapped[str | None] = mapped_column(Text, nullable=True)

    # EASY | MEDIUM | HARD. Nullable: questions banked before this existed have no
    # difficulty, and inferring one would invent data.
    difficulty: Mapped[str | None] = mapped_column(String, nullable=True)
    # AI_GENERATED | PAST_PAPER. Set by the server, never reported by the
    # generator: provenance a producer can self-declare is not provenance.
    source: Mapped[str | None] = mapped_column(String, nullable=True)
    # Only meaningful for past papers.
    source_year: Mapped[int | None] = mapped_column("sourceYear", Integer, nullable=True)
    # Advice for tackling this kind of question. Disclosed with the answer key, not
    # before: a tip written about a specific question can hint at its answer.
    exam_tip: Mapped[str | None] = mapped_column("examTip", Text, nullable=True)
    # Points at the approach without giving the answer away. Deliberately weaker
    # than `explanation`: a hint that paraphrases the explanation is an answer key
    # with a different label. Validated on the way in — a hint containing the
    # correct answer is rejected rather than stored.
    hint_nudge: Mapped[str | None] = mapped_column("hintNudge", Text, nullable=True)

    # Lifetime statistics for this question, across every session that used it.
    # Only expressible now that a question outlives a session.
    times_answered: Mapped[int] = mapped_column(
        "timesAnswered", Integer, default=0, server_default="0"
    )
    times_correct: Mapped[int] = mapped_column(
        "timesCorrect", Integer, default=0, server_default="0"
    )

    __table_args__ = (Index("PrepQuestion_prepId_prepTopicId_idx", "prepId", "prepTopicId"),)

    def __repr__(self) -> str:
        return f"<PrepQuestion id={self.id} type={self.question_type}>"


# ---------------------------------------------------------------------------
# PracticeObservation
# ---------------------------------------------------------------------------


class PracticeObservation(Base, TimestampMixin):
    """One thing practice revealed about a learner. Append-only.

    Separate from ``QuizAnswer`` because that table cascades from ``QuizSession``:
    deleting a practice session would erase the evidence, which would make it
    impossible to revisit earlier observations in light of new ones. Here the
    session and question references are ``SET NULL``, so the evidence outlives
    them.

    Still cascades from ``User`` and ``ExamPrep`` — deleting an account or a
    preparation is a request to forget, and memory has to be forgettable.

    Never updated. A conclusion about a learner should change because new
    observations arrived, not because an old one was rewritten.
    """

    __tablename__ = "PracticeObservation"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: __import__("uuid").uuid4().hex[:25]
    )
    user_id: Mapped[str] = mapped_column(
        "userId", String, ForeignKey("User.id", ondelete="CASCADE"), index=True
    )
    prep_id: Mapped[str] = mapped_column(
        "prepId", String, ForeignKey("ExamPrep.id", ondelete="CASCADE"), index=True
    )
    prep_topic_id: Mapped[str | None] = mapped_column(
        "prepTopicId", String, ForeignKey("PrepTopic.id", ondelete="SET NULL"), nullable=True
    )
    prep_question_id: Mapped[str | None] = mapped_column(
        "prepQuestionId",
        String,
        ForeignKey("PrepQuestion.id", ondelete="SET NULL"),
        nullable=True,
    )
    quiz_session_id: Mapped[str | None] = mapped_column(
        "quizSessionId",
        String,
        ForeignKey("QuizSession.id", ondelete="SET NULL"),
        nullable=True,
    )

    is_correct: Mapped[bool] = mapped_column("isCorrect", Boolean, nullable=False)
    # Null when the client did not report it — distinct from "answered instantly".
    # A fluency signal only. Never a score, and never surfaced as a judgement.
    response_ms: Mapped[int | None] = mapped_column("responseMs", Integer, nullable=True)
    hint_used: Mapped[bool] = mapped_column(
        "hintUsed", Boolean, default=False, server_default="false"
    )
    hint_count: Mapped[int] = mapped_column("hintCount", Integer, default=0, server_default="0")
    # Copied, not joined: a question's difficulty may be recalibrated later, and an
    # observation records what was true when it happened.
    difficulty: Mapped[str | None] = mapped_column(String, nullable=True)
    observed_at: Mapped[datetime] = mapped_column(
        "observedAt", DateTime(timezone=True), nullable=False
    )

    __table_args__ = (
        Index(
            "PracticeObservation_userId_prepTopicId_observedAt_idx",
            "userId",
            "prepTopicId",
            "observedAt",
        ),
        Index(
            "PracticeObservation_userId_prepId_observedAt_idx",
            "userId",
            "prepId",
            "observedAt",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<PracticeObservation user={self.user_id} topic={self.prep_topic_id} "
            f"correct={self.is_correct} hints={self.hint_count}>"
        )


# ---------------------------------------------------------------------------
# PrepReadinessSnapshot
# ---------------------------------------------------------------------------


class PrepReadinessSnapshot(Base, TimestampMixin):
    """One day's readiness for one preparation.

    The only history in the Prepare domain. Topic mastery is a mutable float, so
    without this table yesterday's readiness is unrecoverable and a trend is not
    derivable — no amount of querying fixes that.

    Written from the same `prep_readiness` helper that serves live reads, so a
    snapshot cannot disagree with what the dashboard showed that day.
    """

    __tablename__ = "PrepReadinessSnapshot"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: __import__("uuid").uuid4().hex[:25]
    )
    prep_id: Mapped[str] = mapped_column(
        "prepId", String, ForeignKey("ExamPrep.id", ondelete="CASCADE"), index=True
    )
    # A day, not an instant: the unit of the trend, and what makes the writer
    # idempotent through the unique constraint below.
    captured_on: Mapped[date] = mapped_column("capturedOn", Date, nullable=False)

    progress_percent: Mapped[float] = mapped_column("progressPercent", Float, nullable=False)
    # Null when there are no topics to average — not measured, rather than zero.
    average_mastery_percent: Mapped[float | None] = mapped_column(
        "averageMasteryPercent", Float, nullable=True
    )
    topics_total: Mapped[int] = mapped_column("topicsTotal", Integer, default=0)
    topics_strong: Mapped[int] = mapped_column("topicsStrong", Integer, default=0)
    topics_focus: Mapped[int] = mapped_column("topicsFocus", Integer, default=0)
    topics_assessed: Mapped[int] = mapped_column("topicsAssessed", Integer, default=0)
    questions_answered: Mapped[int] = mapped_column("questionsAnswered", Integer, default=0)
    # Null until at least one question has been answered.
    accuracy_percent: Mapped[float | None] = mapped_column("accuracyPercent", Float, nullable=True)
    quizzes_taken: Mapped[int] = mapped_column("quizzesTaken", Integer, default=0)
    # Where readiness needed to be on this day to reach the target by the exam.
    # Stored rather than recomputed on read: a learner who raises their target
    # should not find last month's chart redrawn around the new one. Null when the
    # preparation has no stated target.
    target_percent: Mapped[float | None] = mapped_column("targetPercent", Float, nullable=True)

    __table_args__ = (
        UniqueConstraint("prepId", "capturedOn", name="PrepReadinessSnapshot_unique"),
        Index("PrepReadinessSnapshot_prepId_capturedOn_idx", "prepId", "capturedOn"),
    )

    def __repr__(self) -> str:
        return f"<PrepReadinessSnapshot prep={self.prep_id} on={self.captured_on}>"


# ---------------------------------------------------------------------------
# PrepQuestionFlag
# ---------------------------------------------------------------------------


class PrepQuestionFlag(Base, TimestampMixin):
    """A learner's flag on a banked question, for later review.

    Scoped by learner and question rather than by session, so a flag survives the
    session it was raised in — which is the only reason to flag anything.
    """

    __tablename__ = "PrepQuestionFlag"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: __import__("uuid").uuid4().hex[:25]
    )
    user_id: Mapped[str] = mapped_column(
        "userId", String, ForeignKey("User.id", ondelete="CASCADE"), index=True
    )
    prep_question_id: Mapped[str] = mapped_column(
        "prepQuestionId", String, ForeignKey("PrepQuestion.id", ondelete="CASCADE"), index=True
    )
    # Optional: the act of flagging is the signal, and requiring a reason would
    # suppress it.
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (UniqueConstraint("userId", "prepQuestionId", name="PrepQuestionFlag_unique"),)

    def __repr__(self) -> str:
        return f"<PrepQuestionFlag user={self.user_id} question={self.prep_question_id}>"


# ---------------------------------------------------------------------------
# QuizSessionQuestion
# ---------------------------------------------------------------------------


class QuizSessionQuestion(Base, TimestampMixin):
    """Which banked questions a session asked, and in what order.

    Order lives here rather than on the question, because the same banked question
    can appear at a different position in a later session.
    """

    __tablename__ = "QuizSessionQuestion"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: __import__("uuid").uuid4().hex[:25]
    )
    quiz_session_id: Mapped[str] = mapped_column(
        "quizSessionId", String, ForeignKey("QuizSession.id", ondelete="CASCADE"), index=True
    )
    prep_question_id: Mapped[str] = mapped_column(
        "prepQuestionId", String, ForeignKey("PrepQuestion.id", ondelete="CASCADE"), index=True
    )
    order_index: Mapped[int] = mapped_column("orderIndex", Integer, default=0)
    # Hints taken for this question in this session. On the link rather than the
    # question, because meeting the same banked question again later may need none.
    hint_count: Mapped[int] = mapped_column("hintCount", Integer, default=0, server_default="0")

    __table_args__ = (
        # A session asks a given question at most once.
        UniqueConstraint("quizSessionId", "prepQuestionId", name="QuizSessionQuestion_unique"),
        Index("QuizSessionQuestion_quizSessionId_orderIndex_idx", "quizSessionId", "orderIndex"),
    )

    def __repr__(self) -> str:
        return (
            f"<QuizSessionQuestion session={self.quiz_session_id} question={self.prep_question_id}>"
        )


# ---------------------------------------------------------------------------
# QuizAnswer
# ---------------------------------------------------------------------------


class QuizAnswer(Base, TimestampMixin):
    __tablename__ = "QuizAnswer"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: __import__("uuid").uuid4().hex[:25]
    )
    quiz_session_id: Mapped[str] = mapped_column(
        "quizSessionId", String, ForeignKey("QuizSession.id", ondelete="CASCADE"), index=True
    )
    # Points at the banked question. The column keeps its name, and migration 008
    # preserves question ids, so existing answer rows stay valid across the move
    # from QuizQuestion to PrepQuestion without being rewritten.
    question_id: Mapped[str] = mapped_column(
        "questionId", String, ForeignKey("PrepQuestion.id", ondelete="CASCADE")
    )
    user_answer: Mapped[str] = mapped_column("userAnswer", String, nullable=False)
    is_correct: Mapped[bool] = mapped_column("isCorrect", Boolean, default=False)
    time_taken_seconds: Mapped[int | None] = mapped_column(
        "timeTakenSeconds", Integer, nullable=True
    )

    # Relationships
    quiz_session: Mapped["QuizSession"] = relationship("QuizSession", back_populates="answers")

    def __repr__(self) -> str:
        return f"<QuizAnswer id={self.id} correct={self.is_correct}>"


# ---------------------------------------------------------------------------
# StudyPlan
# ---------------------------------------------------------------------------


class StudyPlan(Base, TimestampMixin):
    __tablename__ = "StudyPlan"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: __import__("uuid").uuid4().hex[:25]
    )
    user_id: Mapped[str] = mapped_column(
        "userId", String, ForeignKey("User.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String, nullable=False)
    goal_description: Mapped[str | None] = mapped_column("goalDescription", Text, nullable=True)
    deadline: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    prep_id: Mapped[str | None] = mapped_column(
        "prepId", String, ForeignKey("ExamPrep.id", ondelete="SET NULL"), nullable=True
    )
    # `ACTIVE`, `PAUSED`, `COMPLETED`, or `SUPERSEDED` — the last meaning a plan
    # replaced by a newer one for the same preparation. The preparation timeline takes
    # only completed items from a superseded plan, so regenerating replaces the
    # schedule instead of doubling it.
    #
    # `PAUSED` is a learner action: the detail page has always had a pause control,
    # and until it could be stored the button changed local state and forgot. A paused
    # plan keeps its items and its dates; it is excluded from "what should I do today"
    # rather than rescheduled, because pausing is not a statement about the deadline.
    status: Mapped[str] = mapped_column(String, default="ACTIVE")
    # Which scheduler produced this plan: `ADAPTIVE` or `EVEN`.
    #
    # Recorded because "adaptive study plans" was sold as a Plus capability while
    # `generate_plan` computed `is_adaptive` and branched on nothing — a Plus plan
    # was byte-for-byte a Free plan. A claim about behaviour that leaves no trace in
    # the data is a claim nobody can check, which is why it survived so long.
    #
    # Nullable: plans created before this column exist and were all even, but
    # backfilling them to `EVEN` would assert something about rows nobody measured.
    strategy: Mapped[str | None] = mapped_column(String, nullable=True)
    # Minutes a week the learner means to spend. An intention rather than a
    # measurement, so it is nullable: it cannot be derived, and a default would set a
    # goal on their behalf. Same reasoning as `ExamPrep.targetReadiness`.
    weekly_goal_minutes: Mapped[int | None] = mapped_column(
        "weeklyGoalMinutes", Integer, nullable=True
    )

    # --- Rhythm, from steps 1 and 2 of the create wizard ---
    #
    # The two facts behind the pace, kept separately as well as multiplied into
    # `weeklyGoalMinutes`, because the product cannot be taken apart again: 175 minutes a
    # week is 5x35 or 7x25, and the detail page prints "35 min - 5x week".
    sessions_per_week: Mapped[int | None] = mapped_column("sessionsPerWeek", Integer, nullable=True)
    session_minutes: Mapped[int | None] = mapped_column("sessionMinutes", Integer, nullable=True)
    # ISO weekday numbers the learner is available: 1 = Monday ... 7 = Sunday. Numbers
    # rather than the wizard's "Mon"/"Tue" labels, which are English and would have to be
    # parsed back before comparing against `date.isoweekday()`.
    #
    # Null means never asked, and the scheduler treats every day as available. An empty
    # list would mean no day is acceptable, which is not a schedulable plan, so the
    # contract refuses it rather than storing a row nothing can distribute.
    preferred_days: Mapped[list | None] = mapped_column("preferredDays", JSON, nullable=True)
    # The template the learner chose, e.g. `skill-mastery`. Stored so the generator can be
    # told which phase structure to follow: step 4 of the wizard previews the template's
    # phases under "Generated roadmap", so a plan grouped by different phases would
    # contradict the screen the learner accepted.
    shape: Mapped[str | None] = mapped_column(String, nullable=True)

    # What this plan builds, as a JSON array of strings, named by the generator
    # alongside the items. Not derivable from item titles after the fact without
    # guessing. Follows `LearningProfile.subjects`.
    skills: Mapped[list | None] = mapped_column(JSON, nullable=True)

    # Counts of this plan's items. Derived rather than accumulated — see
    # `repository.recount_plan_progress` for why: incrementing `completedItems` on each
    # completion double-counted a repeated completion and could not express
    # uncompleting or skipping at all.
    total_items: Mapped[int] = mapped_column("totalItems", Integer, default=0)
    completed_items: Mapped[int] = mapped_column("completedItems", Integer, default=0)

    # --- Connected learning, from step 3 of the create wizard ---
    #
    # Both flags are false by default rather than nullable: a plan created before they
    # existed was never asked, and "not asked" and "declined" lead to the same
    # behaviour — nothing happens — so a tri-state would be a distinction without a
    # consequence. Both are only stored because something reads them; see migration 022.
    generate_review_cards: Mapped[bool] = mapped_column(
        "generateReviewCards", Boolean, nullable=False, default=False, server_default="false"
    )
    weekly_check_in: Mapped[bool] = mapped_column(
        "weeklyCheckIn", Boolean, nullable=False, default=False, server_default="false"
    )
    # The deck this plan generates review cards into, created on first use and reused
    # after, so a plan's cards stay together instead of scattering through the library.
    review_deck_id: Mapped[str | None] = mapped_column(
        "reviewDeckId",
        String,
        ForeignKey("FlashcardDeck.id", ondelete="SET NULL"),
        nullable=True,
    )
    # When the weekly check-in last went out. What makes the beat task idempotent: a
    # retried or overlapping run cannot send twice, and a missed week cannot silently
    # become two notifications at once.
    last_check_in_at: Mapped[datetime | None] = mapped_column(
        "lastCheckInAt", DateTime(timezone=True), nullable=True
    )

    # Relationships
    items: Mapped[list["StudyPlanItem"]] = relationship(
        "StudyPlanItem",
        back_populates="plan",
        cascade="all, delete-orphan",
        order_by="StudyPlanItem.scheduled_date",
    )
    course_links: Mapped[list["StudyPlanCourse"]] = relationship(
        "StudyPlanCourse", back_populates="plan", cascade="all, delete-orphan"
    )
    materials: Mapped[list["StudyPlanMaterial"]] = relationship(
        "StudyPlanMaterial", back_populates="plan", cascade="all, delete-orphan"
    )

    __table_args__ = (Index("StudyPlan_userId_status_idx", "userId", "status"),)

    def __repr__(self) -> str:
        return f"<StudyPlan id={self.id} title={self.title}>"


# ---------------------------------------------------------------------------
# StudyPlanItem
# ---------------------------------------------------------------------------


class StudyPlanItem(Base, TimestampMixin):
    __tablename__ = "StudyPlanItem"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: __import__("uuid").uuid4().hex[:25]
    )
    plan_id: Mapped[str] = mapped_column(
        "planId", String, ForeignKey("StudyPlan.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    scheduled_date: Mapped[datetime] = mapped_column(
        "scheduledDate", DateTime(timezone=True), nullable=False
    )
    estimated_minutes: Mapped[int] = mapped_column("estimatedMinutes", Integer, default=30)
    item_type: Mapped[str] = mapped_column("itemType", String, default="STUDY")
    # A grouping label — "Foundations", "Core patterns", "Mock interviews". The phase a
    # plan is drawn in terms of is this, and nothing else: a phase's week range is the
    # span of its items' dates, its progress is the share of them completed, and its
    # number is its position in that order. A separate phase table would hold a name
    # and a foreign key and derive the rest from these rows anyway.
    #
    # Null for items generated before this existed; those plans render as one flat
    # list, which is what they are. Same shape and reasoning as `PrepTopic.category`.
    phase: Mapped[str | None] = mapped_column(String, nullable=True)
    topic_id: Mapped[str | None] = mapped_column("topicId", String, nullable=True)
    prep_topic_id: Mapped[str | None] = mapped_column("prepTopicId", String, nullable=True)
    # `PENDING`, `COMPLETED` or `SKIPPED`. Only `COMPLETED` was ever written before
    # stage 3; skipping is how a learner says "not doing this" without it counting
    # against the plan's progress or sitting overdue forever.
    status: Mapped[str] = mapped_column(String, default="PENDING")
    completed_at: Mapped[datetime | None] = mapped_column(
        "completedAt", DateTime(timezone=True), nullable=True
    )

    # Relationships
    plan: Mapped["StudyPlan"] = relationship("StudyPlan", back_populates="items")

    __table_args__ = (
        Index("StudyPlanItem_planId_scheduledDate_idx", "planId", "scheduledDate"),
        Index("StudyPlanItem_planId_phase_idx", "planId", "phase"),
    )

    def __repr__(self) -> str:
        return f"<StudyPlanItem id={self.id} title={self.title}>"


# ---------------------------------------------------------------------------
# StudyPlanCourse
# ---------------------------------------------------------------------------


class StudyPlanCourse(Base):
    """A course the learner linked to a plan.

    A table with a real foreign key rather than a JSON list of ids on the plan, because
    the two behave differently when a course is deleted: `CASCADE` removes the link,
    while a JSON array keeps an id that resolves to nothing and has to be filtered by
    every reader who remembers to. The detail page lists these by title, so it joins to
    `Course` regardless.
    """

    __tablename__ = "StudyPlanCourse"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: __import__("uuid").uuid4().hex[:25]
    )
    plan_id: Mapped[str] = mapped_column(
        "planId", String, ForeignKey("StudyPlan.id", ondelete="CASCADE"), index=True
    )
    course_id: Mapped[str] = mapped_column(
        "courseId", String, ForeignKey("Course.id", ondelete="CASCADE"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        "createdAt",
        DateTime(timezone=True),
        default=lambda: __import__("datetime").datetime.now(__import__("datetime").timezone.utc),
    )

    plan: Mapped["StudyPlan"] = relationship("StudyPlan", back_populates="course_links")

    __table_args__ = (
        # Linking the same course twice is not a state the UI can produce or the page
        # can render, so it is refused by the database rather than deduplicated on read.
        UniqueConstraint("planId", "courseId", name="StudyPlanCourse_planId_courseId_key"),
        Index("StudyPlanCourse_planId_idx", "planId"),
    )

    def __repr__(self) -> str:
        return f"<StudyPlanCourse plan={self.plan_id} course={self.course_id}>"


# ---------------------------------------------------------------------------
# StudyPlanMaterial
# ---------------------------------------------------------------------------


class StudyPlanMaterial(Base, TimestampMixin):
    """A reference file the learner attached to a plan.

    Mirrors `PrepMaterial`, which already does this for a preparation. Deliberately
    without an `extractedText` column: `PrepMaterial` has one because preparation topics
    are extracted from material, and nothing reads a plan's files that way. A column for
    a use that does not exist is how `Course.progress` came to be written by nothing.
    """

    __tablename__ = "StudyPlanMaterial"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: __import__("uuid").uuid4().hex[:25]
    )
    plan_id: Mapped[str] = mapped_column(
        "planId", String, ForeignKey("StudyPlan.id", ondelete="CASCADE"), index=True
    )
    filename: Mapped[str] = mapped_column(String, nullable=False)
    url: Mapped[str] = mapped_column(String, nullable=False)
    file_type: Mapped[str | None] = mapped_column("fileType", String, nullable=True)
    size: Mapped[int | None] = mapped_column(Integer, nullable=True)

    plan: Mapped["StudyPlan"] = relationship("StudyPlan", back_populates="materials")

    __table_args__ = (Index("StudyPlanMaterial_planId_idx", "planId"),)

    def __repr__(self) -> str:
        return f"<StudyPlanMaterial id={self.id} filename={self.filename}>"


# ---------------------------------------------------------------------------
# Reflection
# ---------------------------------------------------------------------------


class Reflection(Base, TimestampMixin):
    __tablename__ = "Reflection"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: __import__("uuid").uuid4().hex[:25]
    )
    user_id: Mapped[str] = mapped_column(
        "userId", String, ForeignKey("User.id", ondelete="CASCADE"), index=True
    )
    type: Mapped[str] = mapped_column(String, nullable=False)
    period_start: Mapped[datetime] = mapped_column(
        "periodStart", DateTime(timezone=True), nullable=False
    )
    period_end: Mapped[datetime] = mapped_column(
        "periodEnd", DateTime(timezone=True), nullable=False
    )
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    activities_layer: Mapped[dict | None] = mapped_column("activitiesLayer", JSON, nullable=True)
    progress_layer: Mapped[dict | None] = mapped_column("progressLayer", JSON, nullable=True)
    achievements_layer: Mapped[dict | None] = mapped_column(
        "achievementsLayer", JSON, nullable=True
    )
    recommendations: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    __table_args__ = (
        Index("Reflection_userId_type_idx", "userId", "type"),
        Index("Reflection_periodEnd_idx", "periodEnd"),
    )

    def __repr__(self) -> str:
        return f"<Reflection id={self.id} type={self.type}>"


# ---------------------------------------------------------------------------
# DiscoveryRecommendation
# ---------------------------------------------------------------------------


class DiscoveryRecommendation(Base, TimestampMixin):
    __tablename__ = "DiscoveryRecommendation"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: __import__("uuid").uuid4().hex[:25]
    )
    user_id: Mapped[str] = mapped_column(
        "userId", String, ForeignKey("User.id", ondelete="CASCADE"), index=True
    )
    item_type: Mapped[str] = mapped_column("itemType", String, nullable=False)
    item_id: Mapped[str] = mapped_column("itemId", String, nullable=False)
    title: Mapped[str] = mapped_column(String, nullable=False)
    reason: Mapped[str] = mapped_column(String, nullable=False)
    relevance_score: Mapped[float] = mapped_column("relevanceScore", Float, default=0.0)
    status: Mapped[str] = mapped_column(String, default="ACTIVE")
    dismissed_at: Mapped[datetime | None] = mapped_column(
        "dismissedAt", DateTime(timezone=True), nullable=True
    )
    followed_at: Mapped[datetime | None] = mapped_column(
        "followedAt", DateTime(timezone=True), nullable=True
    )

    __table_args__ = (Index("DiscoveryRecommendation_userId_status_idx", "userId", "status"),)

    def __repr__(self) -> str:
        return f"<DiscoveryRecommendation id={self.id} title={self.title}>"


# ---------------------------------------------------------------------------
# ActivityFeedEntry (no TimestampMixin — just occurred_at)
# ---------------------------------------------------------------------------


class ActivityFeedEntry(Base):
    __tablename__ = "ActivityFeedEntry"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: __import__("uuid").uuid4().hex[:25]
    )
    user_id: Mapped[str] = mapped_column(
        "userId", String, ForeignKey("User.id", ondelete="CASCADE"), index=True
    )
    activity_type: Mapped[str] = mapped_column("activityType", String, nullable=False)
    title: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str | None] = mapped_column(String, nullable=True)
    context: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(
        "occurredAt", DateTime(timezone=True), nullable=False
    )

    __table_args__ = (Index("ActivityFeedEntry_userId_occurredAt_idx", "userId", "occurredAt"),)

    def __repr__(self) -> str:
        return f"<ActivityFeedEntry id={self.id} type={self.activity_type}>"


# ---------------------------------------------------------------------------
# ConversionTriggerLog
# ---------------------------------------------------------------------------


class ConversionTriggerLog(Base):
    __tablename__ = "ConversionTriggerLog"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: __import__("uuid").uuid4().hex[:25]
    )
    user_id: Mapped[str] = mapped_column(
        "userId", String, ForeignKey("User.id", ondelete="CASCADE"), index=True
    )
    trigger_id: Mapped[str] = mapped_column("triggerId", String, nullable=False)
    shown_at: Mapped[datetime] = mapped_column("shownAt", DateTime(timezone=True), nullable=False)
    dismissed_at: Mapped[datetime | None] = mapped_column(
        "dismissedAt", DateTime(timezone=True), nullable=True
    )
    converted_at: Mapped[datetime | None] = mapped_column(
        "convertedAt", DateTime(timezone=True), nullable=True
    )
    capability_highlighted: Mapped[str] = mapped_column(
        "capabilityHighlighted", String, nullable=False
    )

    __table_args__ = (Index("ConversionTriggerLog_userId_shownAt_idx", "userId", "shownAt"),)

    def __repr__(self) -> str:
        return f"<ConversionTriggerLog id={self.id} trigger={self.trigger_id}>"


# ---------------------------------------------------------------------------
# LearningMilestone
# ---------------------------------------------------------------------------


class LearningMilestone(Base):
    __tablename__ = "LearningMilestone"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: __import__("uuid").uuid4().hex[:25]
    )
    user_id: Mapped[str] = mapped_column(
        "userId", String, ForeignKey("User.id", ondelete="CASCADE"), index=True
    )
    milestone_id: Mapped[str] = mapped_column("milestoneId", String, nullable=False)
    achieved_at: Mapped[datetime] = mapped_column(
        "achievedAt", DateTime(timezone=True), nullable=False
    )
    shared_at: Mapped[datetime | None] = mapped_column(
        "sharedAt", DateTime(timezone=True), nullable=True
    )
    share_card_url: Mapped[str | None] = mapped_column("shareCardUrl", String, nullable=True)
    referral_link: Mapped[str | None] = mapped_column("referralLink", String, nullable=True)

    __table_args__ = (
        Index(
            "LearningMilestone_userId_milestoneId_idx",
            "userId",
            "milestoneId",
            unique=True,
        ),
    )

    def __repr__(self) -> str:
        return f"<LearningMilestone id={self.id} milestone={self.milestone_id}>"


# ---------------------------------------------------------------------------
# RetentionIntervention
# ---------------------------------------------------------------------------


class RetentionIntervention(Base):
    __tablename__ = "RetentionIntervention"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: __import__("uuid").uuid4().hex[:25]
    )
    user_id: Mapped[str] = mapped_column(
        "userId", String, ForeignKey("User.id", ondelete="CASCADE"), index=True
    )
    churn_risk_score: Mapped[float] = mapped_column("churnRiskScore", Float, nullable=False)
    intervention_type: Mapped[str] = mapped_column("interventionType", String, nullable=False)
    delivered_at: Mapped[datetime] = mapped_column(
        "deliveredAt", DateTime(timezone=True), nullable=False
    )
    outcome: Mapped[str | None] = mapped_column(String, nullable=True)
    outcome_at: Mapped[datetime | None] = mapped_column(
        "outcomeAt", DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        Index("RetentionIntervention_userId_deliveredAt_idx", "userId", "deliveredAt"),
    )

    def __repr__(self) -> str:
        return f"<RetentionIntervention id={self.id} type={self.intervention_type}>"


# ---------------------------------------------------------------------------
# ValueSummaryRecord
# ---------------------------------------------------------------------------


class ValueSummaryRecord(Base):
    __tablename__ = "ValueSummaryRecord"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: __import__("uuid").uuid4().hex[:25]
    )
    user_id: Mapped[str] = mapped_column(
        "userId", String, ForeignKey("User.id", ondelete="CASCADE"), index=True
    )
    period_start: Mapped[datetime] = mapped_column(
        "periodStart", DateTime(timezone=True), nullable=False
    )
    period_end: Mapped[datetime] = mapped_column(
        "periodEnd", DateTime(timezone=True), nullable=False
    )
    summary_data: Mapped[dict] = mapped_column("summaryData", JSON, nullable=False)
    delivered_at: Mapped[datetime | None] = mapped_column(
        "deliveredAt", DateTime(timezone=True), nullable=True
    )
    delivery_method: Mapped[str] = mapped_column("deliveryMethod", String, default="notification")

    __table_args__ = (Index("ValueSummaryRecord_userId_periodEnd_idx", "userId", "periodEnd"),)

    def __repr__(self) -> str:
        return f"<ValueSummaryRecord id={self.id} userId={self.user_id}>"
