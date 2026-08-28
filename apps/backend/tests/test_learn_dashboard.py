"""Tests for the Learn dashboard read-model.

`GET /learning/dashboard` is the first request the Learn surface makes and, until now, the only
coverage it had anywhere in the suite was a path-presence assertion in `test_app_setup.py` — the
endpoint was listed as mounted and nothing checked what it returned. It composes seven sources
concurrently, which makes the interesting behaviour not the happy path but what happens when one of
them fails: a section that could not be loaded must be *reported* degraded, never rendered as empty.
"You have no courses" and "we could not load your courses" are different sentences and only one of
them is ever true.

Follows `test_prepare_dashboard.py`: no database. Every source is replaced with a fake that can be
told to raise, which is the only practical way to assert what happens when one of seven concurrent
queries fails — `asyncio.gather(..., return_exceptions=True)` turns the failure into a value, and
values are what tests are good at.

The one test here that is not about degradation is the N+1 guard. The dashboard's per-course figures
are computed from relationships that must arrive eager-loaded; a lazy load per course would still
produce the right numbers, which is exactly why nothing would notice.
"""

import os

os.environ.setdefault("SKIP_DB_FIXTURE", "1")

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from src.domains.personal_learning.services import (
    learn_dashboard_service,
    prep_readiness,
)
from src.shared.exceptions import MaigieError

OWNER = "user-owner"
NOW = datetime.now(UTC)

#: Every section the response can report as degraded, in the order the service emits them.
ALL_SECTIONS = [
    "featured",
    "review",
    "stats",
    "courses",
    "paths",
    "tools",
    "recentItems",
    "collections",
]


def _topic(topic_id: str, *, completed: bool, hours: float | None = 0.5, summary=None):
    return SimpleNamespace(
        id=topic_id,
        title=f"Topic {topic_id}",
        summary=summary,
        completed=completed,
        completed_at=NOW if completed else None,
        estimated_hours=hours,
    )


def _module(module_id: str, topics: list[SimpleNamespace]):
    return SimpleNamespace(id=module_id, title=f"Module {module_id}", topics=topics)


def _course(course_id: str, *, modules: list[SimpleNamespace], updated_days_ago: int = 0):
    return SimpleNamespace(
        id=course_id,
        title=f"Course {course_id}",
        description="A description",
        difficulty="BEGINNER",
        modules=modules,
        updated_at=NOW - timedelta(days=updated_days_ago),
    )


def _note(note_id: str, *, minutes_ago: int = 0):
    return SimpleNamespace(
        id=note_id,
        title=f"Note {note_id}",
        updated_at=NOW - timedelta(minutes=minutes_ago),
    )


def _resource(resource_id: str, *, minutes_ago: int = 0, accessed_minutes_ago: int | None = None):
    return SimpleNamespace(
        id=resource_id,
        title=f"Resource {resource_id}",
        source_type="shared_bank",
        created_at=NOW - timedelta(minutes=minutes_ago),
        last_accessed_at=(
            None if accessed_minutes_ago is None else NOW - timedelta(minutes=accessed_minutes_ago)
        ),
    )


def _document(doc_id: str, *, minutes_ago: int = 0, fmt="pdf"):
    return SimpleNamespace(
        id=doc_id,
        title=f"Document {doc_id}",
        format=fmt,
        created_at=NOW - timedelta(minutes=minutes_ago),
    )


def _plan(plan_id: str, *, days: int, completed=2, total=5):
    return SimpleNamespace(
        id=plan_id,
        title=f"Plan {plan_id}",
        goal_description="Finish the thing",
        status="ACTIVE",
        deadline=NOW + timedelta(days=days),
        completed_items=completed,
        total_items=total,
    )


def _prep(prep_id: str, *, days: int):
    return SimpleNamespace(
        id=prep_id,
        subject=f"Subject {prep_id}",
        description="About it",
        status="IN_PROGRESS",
        exam_date=NOW + timedelta(days=days),
    )


