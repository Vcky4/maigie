"""Tests for the Prepare dashboard read-model.

This service composes six sections from five sources and had **no test of any
kind** — nothing in the suite referenced it. It is the one endpoint on the surface
where a single slow or failing query decides what a learner sees, so the part
worth guarding is not the happy path but the degradation policy: a section that
cannot be loaded must be *reported* degraded and rendered unavailable, never
rendered as empty. "You have no preparations" and "we could not load your
preparations" are different sentences, and only one of them is ever true.

No database. Every source is replaced with a fake or made to raise, which is the
only practical way to assert what happens when one of five concurrent queries
fails — `asyncio.gather(..., return_exceptions=True)` means the failure is a value,
and values are what tests are good at.
"""

import os

os.environ.setdefault("SKIP_DB_FIXTURE", "1")

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from src.domains.personal_learning.services import (
    prep_readiness,
    prepare_dashboard_service,
)
from src.shared.exceptions import MaigieError

OWNER = "user-owner"
NOW = datetime.now(UTC)


def _prep(prep_id: str, *, subject: str, days: int, status: str = "IN_PROGRESS", target=85):
    return SimpleNamespace(
        id=prep_id,
        user_id=OWNER,
        subject=subject,
        description=f"About {subject}",
        status=status,
        prep_type="EXAM",
        exam_date=NOW + timedelta(days=days),
        target_readiness=target,
    )


def _topic(topic_id: str, prep_id: str, *, mastery: float, order: int = 0):
    return SimpleNamespace(
        id=topic_id,
        prep_id=prep_id,
        title=f"Topic {topic_id}",
        description=None,
        category="Foundations",
        mastery_score=mastery,
        target_mastery=None,
        order_index=order,
        estimated_minutes=30,
        status="IN_PROGRESS",
    )


def _progress(**overrides):
    defaults = {
        "topics_total": 10,
        "topics_strong": 4,
        "topics_focus": 3,
        "topics_assessed": 6,
        "progress_percent": 40.0,
        "average_mastery_percent": 70.0,
        "questions_answered": 20,
        "questions_correct": 15,
        "accuracy_percent": 75.0,
        "quizzes_taken": 3,
        "practice_seconds": 900,
        "practice_minutes": 15,
        "practice_ready": True,
    }
    return SimpleNamespace(**{**defaults, **overrides})


def _session(session_id: str, prep_id: str):
    return SimpleNamespace(
        id=session_id,
        prep_id=prep_id,
        mode="QUICK_REVIEW",
        status="COMPLETED",
        total_questions=10,
        correct_count=7,
        score_percentage=70.0,
        duration_seconds=300,
        completed_at=NOW,
        created_at=NOW - timedelta(hours=1),
    )


def _milestone(item_id: str, *, days: int, status: str = "PENDING", completed_at=None):
    return SimpleNamespace(
        id=item_id,
        title=f"Study {item_id}",
        description="Detail",
        scheduled_date=NOW + timedelta(days=days),
        status=status,
        estimated_minutes=30,
        prep_topic_id="t-1",
        completed_at=completed_at,
    )


class FakeSources:
    """Every source the dashboard reads, each independently switchable to raise.

    `fail` names the sources that should blow up, so a test says which of the five
    concurrent queries failed rather than constructing five separate fakes.
    """

    def __init__(self):
        self.in_progress: list[SimpleNamespace] = []
        self.setup: list[SimpleNamespace] = []
        self.awaiting_review: list[SimpleNamespace] = []
        self.topics: list[SimpleNamespace] = []
        self.counts: dict[str, dict[str, int]] = {}
        self.sessions: list[SimpleNamespace] = []
        self.milestones: list[tuple[SimpleNamespace, str]] = []
        self.progress: dict[str, SimpleNamespace] = {}
        self.streak = 4
        self.fail: set[str] = set()
        self.topic_takes: list[int] = []

    def _boom(self, source: str):
        if source in self.fail:
            raise RuntimeError(f"{source} unavailable")

    async def search_exam_preps(self, user_id, *, status=None, skip=0, take=10, **kwargs):
        self._boom("preparations")
        # Keyed by status rather than an if/else, so a status the service starts querying and this fake
        # does not know about returns nothing instead of silently returning the `SETUP` list.
        rows = {
            "IN_PROGRESS": self.in_progress,
            "SETUP": self.setup,
            "AWAITING_REVIEW": self.awaiting_review,
        }.get(status, [])
        return rows[:take], len(rows)

    async def list_recent_quiz_sessions(self, user_id, *, take=6):
        self._boom("sessions")
        return self.sessions[:take]

    async def list_weakest_prep_topics(self, prep_ids, *, take=8):
        # Recorded so a test can assert the recommendation window is loaded
        # separately from, and wider than, the displayed focus list.
        self.topic_takes.append(take)
        if take <= 20:
            self._boom("focusTopics")
        else:
            self._boom("recommendations")
        return self.topics[:take]

    async def get_prep_topic_question_counts(self, prep_ids):
        self._boom("topicCounts")
        return self.counts

    async def list_prep_milestone_items(self, prep_ids, user_id, *, take=6):
        self._boom("milestones")
        return self.milestones[:take]


