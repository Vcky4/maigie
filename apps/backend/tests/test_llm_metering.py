"""Every provider call is charged to the learner's window, and no charge can fail a generation.

This is Decision L, tested at the chokepoint. Before Phase 3b, `llm_resilient` discarded the
provider response and returned text only, so 26 of the 31 LLM call sites in the product were
unmetered — quiz generation, lesson bodies, course outlines, every flashcard path, every narrative
panel. The plan's own cost model put that at roughly −$270 of contribution at 10 000 MAU.

Four properties are asserted here, and three of them are the ones that will be argued about:

**Retries are charged.** One logical generation can bill up to nine provider calls, and each one
costs real money. Metering inside the attempt loop counts what was spent rather than what was
delivered, which will look unfair the first time a learner's allowance goes on our own instability.
The honest response is to shorten the retry chain, not to hide the charge — so a test pins the
charging, and the retry budget stays a cost decision.

**An empty reply is charged.** It consumed tokens. Charging on delivery instead of on spend is
precisely how this cost became invisible in the first place.

**A failing meter never fails a generation.** Charge on success, absorb on failure. A learner who
has waited for a lesson keeps it even if the accounting falls over afterwards.

**Some operations are never charged.** Onboarding and memory extraction, on principle rather than
on cost (§6.6).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.domains.intelligence.reasoning.llm import GenerationUsage
from src.domains.personal_learning.services import llm_resilient
from src.shared.exceptions import SubscriptionLimitError

# A Plus-model turn at the corrected rate card: 8k in, 600 out. §6.2 prices this at ~175 units.
PLUS_TURN = GenerationUsage(
    model="gemini-3.5-flash", input_tokens=8_000, output_tokens=600, thoughts_tokens=0
)


@pytest.fixture
def metered(monkeypatch):
    """One provider, no circuit breaker, and a recording meter in place of the database."""

    async def resolve(_user_id):
        return "gemini"

    monkeypatch.setattr(llm_resilient, "_resolve_provider", resolve)
    monkeypatch.setattr(llm_resilient, "_get_fallback_providers", lambda _p: [])
    monkeypatch.setattr(llm_resilient, "_is_circuit_open", lambda _p: False)
    monkeypatch.setattr(llm_resilient, "_record_success", lambda _p: None)
    monkeypatch.setattr(llm_resilient, "_record_failure", lambda _p: None)

    charges: list[tuple[str, int, str]] = []
    proactive_flags: list[bool] = []

    models_charged: list[str | None] = []

    # `model` is accepted because the chokepoint now passes it: the itemised `UsageEvent` row records
    # which model was charged for, since under Decision P the same operation costs different units on
    # different tiers. A stub missing the keyword raises `TypeError` inside `meter_usage`, which
    # swallows it — so the symptom is a silently unmetered call rather than an error.
    async def fake_record(user_id, units, operation="unknown", *, proactive=False, model=None):
        charges.append((user_id, units, operation))
        proactive_flags.append(proactive)
        models_charged.append(model)

    monkeypatch.setattr(
        "src.domains.billing.services.credit_consumption_service.record_units",
        fake_record,
    )

    calls: list[str] = []

    def install(replies):
        """`replies` is a list of `(text, usage)` pairs, cycled on the last entry."""

        async def call(prompt, **_kwargs):
            calls.append(prompt)
            text, usage = replies[min(len(calls) - 1, len(replies) - 1)]
            return llm_resilient.ProviderReply(text=text, usage=usage)

        monkeypatch.setattr(llm_resilient, "_PROVIDER_CALLABLES", {"gemini": call})

    return type(
        "Env",
        (),
        {
            "install": staticmethod(install),
            "calls": calls,
            "charges": charges,
            "proactive_flags": proactive_flags,
            "models_charged": models_charged,
        },
    )


class TestACallIsCharged:
    async def test_a_successful_generation_charges_measured_units(self, metered):
        metered.install([("an answer", PLUS_TURN)])

        await llm_resilient.generate_content("p", user_id="u1", operation="quiz_generation")

        assert len(metered.charges) == 1
        user_id, units, operation = metered.charges[0]
        assert user_id == "u1"
        assert operation == "quiz_generation"
        # Measured from real token counts and the model that produced them, not from a table.
        assert units > 0
        # And the model reaches the meter, so the itemised `UsageEvent` row can say which one was
        # charged for. Under Decision P the same operation costs different units on different tiers,
        # so a null here would make the per-operation figures unattributable to a tier.
        assert metered.models_charged == [PLUS_TURN.model]

    async def test_reasoning_tokens_are_charged_with_output(self, metered):
        """`thoughts_tokens` are drawn from the output allowance and billed at the output rate.

        This is the number that made the withdrawn `max_tokens` audit look attractive: a reply cut
        short with a large thinking count is a `thinking_budget` problem. Either way it is spend, and
        omitting it would undercharge the most expensive operations by the most.
        """
        thinky = GenerationUsage(
            model="gemini-3.5-flash",
            input_tokens=8_000,
            output_tokens=600,
            thoughts_tokens=4_000,
        )
        metered.install([("an answer", thinky)])
        await llm_resilient.generate_content("p", user_id="u1")
        with_thinking = metered.charges[0][1]

        metered.charges.clear()
        metered.calls.clear()
        metered.install([("an answer", PLUS_TURN)])
        await llm_resilient.generate_content("p", user_id="u1")
        without = metered.charges[0][1]

        assert with_thinking > without


class TestRetriesAreCharged:
    async def test_each_attempt_is_charged_separately(self, metered):
        """Decision L, and the property most likely to be argued about.

        Three attempts that each burned tokens are three charges. Counting the operation once would
        make our own instability free to us and invisible to everyone.
        """
        metered.install([("", PLUS_TURN), ("", PLUS_TURN), ("finally", PLUS_TURN)])

        result = await llm_resilient.generate_content("p", user_id="u1", max_retries=2)

        assert result == "finally"
        assert len(metered.calls) == 3
        assert len(metered.charges) == 3, "every provider call that spent tokens must be charged"

    async def test_an_empty_reply_is_still_charged(self, metered):
        """The provider answered, billed us and produced nothing usable.

        Charging on delivery rather than on spend is how this cost stayed invisible.
        """
        metered.install([("", PLUS_TURN)])

        with pytest.raises(llm_resilient.LLMUnavailableError):
            await llm_resilient.generate_content("p", user_id="u1", max_retries=0)

        assert len(metered.charges) == 1


class TestTheMeterCannotBreakAGeneration:
    async def test_a_failing_meter_does_not_lose_the_artefact(self, metered, monkeypatch):
        """Charge on success, absorb on failure — never the reverse.

        **This test found a real defect on first run.** `_meter` documented itself as never raising
        but only relied on `record_units` swallowing its own database errors. Anything else that
        failed — pricing an unknown model, the import itself — escaped into the attempt loop, where
        `except Exception` counted it as a *provider* failure, retried it, and could exhaust the
        chain. An accounting error presented as an outage, and burned two more provider calls doing
        it. `_meter` now catches for itself.
        """

        async def exploding_record(*_a, **_kw):
            raise RuntimeError("the database is on fire")

        monkeypatch.setattr(
            "src.domains.billing.services.credit_consumption_service.record_units",
            exploding_record,
        )
        metered.install([("the lesson the learner waited for", PLUS_TURN)])

        result = await llm_resilient.generate_content("p", user_id="u1")

        assert result == "the lesson the learner waited for"
        assert len(metered.calls) == 1, "a meter failure must not be retried as a provider failure"

    async def test_a_reply_with_no_usage_is_not_charged_zero_silently(self, metered, caplog):
        """An unmetered provider path must be visible in the logs.

        Charging zero is the failure mode that lets an unmetered surface return: it looks like a
        working meter and costs the same as no meter at all.
        """
        metered.install([("an answer", None)])

        with caplog.at_level("WARNING"):
            await llm_resilient.generate_content("p", user_id="u1", operation="lesson_body")

        assert not metered.charges
        assert any("unmetered provider reply" in r.getMessage() for r in caplog.records)


class TestWhatIsNeverCharged:
    async def test_onboarding_and_memory_are_exempt_on_principle(self, metered):
        """§6.6. Charging a learner before they have learned anything is self-defeating, and nobody
        can be asked to pay for the product remembering them."""
        for operation in sorted(llm_resilient.UNCHARGED_OPERATIONS):
            metered.charges.clear()
            metered.calls.clear()
            metered.install([("an answer", PLUS_TURN)])

            await llm_resilient.generate_content("p", user_id="u1", operation=operation)

            assert not metered.charges, f"{operation} must not be charged"

    async def test_an_unlabelled_call_is_still_charged(self, metered):
        """The default has to be "charge", or exemption becomes the thing that happens by
        forgetting. `operation="unknown"` is visible in the logs until a label arrives.
        """
        metered.install([("an answer", PLUS_TURN)])

        await llm_resilient.generate_content("p", user_id="u1")

        assert len(metered.charges) == 1
        assert metered.charges[0][2] == "unknown"

    async def test_a_call_with_no_user_is_not_charged(self, metered):
        """Correct for genuinely system-initiated work, and a bug anywhere else. Passing `user_id`
        is what makes a call chargeable, which is why background tasks that should be attributed
        have to pass one."""
        metered.install([("an answer", PLUS_TURN)])

        await llm_resilient.generate_content("p", user_id=None)

        assert not metered.charges


class TestTheJsonWrapperCharges:
    async def test_generate_content_json_threads_the_operation_label(self, metered):
        """26 of the 31 call sites reach a provider through this wrapper, so a label it dropped
        would be a label that never arrives."""
        metered.install([('{"ok": true}', PLUS_TURN)])

        parsed = await llm_resilient.generate_content_json(
            "p", user_id="u1", operation="course_outline", fallback={}
        )

        assert parsed == {"ok": True}
        assert metered.charges[0][2] == "course_outline"


class TestAnExhaustedLearnerIsRefusedBeforeSpending:
    """The counterpart to measured metering, and the reason it is safe to charge after the fact.

    `record_units` accounts and never refuses, so without a pre-flight gate an exhausted learner
    would generate indefinitely while the meter logged an ever-growing overshoot. The gate cannot ask
    "can they afford this?" — the cost is not known until the generation has happened — so it asks
    "do they have anything left at all?".
    """

    @pytest.fixture
    def gate(self, monkeypatch):
        state = {"available": (True, None), "calls": 0}

        async def fake_find(_self, _user_id):
            return object()

        async def fake_headroom(_user):
            state["calls"] += 1
            return state["available"]

        monkeypatch.setattr(
            "src.domains.identity.repository.IdentityRepository.find_by_id", fake_find
        )
        monkeypatch.setattr(
            "src.domains.billing.services.credit_consumption_service.has_headroom",
            fake_headroom,
        )
        return state

    async def test_an_exhausted_learner_is_refused_before_a_provider_is_called(self, metered, gate):
        gate["available"] = (False, "You've used this session's allowance.")
        metered.install([("should never be reached", PLUS_TURN)])

        with pytest.raises(SubscriptionLimitError):
            await llm_resilient.generate_content("p", user_id="u1", operation="quiz_generation")

        assert not metered.calls, "no provider call may be made for an exhausted learner"
        assert not metered.charges

    async def test_the_gate_runs_once_per_operation_not_once_per_attempt(self, metered, gate):
        """A retry must not re-gate. An operation that started legitimately could otherwise be
        refused halfway through its own retry chain, leaving the learner with nothing after we had
        already paid for two provider calls."""
        metered.install([("", PLUS_TURN), ("", PLUS_TURN), ("finally", PLUS_TURN)])

        await llm_resilient.generate_content("p", user_id="u1", max_retries=2)

        assert len(metered.calls) == 3
        assert gate["calls"] == 1, "the headroom check must not run per attempt"

    async def test_a_broken_gate_fails_open(self, metered, gate, monkeypatch):
        """A gate that cannot read the meter must not become an outage.

        Failing closed would turn a transient database blip into a product-wide refusal, which is a
        far worse failure than one unbilled operation.
        """

        async def exploding_headroom(_user):
            raise RuntimeError("the database is on fire")

        monkeypatch.setattr(
            "src.domains.billing.services.credit_consumption_service.has_headroom",
            exploding_headroom,
        )
        metered.install([("the generation still happens", PLUS_TURN)])

        result = await llm_resilient.generate_content("p", user_id="u1")

        assert result == "the generation still happens"

    async def test_an_exempt_operation_is_not_gated_either(self, metered, gate):
        """Onboarding is not charged, so it must not be refused for lack of allowance. A learner
        refused at onboarding has been refused before they ever saw the product work."""
        gate["available"] = (False, "out")
        metered.install([("welcome", PLUS_TURN)])

        result = await llm_resilient.generate_content(
            "p", user_id="u1", operation="onboarding_auto_setup"
        )

        assert result == "welcome"
        assert gate["calls"] == 0

    async def test_a_refusal_is_not_swallowed_into_a_json_fallback(self, metered, gate):
        """The one that would have been a silent product defect.

        `generate_content_json` catches `Exception` and returns `fallback`, so a refusal would have
        become an empty object — and a learner out of allowance would see an empty quiz instead of
        being told why. A `402` has to survive the JSON wrapper.
        """
        gate["available"] = (False, "out of allowance")
        metered.install([('{"ok": true}', PLUS_TURN)])

        with pytest.raises(SubscriptionLimitError):
            await llm_resilient.generate_content_json(
                "p", user_id="u1", operation="quiz_generation", fallback={}
            )


class TestTheTotalAttemptBudget:
    """One logical operation cannot bill an unbounded number of provider calls.

    `max_retries` is per provider and says nothing about the sum, so three enabled providers meant a
    worst case of nine charged calls for one generation — a reliability decision taken before retries
    cost money. Dormant while Gemini is the only enabled provider, which is why it is worth pinning
    now: it returns the moment a second provider is enabled, and it returns as a bill rather than as
    an error.
    """

    @pytest.fixture
    def three_providers(self, monkeypatch, metered):
        """Three providers that all fail, so the walk is only stopped by the budget."""
        calls: list[str] = []

        async def failing(prompt, **_kwargs):
            calls.append("call")
            raise RuntimeError("provider is down")

        async def resolve(_user_id):
            return "gemini"

        monkeypatch.setattr(llm_resilient, "_resolve_provider", resolve)
        monkeypatch.setattr(
            llm_resilient, "_get_fallback_providers", lambda _p: ["openai", "anthropic"]
        )
        monkeypatch.setattr(llm_resilient, "_is_circuit_open", lambda _p: False)
        monkeypatch.setattr(llm_resilient, "_record_failure", lambda _p: None)
        monkeypatch.setattr(
            llm_resilient,
            "_PROVIDER_CALLABLES",
            {"gemini": failing, "openai": failing, "anthropic": failing},
        )
        return calls

    async def test_the_total_is_capped_below_retries_times_providers(self, three_providers):
        """Three providers × three attempts would be nine. The budget stops it at four."""
        with pytest.raises(llm_resilient.LLMUnavailableError):
            await llm_resilient.generate_content("p", user_id="u1", max_retries=2)

        assert len(three_providers) == llm_resilient._MAX_TOTAL_ATTEMPTS
        assert len(three_providers) < 9

    async def test_a_single_provider_still_gets_its_full_retry_budget(self, metered):
        """The cap must not cost the retry that actually recovers things.

        A transient blank recovers on the second attempt — that is the failure the empty-reply
        handling exists for — so capping the total must not clip the primary provider's retries.
        """
        metered.install([("", PLUS_TURN), ("", PLUS_TURN), ("recovered", PLUS_TURN)])

        result = await llm_resilient.generate_content("p", user_id="u1", max_retries=2)

        assert result == "recovered"
        assert len(metered.calls) == 3

    async def test_every_attempt_inside_the_budget_is_still_charged(self, three_providers, metered):
        """Capping the count does not stop the calls that did happen from being billed. The budget
        bounds the spend; it does not hide it."""
        with pytest.raises(llm_resilient.LLMUnavailableError):
            await llm_resilient.generate_content("p", user_id="u1", max_retries=2)

        # These particular attempts raised before returning usage, so nothing is charged — the point
        # is that the count is bounded, and that a failure charges for tokens it never received.
        assert not metered.charges


class TestProactiveSpendIsTaggedNotSeparate:
    """The proactive column counts the same units as the month, a second time.

    That is what keeps it clear of Decision R's rule against a third meter — Decision R forbids a new
    *currency* with its own counter, and this is the same currency asked a second question. The
    property that matters: a background task cannot dodge the month by being proactive, and cannot
    dodge the sub-cap by being ordinary.
    """

    async def test_a_proactive_call_advances_both_counters(self, monkeypatch):
        # Deliberately does **not** take the `metered` fixture: that fixture replaces
        # `record_units` with a recorder, which is what the other tests want and is exactly
        # what this one must not have. This asserts what the real function writes.

        from src.domains.personal_learning.services import llm_resilient

        written: dict = {}

        class FakeRepo:
            async def find_by_id(self, _user_id):
                return SimpleNamespace(
                    id="u1",
                    usage_window_started_at=None,
                    usage_window_units_used=0,
                    usage_month_started_at=None,
                    usage_month_units_used=0,
                    usage_month_proactive_units_used=0,
                )

            async def update(self, _user_id, changes):
                written.update(changes)

        monkeypatch.setattr(
            "src.domains.billing.services.credit_consumption_service.IdentityRepository",
            FakeRepo,
        )
        from src.domains.billing.services.credit_consumption_service import record_units

        with llm_resilient.proactive_scope():
            await record_units("u1", 500, operation="reflection_summary", proactive=True)

        assert written["usageMonthUnitsUsed"] == 500
        assert written["usageMonthProactiveUnitsUsed"] == 500, (
            "proactive spend must also land in the month, or the sub-cap becomes a way to spend "
            "outside the budget"
        )

    async def test_an_ordinary_call_leaves_the_proactive_counter_alone(self, monkeypatch):
        written: dict = {}

        class FakeRepo:
            async def find_by_id(self, _user_id):
                return SimpleNamespace(
                    id="u1",
                    usage_window_started_at=None,
                    usage_window_units_used=0,
                    usage_month_started_at=None,
                    usage_month_units_used=0,
                    usage_month_proactive_units_used=0,
                )

            async def update(self, _user_id, changes):
                written.update(changes)

        monkeypatch.setattr(
            "src.domains.billing.services.credit_consumption_service.IdentityRepository",
            FakeRepo,
        )
        from src.domains.billing.services.credit_consumption_service import record_units

        await record_units("u1", 500, operation="quiz_generation")

        assert written["usageMonthUnitsUsed"] == 500
        assert "usageMonthProactiveUnitsUsed" not in written


class TestTheScopeReachesTheMeter:
    """A context variable rather than a parameter, because of the call depth.

    A Celery task reaches a provider through a service that calls the chokepoint several frames down.
    Threading a flag would mean widening every signature in between, each one existing only to pass it
    on — a lot of places for one to be forgotten, and forgetting it fails silently by charging the
    month without the sub-budget.
    """

    async def test_generation_inside_the_scope_is_marked_proactive(self, metered):
        from src.domains.personal_learning.services import llm_resilient

        metered.install([("an answer", PLUS_TURN)])

        with llm_resilient.proactive_scope():
            await llm_resilient.generate_content(
                "p", user_id="u1", operation="discovery_recommendations"
            )

        assert metered.proactive_flags == [True]

    async def test_generation_outside_the_scope_is_not(self, metered):
        from src.domains.personal_learning.services import llm_resilient

        metered.install([("an answer", PLUS_TURN)])

        await llm_resilient.generate_content("p", user_id="u1", operation="quiz_generation")

        assert metered.proactive_flags == [False]

    async def test_the_scope_does_not_leak_past_its_block(self, metered):
        """Scoped per learner rather than around a batch, so one learner's accounting cannot leak
        into the next one's if a batch raises midway."""
        from src.domains.personal_learning.services import llm_resilient

        with llm_resilient.proactive_scope():
            assert llm_resilient.is_proactive() is True

        assert llm_resilient.is_proactive() is False

    async def test_the_scope_resets_even_when_the_block_raises(self):
        from src.domains.personal_learning.services import llm_resilient

        with pytest.raises(RuntimeError):
            with llm_resilient.proactive_scope():
                raise RuntimeError("a batch failed midway")

        assert llm_resilient.is_proactive() is False
