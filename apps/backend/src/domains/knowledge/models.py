"""
Knowledge domain — Pydantic request/response schemas.

Covers courses, modules, topics, resources, resource bank,
AI generation, and progress analytics.
"""

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from src.shared.schemas import CamelModel, PaginatedResponse

# ===========================================================================
# Enums
# ===========================================================================


class DifficultyLevel(str, Enum):
    BEGINNER = "BEGINNER"
    INTERMEDIATE = "INTERMEDIATE"
    ADVANCED = "ADVANCED"
    EXPERT = "EXPERT"


class ResourceType(str, Enum):
    VIDEO = "VIDEO"
    ARTICLE = "ARTICLE"
    BOOK = "BOOK"
    COURSE = "COURSE"
    DOCUMENT = "DOCUMENT"
    WEBSITE = "WEBSITE"
    PODCAST = "PODCAST"
    OTHER = "OTHER"


# ===========================================================================
# Topics
# ===========================================================================


SectionKind = Literal["concept", "example", "algorithm", "comparison", "check"]


class TopicSectionStep(CamelModel):
    """One step of a walkthrough. Kept as a title and a detail rather than a single string, because
    the reader renders the title as a heading and the detail as prose beneath it."""

    title: str
    detail: str


class TopicSectionCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    order: float = Field(..., ge=0)
    kind: SectionKind = "concept"
    eyebrow: str | None = Field(None, max_length=120)
    summary: str | None = None
    durationMinutes: int | None = Field(None, ge=0, le=600)
    paragraphs: list[str] | None = None
    keyIdea: str | None = None
    steps: list[TopicSectionStep] | None = None
    bullets: list[str] | None = None
    code: str | None = None


class TopicSectionUpdate(BaseModel):
    """Every field optional, and read with `exclude_unset=True` so omitting a key leaves it alone
    while sending an explicit null clears it."""

    title: str | None = Field(None, min_length=1, max_length=255)
    order: float | None = Field(None, ge=0)
    kind: SectionKind | None = None
    eyebrow: str | None = Field(None, max_length=120)
    summary: str | None = None
    durationMinutes: int | None = Field(None, ge=0, le=600)
    paragraphs: list[str] | None = None
    keyIdea: str | None = None
    steps: list[TopicSectionStep] | None = None
    bullets: list[str] | None = None
    code: str | None = None


class TopicSectionResponse(CamelModel):
    """One step of a lesson.

    `completed` is per section and lives beside `Topic.completed` rather than replacing it. The two
    answer different questions — whether the learner has worked through this step, and whether they
    consider the topic done — and the topic's flag is not derived from the sections', because a
    learner may mark a topic complete without clicking through every section of it.
    """

    id: str
    topic_id: str
    order: float
    kind: str
    title: str
    eyebrow: str | None = None
    summary: str | None = None
    #: Minutes, not a formatted string: the lesson header sums these into a total, which a
    #: pre-formatted "6 min" could not be added up.
    duration_minutes: int | None = None
    paragraphs: list[str] | None = None
    key_idea: str | None = None
    steps: list[TopicSectionStep] | None = None
    bullets: list[str] | None = None
    code: str | None = None
    completed: bool = False
    completed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class KnowledgeCheckChoice(CamelModel):
    """One option of a topic's knowledge check.

    `correct` is sent to the client deliberately. The check is a self-test at the end of a lesson in
    a course the learner owns — it gates nothing, is not scored, and no attempt is recorded — so the
    page grades the answer and shows the explanation without a round trip. An assessment whose result
    matters belongs in the preparation domain, where the correct answer is not published.
    """

    id: str
    label: str
    correct: bool = False


class KnowledgeCheck(CamelModel):
    """The end-of-lesson check: one question, its choices, and why the answer is what it is."""

    question: str
    explanation: str
    choices: list[KnowledgeCheckChoice] = []


#: What kind of work a sitting is. Distinct from `SectionKind`, which is how one passage within a
#: lesson explains something — see the note on `Topic.kind`.
TopicKind = Literal["Lesson", "Practice", "Project", "Check"]


class TopicCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    order: float = Field(..., ge=0)
    content: str | None = None
    estimatedHours: float | None = Field(None, ge=0)
    kind: TopicKind | None = None
    summary: str | None = None
    objectives: list[str] | None = None
    knowledgeCheck: KnowledgeCheck | None = None


class TopicUpdate(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=255)
    order: float | None = Field(None, ge=0)
    content: str | None = None
    estimatedHours: float | None = Field(None, ge=0)
    completed: bool | None = None
    kind: TopicKind | None = None
    summary: str | None = None
    objectives: list[str] | None = None
    knowledgeCheck: KnowledgeCheck | None = None


class TopicResponse(CamelModel):
    """A topic as returned to the client.

    Fields are snake_case and the wire format stays camelCase, which is not cosmetic here: this model
    is validated straight off a SQLAlchemy `Topic`, and while the fields were declared camelCase that
    validation could not find `module_id`, `created_at` or `updated_at` at all — `POST .../topics`
    answered `500`. `estimatedHours` was worse: it has a default, so it silently came back null
    rather than failing, and the endpoint looked like it worked.
    """

    id: str
    module_id: str
    title: str
    order: float
    content: str | None = None
    completed: bool = False
    # Null for a pending topic, and for one completed before the column existed — those are absent
    # from any history rather than given a date nothing observed.
    completed_at: datetime | None = None
    estimated_hours: float | None = None
    #: Lesson, Practice, Project or Check. Null on topics written before the column existed, and the
    #: outline shows no label rather than assuming Lesson.
    kind: str | None = None
    #: The lesson header's one-line description. Null rather than derived: the first section's summary
    #: describes that section, and the opening of `content` is the material rather than a description
    #: of it, so either substitute would say something other than what the header claims.
    summary: str | None = None
    #: What the learner will be able to do after this topic. Null means none were written, which is
    #: not the same as an empty list — the reader shows no objectives block rather than an empty one.
    objectives: list[str] | None = None
    knowledge_check: KnowledgeCheck | None = None
    #: The lesson body as an ordered sequence of steps. Empty for a topic whose content is a single
    #: markdown blob in `content`, which the reader falls back to rather than showing nothing.
    sections: list[TopicSectionResponse] = []
    created_at: datetime
    updated_at: datetime


class TopicGenerateRequest(BaseModel):
    """AI content generation type for a topic."""

    type: Literal["explain", "quiz", "summary", "flashcards"]
    #: Required for `flashcards`, ignored otherwise. Generated cards have to land in a deck: an
    #: unfiled card is invisible to the deck pages and only reachable through the flat card list.
    deckId: str | None = None


class TopicGenerateResponse(BaseModel):
    type: str
    topicId: str
    content: str
    #: What was stored, in words — "7 sections", "5 flashcards" — or null when the type deliberately
    #: stores nothing. Stated rather than implied by the type, so a caller can report the outcome
    #: without encoding the rule about which types persist.
    persisted: str | None = None


# ===========================================================================
# Modules
# ===========================================================================


class TopicBulkCreate(BaseModel):
    """A whole module's worth of topics in one request.

    The create wizard saves an outline of a dozen or more topics. Sent one at a time, a failure halfway
    leaves a course that is half an outline, and the learner is the one who has to work out which half
    and finish it by hand. One request either writes the outline or writes none of it.
    """

    topics: list[TopicCreate] = Field(..., min_length=1, max_length=100)


class ReorderRequest(BaseModel):
    """The new order, as ids from first to last.

    Ids rather than `{id, order}` pairs, because the caller knows the sequence it wants and not the
    float values that encode it. Letting a client send the numbers means two clients can disagree about
    the spacing, and a dragged item can be given an order that collides with another.
    """

    ids: list[str] = Field(..., min_length=1, max_length=500)


class ReorderResponse(CamelModel):
    """What the reorder wrote, so the caller can tell a no-op from a partial write."""

    reordered: int


class ModuleCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    order: float = Field(..., ge=0)
    description: str | None = None