@pytest.fixture
def sources(monkeypatch):
    fake = FakeSources()
    monkeypatch.setattr(prepare_dashboard_service, "repo", fake)

    async def load_for_preparations(prep_ids):
        fake._boom("progress")
        return {pid: fake.progress[pid] for pid in prep_ids if pid in fake.progress}

    async def load_practice_streak(user_id):
        fake._boom("practiceStreak")
        return fake.streak

    monkeypatch.setattr(prep_readiness, "load_for_preparations", load_for_preparations)
    monkeypatch.setattr(prep_readiness, "load_practice_streak", load_practice_streak)
    return fake


async def _dashboard(**overrides):
    kwargs = {
        "user_id": OWNER,
        "preparation_limit": 6,
        "topic_limit": 8,
        "session_limit": 6,
        "milestone_limit": 6,
    }
    kwargs.update(overrides)
    return await prepare_dashboard_service.get_dashboard(**kwargs)


class TestComposition:
    @pytest.mark.asyncio
    async def test_sections_are_composed_from_their_own_sources(self, sources):
        sources.in_progress = [_prep("p-1", subject="Statistics", days=11)]
        sources.progress = {"p-1": _progress()}
        sources.topics = [_topic("t-weak", "p-1", mastery=58.0)]
        sources.counts = {"t-weak": {"question_count": 36, "answered_count": 21}}
        sources.sessions = [_session("q-1", "p-1")]
        sources.milestones = [(_milestone("m-1", days=1), "p-1")]

        dashboard = await _dashboard()

        assert dashboard.meta.degraded_sections == []
        assert [p.id for p in dashboard.preparations] == ["p-1"]
        assert [t.id for t in dashboard.focus_topics] == ["t-weak"]
        assert [s.id for s in dashboard.recent_sessions] == ["q-1"]
        assert [m.id for m in dashboard.milestones] == ["m-1"]
        assert dashboard.summary.practice_streak == 4

    @pytest.mark.asyncio
    async def test_the_subject_is_joined_onto_every_section(self, sources):
        """Each section carries its preparation's subject so nothing needs a
        second request to label a row."""
        sources.in_progress = [_prep("p-1", subject="Statistics", days=11)]
        sources.progress = {"p-1": _progress()}
        sources.topics = [_topic("t-1", "p-1", mastery=58.0)]
        sources.sessions = [_session("q-1", "p-1")]
        sources.milestones = [(_milestone("m-1", days=1), "p-1")]

        dashboard = await _dashboard()

        assert dashboard.focus_topics[0].preparation_subject == "Statistics"
        assert dashboard.recent_sessions[0].preparation_subject == "Statistics"
        assert dashboard.milestones[0].preparation_subject == "Statistics"

    @pytest.mark.asyncio
    async def test_every_unfinished_status_is_active_and_ordered_by_date(self, sources):
        """One list from every status that still wants something, in exam-date order.

        A preparation in `SETUP` is active — the learner created it and it is what they are working
        towards. So is one in `AWAITING_REVIEW`: its exam has happened and it is waiting on them to say
        how it went, which is the answer that completes it.
        """
        sources.in_progress = [_prep("p-late", subject="Later", days=30)]
        sources.setup = [_prep("p-soon", subject="Sooner", days=2, status="SETUP")]
        sources.awaiting_review = [
            _prep("p-past", subject="Sat last week", days=-7, status="AWAITING_REVIEW")
        ]
        sources.progress = {
            "p-late": _progress(),
            "p-soon": _progress(),
            "p-past": _progress(),
        }

        dashboard = await _dashboard()

        assert [p.id for p in dashboard.preparations] == ["p-past", "p-soon", "p-late"]
        # The total counts all three, not just the page that fitted.
        assert dashboard.preparations_total == 3

    @pytest.mark.asyncio
    async def test_a_preparation_awaiting_review_is_not_hidden(self, sources):
        """The case that would make the review unanswerable.

        `AWAITING_REVIEW` was added to `ExamPrep.status` so that a passed exam stops being recorded as
        `COMPLETED` by a nightly clock. If the dashboard kept querying only `SETUP` and `IN_PROGRESS`, the
        preparation would vanish the morning after the exam and the review would be reachable only from a
        notification — which quiet hours and the daily cap can both suppress. Asserted on its own rather
        than only inside the ordering test, so a regression names the reason.
        """
        sources.awaiting_review = [
            _prep("p-past", subject="Sat already", days=-3, status="AWAITING_REVIEW")
        ]
        sources.progress = {"p-past": _progress()}

        dashboard = await _dashboard()

        assert [p.id for p in dashboard.preparations] == ["p-past"]

    @pytest.mark.asyncio
    async def test_only_unfinished_statuses_are_queried(self, sources):
        """`COMPLETED` is history and must not be asked for.

        Asserted on the queries actually issued rather than on the constant, so the test fails if the
        constant stops being what the queries use. **It did exactly that**: `ACTIVE_STATUSES` existed and
        `_load_active_preparations` hard-coded its two values instead of reading it, so adding a third
        status changed the constant while the queries stayed as they were.
        """
        queried: list[str | None] = []
        original = sources.search_exam_preps

        async def recording(user_id, *, status=None, skip=0, take=10, **kwargs):
            queried.append(status)
            return await original(user_id, status=status, skip=skip, take=take, **kwargs)

        sources.search_exam_preps = recording

        await _dashboard()

        assert sorted(queried) == ["AWAITING_REVIEW", "IN_PROGRESS", "SETUP"]
        assert set(queried) == set(prepare_dashboard_service.ACTIVE_STATUSES)
        assert "COMPLETED" not in queried

    @pytest.mark.asyncio
    async def test_a_preparation_without_progress_is_omitted_not_zeroed(self, sources):
        """A card cannot be rendered without its numbers.

        Showing it at 0% would report a learner as unprepared when the truth is
        that the aggregate did not load for them.
        """
        sources.in_progress = [
            _prep("p-1", subject="Has progress", days=11),
            _prep("p-2", subject="No progress", days=12),
        ]
        sources.progress = {"p-1": _progress()}

        dashboard = await _dashboard()

        assert [p.id for p in dashboard.preparations] == ["p-1"]
        # Still counted as active, because it is. Only its card is missing.
        assert dashboard.preparations_total == 2

    @pytest.mark.asyncio
    async def test_a_passed_target_date_reports_no_days_remaining(self, sources):
        sources.in_progress = [_prep("p-1", subject="Passed", days=-3)]
        sources.progress = {"p-1": _progress()}

        dashboard = await _dashboard()

        assert dashboard.preparations[0].days_until_exam is None


