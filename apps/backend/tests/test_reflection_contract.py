"""Phase 1 of the Reflect surface: the reflection contract, and the end of invented metrics.

The defect these tests exist for was not a wrong number. It was a *typeless* number: three
untyped JSON columns filled from a language model that had been asked for `topics_studied`,
`sessions_completed`, `notes_created`, `total_minutes`, `concepts_mastered`,
`retention_score`, `streak_days` and `milestones`, while the only context it received was the
behaviour profile. Nothing queried a session, note, topic, flashcard, quiz or achievement
row, so the model was producing counts for data it had never seen, and the row stored them
beside a real `periodStart` as though they had been measured.

Most of this file therefore tests an absence, which is the hard thing to keep tested: that no
metric can arrive from a model response, that the prompt cannot ask for one, and that an
unmeasured field is null rather than zero. A test that only checked the happy path would pass
just as well against the old code.

No database is needed. The service is exercised with its LLM call and its repository both
substituted, because what is under test is what the service *decides*, not where it stores it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from src.domains.personal_learning import db_models, models
from src.domains.personal_learning.repository import PersonalLearningRepository
from src.domains.personal_learning.services import (
    reflection_narrative,
    reflection_service,
)
from src.shared.field_mapping import UnmappedFieldError

#: The keys the old prompt asked the model to invent. None of them may reappear.
FABRICATED_KEYS = (
    "topics_studied",
    "sessions_completed",
    "notes_created",
    "total_minutes",
    "concepts_mastered",
    "retention_score",
    "streak_days",
)


class TestMetricsAreNullNotZero:
    """`None` means "not measured". `0` means "measured, and it was none".

    The old failure path wrote zeros for every field, which made a broken generation
    indistinguishable from a genuinely inactive week — and the inactive week is the one where
    a wall of zeros reads as a judgement about the learner.
    """

    def test_a_fresh_metrics_object_is_entirely_null(self):
        dumped = models.ReflectionMetrics().model_dump(by_alias=True)
        assert dumped, "metrics must publish fields, not be an empty object"
        assert all(value is None for value in dumped.values()), {
            key: value for key, value in dumped.items() if value is not None
        }

    def test_no_metric_defaults_to_zero(self):
        """Guards the specific regression: a well-meaning `= 0` default on a count."""
        for name, field in models.ReflectionMetrics.model_fields.items():
            assert field.default != 0, f"{name} defaults to 0, which claims a measurement"

    def test_list_metrics_default_to_null_rather_than_empty(self):
        """`[]` is a finding. `None` is silence. The list fields must start silent."""
        metrics = models.ReflectionMetrics()
        assert metrics.new_topics_mastered is None
        assert metrics.milestones_reached is None

    def test_zero_is_still_expressible(self):
        """Nulls must not have cost us the ability to say a week was genuinely empty."""
        metrics = models.ReflectionMetrics(focused_minutes=0, flashcards_reviewed=0)
        assert metrics.focused_minutes == 0
        assert metrics.flashcards_reviewed == 0


class TestTheModelSuppliesNoNumbers:
    def test_the_prompt_never_asks_for_a_count(self):
        """The prompt now *supplies* figures, so this asserts the shape of that: the model is
        handed labelled facts and asked for two prose keys, never for the raw field names the
        old prompt requested."""
        prompt = reflection_service._build_prompt(
            type_=models.ReflectionType.WEEKLY,
            period_start=datetime(2026, 8, 12, tzinfo=UTC),
            period_end=datetime(2026, 8, 19, tzinfo=UTC),
            deep=False,
            metrics=models.ReflectionMetrics(focused_minutes=326, active_days=5),
        )
        for key in FABRICATED_KEYS:
            assert key not in prompt, f"the prompt asks the model for {key}"
        assert '"title"' in prompt
        assert '"summary"' in prompt
        # And the measured values are present as facts rather than requests.
        assert "Tracked focused minutes: 326" in prompt

    def test_a_learner_with_nothing_measured_gets_a_no_statistics_brief(self):
        prompt = reflection_service._build_prompt(
            type_=models.ReflectionType.MONTHLY,
            period_start=datetime(2026, 7, 20, tzinfo=UTC),
            period_end=datetime(2026, 8, 19, tzinfo=UTC),
            deep=True,
            metrics=models.ReflectionMetrics(),
        )
        assert "state no figures at all" in prompt
        assert "do not invent" in prompt.lower()

    @pytest.mark.anyio
    async def test_a_model_that_returns_counts_has_them_ignored(
        self, reflection_harness, plus_tier
    ):
        """The load-bearing test. Run as Plus, since Free calls no model to return counts.

        A model will volunteer numbers whether or not it was asked. The service must take its
        wording and nothing else, so what reaches the row is the *measured* metrics — and none
        of the plausible-looking counts in the response.
        """
        reflection_harness.llm_response = {
            "title": "A strong week",
            "summary": "You studied often.",
            # Everything below is what the old service would have persisted.
            "activitiesLayer": {"topics_studied": 12, "sessions_completed": 9},
            "progressLayer": {"concepts_mastered": 24, "retention_score": "89%"},
            "achievementsLayer": {"milestones": ["Ten-day rhythm"], "streak_days": 12},
            "recommendations": ["Keep going"],
            "focusedMinutes": 999,
            "consistencyScore": 100,
        }

        await reflection_service.generate_reflection(user_id="u1", type="weekly")

        written = reflection_harness.written
        assert written["title"] == "A strong week"
        assert written["summary"] == "You studied often."
        # The measurement survives; the model's version of it does not.
        assert written["metrics"] == reflection_harness.measured.model_dump(by_alias=True)
        assert written["metrics"]["focusedMinutes"] == 42
        assert written["metrics"]["consistencyScore"] is None
        assert written["recommendations"] == []

    @pytest.mark.anyio
    async def test_a_failed_generation_keeps_the_measurements(self, reflection_harness):
        """The old fallback wrote zeros over everything. Now the numbers were taken before the
        model was called, so a provider outage costs the prose and nothing else."""

        async def explode(*args, **kwargs):
            raise RuntimeError("provider down")

        reflection_harness.llm = explode

        result = await reflection_service.generate_reflection(user_id="u1", type="weekly")

        written = reflection_harness.written
        assert written["metrics"] == reflection_harness.measured.model_dump(by_alias=True)
        assert not any(value == 0 for value in written["metrics"].values())
        # A reflection is still delivered — the requirement was always to degrade, not to fail.
        assert written["summary"]
        assert result is reflection_harness.row

    @pytest.mark.anyio
    async def test_metrics_are_computed_before_the_model_is_called(
        self, reflection_harness, plus_tier
    ):
        """Ordering is the guarantee. If the model ran first, a hang or a crash could take the
        measurements with it.

        Run as Plus, because Free no longer calls a model at all (Decision M rule 2) — so on Free
        there is no ordering left to assert. `TestFreeSpendsNoModelCall` covers that instead.
        """
        await reflection_service.generate_reflection(user_id="u1", type="weekly")
        assert reflection_harness.order == ["metrics", "llm"]


class TestReflectionType:
    def test_lowercase_is_accepted(self):
        assert models.ReflectionType("weekly") is models.ReflectionType.WEEKLY
        assert models.ReflectionType("monthly") is models.ReflectionType.MONTHLY

    def test_uppercase_is_refused_at_the_boundary(self):
        """What the Sunday task used to send. It has to fail loudly now, not fall through."""
        with pytest.raises(ValueError):
            models.ReflectionType("WEEKLY")

    def test_the_generate_request_refuses_an_unknown_cadence(self):
        with pytest.raises(ValueError):
            models.ReflectionGenerateRequest(type="quarterly")

    @pytest.mark.anyio
    async def test_the_service_normalises_a_legacy_uppercase_caller(self, reflection_harness):
        """Defence in depth for the scheduled task, whose old value took the fallback period
        silently rather than being rejected."""
        await reflection_service.generate_reflection(user_id="u1", type="WEEKLY")
        assert reflection_harness.written["type"] == "weekly"

    @pytest.mark.anyio
    @pytest.mark.parametrize(
        ("cadence", "days"),
        [("weekly", 7), ("monthly", 30)],
    )
    async def test_each_cadence_gets_its_own_period(
        self, reflection_harness, plus_tier, cadence, days
    ):
        """Run as Plus, because monthly is a Plus cadence (Decision T).

        The harness is Free by default and this test is about periods rather than tiers, so it opts
        into Plus explicitly instead of the gate being loosened to keep it passing.
        """
        await reflection_service.generate_reflection(user_id="u1", type=cadence)
        written = reflection_harness.written
        span = written["periodEnd"] - written["periodStart"]
        assert span == timedelta(days=days)

    @pytest.mark.anyio
    async def test_weekly_is_never_gated(self, reflection_harness):
        """The learner's own weekly summary is free. Gating it is not defensible on its own terms."""
        await reflection_service.generate_reflection(user_id="u1", type="weekly")
        assert reflection_harness.written["type"] == "weekly"

    @pytest.mark.anyio
    async def test_monthly_on_free_is_refused_with_an_actionable_payload(self, reflection_harness):
        """A `403`, unlike the locked trend range's `200`.

        The difference is that this is a mutation the learner explicitly asked for: refusing it with a
        typed upgrade payload is something the client can act on, and there is no chart left looking
        broken. A locked *read* has to answer `200`, because the design renders the control and Free
        must be able to press it.
        """
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as caught:
            await reflection_service.generate_reflection(user_id="u1", type="monthly")

        assert caught.value.status_code == 403
        detail = caught.value.detail
        assert detail["upgradeRequired"] is True
        assert detail["capability"] == "reflection"
        assert "upgradeUrl" in detail
        # And nothing was written — a refused generation must not leave a half-made row.
        assert reflection_harness.written == {}