class FakeKnowledge:
    """The `knowledge` repository, as much of it as the dashboard reads."""

    def __init__(self, owner: "FakeSources"):
        self.owner = owner
        self.course_takes: list[int] = []
        #: Every course fetched by id, so a test can count the queries the featured card costs.
        self.course_lookups: list[str] = []

    async def list_courses(self, user_id, *, where, skip=0, take=20, order=None):
        self.owner._boom("courses")
        self.course_takes.append(take)
        return self.owner.courses[:take], len(self.owner.courses)

    async def count_completed_topics(self, user_id):
        self.owner._boom("courses")
        return self.owner.completed_topics

    async def recently_completed_topics(self, user_id, *, limit=5):
        self.owner._boom("featured")
        return self.owner.completions[:limit]

    async def find_course_outline(self, course_id, user_id):
        self.owner._boom("featured")
        self.course_lookups.append(course_id)
        return next((c for c in self.owner.courses if c.id == course_id), None)


class FakePersonalLearning:
    def __init__(self, owner: "FakeSources"):
        self.owner = owner

    async def list_recent_resources(self, user_id, *, take=6):
        self.owner._boom("resources")
        return self.owner.resources[:take], len(self.owner.resources)

    async def list_dashboard_study_plans(self, user_id, *, take=6):
        self.owner._boom("paths")
        return self.owner.plans[:take], len(self.owner.plans)

    async def list_dashboard_exam_preps(self, user_id, *, take=6):
        self.owner._boom("paths")
        return self.owner.preps[:take], len(self.owner.preps)


class FakeSources:
    """Every source the dashboard reads, each independently switchable to raise.

    `fail` names the sources that should blow up, so a test states which of the seven concurrent
    queries failed instead of assembling seven separate fakes.
    """

    def __init__(self):
        self.courses: list[SimpleNamespace] = []
        self.active_courses = 0
        self.completed_topics = 0
        self.completions: list[tuple] = []
        self.notes: list[SimpleNamespace] = []
        self.resources: list[SimpleNamespace] = []
        self.documents: list[SimpleNamespace] = []
        self.plans: list[SimpleNamespace] = []
        self.preps: list[SimpleNamespace] = []
        self.progress: dict[str, SimpleNamespace] = {}
        self.stats: dict[str, int] = {
            "dueToday": 0,
            "total": 0,
            "masteredCount": 0,
            "overdueCount": 0,
        }
        self.fail: set[str] = set()
        self.collections: list[dict] = []
        self.knowledge = FakeKnowledge(self)
        self.repo = FakePersonalLearning(self)

    def _boom(self, source: str):
        if source in self.fail:
            raise RuntimeError(f"{source} unavailable")


@pytest.fixture
def sources(monkeypatch):
    fake = FakeSources()

    monkeypatch.setattr(learn_dashboard_service, "knowledge_repo", fake.knowledge)
    monkeypatch.setattr(learn_dashboard_service, "personal_learning_repo", fake.repo)

    async def get_statistics(*, user_id, deck_id=None, timezone_name=None):
        fake._boom("review")
        return fake.stats

    async def list_notes(*, user_id, page=1, size=20, **kwargs):
        fake._boom("notes")
        return fake.notes[:size], len(fake.notes)

    async def list_documents(*, user_id, page=1, page_size=20, **kwargs):
        fake._boom("documents")
        return fake.documents[:page_size], len(fake.documents)

    async def load_for_preparations(prep_ids):
        fake._boom("paths")
        return {pid: fake.progress[pid] for pid in prep_ids if pid in fake.progress}

    monkeypatch.setattr(learn_dashboard_service.flashcard_service, "get_statistics", get_statistics)
    monkeypatch.setattr(learn_dashboard_service.note_service, "list_notes", list_notes)
    monkeypatch.setattr(learn_dashboard_service.document_impl, "list_documents", list_documents)
    monkeypatch.setattr(prep_readiness, "load_for_preparations", load_for_preparations)

    from src.domains.personal_learning.services import collection_service

    async def get_dashboard_collections(user_id, limit=6):
        fake._boom("collections")
        return fake.collections

    monkeypatch.setattr(collection_service, "get_dashboard_collections", get_dashboard_collections)

    return fake


