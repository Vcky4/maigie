"""
Knowledge domain — SQLAlchemy models.

Course, Module, Topic, TopicSection, TopicCheckAttempt, TopicIllustration, CourseRating, Resource,
Embedding, CourseOutlineSatisfaction, UserTopicProgress.
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.shared.database.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Course(Base, TimestampMixin):
    __tablename__ = "Course"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: __import__("uuid").uuid4().hex[:25]
    )
    user_id: Mapped[str] = mapped_column(
        "userId", String, ForeignKey("User.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str | None] = mapped_column(String, nullable=True)
    difficulty: Mapped[str] = mapped_column(String, default="BEGINNER", server_default="BEGINNER")
    target_date: Mapped[datetime | None] = mapped_column(
        "targetDate", DateTime(timezone=True), nullable=True
    )
    is_ai_generated: Mapped[bool] = mapped_column(
        "isAIGenerated", Boolean, default=False, server_default="false"
    )
    archived: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    # Completion as a percentage, derived from the topics and **stored**. Kept true by
    # `repository.recount_course_progress`, which runs whenever a topic is completed, reopened, added
    # or removed — a stored derived value drifts the moment the thing it derives from changes.
    #
    # It is stored rather than always computed because readers outside this domain want it cheaply:
    # the assigned-course list a classroom shows, and the course summary handed to the model as memory
    # context. Both read this column, and until it was written both reported every course as 0%
    # complete — a column nothing writes is not unused, it is wrong.
    progress: Mapped[float] = mapped_column(Float, default=0.0, server_default="0")
    space_id: Mapped[str | None] = mapped_column("spaceId", String, nullable=True, index=True)
    # What the course is about, as a subject label. Distinct from `difficulty`: how hard a course is
    # and what it covers are two different facts, and difficulty was briefly used to stand in for
    # this, which put the wrong word on the badge.
    category: Mapped[str | None] = mapped_column(String, nullable=True)
    # Free-form labels for the library card. JSON array of strings.
    tags: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    # "What you'll be able to do" — the promise the whole course makes. Deliberately separate from
    # `Topic.objectives`, which is what one sitting delivers; deriving one from the other would
    # flatten a curriculum into a list of lessons.
    outcomes: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    # Authoring credit, nullable because most courses have none. A learner who generated a course for
    # themselves has no instructor, and crediting them as the teacher of their own course — or
    # crediting "Maigie" — would state something untrue. Null means the panel does not render.
    #: How this course should be explained: Visual, Hands-on, Concept first or Mixed.
    #:
    #: Deliberately **not** written to `LearningProfile.preferredExplanationStyle`, which exists and uses
    #: the same words. That field is a global preference read for every subject; this is a choice about
    #: one course. Writing the wizard's answer to the profile would let a style picked for a geometry
    #: course silently change how an unrelated writing course is explained, with nothing on screen
    #: saying so. The generator applies a precedence instead: the course's own style wins, and the
    #: profile supplies the default when the course has none.
    teaching_style: Mapped[str | None] = mapped_column("teachingStyle", String, nullable=True)
    #: The learner's own brief: what they asked for in the create wizard, in their words.
    #:
    #: Named `sourcePrompt`, not `prompt`, because it is the brief rather than the text sent to the
    #: model. The sent prompt is composed from this plus the topic title and existing content; storing
    #: the composed version would freeze prompt wording into the data, so improving it later would not
    #: reach any existing course.
    source_prompt: Mapped[str | None] = mapped_column("sourcePrompt", Text, nullable=True)
    instructor_name: Mapped[str | None] = mapped_column("instructorName", String, nullable=True)
    instructor_role: Mapped[str | None] = mapped_column("instructorRole", String, nullable=True)

    # Relationships
    modules: Mapped[list["Module"]] = relationship(
        "Module", back_populates="course", lazy="selectin", order_by="Module.order"
    )
    ratings: Mapped[list["CourseRating"]] = relationship(
        "CourseRating", back_populates="course", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Course id={self.id} title={self.title}>"


class Module(Base, TimestampMixin):
    __tablename__ = "Module"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: __import__("uuid").uuid4().hex[:25]
    )
    course_id: Mapped[str] = mapped_column(
        "courseId", String, ForeignKey("Course.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str | None] = mapped_column(String, nullable=True)
    order: Mapped[float] = mapped_column(Float, default=0, server_default="0")
    completed: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")

    # Relationships
    course: Mapped["Course"] = relationship("Course", back_populates="modules")
    topics: Mapped[list["Topic"]] = relationship(
        "Topic", back_populates="module", lazy="selectin", order_by="Topic.order"
    )

    def __repr__(self) -> str:
        return f"<Module id={self.id} title={self.title}>"


class Topic(Base, TimestampMixin):
    __tablename__ = "Topic"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: __import__("uuid").uuid4().hex[:25]
    )
    module_id: Mapped[str] = mapped_column(
        "moduleId", String, ForeignKey("Module.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String, nullable=False)
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    order: Mapped[float] = mapped_column(Float, default=0, server_default="0")
    completed: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    # When the learner completed it. Cleared when they reopen it, so a pending topic never carries a
    # completion time — a row that says both would be counted by anything building a history.
    #
    # Without this, `completed` was a boolean with no "when", and every recent-activity question the
    # course library asks was unanswerable. `updatedAt` is not a substitute: it moves when a topic is
    # renamed or has content generated into it, so reading it as study activity reports an edit as
    # learning. Mirrors `StudyPlanItem.completedAt`, so both areas answer "when" the same way.
    completed_at: Mapped[datetime | None] = mapped_column(
        "completedAt", DateTime(timezone=True), nullable=True
    )
    estimated_hours: Mapped[float | None] = mapped_column("estimatedHours", Float, nullable=True)
    # What the learner will be able to do after this topic, shown above the first section. Nullable
    # rather than defaulting to an empty list: a topic written before this existed has no objectives,
    # which is a different thing from having none, and the page renders no block rather than an empty
    # one.
    #: What kind of work this sitting is: Lesson, Practice, Project or Check. The create wizard labels
    #: every lesson in its outline preview, and without this the label is lost on save.
    #:
    #: **Distinct from `TopicSection.kind`**, and the two are easy to confuse. A section's kind is how
    #: one passage explains something — concept, example, algorithm. A topic's kind is what the whole
    #: sitting asks of the learner: reading, practising, building, or being tested. A project made of
    #: three explanatory sections is coherent; one field serving both would make it contradictory.
    #:
    #: Nullable with no default: a topic written before this has no kind, which is not the same as being
    #: a Lesson, so the outline shows no label rather than a guessed one.
    kind: Mapped[str | None] = mapped_column(String, nullable=True)
    #: The lesson header's one-line description. Nullable, and deliberately not derived: the first
    #: section's summary describes that section, and the first paragraph of `content` is the opening of
    #: the material rather than a description of it — either would put a line under the heading saying
    #: something other than what it claims to.
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    objectives: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    # The end-of-lesson check: {question, explanation, choices: [{id, label, correct}]}. JSON because
    # it is one value object per topic, always read whole with its topic, never queried on its own
    # and never listed across topics. Deliberately not a `QuizSession`: that models a timed, scored,
    # multi-question attempt feeding readiness scoring, and borrowing it would leave an abandoned
    # scored session in preparation analytics for every lesson opened.
    knowledge_check: Mapped[dict | None] = mapped_column("knowledgeCheck", JSON, nullable=True)
    #: Which phase writing this lesson has reached, while it is being written.
    #:
    #: Exists so the reader can report a stage the server actually reached, the same reason
    #: `QuizSession.generationStage` does. Writing a lesson is the longest generation in the product and
    #: the reader showed a pulsing icon for all of it. Every value has a write behind it; nothing here is
    #: interpolated.
    #:
    #: Cleared on every terminal path. A value left behind by a server restart is overwritten by the next
    #: attempt, and nothing derives anything from it — the client's terminal signal is its own request
    #: resolving, not this column.
    lesson_generation_stage: Mapped[str | None] = mapped_column(
        "lessonGenerationStage", String, nullable=True
    )

    # Relationships
    module: Mapped["Module"] = relationship("Module", back_populates="topics")
    sections: Mapped[list["TopicSection"]] = relationship(
        "TopicSection",
        back_populates="topic",
        lazy="selectin",
        order_by="TopicSection.order",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<Topic id={self.id} title={self.title}>"


class TopicSection(Base, TimestampMixin):
    """One step of a lesson: a titled, typed, separately completable piece of a topic.

    A row rather than a heading inside `Topic.content`. Treating section boundaries as markdown
    headings was the first design and it fails on one requirement: each section is separately
    completable, so completion needs an identity to write against and a heading in a text blob has
    none. Locating a section by "the third `##`" would make renaming a heading silently reassign the
    learner's progress.

    `completed` sits on the row rather than in a per-learner join table, matching `Topic.completed`
    and `Module.completed`. A course has one owner, so the row and the learner are the same thing
    today; per-learner section progress is what `UserTopicProgress` would grow into.
    """

    __tablename__ = "TopicSection"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: __import__("uuid").uuid4().hex[:25]
    )
    topic_id: Mapped[str] = mapped_column(
        "topicId", String, ForeignKey("Topic.id", ondelete="CASCADE"), index=True
    )
    order: Mapped[float] = mapped_column(Float, default=0, server_default="0")
    # concept | example | algorithm | comparison | check. A plain string, not a native enum: migration
    # 001 dropped the Prisma-era enums because extending one needed a migration and a deploy in
    # lockstep, and the set of ways to explain something is not closed.
    kind: Mapped[str] = mapped_column(String, default="concept", server_default="concept")
    title: Mapped[str] = mapped_column(String, nullable=False)
    eyebrow: Mapped[str | None] = mapped_column(String, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Minutes as an integer, not the "6 min" string the fixture carried. A number sums into a lesson
    # total and formats per locale; a formatted string does neither, and the page needs the total.
    duration_minutes: Mapped[int | None] = mapped_column("durationMinutes", Integer, nullable=True)
    paragraphs: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    key_idea: Mapped[str | None] = mapped_column("keyIdea", Text, nullable=True)
    # [{title, detail}] — kept structured because a step is not a numbered paragraph, and flattening
    # it into prose only to parse it back out loses that.
    steps: Mapped[list[dict] | None] = mapped_column(JSON, nullable=True)
    bullets: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    code: Mapped[str | None] = mapped_column(Text, nullable=True)
    completed: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    # Cleared on reopen, so a pending section never carries a completion time. Same contract as
    # `Topic.completedAt`.
    completed_at: Mapped[datetime | None] = mapped_column(
        "completedAt", DateTime(timezone=True), nullable=True
    )

    # Relationships
    topic: Mapped["Topic"] = relationship("Topic", back_populates="sections")

    def __repr__(self) -> str:
        return f"<TopicSection id={self.id} kind={self.kind} title={self.title}>"


class TopicCheckAttempt(Base):
    """One answer to a topic's end-of-lesson knowledge check.

    A row per press of `Check answer`, not a column holding the current answer. Changing the answer is
    allowed by design — the reader reveals the correct choice and its explanation, then lets the learner
    pick again — so a latest-answer column would record what they clicked *after* being told, which is
    almost always correct and says nothing about what they understood. Keeping every attempt keeps the
    first one, which is the only one that measures anything.

    `TimestampMixin` is deliberately not used: an attempt is an event. It happened once, at one time,
    and nothing about it is ever edited, so an `updatedAt` would be a column that can only ever equal
    `createdAt` while implying otherwise.

    `question` and `choiceLabel` are snapshots. `Topic.knowledgeCheck` is JSON that regeneration
    replaces wholesale, so without them an attempt would reference a choice id that no longer exists on
    a question that may no longer be asked.
    """

    __tablename__ = "TopicCheckAttempt"
    __table_args__ = (
        Index("TopicCheckAttempt_userId_topicId_createdAt_idx", "userId", "topicId", "createdAt"),
    )

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: __import__("uuid").uuid4().hex[:25]
    )
    topic_id: Mapped[str] = mapped_column(
        "topicId", String, ForeignKey("Topic.id", ondelete="CASCADE")
    )
    user_id: Mapped[str] = mapped_column(
        "userId", String, ForeignKey("User.id", ondelete="CASCADE")
    )
    choice_id: Mapped[str] = mapped_column("choiceId", String, nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    choice_label: Mapped[str] = mapped_column("choiceLabel", Text, nullable=False)
    #: Graded on the server from the stored check. The answer key ships to the browser so the reader
    #: can reveal the verdict without a round trip, which is exactly why the verdict cannot be taken
    #: from the browser: it would be a score the learner's own page got to decide.
    correct: Mapped[bool] = mapped_column(Boolean, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        "createdAt",
        DateTime(timezone=True),
        default=lambda: __import__("datetime").datetime.now(__import__("datetime").timezone.utc),
    )

    def __repr__(self) -> str:
        return f"<TopicCheckAttempt topic={self.topic_id} correct={self.correct}>"


class TopicIllustration(Base):
    """A diagram or equation produced while studying a topic.

    ## Why this table exists

    Two paths already generated visuals and neither kept them. The voice tutor calls `study_show_visual`
    when a picture would help, and the learner can ask for one directly through
    `POST /gemini-live/study/diagram`, which costs 80 credits. Both delivered `{mermaid, display_math,
    caption}` to the browser, where it went into a zustand map capped at 48 entries — **read by nothing, and
    with no mermaid or KaTeX renderer installed anywhere in the client.** So the tutor said "look at this
    diagram", the learner saw nothing, and reloading the page discarded whatever had been generated.

    Rendering it was the first half. This is the second: a diagram the learner paid for and understood
    something from should still be there tomorrow, and a lesson they return to should show what they were
    shown last time. Client memory cannot do that.

    ## Columns rather than an opaque block array

    The web models these as `MessageContentBlock[]`, and storing that shape verbatim was the obvious move.
    It is rejected because the server would then hold a JSON blob it cannot reason about — it could not tell
    a diagram from an image, could not enforce that a row contains *something* drawable, and could not
    reject an `image` block pointing at an arbitrary URL. Both producers emit exactly these three values, so
    these are the three columns, and the client composes its own blocks from them the way it already does
    for the REST response.

    ## `userId`, even though a course has one owner

    Same reasoning as `TopicCheckAttempt`. Classrooms assign courses to their members, so several learners
    genuinely study one topic — and a visual generated inside *one* learner's voice conversation was framed
    by what that learner was struggling with. Showing it to everyone who opens the topic would leak one
    person's session into another's lesson.

    ## No `TimestampMixin`

    An illustration is an event: generated once, never edited. An `updatedAt` here could only ever equal
    `createdAt` while implying that editing is a thing that happens. Deletion is offered — an unhelpful
    diagram is clutter on a lesson the learner returns to — and deleting is not editing.
    """

    __tablename__ = "TopicIllustration"
    __table_args__ = (
        Index("TopicIllustration_topicId_userId_createdAt_idx", "topicId", "userId", "createdAt"),
        # At least one of the two must hold something. A row with neither renders as an empty panel, which
        # reads as a broken feature rather than as an absent one — the same reason `generate_for_topic`
        # raises instead of returning a blank diagram.
        CheckConstraint(
            "(mermaid IS NOT NULL AND mermaid <> '') OR"
            " (\"displayMath\" IS NOT NULL AND \"displayMath\" <> '')",
            name="TopicIllustration_has_content",
        ),
    )

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: __import__("uuid").uuid4().hex[:25]
    )
    topic_id: Mapped[str] = mapped_column(
        "topicId", String, ForeignKey("Topic.id", ondelete="CASCADE")
    )
    user_id: Mapped[str] = mapped_column(
        "userId", String, ForeignKey("User.id", ondelete="CASCADE")
    )
    #: Mermaid source, diagram body only — no markdown fences. Nullable because a row may be maths alone.
    mermaid: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Display-mode LaTeX without delimiters. Nullable because a row may be a diagram alone.
    display_math: Mapped[str | None] = mapped_column("displayMath", Text, nullable=True)
    #: The model's own one-line account of what it drew. Null rather than derived from the topic title,
    #: which would describe the lesson instead of the picture.
    caption: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: `tutor` when the model chose to show it mid-conversation, `learner` when it was asked for directly.
    #: Worth keeping apart: one is teaching, the other is a request, and a lesson that lists them together
    #: without saying which is which attributes the learner's own question to the tutor.
    source: Mapped[str] = mapped_column(String, default="tutor", server_default="tutor")

    created_at: Mapped[datetime] = mapped_column(
        "createdAt",
        DateTime(timezone=True),
        default=lambda: __import__("datetime").datetime.now(__import__("datetime").timezone.utc),
    )

    def __repr__(self) -> str:
        return f"<TopicIllustration topic={self.topic_id} source={self.source}>"


class CourseRating(Base, TimestampMixin):
    """One learner's rating of one course.

    A table rather than a `Course.rating` float. A float is a number with nothing behind it: any
    value can be written, a learner cannot change their mind, and the "4.9 learner rating" the page
    prints would be as invented as the fixture it replaces. One row per learner per course, unique on
    the pair, means the aggregate is computed from ratings that were actually given.

    Several raters is a real case, not a hypothetical: classrooms assign courses to their members, so
    an assigned course accumulates ratings from everyone who took it. A course with none aggregates to
    null, and the page shows no rating rather than a zero — those are different statements.
    """

    __tablename__ = "CourseRating"
    __table_args__ = (
        CheckConstraint("value >= 1 AND value <= 5", name="CourseRating_value_range"),
        UniqueConstraint("courseId", "userId", name="CourseRating_courseId_userId_key"),
        Index("CourseRating_courseId_idx", "courseId"),
    )

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: __import__("uuid").uuid4().hex[:25]
    )
    course_id: Mapped[str] = mapped_column(
        "courseId", String, ForeignKey("Course.id", ondelete="CASCADE")
    )
    user_id: Mapped[str] = mapped_column(
        "userId", String, ForeignKey("User.id", ondelete="CASCADE")
    )
    # 1 to 5, constrained in the database as well as in the request model — the aggregate the page
    # prints has no way to notice a 40 that arrived by some other path.
    value: Mapped[int] = mapped_column(Integer, nullable=False)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Relationships
    course: Mapped["Course"] = relationship("Course", back_populates="ratings")

    def __repr__(self) -> str:
        return f"<CourseRating course={self.course_id} value={self.value}>"


class UserTopicProgress(Base, TimestampMixin):
    """Per-user progress for shared Circle courses."""

    __tablename__ = "UserTopicProgress"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: __import__("uuid").uuid4().hex[:25]
    )
    user_id: Mapped[str] = mapped_column(
        "userId", String, ForeignKey("User.id", ondelete="CASCADE"), index=True
    )
    topic_id: Mapped[str] = mapped_column(
        "topicId", String, ForeignKey("Topic.id", ondelete="CASCADE"), index=True
    )
    completed: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    completed_at: Mapped[datetime | None] = mapped_column(
        "completedAt", DateTime(timezone=True), nullable=True
    )
    minutes_spent: Mapped[float] = mapped_column(
        "minutesSpent", Float, default=0, server_default="0"
    )

    __table_args__ = (
        Index("UserTopicProgress_userId_topicId_key", "userId", "topicId", unique=True),
    )


class Resource(Base, TimestampMixin):
    __tablename__ = "Resource"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: __import__("uuid").uuid4().hex[:25]
    )
    user_id: Mapped[str] = mapped_column(
        "userId", String, ForeignKey("User.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String, nullable=False)
    url: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str | None] = mapped_column(String, nullable=True)
    type: Mapped[str] = mapped_column(String, default="OTHER", server_default="OTHER")
    metadata_json: Mapped[dict | None] = mapped_column("metadata", JSON, nullable=True)
    is_recommended: Mapped[bool] = mapped_column(
        "isRecommended", Boolean, default=False, server_default="false"
    )
    recommendation_score: Mapped[float | None] = mapped_column(
        "recommendationScore", Float, nullable=True
    )
    recommendation_source: Mapped[str | None] = mapped_column(
        "recommendationSource", String, nullable=True
    )
    recommendation_reason: Mapped[str | None] = mapped_column(
        "recommendationReason", String, nullable=True
    )
    course_id: Mapped[str | None] = mapped_column("courseId", String, nullable=True, index=True)
    topic_id: Mapped[str | None] = mapped_column("topicId", String, nullable=True, index=True)
    space_id: Mapped[str | None] = mapped_column("spaceId", String, nullable=True, index=True)
    click_count: Mapped[int] = mapped_column("clickCount", Integer, default=0, server_default="0")
    bookmark_count: Mapped[int] = mapped_column(
        "bookmarkCount", Integer, default=0, server_default="0"
    )
    last_accessed_at: Mapped[datetime | None] = mapped_column(
        "lastAccessedAt", DateTime(timezone=True), nullable=True
    )


class CourseOutlineSatisfaction(Base):
    """KPI tracking for AI-generated course outlines."""

    __tablename__ = "CourseOutlineSatisfaction"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: __import__("uuid").uuid4().hex[:25]
    )
    user_id: Mapped[str] = mapped_column(
        "userId", String, ForeignKey("User.id", ondelete="CASCADE"), index=True
    )
    course_id: Mapped[str] = mapped_column(
        "courseId", String, ForeignKey("Course.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String, nullable=False)
    feedback: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        "createdAt",
        DateTime(timezone=True),
        default=lambda: __import__("datetime").datetime.now(__import__("datetime").timezone.utc),
    )


class Embedding(Base, TimestampMixin):
    __tablename__ = "Embedding"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: __import__("uuid").uuid4().hex[:25]
    )
    object_type: Mapped[str] = mapped_column("objectType", String, nullable=False)
    object_id: Mapped[str] = mapped_column("objectId", String, nullable=False)
    vector: Mapped[dict] = mapped_column(JSON, nullable=False)
    content: Mapped[str | None] = mapped_column(String, nullable=True)
    metadata_json: Mapped[dict | None] = mapped_column("metadata", JSON, nullable=True)
    resource_id: Mapped[str | None] = mapped_column("resourceId", String, nullable=True, index=True)
    resource_bank_item_id: Mapped[str | None] = mapped_column(
        "resourceBankItemId", String, nullable=True, index=True
    )

    __table_args__ = (Index("Embedding_objectType_objectId_idx", "objectType", "objectId"),)