class TestTheSchemaHasNoUntypedLayers:
    def test_the_columns_are_gone(self):
        columns = {column.name for column in db_models.Reflection.__table__.columns}
        assert "activitiesLayer" not in columns
        assert "progressLayer" not in columns
        assert "achievementsLayer" not in columns
        assert "metrics" in columns

    def test_the_response_publishes_a_typed_metrics_object(self):
        annotation = models.ReflectionResponse.model_fields["metrics"].annotation
        assert annotation is models.ReflectionMetrics

    def test_recommendations_is_a_list_of_actions_not_a_three_way_union(self):
        """It was `list[str] | dict | None`, and the design needs objects with a target."""
        annotation = str(models.ReflectionResponse.model_fields["recommendations"].annotation)
        assert "ReflectionAction" in annotation
        assert "dict" not in annotation

    def test_metrics_and_recommendations_cannot_be_null_in_the_database(self):
        """A response type over a nullable column means every reader coerces the null."""
        columns = {c.name: c for c in db_models.Reflection.__table__.columns}
        assert columns["metrics"].nullable is False
        assert columns["recommendations"].nullable is False

    def test_the_period_is_unique_per_learner_and_cadence(self):
        """What makes generation idempotent, and keeps the library counting periods rather
        than generation attempts."""
        constraints = {
            tuple(column.name for column in constraint.columns)
            for constraint in db_models.Reflection.__table__.constraints
            if constraint.__class__.__name__ == "UniqueConstraint"
        }
        assert ("userId", "type", "periodStart") in constraints