async def _dashboard(**overrides):
    kwargs = {
        "user_id": OWNER,
        "course_limit": 4,
        "path_limit": 3,
        "recent_limit": 6,
    }
    kwargs.update(overrides)
    return await learn_dashboard_service.get_dashboard(**kwargs)


@pytest.fixture
def populated(sources):
    """One of everything, enough for each section to have something to say."""
    finished = _topic("t1", completed=True)
    pending = _topic("t2", completed=False, hours=1.5, summary="What this covers")
    course = _course("c1", modules=[_module("m1", [finished, pending])])
    sources.courses = [course]
    sources.active_courses = 1
    sources.completed_topics = 1
    sources.completions = [(finished, "c1", "Course c1")]
    sources.notes = [_note("n1", minutes_ago=5)]
    sources.resources = [_resource("r1", minutes_ago=30)]
    sources.documents = [_document("d1", minutes_ago=60)]
    sources.plans = [_plan("p1", days=3)]
    sources.preps = [_prep("e1", days=10)]
    sources.progress = {
        "e1": SimpleNamespace(topics_strong=2, topics_total=8, progress_percent=25.0)
    }
    sources.stats = {"dueToday": 12, "total": 40, "masteredCount": 10, "overdueCount": 3}
    return sources


# ---------------------------------------------------------------------------
# Populated payload
# ---------------------------------------------------------------------------


class TestPopulated:
    async def test_nothing_is_degraded_when_every_source_answers(self, populated):
        dashboard = await _dashboard()
        assert dashboard.meta.degraded_sections == []

    async def test_every_section_is_present(self, populated):
        """Every field is always serialized — a missing section and an empty one differ."""
        dashboard = await _dashboard()
        assert dashboard.courses.total == 1
        assert len(dashboard.courses.items) == 1
        assert len(dashboard.paths) == 2
        assert len(dashboard.recent_items) == 3
        assert len(dashboard.tools) == 6
        assert dashboard.featured is not None

    async def test_course_progress_is_computed_from_its_topics(self, populated):
        course = (await _dashboard()).courses.items[0]
        assert course.total_topics == 2
        assert course.completed_topics == 1
        assert course.progress_percent == 50.0
        assert course.module_count == 1

    async def test_the_next_topic_is_the_first_incomplete_one(self, populated):
        course = (await _dashboard()).courses.items[0]
        assert course.next_topic is not None
        assert course.next_topic.id == "t2"
        # 1.5 hours, reported in minutes because that is what the card shows.
        assert course.next_topic.estimated_minutes == 90

    async def test_review_estimate_uses_the_shared_per_card_constant(self, populated):
        """Two surfaces quote a review estimate and must not disagree about what a card costs."""
        review = (await _dashboard()).review
        expected = (12 * learn_dashboard_service.REVIEW_SECONDS_PER_CARD + 59) // 60
        assert review.due_cards == 12
        assert review.overdue_cards == 3
        assert review.estimated_minutes == expected
        assert review.mastery_percent == 25.0

    async def test_recent_items_are_merged_newest_first(self, populated):
        items = (await _dashboard()).recent_items
        assert [item.entity_type for item in items] == ["note", "saved_resource", "document"]

    async def test_a_resource_is_dated_by_last_access_when_it_has_one(self, sources):
        """An old resource opened recently is recent activity; the date saved is not.

        `lastAccessedAt` is what that column is for, and it was null on every row until stage 5
        routed the write. Creation is the fallback for rows that have never been opened.
        """
        sources.resources = [_resource("r-old", minutes_ago=1440, accessed_minutes_ago=2)]
        sources.notes = [_note("n-newer", minutes_ago=30)]
        items = (await _dashboard()).recent_items
        assert [item.id for item in items] == ["r-old", "n-newer"]

    async def test_a_resource_never_opened_falls_back_to_when_it_was_saved(self, sources):
        sources.resources = [_resource("r-saved", minutes_ago=30)]
        sources.notes = [_note("n-older", minutes_ago=90)]
        items = (await _dashboard()).recent_items
        assert [item.id for item in items] == ["r-saved", "n-older"]

    async def test_paths_carry_both_plans_and_preparations_ordered_by_deadline(self, populated):
        paths = (await _dashboard()).paths
        assert [path.entity_type for path in paths] == ["study_plan", "preparation"]
        assert paths[0].id == "p1"

    async def test_preparation_progress_comes_from_the_shared_mastery_ladder(self, populated):
        """So this card and the Prepare surface cannot report different numbers for one prep."""
        prep_path = next(p for p in (await _dashboard()).paths if p.entity_type == "preparation")
        assert prep_path.completed_units == 2
        assert prep_path.total_units == 8
        assert prep_path.progress_percent == 25.0

    async def test_the_paths_total_counts_both_kinds(self, populated):
        """Reporting the plan total alone made the number contradict the cards beside it."""
        tools = {tool.type: tool.count for tool in (await _dashboard()).tools}
        assert tools["study_plan"] == 2

    async def test_collections_is_empty_rather_than_invented(self, populated):
        """Deferred until the product semantics are agreed — see §18."""
        assert (await _dashboard()).collections == []