class ModuleUpdate(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=255)
    order: float | None = Field(None, ge=0)
    description: str | None = None


class ModuleResponse(CamelModel):
    """A module with its topics and derived progress.

    Built from `course_service.calculate_module_progress`, whose keys are camelCase and reach these
    snake_case fields as aliases. `topics` holds raw ORM rows, validated through `TopicResponse`.
    """

    id: str
    course_id: str
    title: str
    order: float
    description: str | None = None
    completed: bool = False
    progress: float = 0.0
    topic_count: int = 0
    completed_topic_count: int = 0
    topics: list[TopicResponse] = []
    created_at: datetime
    updated_at: datetime


# ===========================================================================
# Courses
# ===========================================================================


class CourseInstructor(CamelModel):
    """Who authored or teaches the course, when anyone did.

    Absent — the whole object null — for a course the learner generated for themselves, which is most
    of them. Crediting the owner as the instructor of their own course, or crediting "Maigie", would
    put a claim on the page that nothing supports.

    Initials are not stored or returned: they are the first letters of the name, which is formatting,
    and a stored copy is one more thing that can disagree with the name beside it.
    """

    name: str
    role: str | None = None


class CourseRatingSummary(CamelModel):
    """The aggregate of a course's ratings, plus this learner's own.

    `average` and `count` are null and zero for an unrated course, so the page can show no rating
    rather than a zero — "nobody has rated this" and "everybody rated it 0" are different statements
    and only one of them is ever true here.

    `yourRating` is included so the control can show the learner what they already gave without a
    second request, and so re-rating updates rather than adds.
    """

    average: float | None = None
    count: int = 0
    your_rating: int | None = None


class CourseRatingCreate(BaseModel):
    """Rating a course. Re-rating updates the existing row, so the average cannot be weighted by
    submitting repeatedly."""

    value: int = Field(..., ge=1, le=5)
    comment: str | None = Field(None, max_length=2000)


class CourseCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    description: str | None = None
    difficulty: DifficultyLevel | None = None
    targetDate: datetime | None = None
    isAIGenerated: bool = False
    spaceId: str | None = None
    #: What the course is about. Distinct from `difficulty`, which says how hard it is — one cannot
    #: answer for the other, and using difficulty as the badge put the wrong word on the card.
    category: str | None = Field(None, max_length=120)
    tags: list[str] | None = None
    outcomes: list[str] | None = None
    instructorName: str | None = Field(None, max_length=200)
    instructorRole: str | None = Field(None, max_length=200)
    #: The learner's brief, in their words — what the create wizard's description box collects. Stored
    #: because it drives generation and because a learner should be able to see what they asked for.
    sourcePrompt: str | None = None
    #: Visual, Hands-on, Concept first or Mixed. Scoped to this course, overriding the learner's global
    #: `preferredExplanationStyle` rather than replacing it.
    teachingStyle: str | None = Field(None, max_length=60)


class CourseUpdate(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=255)
    description: str | None = None
    difficulty: DifficultyLevel | None = None
    targetDate: datetime | None = None
    archived: bool | None = None
    spaceId: str | None = None
    category: str | None = Field(None, max_length=120)
    tags: list[str] | None = None
    outcomes: list[str] | None = None
    instructorName: str | None = Field(None, max_length=200)
    instructorRole: str | None = Field(None, max_length=200)
    sourcePrompt: str | None = None
    teachingStyle: str | None = Field(None, max_length=60)