class TestActionTargets:
    def test_a_target_defaults_to_going_nowhere(self):
        """An action can be advice. The card renders without a button."""
        assert models.ReflectionActionTarget().kind is models.ReflectionActionKind.NONE

    def test_the_kind_set_is_closed(self):
        with pytest.raises(ValueError):
            models.ReflectionActionTarget(kind="open_a_url")

    def test_an_action_carries_a_target_rather_than_a_path(self):
        """A backend emitting `/prepare/x/practice/weak` owns the web client's routing, and
        the same string is wrong on any other client."""
        assert "route" not in models.ReflectionAction.model_fields
        action = models.ReflectionAction(
            id="a1",
            title="Consolidate the strongest gain",
            detail="One short weak-area session.",
            label="Start focused practice",
            target=models.ReflectionActionTarget(
                kind=models.ReflectionActionKind.PREPARATION_PRACTICE,
                entity_id="prep_1",
                mode="weak",
            ),
        )
        dumped = action.model_dump(by_alias=True)
        assert dumped["target"] == {
            "kind": "preparation_practice",
            "entityId": "prep_1",
            "mode": "weak",
        }


class TestFieldMapping:
    def test_the_new_keys_are_mapped(self):
        mapped = PersonalLearningRepository._map_reflection(
            {
                "userId": "u1",
                "type": "weekly",
                "periodStart": datetime(2026, 8, 12, tzinfo=UTC),
                "periodEnd": datetime(2026, 8, 19, tzinfo=UTC),
                "title": "A strong week",
                "summary": "…",
                "depth": "standard",
                "metrics": {},
                "recommendations": [],
                "openedAt": None,
            }
        )
        assert mapped["user_id"] == "u1"
        assert mapped["metrics"] == {}
        assert mapped["opened_at"] is None

    def test_a_removed_layer_key_is_refused_rather_than_dropped(self):
        """If it were dropped, a caller still writing the old shape would get a 201 and store
        nothing — which is how the original defect stayed invisible."""
        with pytest.raises(UnmappedFieldError):
            PersonalLearningRepository._map_reflection(
                {"userId": "u1", "activitiesLayer": {"topics_studied": 3}}
            )