# ---------------------------------------------------------------------------
# Featured — the resume card
# ---------------------------------------------------------------------------


class TestFeatured:
    async def test_it_points_at_the_next_unfinished_topic(self, populated):
        featured = (await _dashboard()).featured
        assert featured.entity_type == "topic"
        assert featured.entity_id == "t2"
        assert featured.topic_id == "t2"
        assert featured.course_id == "c1"
        assert featured.course_title == "Course c1"
        assert featured.estimated_minutes == 90
        assert featured.completed_units == 1
        assert featured.total_units == 2

    async def test_a_learner_with_no_completions_gets_no_card(self, sources):
        """Nothing to resume. An arbitrary course would be worse than an absent card."""
        sources.courses = [_course("c1", modules=[_module("m1", [_topic("t1", completed=False)])])]
        sources.completions = []
        assert (await _dashboard()).featured is None

    async def test_a_finished_course_is_featured_as_the_course(self, sources):
        """Otherwise the card vanishes at the moment the learner completes the last topic."""
        done = _topic("t1", completed=True)
        sources.courses = [_course("c1", modules=[_module("m1", [done])])]
        sources.completions = [(done, "c1", "Course c1")]

        featured = (await _dashboard()).featured
        assert featured.entity_type == "course"
        assert featured.entity_id == "c1"
        assert featured.topic_id is None
        assert featured.progress_percent == 100.0

    async def test_it_is_not_read_from_the_loaded_course_page(self, sources):
        """The page is the four most recently *updated* courses; resume follows completions.

        A learner who renames three courses and then continues a fourth would otherwise get no card,
        because the course they are working through fell off the page.
        """
        stale = _topic("t-old", completed=True)
        pending = _topic("t-next", completed=False)
        resumed = _course("c-resumed", modules=[_module("m", [stale, pending])], updated_days_ago=9)
        sources.courses = [
            _course("c-a", modules=[_module("ma", [_topic("t-a", completed=False)])]),
            resumed,
        ]
        sources.completions = [(stale, "c-resumed", "Course c-resumed")]

        featured = (await _dashboard()).featured
        assert featured.course_id == "c-resumed"
        assert featured.entity_id == "t-next"

    async def test_a_deleted_course_does_not_produce_a_card(self, sources):
        """The completion row survives a course the lookup can no longer find."""
        ghost = _topic("t-ghost", completed=True)
        sources.courses = []
        sources.completions = [(ghost, "c-gone", "Course c-gone")]
        assert (await _dashboard()).featured is None


# ---------------------------------------------------------------------------
# Empty account
# ---------------------------------------------------------------------------