class CourseResponse(CamelModel):
    """A course with its modules, topics and derived progress.

    `progress`, `totalTopics` and `completedTopics` are computed per request from the topics rather
    than read from `Course.progress`, which nothing writes — see the note on that column.
    """

    id: str
    user_id: str
    title: str
    description: str | None = None
    difficulty: DifficultyLevel | None = None
    target_date: datetime | None = None
    # Explicit alias: the generator would produce `isAiGenerated`, and the published contract
    # — and every client reading it — says `isAIGenerated`. An acronym is where a camelCase
    # generator and a hand-named field disagree, so this one is pinned rather than derived.
    is_ai_generated: bool = Field(default=False, alias="isAIGenerated")
    archived: bool = False
    progress: float = 0.0
    total_topics: int = 0
    completed_topics: int = 0
    modules: list[ModuleResponse] = []
    created_at: datetime
    updated_at: datetime
    outline_satisfaction_recorded: bool = False
    #: What the course is about, as a subject label — the badge on the detail page. Null for courses
    #: created before it existed; the badge is omitted rather than guessed from the difficulty.
    category: str | None = None
    tags: list[str] | None = None
    #: "What you'll be able to do" — the promise the course makes, separate from the objectives of any
    #: one topic within it.
    outcomes: list[str] | None = None
    #: What the learner asked for, kept so the request that produced the course is visible and
    #: regeneration has something to work from.
    source_prompt: str | None = None
    #: How this course should be explained. Null means fall through to the learner's global preference.
    teaching_style: str | None = None
    #: Null when nobody is credited, which is the normal case for a self-generated course.
    instructor: CourseInstructor | None = None
    rating: CourseRatingSummary | None = None


class TopicLocationResponse(CamelModel):
    """A topic, plus enough of its module and course to open it.

    Exists because every caller that holds a topic id needs its course before it can show the topic,
    and nothing could get from one to the other. Two surfaces were blocked on that:

    - The lesson route, `/learn/lessons/{id}`, which had no backend concept behind it at all.
    - A study plan item carrying a `topicId`, which could be completed but not opened.

    Returns the ids and titles of the ancestors rather than the whole `CourseResponse`. A caller that
    wants the full outline asks for the course; a caller opening one topic wants a breadcrumb, and
    embedding every module and topic of the course to supply it would make opening one topic cost the
    whole curriculum.
    """

    topic: TopicResponse
    module_id: str
    module_title: str
    course_id: str
    course_title: str
    #: Position among the course's topics in outline order, 1-based, and how many there are. What a
    #: "Topic 4 of 12" line needs, and not derivable by a caller holding only this topic.
    position: int
    total_topics: int
    #: Completion of the whole course as a percentage. The lesson header shows the learner where this
    #: sitting sits in the course, so without it the surface would have to fetch the entire course to
    #: print one number.
    course_progress: float = 0.0


class CourseNextTopic(CamelModel):
    """The next incomplete topic of a course, in outline order.

    Deliberately not the whole `TopicResponse`: a card prints a title and needs the ids to link, and
    `content` on a topic can be a page of markdown that no library view renders.
    """

    id: str
    module_id: str
    title: str
    estimated_hours: float | None = None


class CourseListItem(CamelModel):
    """A course as it appears in the library, without its modules or topics.

    `moduleCount` rather than the modules themselves: a library card shows a count, and the client
    type comment already records that this is not named `totalModules`.
    """

    id: str
    user_id: str
    title: str
    description: str | None = None
    difficulty: DifficultyLevel | None = None
    target_date: datetime | None = None
    # Explicit alias: the generator would produce `isAiGenerated`, and the published contract
    # — and every client reading it — says `isAIGenerated`. An acronym is where a camelCase
    # generator and a hand-named field disagree, so this one is pinned rather than derived.
    is_ai_generated: bool = Field(default=False, alias="isAIGenerated")
    archived: bool = False
    progress: float = 0.0
    total_topics: int = 0
    completed_topics: int = 0
    module_count: int = 0
    # Both derived from the topics the list response does not carry, aggregated in SQL. A card names
    # the next thing to do and how much is left, and loading every topic of every course to work that
    # out is what the counts above exist to avoid.
    #
    # `nextTopic` is null when nothing is incomplete — the course is finished, which wants a different
    # label rather than a blank one. `remainingHours` is null when no remaining topic carries an
    # estimate, so a client says "no estimate" instead of printing a confident `0h` for work that has
    # never been sized.
    next_topic: CourseNextTopic | None = None
    remaining_hours: float | None = None
    #: The card's own fields, restored. `difficulty` was briefly used as the category badge and a
    #: module count as the tags, which put facts on the card that were not the ones it was showing.
    category: str | None = None
    tags: list[str] | None = None
    created_at: datetime
    updated_at: datetime


