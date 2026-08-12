"""Tests for backgrounded quiz generation and the stages a client polls.

Decision H fixed quiz start as synchronous "until p95 start latency exceeds 10s".
Migration `018` made the figure readable and the first reading settled it: **p50
16,346 ms, max 17,738 ms**, every sample past the threshold. Phase 4e had refused to
show a staged progress bar until the server could report stages, because a bar driven
by a timer describes state the browser has no access to — it would read "Writing
questions" for a request that had already failed selecting them.

Generation now runs outside the request and records each phase it reaches. Three
properties are worth pinning:

- **Validation stays in the request.** Every refusal a learner can act on — no topics,
  no topic chosen, no readable material, mode not on their plan — must remain a 4xx,
  not a session that quietly fails. That boundary is the whole design.
- **Progress is derived from the stage**, so the two cannot disagree, and is `None`
  rather than 0 when no stage is known.
- **A lost task is caught.** The background task lives in the API process, so a restart
  mid-generation would leave the row `GENERATING` forever and the client polling a
  spinner with no end.
"""

import os

os.environ.setdefault("SKIP_DB_FIXTURE", "1")

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from src.domains.personal_learning import models
from src.domains.personal_learning.services import quiz_engine

Stage = quiz_engine.GenerationStage


class TestStageVocabulary:
    def test_the_stages_are_ordered_and_unique(self):
        assert len(set(Stage.ORDER)) == len(Stage.ORDER)
        assert Stage.ORDER[0] == Stage.PREPARING
        assert Stage.ORDER[-1] == Stage.READY

    def test_writing_questions_comes_after_preparing(self):
        """The order is what a progress bar draws, so it has to match reality: the
        expensive LLM call happens after topics are resolved and the bank is read."""
        assert Stage.INDEX[Stage.PREPARING] < Stage.INDEX[Stage.REUSING_BANK]
        assert Stage.INDEX[Stage.REUSING_BANK] < Stage.INDEX[Stage.WRITING_QUESTIONS]
        assert Stage.INDEX[Stage.WRITING_QUESTIONS] < Stage.INDEX[Stage.CHECKING_QUESTIONS]

    def test_the_published_type_matches_the_service(self):
        """The client switches on a closed set. If these drift, a wait screen either
        misses a stage or renders one the server never sends."""
        from typing import get_args

        assert set(get_args(models.GenerationStage)) == set(Stage.ORDER)


class TestGenerationProgress:
    def test_progress_runs_from_zero_to_one(self):
        assert quiz_engine.generation_progress(Stage.PREPARING) == 0.0
        assert quiz_engine.generation_progress(Stage.READY) == 1.0

    def test_progress_increases_with_the_stage(self):
        values = [quiz_engine.generation_progress(stage) for stage in Stage.ORDER]
        assert values == sorted(values)
        assert len(set(values)) == len(values)

    def test_an_unknown_stage_is_none_rather_than_zero(self):
        """Zero would claim generation had not started. `None` says we do not know,
        which is the truth for a session created before the column existed."""
        assert quiz_engine.generation_progress(None) is None
        assert quiz_engine.generation_progress("SOMETHING_ELSE") is None

    def test_it_is_derived_not_stored(self):
        """Two fields that could disagree would eventually disagree. This one is a
        function of the other."""
        for stage in Stage.ORDER:
            assert quiz_engine.generation_progress(stage) == pytest.approx(
                round(Stage.INDEX[stage] / (len(Stage.ORDER) - 1), 2)
            )


def _session(status: str, *, stage: str | None = None, created_at: datetime | None = None):
    return SimpleNamespace(
        id="quiz-1",
        user_id="user-1",
        prep_id="prep-1",
        mode="QUICK_REVIEW",
        topic_id=None,
        status=status,
        total_questions=5,
        correct_count=0,
        score_percentage=None,
        duration_seconds=None,
        completed_at=None,
        created_at=created_at or datetime.now(UTC),
        generation_stage=stage,
    )


class TestTheResponseCarriesTheStage:
    def test_a_generating_session_reports_its_stage_and_progress(self):
        payload = quiz_engine._build_quiz_response(
            _session("GENERATING", stage=Stage.WRITING_QUESTIONS), [], []
        )
        validated = models.QuizSessionResponse.model_validate(payload)

        assert validated.status == "GENERATING"
        assert validated.generation_stage == "WRITING_QUESTIONS"
        assert validated.generation_progress == 0.5
        # Nothing to play yet, and the client is told why rather than having to infer
        # it from an empty array.
        assert validated.questions == []

    def test_a_playable_session_carries_no_stage(self):
        """So the client's signal to stop waiting is unambiguous: `status` leaving
        `GENERATING`. A terminal stage value would be a second, redundant signal."""
        payload = quiz_engine._build_quiz_response(_session("IN_PROGRESS"), [], [])
        validated = models.QuizSessionResponse.model_validate(payload)

        assert validated.generation_stage is None
        assert validated.generation_progress is None

    def test_it_serialises_camel_case(self):
        payload = quiz_engine._build_quiz_response(
            _session("GENERATING", stage=Stage.PREPARING), [], []
        )
        wire = models.QuizSessionResponse.model_validate(payload).model_dump(by_alias=True)
        assert "generationStage" in wire
        assert "generationProgress" in wire