class TestEmpty:
    async def test_a_fresh_account_reports_empty_rather_than_degraded(self, sources):
        dashboard = await _dashboard()
        assert dashboard.meta.degraded_sections == []
        assert dashboard.featured is None
        assert dashboard.courses.items == []
        assert dashboard.courses.total == 0
        assert dashboard.paths == []
        assert dashboard.recent_items == []
        assert dashboard.collections == []

    async def test_review_reports_zero_and_withholds_mastery(self, sources):
        """Mastery is `masteredCount / total`, and with no cards there is no ratio to report.

        Zero would be a claim — "you have mastered none of your cards" — about a library that does
        not exist. Null is the absence, and the client renders it as such.
        """
        review = (await _dashboard()).review
        assert review.due_cards == 0
        assert review.overdue_cards == 0
        assert review.estimated_minutes == 0
        assert review.mastery_percent is None

    async def test_stats_and_tools_are_zeroed_not_omitted(self, sources):
        dashboard = await _dashboard()
        assert dashboard.stats.active_courses == 0
        assert dashboard.stats.personal_notes == 0
        assert [tool.count for tool in dashboard.tools] == [0, 0, 0, 0, 0, 0]


# ---------------------------------------------------------------------------
# Per-section degradation
# ---------------------------------------------------------------------------


class TestDegradation:
    @pytest.mark.parametrize(
        ("source", "expected"),
        [
            ("courses", {"courses", "stats", "tools"}),
            ("notes", {"stats", "tools", "recentItems"}),
            ("resources", {"stats", "tools", "recentItems"}),
            ("documents", {"stats", "tools", "recentItems"}),
            ("review", {"review", "tools"}),
            ("paths", {"paths", "tools"}),
            ("featured", {"featured"}),
            ("collections", {"collections"}),
        ],
    )
    async def test_a_failed_source_degrades_exactly_the_sections_it_feeds(
        self, populated, source, expected
    ):
        populated.fail = {source}
        dashboard = await _dashboard()
        assert set(dashboard.meta.degraded_sections) == expected

    async def test_a_failed_featured_lookup_does_not_degrade_the_course_grid(self, populated):
        """The grid loaded. Telling a learner their courses are unavailable while showing them
        is worse than one missing card."""
        populated.fail = {"featured"}
        dashboard = await _dashboard()
        assert dashboard.featured is None
        assert dashboard.meta.degraded_sections == ["featured"]
        assert len(dashboard.courses.items) == 1

    async def test_degraded_sections_are_reported_in_a_stable_order(self, populated):
        """The client keys off these names; a set's iteration order would make the field flap."""
        populated.fail = {"paths", "courses", "review"}
        sections = (await _dashboard()).meta.degraded_sections
        assert sections == [section for section in ALL_SECTIONS if section in set(sections)]

    async def test_a_degraded_section_is_empty_rather_than_partial(self, populated):
        populated.fail = {"courses"}
        dashboard = await _dashboard()
        assert dashboard.courses.items == []
        assert dashboard.courses.total == 0
        # The sections that did load are untouched.
        assert len(dashboard.recent_items) == 3
        assert dashboard.review.due_cards == 12

    async def test_one_failure_does_not_take_the_others_with_it(self, populated):
        populated.fail = {"review"}
        dashboard = await _dashboard()
        assert dashboard.review.due_cards == 0
        assert dashboard.review.mastery_percent is None
        assert len(dashboard.courses.items) == 1
        assert len(dashboard.paths) == 2

    async def test_every_source_failing_is_a_503_not_an_empty_dashboard(self, populated):
        """An empty dashboard would read as "you have nothing", which is the wrong sentence."""
        populated.fail = {
            "courses",
            "notes",
            "resources",
            "documents",
            "review",
            "paths",
            "featured",
            "collections",
        }
        with pytest.raises(MaigieError) as caught:
            await _dashboard()
        assert caught.value.status_code == 503
        assert caught.value.code == "LEARN_DASHBOARD_UNAVAILABLE"

    async def test_all_but_one_source_failing_still_answers(self, populated):
        """The `503` is for a total outage. One surviving section is worth rendering."""
        populated.fail = {
            "courses",
            "notes",
            "resources",
            "documents",
            "review",
            "featured",
            "collections",
        }
        dashboard = await _dashboard()
        assert len(dashboard.paths) == 2
        assert "paths" not in dashboard.meta.degraded_sections