class TestRoutes:
    """The surface exists and nothing writes a metric through it."""

    def _paths(self) -> dict[str, set[str]]:
        """Path to the union of its methods.

        Accumulated rather than assigned: FastAPI registers one route object per method, so a
        dict comprehension keeps whichever was declared last and every assertion below would
        be testing declaration order instead of the surface.
        """
        from src.app import app

        paths: dict[str, set[str]] = {}
        for route in app.routes:
            path = getattr(route, "path", "")
            if path.startswith("/api/v1/learning/reflections"):
                paths.setdefault(path, set()).update(getattr(route, "methods", set()))
        return paths

    def test_the_lifecycle_is_complete(self):
        paths = self._paths()
        assert "PATCH" in paths["/api/v1/learning/reflections/{reflection_id}"]
        assert "DELETE" in paths["/api/v1/learning/reflections/{reflection_id}"]
        assert "POST" in paths["/api/v1/learning/reflections/{reflection_id}/read"]

    def test_reading_a_reflection_is_a_separate_call_from_getting_it(self):
        """`openedAt` feeds the reflection streak, so a mutating GET would let a dashboard
        prefetch count as engagement."""
        paths = self._paths()
        assert "GET" in paths["/api/v1/learning/reflections/{reflection_id}"]
        assert "GET" not in paths["/api/v1/learning/reflections/{reflection_id}/read"]

    def test_no_route_accepts_metrics_from_a_client(self):
        assert "metrics" not in models.ReflectionUpdate.model_fields
        assert set(models.ReflectionUpdate.model_fields) == {"title", "summary"}


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