class CourseListResponse(PaginatedResponse[CourseListItem]):
    """The course library, one page at a time.

    Migrated onto the shared envelope: `items` rather than `courses`, and `pages` rather than
    `hasMore`. Done now because the only consumer is being rewritten from scratch in the same change,
    which is the cheapest this migration will ever be — every later moment costs a client rewrite as
    well.

    `pages` replaces `hasMore` because it answers strictly more: a pager needs to know how many pages
    there are, and "is there another one" is `page < pages`. `hasMore` could not be derived the other
    way round.
    """


class CourseActivityEntry(CamelModel):
    """A topic the learner completed, with the course it belongs to.

    Built from `Topic.completedAt`, not from a stored activity log. Topics completed before that
    column existed are absent rather than dated from `updatedAt`, which moves on any edit.
    """

    topic_id: str
    topic_title: str
    course_id: str
    course_title: str
    completed_at: datetime
    estimated_hours: float | None = None


class CoursesDashboardResponse(CamelModel):
    """Everything the course library shows above its grid, in one request.

    Composed for the same reason as the flashcards and study-plan dashboards: assembled from the
    endpoints that already existed this was several requests, and the recent-activity list could not
    be produced at all.

    **`weeklyHours` is estimated, not measured.** It sums the estimates on the topics completed since
    the learner's week began. Nothing anywhere records how long a topic actually took, and a column
    for it would not make the measurement exist — so the figure is named for what it is, exactly as
    `completedMinutes` is on a study plan.

    There is deliberately **no weekly goal**. A learner sets a weekly target on a study plan, where it
    is asked for; nothing asks for one against their course library, and dividing by a number nobody
    chose would put an invented target on screen.
    """

    active_courses: int
    archived_courses: int
    total_topics: int
    completed_topics: int
    #: Estimated hours on topics completed since the learner's week began. Planned effort, not time.
    weekly_hours: float
    #: Topics completed this week, which unlike the hours above is a count of real events.
    weekly_topics_completed: int
    #: Consecutive days with a completed topic, in the learner's timezone. Distinct from the flashcard
    #: and study-plan streaks: this one counts topics, and they count graded cards and finished tasks.
    current_streak_days: int
    #: Flashcards due now, so the library can point at review work without inventing a figure. From
    #: the flashcard domain, and labelled as such on screen rather than presented as course progress.
    flashcards_due: int
    #: The course to resume: the one whose most recent topic completion is newest.
    featured: CourseListItem | None = None
    recent_activity: list[CourseActivityEntry] = Field(default_factory=list)
    #: False when the learner's timezone was never captured, so "this week" and the streak are a UTC
    #: assumption rather than their actual week.
    timezone_known: bool = False


# `AICourseRequest` was deleted with the `POST /courses/generate` endpoint it served. That endpoint was a
# published `501` whose implementation had been unreachable since the LLM migration, and a permanent `501`
# advertises a capability while refusing it — every client has to special-case something that never worked.
# `CourseOutlineRequest` below replaces it, and takes the learner's brief rather than a bare topic string.


class CourseOutlineRequest(BaseModel):
    """The brief an outline is generated from.

    Everything the create wizard has gathered by its review step. `brief` is the learner's own words and is
    the thing being answered; the rest is context that shapes it.
    """

    title: str = Field(..., min_length=1, max_length=255)
    brief: str = Field(..., min_length=12, max_length=8000)
    difficulty: DifficultyLevel | None = None
    teachingStyle: str | None = Field(None, max_length=60)
    category: str | None = Field(None, max_length=120)


class GeneratedOutlineTopic(CamelModel):
    """One planned sitting. No id and no order: nothing is persisted yet."""

    title: str
    kind: str = "Lesson"
    duration_minutes: int | None = None


class GeneratedOutlineModule(CamelModel):
    title: str
    description: str | None = None
    topics: list[GeneratedOutlineTopic] = []