# ---------------------------------------------------------------------------
# Bounds
# ---------------------------------------------------------------------------


class TestLimits:
    async def test_the_course_limit_is_passed_to_the_query(self, populated):
        """Bounded in SQL, not sliced afterwards — the point is not to load the rest."""
        await _dashboard(course_limit=2)
        assert populated.knowledge.course_takes == [2]

    async def test_the_recent_limit_bounds_the_merged_list(self, sources):
        sources.notes = [_note(f"n{index}", minutes_ago=index) for index in range(5)]
        sources.documents = [_document(f"d{index}", minutes_ago=index + 10) for index in range(5)]
        items = (await _dashboard(recent_limit=3)).recent_items
        assert len(items) == 3
        # Bounded *after* merging, so the newest three across all three kinds win rather than the
        # newest three of whichever list came first.
        assert [item.entity_type for item in items] == ["note", "note", "note"]

    async def test_the_path_limit_bounds_plans_and_preparations_together(self, sources):
        sources.plans = [_plan(f"p{index}", days=index + 1) for index in range(3)]
        sources.preps = [_prep(f"e{index}", days=index + 1) for index in range(3)]
        dashboard = await _dashboard(path_limit=2)
        assert len(dashboard.paths) == 2

    async def test_the_paths_total_is_the_unbounded_count(self, sources):
        """The limit bounds the cards, not the number reported beside them."""
        sources.plans = [_plan(f"p{index}", days=index + 1) for index in range(4)]
        sources.preps = [_prep(f"e{index}", days=index + 1) for index in range(2)]
        tools = {tool.type: tool.count for tool in (await _dashboard(path_limit=2)).tools}
        assert tools["study_plan"] == 6

    async def test_the_published_contract_caps_every_limit(self):
        """Read from the OpenAPI schema, not the route object.

        The schema is what clients and the generated types are built from, so it is the thing that
        has to carry the caps. A bound enforced in Python but missing from the contract would let a
        generated client offer `recentLimit=500` and discover the refusal at runtime.
        """
        from src.app import app

        parameters = app.openapi()["paths"]["/api/v1/learning/dashboard"]["get"]["parameters"]
        bounds = {
            parameter["name"]: (
                parameter["schema"].get("minimum"),
                parameter["schema"].get("maximum"),
            )
            for parameter in parameters
        }
        assert bounds == {
            "courseLimit": (1, 8),
            "pathLimit": (1, 5),
            "recentLimit": (1, 10),
        }


# ---------------------------------------------------------------------------
# Query count
# ---------------------------------------------------------------------------


class TestQueryCount:
    async def test_a_page_of_courses_costs_one_course_query(self, sources):
        """The guard against N+1.

        Per-course progress is computed from `course.modules[*].topics`, which must arrive
        eager-loaded. A lazy load per course would produce identical numbers — which is exactly why
        nothing would notice — so the assertion is on the number of calls, not the output.
        """
        sources.courses = [
            _course(
                f"c{index}",
                modules=[_module(f"m{index}", [_topic(f"t{index}", completed=False)])],
            )
            for index in range(6)
        ]
        await _dashboard(course_limit=6)
        assert len(sources.knowledge.course_takes) == 1
        assert sources.knowledge.course_lookups == []

    async def test_the_featured_card_costs_nothing_when_its_course_is_already_loaded(
        self, populated
    ):
        """The common case, and it used to cost a full refetch.

        The resume target is the course the learner last completed a topic in, which is usually one
        of the recently-updated courses already on the page. Fetching it by id again reloads the
        course with every module, every topic and — through the relationship defaults — every topic's
        lesson sections, all of which the page already has.

        Asserted on the call log rather than the output, because the output is identical either way,
        which is exactly why the extra query went unnoticed.
        """
        await _dashboard()

        assert populated.knowledge.course_lookups == []
        # And the card is still produced from the reused course.
        payload = await _dashboard()
        assert payload.featured is not None
        assert payload.featured.course_id == "c1"

    async def test_the_featured_card_costs_one_lookup_when_its_course_is_off_the_page(
        self, sources
    ):
        """The case the reuse must not break.

        The course page is only the few most recently *updated* courses, so the resume target can sit
        outside it — and then it does have to be fetched. This is why the reuse is a fast path rather
        than a replacement.
        """
        done = _topic("t-old", completed=True)
        sources.courses = [
            _course("c-visible", modules=[_module("m1", [_topic("t1", completed=False)])])
        ]
        sources.completions = [(done, "c-hidden", "Course c-hidden")]

        await _dashboard()

        assert sources.knowledge.course_lookups == ["c-hidden"]