class _Harness:
    """Substitutes the three things `generate_reflection` reaches outside itself.

    Captures the payload handed to the repository, which is the only place the decision "what
    gets persisted" is observable without a database, and records call order so the guarantee
    that measurement precedes generation is testable rather than merely intended.

    `measured` deliberately mixes a real value with nulls: `focusedMinutes` proves the
    measurement survives, and `consistencyScore` proves a null is not quietly replaced by
    whatever the model offered for it.
    """

    def __init__(self):
        self.llm_response: dict = {"title": "A week", "summary": "Some prose."}
        self.llm = None
        self.written: dict = {}
        self.row = object()
        self.order: list[str] = []
        self.measured = models.ReflectionMetrics(focused_minutes=42, active_days=3)

    async def call_llm(self, prompt, **kwargs):
        return self.llm_response

    async def compute_metrics(self, *, user_id, period_start, period_end):
        self.order.append("metrics")
        return self.measured

    async def upsert(self, data, *, session=None):
        self.written = data
        return self.row


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def reflection_harness(monkeypatch):
    from src.domains.personal_learning.services import (
        feature_tier_service,
        llm_resilient,
        trial_service,
    )

    harness = _Harness()

    async def call_llm(prompt, **kwargs):
        harness.order.append("llm")
        if harness.llm is not None:
            return await harness.llm(prompt, **kwargs)
        return await harness.call_llm(prompt, **kwargs)

    monkeypatch.setattr(llm_resilient, "generate_content_json", call_llm)
    monkeypatch.setattr(reflection_service.repo, "upsert_reflection", harness.upsert)
    monkeypatch.setattr(
        reflection_service.reflection_metrics,
        "compute_metrics",
        harness.compute_metrics,
    )

    async def free_tier(user_id):
        return "free"

    async def noop(*args, **kwargs):
        return None

    async def trial_offered(user_id):
        return True

    monkeypatch.setattr(feature_tier_service, "get_quality_tier", free_tier)
    monkeypatch.setattr(trial_service, "record_plus_feature_used", noop)
    # Reached by the monthly cadence gate when it builds its upgrade payload. Stubbed here rather
    # than in the one test that hits it, so a future gate cannot fall through to the database.
    monkeypatch.setattr(feature_tier_service, "trial_available", trial_offered)

    return harness


@pytest.fixture
def plus_tier(monkeypatch):
    """Run a test as a Plus learner.

    Free is the harness default because it is the tier most rules have to be right for. A test that
    needs Plus asks for it explicitly, which keeps the gate honest — the alternative is loosening a
    gate so an old test keeps passing.
    """
    from src.domains.personal_learning.services import feature_tier_service

    async def plus(user_id):
        return "plus"

    monkeypatch.setattr(feature_tier_service, "get_quality_tier", plus)


class TestFreeSpendsNoModelCall:
    """Decision M rule 2: the free weekly reflection is composed with no model call at all.

    It used to spend **two**, and keep almost nothing from either. The summary call ran for every
    tier, and the narrative call ran whenever `chosen` was non-empty — which on Free is whenever any
    metric is actionable, because `_FREE_RECOMMENDATIONS = 1`. Then `assemble(deep=False)` discarded
    the reply: no signal prose, no patterns, no closing, no per-subject insight. So a free learner
    triggered an 8 192-token `THINKING_DYNAMIC` generation and kept one action's wording out of it.

    What blocked the fix for a phase was not the condition but the copy: the only non-model summary in
    the module was an error message, so skipping the calls would have told every free learner their
    reflection had failed.
    """

    @pytest.mark.anyio
    async def test_no_model_is_called(self, reflection_harness):
        await reflection_service.generate_reflection(user_id="u1", type="weekly")
        assert reflection_harness.order == ["metrics"]
        assert "llm" not in reflection_harness.order

    @pytest.mark.anyio
    async def test_the_summary_is_measured_rather_than_an_apology(self, reflection_harness):
        """The distinction the whole item turned on.

        A free learner's summary must read as their week, not as a failure. The specific string
        guarded against is `_error_summary`'s "could not be generated", which is what would have been
        published had the calls simply been removed.
        """
        await reflection_service.generate_reflection(user_id="u1", type="weekly")
        summary = reflection_harness.written["summary"]

        assert summary
        assert "could not be generated" not in summary
        assert "failed" not in summary.lower()

    @pytest.mark.anyio
    async def test_the_summary_carries_the_measurements(self, reflection_harness):
        """Not merely inoffensive — it has to say something. The harness measures 42 focused minutes,
        so the summary should name them rather than being a generic encouragement.
        """
        await reflection_service.generate_reflection(user_id="u1", type="weekly")
        assert "42m" in reflection_harness.written["summary"]

    @pytest.mark.anyio
    async def test_a_quiet_period_reads_as_quiet_rather_than_broken(self, reflection_harness):
        """Zero measurements is a fact about the learner's week, not an error.

        "0 topics mastered, 0% recall" reads as a judgement; "a quiet week" reads as a fact. This is
        the case where the temptation to reuse the error copy is strongest, because there genuinely is
        nothing to report.
        """
        reflection_harness.measured = models.ReflectionMetrics()
        await reflection_service.generate_reflection(user_id="u1", type="weekly")
        summary = reflection_harness.written["summary"]

        assert "quiet" in summary.lower()
        assert "could not" not in summary

    @pytest.mark.anyio
    async def test_the_measurements_still_reach_the_row(self, reflection_harness):
        """Removing the model must not have removed the content. Everything Free is promised is a pure
        function of the metrics (Decision T2), so the row is shorter than a Plus one rather than holed.
        """
        await reflection_service.generate_reflection(user_id="u1", type="weekly")
        written = reflection_harness.written
        assert written["metrics"] == reflection_harness.measured.model_dump(by_alias=True)
        assert written["depth"] == models.ReflectionDepth.STANDARD.value