class TestSummaryArithmetic:
    @pytest.mark.asyncio
    async def test_totals_are_summed_across_preparations(self, sources):
        sources.in_progress = [
            _prep("p-1", subject="One", days=5),
            _prep("p-2", subject="Two", days=6),
        ]
        sources.progress = {
            "p-1": _progress(questions_answered=20, questions_correct=15, practice_seconds=900),
            "p-2": _progress(questions_answered=30, questions_correct=15, practice_seconds=1800),
        }

        dashboard = await _dashboard()

        assert dashboard.summary.questions_answered == 50
        # 30 of 50, not the mean of 75% and 50%.
        assert dashboard.summary.accuracy_percent == 60.0
        assert dashboard.summary.practice_minutes == 45

    @pytest.mark.asyncio
    async def test_accuracy_with_nothing_answered_is_none_not_zero(self, sources):
        """Zero would say every answer was wrong. None says none were given."""
        sources.in_progress = [_prep("p-1", subject="Fresh", days=5)]
        sources.progress = {"p-1": _progress(questions_answered=0, questions_correct=0)}

        dashboard = await _dashboard()

        assert dashboard.summary.accuracy_percent is None
        assert dashboard.summary.questions_answered == 0

    @pytest.mark.asyncio
    async def test_active_count_is_the_total_not_the_page(self, sources):
        sources.in_progress = [_prep(f"p-{i}", subject=f"S{i}", days=i + 1) for i in range(9)]
        sources.progress = {f"p-{i}": _progress() for i in range(9)}

        dashboard = await _dashboard(preparation_limit=2)

        assert len(dashboard.preparations) == 2
        assert dashboard.summary.active_preparations == 9