class CourseOutlineResponse(CamelModel):
    """A proposed curriculum, generated and **not saved**.

    Returned for review so a learner sees what they are about to get before it exists. The wizard previously
    showed the outline of whichever template they started from, whatever they had asked for — typing "Data
    analytics" against the public-speaking template offered "Working with speaking nerves", and accepting it
    saved exactly that.

    Nothing here carries an id or a position, because nothing has been written. The client sends the parts it
    keeps back through the normal create endpoints, which is what makes rejecting an outline free.
    """

    modules: list[GeneratedOutlineModule] = []
    #: Course-level outcomes the same generation produced, so the learner is not asked for them separately.
    outcomes: list[str] | None = None


class CourseOutlineSatisfactionCreate(BaseModel):
    """Learner feedback on AI-generated outline (KPI)."""

    kind: Literal["SATISFIED", "NOT_SATISFIED", "MODIFICATION_REQUESTED"]
    feedback: str | None = Field(None, max_length=4000)


# ===========================================================================
# Course detail, progress and analytics: deliberately absent
# ===========================================================================
#
# `CourseDetailResponse`, `ProgressResponse` and `UserAnalyticsResponse` lived here with no route
# attached, along with the eight helper models only they referenced. A response model nothing returns
# is worse than nothing: it describes a shape, so a client author reasonably assumes an endpoint
# exists — which is why `coursesApi.getCourseDetailView` and `getUserAnalytics` were written, and why
# both throw.
#
# They are not being routed, either. Between them they carried a study streak, a contribution
# footprint and a schedule list, none of which are facts about a course: the first two belong to the
# progress domain and the third to scheduling, and composing them here would put a second
# implementation of each beside the one that owns it. The detail page is served by
# `GET /knowledge/courses/{id}` plus the endpoints that already own those figures.
#
# Deleted rather than commented out, because a commented-out model is the same claim in a quieter
# voice. Nothing referenced them: FastAPI emits schemas reachable from routes, so they never reached
# the published contract or the generated client types.

# ===========================================================================
# Resources
# ===========================================================================


class ResourceCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    url: str
    description: str | None = None
    type: ResourceType = ResourceType.OTHER
    metadata: dict | None = None
    isRecommended: bool = False
    recommendationScore: float | None = None
    recommendationSource: str | None = None
    courseId: str | None = None
    topicId: str | None = None
    spaceId: str | None = None


class ResourceResponse(BaseModel):
    id: str
    userId: str
    title: str
    url: str
    description: str | None = None
    type: str
    metadata: dict | None = None
    isRecommended: bool = False
    recommendationScore: float | None = None
    recommendationSource: str | None = None
    clickCount: int = 0
    bookmarkCount: int = 0
    spaceId: str | None = None
    lastAccessedAt: str | None = None
    createdAt: str
    updatedAt: str


class ResourceListResponse(BaseModel):
    resources: list[ResourceResponse]
    total: int
    page: int
    pageSize: int
    hasMore: bool


class ResourceRecommendationRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=1000)
    limit: int = Field(5, ge=1, le=20)
    context: dict | None = None


class ResourceRecommendationItem(BaseModel):
    title: str
    url: str
    description: str | None = None
    type: str = "OTHER"
    relevance: str | None = None
    score: float = 0.5


class ResourceRecommendationResponse(BaseModel):
    recommendations: list[ResourceRecommendationItem]
    query: str
    personalized: bool = True


# ===========================================================================
# Course Filters
# ===========================================================================


class CourseFilters(BaseModel):
    """Query parameters for filtering courses."""

    archived: bool | None = None
    difficulty: DifficultyLevel | None = None
    isAIGenerated: bool | None = None
    search: str | None = Field(None, max_length=255)
    page: int = Field(1, ge=1)
    pageSize: int = Field(20, ge=1, le=100)
    sortBy: str = Field("createdAt", pattern="^(createdAt|updatedAt|title|progress)$")
    sortOrder: str = Field("desc", pattern="^(asc|desc)$")
    spaceId: str | None = None
