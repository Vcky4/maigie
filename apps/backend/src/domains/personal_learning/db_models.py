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
    content: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    summary: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    course_id: Mapped[Optional[str]] = mapped_column(
        "courseId", String, ForeignKey("Course.id", ondelete="SET NULL"), nullable=True, index=True
    )
    topic_id: Mapped[Optional[str]] = mapped_column(
        "topicId", String, ForeignKey("Topic.id", ondelete="SET NULL"), nullable=True, index=True
    )
    space_id: Mapped[Optional[str]] = mapped_column("spaceId", String, nullable=True, index=True)

    last_edited_by_id: Mapped[Optional[str]] = mapped_column(
        "lastEditedById", String, ForeignKey("User.id", ondelete="SET NULL"), nullable=True
    )
    archived: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    voice_recording_url: Mapped[Optional[str]] = mapped_column(
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
    size: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

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
    content: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

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
    exam_date: Mapped[datetime] = mapped_column("examDate", DateTime(timezone=True), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, default="SETUP", server_default="SETUP")

    space_id: Mapped[Optional[str]] = mapped_column("spaceId", String, nullable=True, index=True)

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

    # Share settings
    is_public: Mapped[bool] = mapped_column(
        "isPublic", Boolean, default=False, server_default="false"
    )
    share_id: Mapped[Optional[str]] = mapped_column("shareId", String, unique=True, nullable=True)

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
    description: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    course_id: Mapped[Optional[str]] = mapped_column("courseId", String, nullable=True, index=True)
    topic_id: Mapped[Optional[str]] = mapped_column("topicId", String, nullable=True, index=True)
    prep_id: Mapped[Optional[str]] = mapped_column(
        "prepId", String, ForeignKey("ExamPrep.id", ondelete="SET NULL"), nullable=True
    )

    # Relationships
    flashcards: Mapped[list["Flashcard"]] = relationship(
        "Flashcard", back_populates="deck", cascade="all, delete-orphan"
    )

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
    deck_id: Mapped[Optional[str]] = mapped_column(
        "deckId",
        String,
        ForeignKey("FlashcardDeck.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    front: Mapped[str] = mapped_column(Text, nullable=False)
    back: Mapped[str] = mapped_column(Text, nullable=False)

    # SM-2 spaced repetition fields
    interval_days: Mapped[int] = mapped_column("intervalDays", Integer, default=1)
    repetition_count: Mapped[int] = mapped_column("repetitionCount", Integer, default=0)
    ease_factor: Mapped[float] = mapped_column("easeFactor", Float, default=2.5)
    next_review_at: Mapped[datetime] = mapped_column(
        "nextReviewAt", DateTime(timezone=True), nullable=False
    )
    last_reviewed_at: Mapped[Optional[datetime]] = mapped_column(
        "lastReviewedAt", DateTime(timezone=True), nullable=True
    )
    last_quality: Mapped[int] = mapped_column("lastQuality", Integer, default=-1)
    lapse_count: Mapped[int] = mapped_column("lapseCount", Integer, default=0)

    # Source tracking
    source_type: Mapped[Optional[str]] = mapped_column("sourceType", String, nullable=True)
    source_id: Mapped[Optional[str]] = mapped_column("sourceId", String, nullable=True)

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
    url: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    source_type: Mapped[str] = mapped_column("sourceType", String, nullable=False)
    source_id: Mapped[Optional[str]] = mapped_column("sourceId", String, nullable=True)
    tags: Mapped[Optional[dict]] = mapped_column("tags", JSON, nullable=True)
    last_accessed_at: Mapped[Optional[datetime]] = mapped_column(
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
    purpose: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    subjects: Mapped[Optional[dict]] = mapped_column("subjects", JSON, nullable=True)
    goals_text: Mapped[Optional[str]] = mapped_column("goalsText", Text, nullable=True)
    preferred_explanation_style: Mapped[Optional[str]] = mapped_column(
        "preferredExplanationStyle", String, nullable=True
    )
    proficiency_map: Mapped[Optional[dict]] = mapped_column("proficiencyMap", JSON, nullable=True)
    onboarding_completed_at: Mapped[Optional[datetime]] = mapped_column(
        "onboardingCompletedAt", DateTime(timezone=True), nullable=True
    )
    maturity_days: Mapped[int] = mapped_column("maturityDays", Integer, default=0)
    quiet_hours_start: Mapped[Optional[str]] = mapped_column(
        "quietHoursStart", String, nullable=True
    )
    quiet_hours_end: Mapped[Optional[str]] = mapped_column("quietHoursEnd", String, nullable=True)
    max_daily_notifications: Mapped[int] = mapped_column(
        "maxDailyNotifications", Integer, default=5
    )

    # Behaviour cache (updated by background task)
    preferred_study_times: Mapped[Optional[dict]] = mapped_column(
        "preferredStudyTimes", JSON, nullable=True
    )
    avg_session_minutes: Mapped[Optional[float]] = mapped_column(
        "avgSessionMinutes", Float, nullable=True
    )
    consistency_score: Mapped[Optional[float]] = mapped_column(
        "consistencyScore", Float, nullable=True
    )
    best_day_of_week: Mapped[Optional[str]] = mapped_column("bestDayOfWeek", String, nullable=True)
    dropout_risk: Mapped[Optional[float]] = mapped_column("dropoutRisk", Float, nullable=True)

    # LLM provider preference (gemini, openai, anthropic; null = system default)
    preferred_llm_provider: Mapped[Optional[str]] = mapped_column(
        "preferredLlmProvider", String, nullable=True
    )

    # --- Commercial: Trial tracking ---
    trial_started_at: Mapped[Optional[datetime]] = mapped_column(
        "trialStartedAt", DateTime(timezone=True), nullable=True
    )
    trial_ends_at: Mapped[Optional[datetime]] = mapped_column(
        "trialEndsAt", DateTime(timezone=True), nullable=True
    )
    last_trial_ended_at: Mapped[Optional[datetime]] = mapped_column(
        "lastTrialEndedAt", DateTime(timezone=True), nullable=True
    )

    # --- Commercial: Conversion trigger tracking ---
    last_trigger_shown_at: Mapped[Optional[datetime]] = mapped_column(
        "lastTriggerShownAt", DateTime(timezone=True), nullable=True
    )
    trigger_dismissal_count: Mapped[int] = mapped_column(
        "triggerDismissalCount", Integer, default=0, server_default="0"
    )
    last_trigger_dismissed_at: Mapped[Optional[datetime]] = mapped_column(
        "lastTriggerDismissedAt", DateTime(timezone=True), nullable=True
    )

    # --- Commercial: Educator transition tracking ---
    educator_readiness_met_at: Mapped[Optional[datetime]] = mapped_column(
        "educatorReadinessMetAt", DateTime(timezone=True), nullable=True
    )
    educator_suggestion_shown_at: Mapped[Optional[datetime]] = mapped_column(
        "educatorSuggestionShownAt", DateTime(timezone=True), nullable=True
    )
    space_trial_started_at: Mapped[Optional[datetime]] = mapped_column(
        "spaceTrialStartedAt", DateTime(timezone=True), nullable=True
    )

    # --- Commercial: Value tracking ---
    last_value_summary_at: Mapped[Optional[datetime]] = mapped_column(
        "lastValueSummaryAt", DateTime(timezone=True), nullable=True
    )
    plus_features_used_this_period: Mapped[Optional[dict]] = mapped_column(
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
    action_data: Mapped[Optional[dict]] = mapped_column("actionData", JSON, nullable=True)
    scheduled_at: Mapped[datetime] = mapped_column(
        "scheduledAt", DateTime(timezone=True), nullable=False
    )
    delivered_at: Mapped[Optional[datetime]] = mapped_column(
        "deliveredAt", DateTime(timezone=True), nullable=True
    )
    read_at: Mapped[Optional[datetime]] = mapped_column(
        "readAt", DateTime(timezone=True), nullable=True
    )
    dismissed_at: Mapped[Optional[datetime]] = mapped_column(
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
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    estimated_minutes: Mapped[int] = mapped_column("estimatedMinutes", Integer, default=30)
    order_index: Mapped[int] = mapped_column("orderIndex", Integer, default=0)
    mastery_score: Mapped[float] = mapped_column("masteryScore", Float, default=0.0)
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
    file_type: Mapped[Optional[str]] = mapped_column("fileType", String, nullable=True)
    size: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    extracted_text: Mapped[Optional[str]] = mapped_column("extractedText", Text, nullable=True)
    category: Mapped[str] = mapped_column(String, default="OTHER")
    label: Mapped[Optional[str]] = mapped_column(String, nullable=True)

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
    topic_id: Mapped[Optional[str]] = mapped_column("topicId", String, nullable=True)
    status: Mapped[str] = mapped_column(String, default="IN_PROGRESS")
    total_questions: Mapped[int] = mapped_column("totalQuestions", Integer, default=0)
    correct_count: Mapped[int] = mapped_column("correctCount", Integer, default=0)
    score_percentage: Mapped[Optional[float]] = mapped_column(
        "scorePercentage", Float, nullable=True
    )
    duration_seconds: Mapped[Optional[int]] = mapped_column(
        "durationSeconds", Integer, nullable=True
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(
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
    prep_topic_id: Mapped[Optional[str]] = mapped_column(
        "prepTopicId", String, ForeignKey("PrepTopic.id", ondelete="SET NULL"), nullable=True
    )
    question_text: Mapped[str] = mapped_column("questionText", Text, nullable=False)
    question_type: Mapped[str] = mapped_column("questionType", String, default="MULTIPLE_CHOICE")
    # A JSON array of answer strings. The original `dict` annotation never matched
    # what the generator writes.
    options: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    correct_answer: Mapped[str] = mapped_column("correctAnswer", String, nullable=False)
    explanation: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # EASY | MEDIUM | HARD. Nullable: questions banked before this existed have no
    # difficulty, and inferring one would invent data.
    difficulty: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    # AI_GENERATED | PAST_PAPER. Set by the server, never reported by the
    # generator: provenance a producer can self-declare is not provenance.
    source: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    # Only meaningful for past papers.
    source_year: Mapped[Optional[int]] = mapped_column("sourceYear", Integer, nullable=True)
    # Advice for tackling this kind of question. Disclosed with the answer key, not
    # before: a tip written about a specific question can hint at its answer.
    exam_tip: Mapped[Optional[str]] = mapped_column("examTip", Text, nullable=True)

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
    average_mastery_percent: Mapped[Optional[float]] = mapped_column(
        "averageMasteryPercent", Float, nullable=True
    )
    topics_total: Mapped[int] = mapped_column("topicsTotal", Integer, default=0)
    topics_strong: Mapped[int] = mapped_column("topicsStrong", Integer, default=0)
    topics_focus: Mapped[int] = mapped_column("topicsFocus", Integer, default=0)
    topics_assessed: Mapped[int] = mapped_column("topicsAssessed", Integer, default=0)
    questions_answered: Mapped[int] = mapped_column("questionsAnswered", Integer, default=0)
    # Null until at least one question has been answered.
    accuracy_percent: Mapped[Optional[float]] = mapped_column(
        "accuracyPercent", Float, nullable=True
    )
    quizzes_taken: Mapped[int] = mapped_column("quizzesTaken", Integer, default=0)

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
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

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
    time_taken_seconds: Mapped[Optional[int]] = mapped_column(
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
    goal_description: Mapped[Optional[str]] = mapped_column("goalDescription", Text, nullable=True)
    deadline: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    prep_id: Mapped[Optional[str]] = mapped_column(
        "prepId", String, ForeignKey("ExamPrep.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(String, default="ACTIVE")
    total_items: Mapped[int] = mapped_column("totalItems", Integer, default=0)
    completed_items: Mapped[int] = mapped_column("completedItems", Integer, default=0)

    # Relationships
    items: Mapped[list["StudyPlanItem"]] = relationship(
        "StudyPlanItem",
        back_populates="plan",
        cascade="all, delete-orphan",
        order_by="StudyPlanItem.scheduled_date",
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
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    scheduled_date: Mapped[datetime] = mapped_column(
        "scheduledDate", DateTime(timezone=True), nullable=False
    )
    estimated_minutes: Mapped[int] = mapped_column("estimatedMinutes", Integer, default=30)
    item_type: Mapped[str] = mapped_column("itemType", String, default="STUDY")
    topic_id: Mapped[Optional[str]] = mapped_column("topicId", String, nullable=True)
    prep_topic_id: Mapped[Optional[str]] = mapped_column("prepTopicId", String, nullable=True)
    status: Mapped[str] = mapped_column(String, default="PENDING")
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        "completedAt", DateTime(timezone=True), nullable=True
    )

    # Relationships
    plan: Mapped["StudyPlan"] = relationship("StudyPlan", back_populates="items")

    __table_args__ = (Index("StudyPlanItem_planId_scheduledDate_idx", "planId", "scheduledDate"),)

    def __repr__(self) -> str:
        return f"<StudyPlanItem id={self.id} title={self.title}>"


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
    activities_layer: Mapped[Optional[dict]] = mapped_column("activitiesLayer", JSON, nullable=True)
    progress_layer: Mapped[Optional[dict]] = mapped_column("progressLayer", JSON, nullable=True)
    achievements_layer: Mapped[Optional[dict]] = mapped_column(
        "achievementsLayer", JSON, nullable=True
    )
    recommendations: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

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
    dismissed_at: Mapped[Optional[datetime]] = mapped_column(
        "dismissedAt", DateTime(timezone=True), nullable=True
    )
    followed_at: Mapped[Optional[datetime]] = mapped_column(
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
    description: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    context: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
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
    dismissed_at: Mapped[Optional[datetime]] = mapped_column(
        "dismissedAt", DateTime(timezone=True), nullable=True
    )
    converted_at: Mapped[Optional[datetime]] = mapped_column(
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
    shared_at: Mapped[Optional[datetime]] = mapped_column(
        "sharedAt", DateTime(timezone=True), nullable=True
    )
    share_card_url: Mapped[Optional[str]] = mapped_column("shareCardUrl", String, nullable=True)
    referral_link: Mapped[Optional[str]] = mapped_column("referralLink", String, nullable=True)

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
    outcome: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    outcome_at: Mapped[Optional[datetime]] = mapped_column(
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
    delivered_at: Mapped[Optional[datetime]] = mapped_column(
        "deliveredAt", DateTime(timezone=True), nullable=True
    )
    delivery_method: Mapped[str] = mapped_column("deliveryMethod", String, default="notification")

    __table_args__ = (Index("ValueSummaryRecord_userId_periodEnd_idx", "userId", "periodEnd"),)

    def __repr__(self) -> str:
        return f"<ValueSummaryRecord id={self.id} userId={self.user_id}>"
