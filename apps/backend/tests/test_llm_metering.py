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

import pytest

from src.domains.intelligence.reasoning.llm import GenerationUsage
from src.domains.personal_learning.services import llm_resilient

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

    async def fake_record(user_id, units, operation="unknown"):
        charges.append((user_id, units, operation))

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
        {"install": staticmethod(install), "calls": calls, "charges": charges},
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
