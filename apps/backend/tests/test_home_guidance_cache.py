"""Home guidance is cached on the learner state that produced it.

**This was the dearest operation per learner per month in the product**, and it got there by being
cheap. ~140 units a call is nothing; firing on *every* home load is roughly $2.10/month per active
learner (§6.5), which is more than a Plus subscription's entire margin. `growth_service` and
`goal_insight_service` had gone through `narrative_cache` since Phase 6.5; home guidance was the one
expensive composition that had not, and Decision M's cost model assumed it would be.

The key is `_build_llm_context(state)` — the exact string the prompt is built from, not a chosen
subset of fields. If the context is byte-identical the guidance would be too, which makes this the
tightest correct key available. Two properties of that string are load-bearing and both are tested
here: it contains **no timestamp**, so a hit is possible at all, and it *does* contain the day count
and the due-flashcard count, so guidance cannot be stale about what the learner has.
"""

from __future__ import annotations

import os

os.environ.setdefault("SKIP_DB_FIXTURE", "1")

import re  # noqa: E402
from types import SimpleNamespace  # noqa: E402

import pytest  # noqa: E402

from src.domains.personal_learning.services import (
    guidance_engine,  # noqa: E402
    narrative_cache,  # noqa: E402
)


def _state(**overrides):
    """A learner ambiguous enough that `_deterministic_guidance` declines and the model runs.

    Every key `_gather_learner_state` produces is present: the deterministic path reads several of
    them before giving up, and a partial dict would fail with a `KeyError` that looks like a bug in
    the code under test rather than in the fixture.
    """
    base = {
        "purpose": "exam_prep",
        "has_purpose": True,
        "has_subjects": True,
        "subjects": ["Physics"],
        "goals": "Pass JAMB",
        "maturity_days": 12,
        "note_count": 4,
        "flashcard_total": 20,
        "due_flashcard_count": 3,
        "due_flashcards": [],
        "prep_count": 1,
        "plan_count": 1,
        "active_preps": [SimpleNamespace(subject="Physics", status="IN_PROGRESS")],
        "active_plans": [SimpleNamespace(id="plan-1", title="Plan")],
        "todays_plan_items": [],
        "profile": SimpleNamespace(id="profile-1"),
    }
    base.update(overrides)
    return base


class TestTheContextIsAUsableCacheKey:
    """Both halves of "usable": stable enough to hit, complete enough not to go stale."""

    def test_it_carries_no_clock(self):
        """The defect that silently defeats caching, and it has precedent here.

        Prefix caching on the chat path is defeated by `context.py:70` opening every prompt with a
        timestamp — Phase 0 records it. A clock anywhere in this string would make every home load a
        miss, leaving the cache costing a write per read while saving nothing. The symptom would be a
        bill that failed to fall rather than an error anyone would notice, which is why it is pinned.
        """
        context = guidance_engine._build_llm_context(_state())

        assert not re.search(r"\d{1,2}:\d{2}", context), f"a clock time appears in: {context!r}"
        assert not re.search(r"\d{4}-\d{2}-\d{2}", context), f"a date appears in: {context!r}"

    def test_identical_state_gives_an_identical_key(self):
        assert guidance_engine._build_llm_context(_state()) == guidance_engine._build_llm_context(
            _state()
        )

    @pytest.mark.parametrize(
        "change",
        [
            {"maturity_days": 13},
            {"due_flashcard_count": 9},
            {"note_count": 5},
            {"prep_count": 2},
            {"subjects": ["Physics", "Chemistry"]},
        ],
    )
    def test_state_that_should_change_the_advice_changes_the_key(self, change):
        """Each of these changes what "today's focus" ought to be, so none may hit a stale entry.

        `maturity_days` is the one that matters most: it ticks daily, so guidance refreshes every day
        even for a learner who does nothing. That is the right cadence for a panel whose whole claim
        is to be about *today*.
        """
        assert guidance_engine._build_llm_context(
            _state(**change)
        ) != guidance_engine._build_llm_context(_state())