class TestDegradation:
    """The policy: report the section, do not render it as empty."""

    @pytest.mark.asyncio
    async def test_a_failed_section_is_named_and_the_rest_still_render(self, sources):
        sources.in_progress = [_prep("p-1", subject="Statistics", days=11)]
        sources.progress = {"p-1": _progress()}
        sources.sessions = [_session("q-1", "p-1")]
        sources.fail = {"milestones"}

        dashboard = await _dashboard()

        assert "milestones" in dashboard.meta.degraded_sections
        assert dashboard.milestones == []
        # The failure is contained: everything else is intact.
        assert [p.id for p in dashboard.preparations] == ["p-1"]
        assert [s.id for s in dashboard.recent_sessions] == ["q-1"]

    @pytest.mark.asyncio
    async def test_losing_preparations_degrades_everything_derived_from_them(self, sources):
        sources.sessions = [_session("q-1", "p-1")]
        sources.fail = {"preparations"}

        dashboard = await _dashboard()

        assert set(dashboard.meta.degraded_sections) >= {
            "preparations",
            "summary",
            "focusTopics",
        }
        assert dashboard.preparations == []
        # Sessions come from their own query and survive.
        assert [s.id for s in dashboard.recent_sessions] == ["q-1"]

    @pytest.mark.asyncio
    async def test_losing_both_primary_sources_is_an_error_not_an_empty_page(self, sources):
        """With neither preparations nor sessions there is no dashboard to show,
        and a 200 holding nothing would be indistinguishable from a new account."""
        sources.fail = {"preparations", "sessions"}

        with pytest.raises(MaigieError) as excinfo:
            await _dashboard()

        assert excinfo.value.code == "PREPARE_DASHBOARD_UNAVAILABLE"
        assert excinfo.value.status_code == 503

    @pytest.mark.asyncio
    async def test_a_failed_streak_leaves_it_unknown_rather_than_zero(self, sources):
        """Reporting 0 would tell a learner their streak had broken."""
        sources.in_progress = [_prep("p-1", subject="Statistics", days=11)]
        sources.progress = {"p-1": _progress()}
        sources.fail = {"practiceStreak"}

        dashboard = await _dashboard()

        assert dashboard.summary.practice_streak is None
        assert "summary" in dashboard.meta.degraded_sections

    @pytest.mark.asyncio
    async def test_a_zero_streak_is_reported_as_zero(self, sources):
        """The counterpart: `None` must mean unknown, so 0 has to survive."""
        sources.in_progress = [_prep("p-1", subject="Statistics", days=11)]
        sources.progress = {"p-1": _progress()}
        sources.streak = 0

        dashboard = await _dashboard()

        assert dashboard.summary.practice_streak == 0
        assert "summary" not in dashboard.meta.degraded_sections

    @pytest.mark.asyncio
    async def test_failed_progress_degrades_the_cards_and_the_summary(self, sources):
        sources.in_progress = [_prep("p-1", subject="Statistics", days=11)]
        sources.fail = {"progress"}

        dashboard = await _dashboard()

        assert set(dashboard.meta.degraded_sections) >= {"preparations", "summary"}
        assert dashboard.preparations == []

    @pytest.mark.asyncio
    async def test_losing_recommendations_keeps_the_cards(self, sources):
        """A card without a next action is still worth rendering."""
        sources.in_progress = [_prep("p-1", subject="Statistics", days=11)]
        sources.progress = {"p-1": _progress()}
        sources.fail = {"recommendations"}

        dashboard = await _dashboard()

        assert "preparations" in dashboard.meta.degraded_sections
        # Degraded, but present — the numbers came from a query that succeeded.
        assert [p.id for p in dashboard.preparations] == ["p-1"]

    @pytest.mark.asyncio
    async def test_failed_topic_counts_degrade_nothing(self, sources):
        """Counts are an annotation. Zero of zero answered is not a false claim,
        so this is the one source whose failure is logged and not reported."""
        sources.in_progress = [_prep("p-1", subject="Statistics", days=11)]
        sources.progress = {"p-1": _progress()}
        sources.topics = [_topic("t-1", "p-1", mastery=58.0)]
        sources.fail = {"topicCounts"}

        dashboard = await _dashboard()

        assert dashboard.meta.degraded_sections == []
        assert dashboard.focus_topics[0].question_count == 0

    @pytest.mark.asyncio
    async def test_degraded_sections_are_ordered_and_deduplicated(self, sources):
        """The list is built from a set, so without an explicit order it would
        vary between identical requests."""
        sources.fail = {"preparations", "milestones"}
        sources.sessions = [_session("q-1", "p-1")]

        dashboard = await _dashboard()

        # Exact, and in the declared section order. `preparations` contributes
        # three names and would otherwise arrive in whatever order the set
        # iterated, so two identical requests could disagree.
        assert dashboard.meta.degraded_sections == ["summary", "preparations", "focusTopics"]

    @pytest.mark.asyncio
    async def test_no_preparations_is_not_degraded(self, sources):
        """An empty dashboard for a new learner is a correct answer, and must be
        distinguishable from a broken one."""
        dashboard = await _dashboard()

        assert dashboard.meta.degraded_sections == []
        assert dashboard.preparations == []
        assert dashboard.summary.active_preparations == 0