class TestLostGenerationIsCaught:
    @pytest.mark.asyncio
    async def test_a_stale_generating_session_is_marked_failed(self, monkeypatch):
        """The background task lives in the API process. A restart between creating the
        session and finishing it abandons it, and nothing is watching to notice — so the
        bound is the clock, and it has to be written back rather than merely reported.
        """
        stale = _session(
            "GENERATING",
            stage=Stage.WRITING_QUESTIONS,
            created_at=datetime.now(UTC)
            - timedelta(seconds=quiz_engine.GENERATION_TIMEOUT_SECONDS + 30),
        )
        writes: list[tuple[str, dict]] = []

        async def update_quiz_session(quiz_id, data):
            writes.append((quiz_id, data))

        async def get_quiz_session(quiz_id, user_id):
            return _session("FAILED")

        monkeypatch.setattr(quiz_engine.repo, "update_quiz_session", update_quiz_session)
        monkeypatch.setattr(quiz_engine.repo, "get_quiz_session", get_quiz_session)

        result = await quiz_engine._fail_if_generation_was_lost(stale, user_id="user-1")

        assert writes == [("quiz-1", {"status": "FAILED", "generationStage": None})]
        assert result.status == "FAILED"

    @pytest.mark.asyncio
    async def test_a_session_still_inside_the_bound_is_left_alone(self, monkeypatch):
        """The measured p50 is 16.3s against a 90s bound, so a slow generation must not
        be killed for being slow."""
        fresh = _session("GENERATING", stage=Stage.WRITING_QUESTIONS)
        writes: list = []

        async def update_quiz_session(quiz_id, data):
            writes.append((quiz_id, data))

        monkeypatch.setattr(quiz_engine.repo, "update_quiz_session", update_quiz_session)

        result = await quiz_engine._fail_if_generation_was_lost(fresh, user_id="user-1")

        assert writes == []
        assert result is fresh

    @pytest.mark.asyncio
    async def test_a_finished_session_is_never_touched(self, monkeypatch):
        async def boom(*args, **kwargs):
            raise AssertionError("a completed session must not be rewritten")

        monkeypatch.setattr(quiz_engine.repo, "update_quiz_session", boom)

        old = _session("COMPLETED", created_at=datetime.now(UTC) - timedelta(days=30))
        assert await quiz_engine._fail_if_generation_was_lost(old, user_id="user-1") is old

    @pytest.mark.asyncio
    async def test_a_naive_created_at_does_not_raise(self, monkeypatch):
        """Some rows are naive. Comparing one to an aware `now` is a `TypeError`, which
        would be a 500 on the polling endpoint — the one request a waiting client is
        making over and over."""
        naive = _session("GENERATING", created_at=datetime(2020, 1, 1))
        writes: list = []

        async def update_quiz_session(quiz_id, data):
            writes.append((quiz_id, data))

        async def get_quiz_session(quiz_id, user_id):
            return _session("FAILED")

        monkeypatch.setattr(quiz_engine.repo, "update_quiz_session", update_quiz_session)
        monkeypatch.setattr(quiz_engine.repo, "get_quiz_session", get_quiz_session)

        result = await quiz_engine._fail_if_generation_was_lost(naive, user_id="user-1")
        assert result.status == "FAILED"

    def test_the_bound_exceeds_the_provider_timeout(self):
        """Otherwise a generation that is legitimately waiting on a 60s provider call
        would be declared lost while it was still running."""
        assert quiz_engine.GENERATION_TIMEOUT_SECONDS > 60


class TestStageWritesAreBestEffort:
    @pytest.mark.asyncio
    async def test_a_failed_stage_write_does_not_stop_generation(self, monkeypatch):
        """The learner would rather have their questions than an accurate progress bar."""

        async def failing_update(quiz_id, data):
            raise RuntimeError("database blip")

        monkeypatch.setattr(quiz_engine.repo, "update_quiz_session", failing_update)

        # Must not raise.
        await quiz_engine._set_stage("quiz-1", Stage.WRITING_QUESTIONS)