class TestTheModelRunsOncePerState:
    @pytest.fixture
    def spy(self, monkeypatch):
        calls = {"compose": 0}
        store: dict[tuple, dict] = {}

        async def fake_resolve(*, user_id, kind, inputs, compose, entity_id="", scope=""):
            key = (user_id, kind, narrative_cache.fingerprint(inputs), entity_id, scope)
            if key in store:
                return store[key]
            composed = await compose()
            if composed:
                store[key] = composed
            return composed or {}

        async def fake_llm(user_id, state, *, context):
            calls["compose"] += 1
            return {"message": "composed", "stage": "active", "context": context}

        monkeypatch.setattr(narrative_cache, "resolve", fake_resolve)
        monkeypatch.setattr(guidance_engine, "_llm_guidance", fake_llm)
        return SimpleNamespace(calls=calls, store=store)

    async def test_a_second_home_load_does_not_call_the_model(self, spy):
        state = _state()

        first = await guidance_engine._compute_intelligent_guidance("u1", state)
        second = await guidance_engine._compute_intelligent_guidance("u1", state)

        assert first == second
        assert (
            spy.calls["compose"] == 1
        ), "the second home load must be served from cache — this is the whole saving"

    async def test_changed_state_recomposes(self, spy):
        await guidance_engine._compute_intelligent_guidance("u1", _state())
        await guidance_engine._compute_intelligent_guidance("u1", _state(due_flashcard_count=11))

        assert spy.calls["compose"] == 2

    async def test_two_learners_do_not_share_an_entry(self, spy):
        """Keyed on `user_id` as well as the context, so two learners in identical states each get
        their own row. The context is a summary and identical states are plausible; serving one
        learner's guidance to another would be a privacy failure, not a saving."""
        await guidance_engine._compute_intelligent_guidance("u1", _state())
        await guidance_engine._compute_intelligent_guidance("u2", _state())

        assert spy.calls["compose"] == 2

    async def test_the_prompt_is_built_from_the_string_that_keyed_it(self, spy):
        """A cache that stores output produced from a different input than it is keyed on is worse
        than no cache. The context is passed into the composer rather than rebuilt for exactly this
        reason, and this pins that it is the same value."""
        state = _state()

        result = await guidance_engine._compute_intelligent_guidance("u1", state)

        assert result["context"] == guidance_engine._build_llm_context(state)


class TestFailureIsNotCached:
    async def test_a_declined_composition_falls_back_and_stores_nothing(self, monkeypatch):
        """`resolve` answers `{}` for a composer that declined, and deliberately does not store it.

        Caching an empty panel would turn one model timeout into a permanently blank home screen,
        unfixable until the learner's own state happened to move.
        """
        calls = {"n": 0}

        async def empty_resolve(*, user_id, kind, inputs, compose, entity_id="", scope=""):
            calls["n"] += 1
            await compose()
            return {}

        async def fake_llm(user_id, state, *, context):
            return None

        async def fallback(state):
            return {"message": "fallback", "stage": "active"}

        monkeypatch.setattr(narrative_cache, "resolve", empty_resolve)
        monkeypatch.setattr(guidance_engine, "_llm_guidance", fake_llm)
        monkeypatch.setattr(guidance_engine, "_fallback_guidance", fallback)

        result = await guidance_engine._compute_intelligent_guidance("u1", _state())

        assert result["message"] == "fallback"

    async def test_a_composition_that_raises_still_falls_back(self, monkeypatch):
        """The behaviour before caching existed, preserved. A home screen must render."""

        async def exploding_resolve(**_kwargs):
            raise RuntimeError("model unavailable")

        async def fallback(state):
            return {"message": "fallback", "stage": "active"}

        monkeypatch.setattr(narrative_cache, "resolve", exploding_resolve)
        monkeypatch.setattr(guidance_engine, "_fallback_guidance", fallback)

        result = await guidance_engine._compute_intelligent_guidance("u1", _state())

        assert result["message"] == "fallback"


class TestTheDeterministicPathIsUnchanged:
    async def test_a_learner_with_no_purpose_never_reaches_the_model_or_the_cache(
        self, monkeypatch
    ):
        """`_deterministic_guidance` runs first and answers most states for free. Caching must not
        have moved a model call in front of it — that would have made the cheap path expensive.
        """

        async def forbidden(**_kwargs):
            raise AssertionError("the cache must not be consulted for a deterministic state")

        monkeypatch.setattr(narrative_cache, "resolve", forbidden)

        result = await guidance_engine._compute_intelligent_guidance(
            "u1", _state(has_purpose=False, purpose=None)
        )

        assert "todaysFocus" in result