class TestLimits:
    @pytest.mark.asyncio
    async def test_each_limit_bounds_its_own_section(self, sources):
        sources.in_progress = [_prep(f"p-{i}", subject=f"S{i}", days=i + 1) for i in range(5)]
        sources.progress = {f"p-{i}": _progress() for i in range(5)}
        sources.topics = [_topic(f"t-{i}", "p-0", mastery=50.0 + i) for i in range(10)]
        sources.sessions = [_session(f"q-{i}", "p-0") for i in range(10)]
        sources.milestones = [(_milestone(f"m-{i}", days=i), "p-0") for i in range(10)]

        dashboard = await _dashboard(
            preparation_limit=2, topic_limit=3, session_limit=4, milestone_limit=1
        )

        assert len(dashboard.preparations) == 2
        assert len(dashboard.focus_topics) == 3
        assert len(dashboard.recent_sessions) == 4
        assert len(dashboard.milestones) == 1

    @pytest.mark.asyncio
    async def test_recommendations_load_a_wider_window_than_the_displayed_list(self, sources):
        """Two reads of the same query, deliberately.

        `focusTopics` is bounded across *all* preparations, so a preparation can be
        absent from it entirely. Recommending per preparation from that list would
        leave those preparations with no next action at all, so the recommendation
        pass loads its own, much wider window.
        """
        sources.in_progress = [_prep("p-1", subject="Statistics", days=11)]
        sources.progress = {"p-1": _progress()}

        await _dashboard(topic_limit=8)

        assert 8 in sources.topic_takes
        assert prepare_dashboard_service._MAX_TOPICS_FOR_RECOMMENDATION in sources.topic_takes


class TestMilestoneStatus:
    """Derived from today's date, never stored — a stored value is wrong tomorrow."""

    def test_completed_status_wins(self):
        item = _milestone("m", days=-5, status="COMPLETED")
        assert prepare_dashboard_service._milestone_status(item, NOW) == "COMPLETE"

    def test_a_completion_timestamp_counts_even_without_the_status(self):
        item = _milestone("m", days=-5, completed_at=NOW)
        assert prepare_dashboard_service._milestone_status(item, NOW) == "COMPLETE"

    def test_a_past_pending_item_is_overdue(self):
        item = _milestone("m", days=-1)
        assert prepare_dashboard_service._milestone_status(item, NOW) == "OVERDUE"

    def test_today_is_its_own_state(self):
        item = _milestone("m", days=0)
        assert prepare_dashboard_service._milestone_status(item, NOW) == "TODAY"

    def test_a_future_item_is_upcoming(self):
        item = _milestone("m", days=3)
        assert prepare_dashboard_service._milestone_status(item, NOW) == "UPCOMING"

    def test_a_naive_scheduled_date_does_not_raise(self):
        """Some rows are naive. Comparing them to an aware `now` is a `TypeError`,
        which would be a 500 on the whole dashboard for one bad row."""
        item = SimpleNamespace(
            id="m",
            title="t",
            description=None,
            scheduled_date=datetime(2020, 1, 1),
            status="PENDING",
            estimated_minutes=30,
            prep_topic_id=None,
            completed_at=None,
        )
        assert prepare_dashboard_service._milestone_status(item, NOW) == "OVERDUE"
