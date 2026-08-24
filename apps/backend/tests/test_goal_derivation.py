"""Deriving a goal from intent the learner already stated.

A learner with a course, a preparation and a study plan but no goal was shown an empty goals
surface — 593 of them, against 1167 with no goal at all. Every goal that does exist live is
`metricKind='manual'`, so nothing in production currently measures itself.

The rules pinned here:

1. **Only measurable intent becomes a goal.** Prose (`purpose`, `goalsText`) could only produce a
   `manual` goal, whose number nothing moves. A goal that cannot move is not a goal.
2. **One goal per link, whoever made it.** A learner's own course goal is never given a twin.
3. **A target is used when stated and left empty when not** — never guessed.
4. **A passed exam date, or a finished course, earns nothing.**
5. **No recurring sweep**, because deleting a goal is a hard delete and a sweep would rebuild it.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.domains.progress.services import goal_derivation_service as derivation

NOW = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)


def code_only(func) -> str:
    """A function's source with its docstring and comments removed.

    Every guard that asserts "this name does not appear in this function" needs it, because the
    docstring and the comments are exactly where the avoided name gets *explained*. Five separate
    tests in this codebase have been caught by matching their own explanation.
    """
    import ast
    import inspect
    import textwrap

    tree = ast.parse(textwrap.dedent(inspect.getsource(func)))
    node = tree.body[0]
    body = getattr(node, "body", [])
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        node.body = body[1:]  # type: ignore[attr-defined]
    # `ast.unparse` drops comments for free, having never kept them.
    return ast.unparse(tree)


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeSession:
    """Returns the queued result sets in the order `plan_derivations` asks for them: goals, preps,
    courses."""

    def __init__(self, *result_sets):
        self._queued = list(result_sets)
        self.executed = 0

    async def execute(self, _stmt):
        rows = self._queued[self.executed] if self.executed < len(self._queued) else []
        self.executed += 1
        return _FakeResult(rows)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False


def _factory(session):
    def make():
        return session

    return make


async def _plan(*, goals=(), preps=(), courses=(), now=NOW):
    """`plan_derivations` against fixed rows.

    Row shapes match the selected columns: goals `(course_id, prep_id)`, preps
    `(id, subject, exam_date, target_readiness, prep_type)`, courses `(id, title, target_date,
    progress)`.
    """
    session = _FakeSession(list(goals), list(preps), list(courses))
    with patch.object(derivation, "get_session_factory", lambda: _factory(session)):
        return await derivation.plan_derivations("u1", now=now)


class TestACourseBecomesAGoal:
    async def test_a_course_with_no_goal_yields_one_that_measures_the_course(self):
        specs = await _plan(courses=[("c1", "Organic Chemistry", None, 12.0)])

        assert len(specs) == 1
        spec = specs[0]
        assert spec.metric_kind == "course_progress"
        assert spec.course_id == "c1"
        assert spec.prep_id is None
        assert spec.basis == derivation.BASIS_COURSE
        # 100 percent, because completing a course is what enrolling in one means.
        assert spec.target_value == 100.0
        assert "Organic Chemistry" in spec.title

    async def test_a_course_target_date_is_carried_across(self):
        due = datetime(2026, 9, 1, tzinfo=UTC)
        specs = await _plan(courses=[("c1", "Stats", due, 0.0)])
        assert specs[0].target_date == due

    async def test_a_finished_course_earns_nothing(self):
        """A goal to complete a completed course would open already satisfied."""
        assert await _plan(courses=[("c1", "Done", None, 100.0)]) == []

    async def test_the_metric_kind_is_one_the_measurement_layer_knows(self):
        """A kind `derive_current_values` does not handle would leave the goal permanently
        unmeasured, which is the thing this whole module exists to avoid."""
        from src.domains.progress.services import goal_metrics

        specs = await _plan(
            courses=[("c1", "C", None, 0.0)],
            preps=[("p1", "Maths", NOW + timedelta(days=30), None, "exam")],
        )
        for spec in specs:
            assert spec.metric_kind in goal_metrics.METRIC_KINDS
            assert spec.metric_kind != "manual"


class TestAPreparationBecomesAGoal:
    async def test_a_future_preparation_yields_a_readiness_goal(self):
        exam = NOW + timedelta(days=40)
        specs = await _plan(preps=[("p1", "MCAT", exam, None, "exam")])

        assert len(specs) == 1
        spec = specs[0]
        assert spec.metric_kind == "prep_readiness"
        assert spec.prep_id == "p1"
        assert spec.course_id is None
        assert spec.basis == derivation.BASIS_PREPARATION
        assert spec.target_date == exam
        assert "MCAT" in spec.title

    async def test_a_stated_target_readiness_is_used(self):
        specs = await _plan(preps=[("p1", "MCAT", NOW + timedelta(days=40), 85, "exam")])
        assert specs[0].target_value == 85.0

    async def test_an_unstated_target_readiness_is_left_empty_not_guessed(self):
        """The same choice migration 016 made for the prep workspace: readiness shows with no target
        line rather than a guessed one."""
        specs = await _plan(preps=[("p1", "MCAT", NOW + timedelta(days=40), None, "exam")])
        assert specs[0].target_value is None

    async def test_a_passed_exam_date_earns_nothing(self):
        assert await _plan(preps=[("p1", "Old", NOW - timedelta(days=1), None, "exam")]) == []

    async def test_a_naive_exam_date_is_read_as_utc_not_compared_raw(self):
        """`examDate` is one of the 176 columns stored without an offset. Comparing it raw against an
        aware instant raises `TypeError`."""
        naive = (NOW + timedelta(days=10)).replace(tzinfo=None)
        specs = await _plan(preps=[("p1", "Physics", naive, None, "exam")])

        assert len(specs) == 1
        assert specs[0].target_date is not None
        assert specs[0].target_date.tzinfo is not None

    def test_a_completed_preparation_is_not_worth_a_goal(self):
        assert "COMPLETED" not in derivation.PREP_STATUSES_WORTH_A_GOAL
        assert "IN_PROGRESS" in derivation.PREP_STATUSES_WORTH_A_GOAL
        assert "SETUP" in derivation.PREP_STATUSES_WORTH_A_GOAL


class TestNothingIsDerivedTwice:
    async def test_a_course_that_already_has_a_goal_is_left_alone(self):
        specs = await _plan(
            goals=[("c1", None)],
            courses=[("c1", "Organic Chemistry", None, 12.0)],
        )
        assert specs == []

    async def test_a_preparation_that_already_has_a_goal_is_left_alone(self):
        specs = await _plan(
            goals=[(None, "p1")],
            preps=[("p1", "MCAT", NOW + timedelta(days=40), None, "exam")],
        )
        assert specs == []

    async def test_a_goal_on_one_course_does_not_block_another(self):
        specs = await _plan(
            goals=[("c1", None)],
            courses=[("c1", "Covered", None, 0.0), ("c2", "Uncovered", None, 0.0)],
        )
        assert [spec.course_id for spec in specs] == ["c2"]

    async def test_a_goal_with_no_link_blocks_nothing(self):
        """The 42 live goals are all `manual` with no course or prep. They say nothing about which
        intent is covered."""
        specs = await _plan(goals=[(None, None)], courses=[("c1", "C", None, 0.0)])
        assert len(specs) == 1


class TestTheBacklogIsCapped:
    async def test_a_learner_with_many_courses_does_not_get_a_goal_for_each(self):
        """One live learner has sixteen unarchived courses, two of them duplicates. Sixteen goals is
        the course list again, on a surface meant for what they are working towards."""
        courses = [(f"c{n}", f"Course {n}", None, 0.0) for n in range(16)]
        specs = await _plan(courses=courses)

        assert len(specs) == derivation.MAX_DERIVED_COURSE_GOALS

    async def test_preparations_are_not_capped_by_the_course_limit(self):
        """Each carries a date the learner chose, and there are 46 in the whole database."""
        preps = [
            (f"p{n}", f"Subject {n}", NOW + timedelta(days=30 + n), None, "exam") for n in range(5)
        ]
        specs = await _plan(preps=preps)

        assert len(specs) == 5
        assert all(spec.basis == derivation.BASIS_PREPARATION for spec in specs)

    async def test_preparations_come_before_courses(self):
        specs = await _plan(
            preps=[("p1", "MCAT", NOW + timedelta(days=30), None, "exam")],
            courses=[("c1", "Course", None, 0.0)],
        )
        assert [spec.basis for spec in specs] == [
            derivation.BASIS_PREPARATION,
            derivation.BASIS_COURSE,
        ]

    async def test_the_cap_counts_only_what_it_would_create(self):
        """A covered course must not consume a slot, or one goal the learner already made would hide
        two they have not."""
        courses = [(f"c{n}", f"Course {n}", None, 0.0) for n in range(6)]
        specs = await _plan(goals=[("c0", None), ("c1", None)], courses=courses)

        assert len(specs) == derivation.MAX_DERIVED_COURSE_GOALS
        assert "c0" not in {spec.course_id for spec in specs}
        assert "c1" not in {spec.course_id for spec in specs}


class TestScopingToOnePieceOfIntent:
    async def test_creating_a_course_proposes_a_goal_for_that_course_only(self):
        """Not also for three older courses the learner never asked about, which is what an unscoped
        call from the creation path would do.

        A course-scoped call issues two queries — goals, then courses — so the preparation result set
        is absent from the queue rather than merely empty.
        """
        session = _FakeSession([], [("c9", "New", None, 0.0)])
        with patch.object(derivation, "get_session_factory", lambda: _factory(session)):
            specs = await derivation.plan_derivations("u1", now=NOW, course_id="c9")

        assert [spec.course_id for spec in specs] == ["c9"]
        assert session.executed == 2, "a course-scoped call must not query preparations"

    async def test_a_prep_scoped_call_does_not_derive_course_goals(self):
        session = _FakeSession([], [("p1", "MCAT", NOW + timedelta(days=9), None, "exam")])
        with patch.object(derivation, "get_session_factory", lambda: _factory(session)):
            specs = await derivation.plan_derivations("u1", now=NOW, prep_id="p1")

        assert [spec.prep_id for spec in specs] == ["p1"]
        assert session.executed == 2, "a prep-scoped call must not query courses"

    def test_the_creation_paths_pass_the_id_they_just_created(self):
        import inspect

        from src.domains.knowledge.services import course_service
        from src.domains.personal_learning.services import exam_prep_service

        assert "course_id=course.id" in inspect.getsource(course_service.create_course)
        assert "prep_id=prep.id" in inspect.getsource(exam_prep_service.create_preparation)


class TestProseIntentAloneDoesNotMakeAGoal:
    async def test_a_learner_with_no_course_or_preparation_gets_nothing(self):
        """`purpose` and `goalsText` are prose. A goal built on them could only be `manual`, and
        nothing measures a manual goal — it would sit at its birth number while the learner worked."""
        assert await _plan() == []

    def test_the_learning_profile_is_not_read_at_all(self):
        """Pins the decision rather than the wording: if prose ever starts creating goals, this fails
        and the reasoning above has to be revisited deliberately."""
        source = code_only(derivation.plan_derivations)
        assert "LearningProfile" not in source
        assert "goals_text" not in source


class TestTheCreatePayload:
    def _payload(self, **overrides):
        spec = derivation.DerivedGoalSpec(
            title="T",
            description="D",
            metric_kind="course_progress",
            unit="percent complete",
            basis=derivation.BASIS_COURSE,
            course_id="c1",
            target_value=100.0,
            **overrides,
        )
        return spec.to_create_data()

    def test_it_never_asserts_a_current_value(self):
        """`create_goal` refuses one on a measured goal, and storing one would be a second version of
        a number the source already holds."""
        payload = self._payload()
        assert "currentValue" not in payload
        assert "progress" not in payload

    def test_an_absent_target_is_omitted_rather_than_sent_as_zero(self):
        spec = derivation.DerivedGoalSpec(
            title="T",
            description="D",
            metric_kind="prep_readiness",
            unit="percent readiness",
            basis=derivation.BASIS_PREPARATION,
            prep_id="p1",
            target_value=None,
        )
        assert "targetValue" not in spec.to_create_data()

    def test_every_key_is_one_the_repository_can_persist(self):
        """`map_fields` refuses a key absent from `_GOAL_FIELD_MAP`, so a typo here is a 500 on
        creation rather than a lost field. This is that guard, run at test time."""
        from src.domains.progress.repository import progress_repo

        payload = self._payload(target_date=NOW)
        allowed = set(progress_repo._GOAL_FIELD_MAP)
        assert set(payload) <= allowed, sorted(set(payload) - allowed)

    def test_the_payload_survives_the_repository_mapper(self):
        from src.domains.progress.repository import progress_repo

        mapped = progress_repo._map_goal_data({"userId": "u1", **self._payload(target_date=NOW)})
        assert mapped["metric_kind"] == "course_progress"
        assert mapped["course_id"] == "c1"

    def test_the_service_validator_accepts_it(self):
        """`create_goal` raises for a `currentValue` on a measured kind. The derived payload must pass
        that check untouched."""
        from src.domains.progress.services.goal_service import _reject_asserted_current_value

        payload = self._payload()
        _reject_asserted_current_value(payload, metric_kind=payload["metricKind"])


class TestCreatingTheGoals:
    async def test_one_goal_is_created_per_spec(self):
        specs = [
            derivation.DerivedGoalSpec(
                title="T1",
                description="D",
                metric_kind="course_progress",
                unit="percent complete",
                basis=derivation.BASIS_COURSE,
                course_id="c1",
                target_value=100.0,
            )
        ]
        created: list[dict] = []

        async def _create(*, user_id, data):
            created.append(data)
            return SimpleNamespace(id="g1", **{"user_id": user_id})

        async def _plan_stub(_user_id, **_kwargs):
            return specs

        from src.domains.progress.services import goal_service

        with patch.object(derivation, "plan_derivations", _plan_stub):
            with patch.object(goal_service, "create_goal", _create):
                goals = await derivation.derive_goals_for_user("u1")

        assert len(goals) == 1
        assert created[0]["courseId"] == "c1"

    async def test_nothing_is_written_when_everything_is_covered(self):
        async def _plan_stub(_user_id, **_kwargs):
            return []

        from src.domains.progress.services import goal_service

        async def _explode(**_kwargs):
            raise AssertionError("create_goal must not be called when there is nothing to derive")

        with patch.object(derivation, "plan_derivations", _plan_stub):
            with patch.object(goal_service, "create_goal", _explode):
                assert await derivation.derive_goals_for_user("u1") == []

    async def test_a_failure_does_not_reach_the_caller_that_recorded_the_intent(self):
        """Creating a course succeeds or fails on its own terms. A learner who has just created one
        must not be told it failed because the goal beside it could not be written."""

        async def _explode(_user_id, **_kwargs):
            raise RuntimeError("database gone")

        with patch.object(derivation, "plan_derivations", _explode):
            assert await derivation.derive_goals_quietly("u1") == []

    async def test_the_backfill_is_sequential(self):
        """The session-mode pooler allows roughly fifteen clients; fanning out across sessions is what
        took `daily-counts` down."""
        # Both the docstring and a comment name `asyncio.gather` to explain why it is avoided.
        assert "gather" not in code_only(derivation.derive_for_users)


class TestIntentRecordingTriggersDerivation:
    def test_creating_a_course_derives(self):
        import inspect

        from src.domains.knowledge.services import course_service

        source = inspect.getsource(course_service.create_course)
        assert "goal_derivation_service" in source

    def test_creating_a_preparation_derives(self):
        import inspect

        from src.domains.personal_learning.services import exam_prep_service

        source = inspect.getsource(exam_prep_service.create_preparation)
        assert "goal_derivation_service" in source

    def test_derivation_is_not_on_a_beat_schedule(self):
        """Goal deletion is a hard `DELETE`. A recurring sweep over "courses without goals" would
        rebuild a goal the learner deliberately removed, every night, with no way to stop it."""
        from src.workers import progress_tasks

        schedule = progress_tasks.get_beat_schedule()
        tasks = {str(entry.get("task")) for entry in schedule.values()}
        assert not any("derive" in task for task in tasks)


@pytest.mark.parametrize("kind", ["course_progress", "prep_readiness"])
def test_both_derived_kinds_are_measured_as_a_state_not_an_accumulation(kind):
    """Which is why neither needs a target to report progress: the measured value is already a
    percentage. Task 8 depends on this."""
    from src.domains.progress.services import goal_metrics

    assert kind in goal_metrics._STATE_KINDS