class TestTheDeterministicActionCopy:
    """Free's actions are worded by `_ACTION_COPY`, so it is the only copy a free learner reads.

    "Keep going" was an acceptable last-resort string while it appeared only after a model call had
    failed. As the free tier's default it would render the same three words over three different
    suggestions.
    """

    def test_every_action_choose_actions_can_emit_has_its_own_copy(self):
        """The drift this prevents is silent: a new action id falls back to the generic pair and reads
        as "Keep going / Open" for every free learner who triggers it.
        """
        import inspect

        source = inspect.getsource(reflection_narrative.choose_actions)
        emitted = {
            line.split('"')[1]
            for line in source.splitlines()
            if line.strip().startswith('"') and line.strip().endswith('",')
        }
        missing = emitted - set(reflection_narrative._ACTION_COPY)
        assert not missing, f"action ids with no deterministic copy: {sorted(missing)}"

    def test_an_unworded_action_still_ships_with_its_grounds(self):
        """The target is the part that has to be right; the sentence is the part that can be plain."""
        target = models.ReflectionActionTarget(kind=models.ReflectionActionKind.FLASHCARD_REVIEW)
        actions = reflection_narrative.assemble_actions(
            chosen=[("recall", target, "recall was 41% this period")],
            written={},
        )
        assert actions[0].title == "Run a review session"
        assert actions[0].detail == "recall was 41% this period"
        assert actions[0].label == "Review cards"

    def test_the_model_still_wins_when_it_wrote_something(self):
        """The deterministic copy is a floor, not a replacement — Plus still buys the wording."""
        target = models.ReflectionActionTarget(kind=models.ReflectionActionKind.FLASHCARD_REVIEW)
        actions = reflection_narrative.assemble_actions(
            chosen=[("recall", target, "recall was 41% this period")],
            written={"actions": {"recall": {"title": "Shore up recall", "label": "Start"}}},
        )
        assert actions[0].title == "Shore up recall"
        assert actions[0].label == "Start"

    def test_an_unknown_action_id_falls_to_the_generic_pair(self):
        """Rather than raising. A new action with no copy is a cosmetic gap, and failing a learner's
        reflection over it would be the wrong trade — the test above is what catches it in CI instead.
        """
        target = models.ReflectionActionTarget(kind=models.ReflectionActionKind.SCHEDULE)
        actions = reflection_narrative.assemble_actions(
            chosen=[("invented", target, "grounds")], written={}
        )
        assert actions[0].title == "Keep going"