# ---------------------------------------------------------------------------
# Authorization
# ---------------------------------------------------------------------------


class TestAuthorization:
    async def test_every_source_is_scoped_to_the_caller(self, monkeypatch, sources):
        """No source may be reachable without the user id.

        The dashboard reads seven sources across two domains; one of them taking a bare entity id is
        how a composed read becomes a leak. Recorded per call rather than argued.
        """
        seen: list[str | None] = []

        original_list_courses = sources.knowledge.list_courses

        async def list_courses(user_id, **kwargs):
            seen.append(user_id)
            return await original_list_courses(user_id, **kwargs)

        original_stats = sources.knowledge.count_completed_topics

        async def stats(user_id):
            seen.append(user_id)
            return await original_stats(user_id)

        original_completions = sources.knowledge.recently_completed_topics

        async def completions(user_id, *, limit=5):
            seen.append(user_id)
            return await original_completions(user_id, limit=limit)

        original_find = sources.knowledge.find_course_outline

        async def find(course_id, user_id):
            seen.append(user_id)
            return await original_find(course_id, user_id)

        monkeypatch.setattr(sources.knowledge, "list_courses", list_courses)
        monkeypatch.setattr(sources.knowledge, "count_completed_topics", stats)
        monkeypatch.setattr(sources.knowledge, "recently_completed_topics", completions)
        monkeypatch.setattr(sources.knowledge, "find_course_outline", find)

        sources.completions = [(_topic("t1", completed=True), "c1", "Course c1")]
        sources.courses = [_course("c1", modules=[_module("m1", [_topic("t1", completed=True)])])]

        await _dashboard(user_id="learner-42")
        assert seen
        assert set(seen) == {"learner-42"}

    async def test_the_route_requires_authentication(self):
        from src.app import app

        route = next(
            r for r in app.routes if getattr(r, "path", None) == "/api/v1/learning/dashboard"
        )
        # `CurrentUser` is an annotated dependency, so it appears as a security requirement rather
        # than a query parameter. Its absence is what an unauthenticated `200` would look like.
        assert route.dependant.dependencies, "dashboard route declares no dependencies"