class TestCorrectOptionResolution:
    """Matching the model's own key to the model's own options.

    Found by measurement, not by reasoning. Grounding ordinary practice in the
    learner's material (Phase 4m) instructs the model to use "its terminology,
    notation and worked conventions", and on mathematical material that means
    notation. A live session asked for five questions, the model returned five
    perfectly usable ones, and **four were discarded** because the key rendered
    `x = -3` while the option rendered `x = −3` with a unicode minus.

    The rule being enforced was right — a question whose answer is not among its
    options can only ever be wrong — and it was throwing away good questions.
    """

    def test_an_exact_match_is_unchanged(self):
        options = ["x = 3", "x = -3", "x = 0"]
        assert quiz_engine._resolve_correct_option(options, "x = -3") == "x = -3"

    def test_case_only_differences_already_worked(self):
        assert quiz_engine._resolve_correct_option(["Paris", "Rome"], "paris") == "Paris"

    def test_a_unicode_minus_matches_a_hyphen(self):
        """The exact case observed live."""
        options = ["x = 3", "x = -3", "x = 9"]
        assert quiz_engine._resolve_correct_option(options, "x = \u22123") == "x = -3"

    def test_latex_delimiters_are_ignored(self):
        options = ["n - 1", "n + 1"]
        assert quiz_engine._resolve_correct_option(options, "$n - 1$") == "n - 1"

    def test_an_enumeration_label_is_not_stripped(self):
        """Tried and removed. Any pattern loose enough to catch `B) ` also catches
        `n - 1` — label `n`, separator `-`, value `1` — which normalised most of a
        mathematical option set to a bare number and stopped it matching itself. A
        presentational transformation that can change meaning is not one.
        """
        options = ["Reject the null hypothesis", "Fail to reject"]
        assert quiz_engine._resolve_correct_option(options, "B) Reject the null hypothesis") is None

    def test_arithmetic_options_still_match_themselves(self):
        """The regression the label stripper caused, pinned so it cannot return."""
        for options, key in (
            (["n - 1", "n + 1"], "n - 1"),
            (["a - b", "a + b"], "$a - b$"),
            (["1 - p", "p - 1"], "1 - p"),
        ):
            assert quiz_engine._resolve_correct_option(options, key) == options[0]

    def test_collapsed_whitespace_and_nbsp(self):
        options = ["p < 0.05", "p > 0.05"]
        assert quiz_engine._resolve_correct_option(options, "p\u00a0<  0.05") == "p < 0.05"

    def test_a_trailing_full_stop_is_ignored(self):
        options = ["The mean increases", "The mean decreases"]
        assert (
            quiz_engine._resolve_correct_option(options, "The mean increases.")
            == "The mean increases"
        )

    def test_a_genuinely_absent_answer_is_still_rejected(self):
        """The rule this preserves. An answer not on offer makes the question
        unanswerable, and normalising must not invent a match."""
        options = ["x = 3", "x = 9"]
        assert quiz_engine._resolve_correct_option(options, "x = 42") is None

    def test_an_ambiguous_match_is_rejected(self):
        """Two options that normalise alike cannot be scored whichever is stored, so
        rejecting is the honest outcome rather than picking the first."""
        options = ["x = -3", "x = \u22123"]
        # An exact match still wins, because it is unambiguous by construction.
        assert quiz_engine._resolve_correct_option(options, "x = -3") == "x = -3"
        # Needing normalisation to match, and matching both, is unscorable.
        assert quiz_engine._resolve_correct_option(options, "$x = \u22123$") is None

    def test_an_empty_key_is_rejected(self):
        assert quiz_engine._resolve_correct_option(["a", "b"], "   ") is None

    def test_the_stored_key_is_snapped_to_the_option_text(self):
        """Everything downstream compares the stored key against the stored options —
        grading, `balance_answer_positions`, option elimination for hints. A key that
        merely *resembles* an option would fail at grading time on a question that is
        otherwise fine."""
        normalized = quiz_engine._usable_question(
            {
                "questionText": "Solve for x.",
                "questionType": "MULTIPLE_CHOICE",
                "options": ["x = 3", "x = -3", "x = 9", "x = 0"],
                "correctAnswer": "$x = \u22123$",
            }
        )
        assert normalized is not None
        assert normalized["correct_answer"] == "x = -3"
        assert normalized["correct_answer"] in normalized["options"]

    def test_grading_is_not_loosened(self):
        """The line this must not cross.

        Phase 4d removed a substring fallback from *grading* that marked wrong
        multiple-choice answers correct. `_comparable` normalises presentation only,
        and is used solely to match the generator's key to the generator's options —
        never to judge a learner's answer, where being generous tells them they know
        something they do not.
        """
        options = ["x = 3", "x = -3", "x = -3 or x = 3", "x = 0"]
        # A learner's near-miss is still wrong.
        assert not quiz_engine._check_answer_correctness(
            "x = 3", "x = -3", options, question_type="MULTIPLE_CHOICE"
        )
        # And a longer option containing the right one is still wrong — the exact
        # 4d defect.
        assert not quiz_engine._check_answer_correctness(
            "x = -3 or x = 3", "x = -3", options, question_type="MULTIPLE_CHOICE"
        )
        # The right answer is of course still right.
        assert quiz_engine._check_answer_correctness(
            "x = -3", "x = -3", options, question_type="MULTIPLE_CHOICE"
        )