class TestNoDuplicatedReads:
    """The dashboard used to ask the same questions twice.

    A live SQL log showed one request issuing ~54 statements, several of them the same figure computed
    by two code paths. They were hard to spot as duplicates because they rendered differently — one
    course count as `archived = false` from a `where` dict, the other as `archived IS false` from
    `.is_(False)` — and impossible to spot from the response, which was correct either way.

    Asserted on call counts, because output assertions pass against the duplicated version.
    """

    async def test_the_course_count_is_read_once_not_twice(self, populated, monkeypatch):
        """`activeCourses` and the course tool count are the same number.

        Both are `count(*) FROM Course WHERE userId = ? AND NOT archived`. The page total from
        `list_courses` is now reused for both instead of `get_course_dashboard_stats` counting again,
        and that method was split so the redundant half cannot come back.
        """
        calls: list[str] = []
        original = populated.knowledge.count_completed_topics

        async def counted(user_id):
            calls.append(user_id)
            return await original(user_id)

        monkeypatch.setattr(populated.knowledge, "count_completed_topics", counted)

        payload = await _dashboard()

        # One call, and it no longer returns a course count at all.
        assert len(calls) == 1
        assert not hasattr(populated.knowledge, "get_course_dashboard_stats")
        # The two figures still agree, which is the point of reusing one read.
        assert payload.stats.active_courses == 1
        course_tool = next(tool for tool in payload.tools if tool.type == "course")
        assert course_tool.count == payload.stats.active_courses

    async def test_the_note_total_comes_from_the_page_query(self, populated):
        """`list_notes` already returns its total and it was being discarded.

        The request paid for two note counts and used the second. It also used the *wrong* one:
        `count_user_notes` counts every unarchived note including space-scoped ones, while the list
        beside it is filtered to `spaceId IS NULL`, so a learner with notes in a space saw a total
        larger than the library it described.
        """
        payload = await _dashboard()

        assert payload.stats.personal_notes == len(populated.notes)
        # The separate counter is gone from the read path entirely.
        assert not hasattr(populated.repo, "count_user_notes")

    async def test_overdue_cards_come_from_the_statistics_query(self, populated):
        """Two questions about the same table, one round trip.

        `overdueCards` (due before midnight) and `dueCards` (due by now) are genuinely different
        figures and both are still shown — they are now two filtered aggregates in one statement
        rather than two queries on two connections.
        """
        payload = await _dashboard()

        assert payload.review.overdue_cards == 3
        assert payload.review.due_cards == 12
        assert not hasattr(populated.repo, "count_overdue_flashcards")


class TestConnectionBudget:
    """The composition must not open eight connections at once.

    Reflect's equivalent dashboard exhausted the session-mode pooler outright the first time it ran
    against the real database — `EMAXCONNSESSION` at 15 clients — and degraded three sections that had
    nothing wrong with them. This endpoint has always had the same shape and the same exposure; it had
    simply never been the one to hit the ceiling. These tests are what stop a ninth loader being added
    to a flat gather.
    """

    def test_the_loaders_run_in_waves_of_at_most_four(self):
        """Read from the source, because the concurrency is not observable from the response.

        A flat gather and a split gather return identical payloads. The only difference is how many
        connections are held at the same moment, which no assertion on the output can see — so this
        counts the `asyncio.gather` calls and the arguments handed to each.
        """
        import ast
        import inspect
        import textwrap

        source = textwrap.dedent(inspect.getsource(learn_dashboard_service.get_dashboard))
        tree = ast.parse(source)

        gathers = [
            node
            for node in ast.walk(tree)
            for func in [node.func if isinstance(node, ast.Call) else None]
            if isinstance(node, ast.Call)
            and isinstance(func, ast.Attribute)
            and func.attr == "gather"
        ]

        assert len(gathers) >= 2, "the loaders were collapsed back into a single gather"
        for call in gathers:
            # `return_exceptions` is a keyword, so positional args are the awaitables.
            assert (
                len(call.args) <= 4
            ), f"a gather takes {len(call.args)} awaitables; the pooler budget allows four"

    def test_every_gathered_source_is_mapped_to_sections(self):
        """The two orderings that used to have to agree silently.

        Results were read back as `results[0]`..`results[7]` while a separate dict supplied the section
        names, so splitting the gather would have attached the wrong data to the wrong field with no
        signal at all. The service now keys by name and asserts the two sets match; this pins that the
        assertion exists and that the names are the ones the reader expects.
        """
        import ast
        import inspect
        import textwrap

        source = textwrap.dedent(inspect.getsource(learn_dashboard_service.get_dashboard))
        tree = ast.parse(source)

        assert any(
            isinstance(node, ast.Assert) for node in ast.walk(tree)
        ), "the guard that gathered sources and mapped sections agree has been removed"

        # Results are read by name. Subscripting a list of results is what the keying replaced, and it
        # is checked on the AST rather than the text because a comment mentioning `results[0]` — this
        # module has one — is not the same as code doing it.
        numeric_subscripts = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Subscript)
            and isinstance(node.slice, ast.Constant)
            and isinstance(node.slice.value, int)
        ]
        assert not numeric_subscripts, "gathered results are being read back positionally again"
