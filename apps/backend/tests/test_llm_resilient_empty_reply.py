"""An empty provider reply is a failed attempt, not a successful one.

This pins the root cause of a `500` on the study-diagram route. A provider answered with an empty string;
`generate_content` returned it as a success, so it was never retried, never fell through to the next
provider, and the circuit breaker was credited for it. The caller then handed `""` to a JSON parser and got
`Expecting value: line 1 column 1 (char 0)` — a message that reads as a parsing bug and sends whoever is
debugging it to look at the prompt.

Asked again moments later the same provider produced the diagram correctly, verified against the live model.
So a retry was all that failure ever needed, and the machinery for it already existed.

The two tests here are the two halves of the fix: that a blank is retried, and that a caller who supplied a
fallback still gets it when every provider comes back blank.
"""

from __future__ import annotations

import pytest

from src.domains.personal_learning.services import llm_resilient

# This module defines its own `LLMUnavailableError`, distinct from the one in
# `intelligence.reasoning.llm.errors`. Imported from the module under test so the test asserts on the
# exception callers of *this* helper will actually catch.
from src.domains.personal_learning.services.llm_resilient import LLMUnavailableError


@pytest.fixture
def one_provider(monkeypatch):
    """A single provider with no circuit breaker and no fallback chain, so attempts are countable."""

    async def resolve(_user_id):
        return "gemini"

    monkeypatch.setattr(llm_resilient, "_resolve_provider", resolve)
    monkeypatch.setattr(llm_resilient, "_get_fallback_providers", lambda _p: [])
    monkeypatch.setattr(llm_resilient, "_is_circuit_open", lambda _p: False)
    monkeypatch.setattr(llm_resilient, "_record_success", lambda _p: None)
    monkeypatch.setattr(llm_resilient, "_record_failure", lambda _p: None)

    calls: list[str] = []

    def install(replies):
        """`replies` are plain strings; the fake wraps each in the `ProviderReply` the loop expects.

        Phase 3b changed the provider callables to return text *plus* token usage, because the
        attempt loop cannot charge for what it cannot see (Decision L). The fake carries
        `usage=None`, which is the "unmetered provider" path — these tests are about retry
        behaviour, and a fixture that invented token counts would be asserting on fiction.
        """

        async def call(prompt, **_kwargs):
            calls.append(prompt)
            text = replies[min(len(calls) - 1, len(replies) - 1)]
            return llm_resilient.ProviderReply(text=text)

        monkeypatch.setattr(llm_resilient, "_PROVIDER_CALLABLES", {"gemini": call})

    return type("Env", (), {"install": staticmethod(install), "calls": calls})


async def test_a_blank_reply_is_retried_and_recovers(one_provider):
    """The production symptom exactly: empty once, then fine.

    Before this, the first reply was returned as a success and the caller got `""`.
    """
    one_provider.install(["", "the real answer"])

    result = await llm_resilient.generate_content("p", max_retries=2)

    assert result == "the real answer"
    assert len(one_provider.calls) == 2, "the blank reply should have been retried"


async def test_whitespace_counts_as_blank(one_provider):
    """A reply of newlines parses no better than an empty one and is just as useless to a caller."""
    one_provider.install(["   \n\t ", "the real answer"])

    assert await llm_resilient.generate_content("p", max_retries=2) == "the real answer"
    assert len(one_provider.calls) == 2


async def test_every_attempt_blank_falls_back_rather_than_returning_nothing(
    one_provider,
):
    """A caller who asked for a fallback still gets it. Only the retry behaviour changed."""
    one_provider.install([""])

    result = await llm_resilient.generate_content("p", max_retries=1, fallback="nothing doing")

    assert result == "nothing doing"
    # Two attempts on the one provider, then the fallback — not a silent empty string.
    assert len(one_provider.calls) == 2


async def test_every_attempt_blank_and_no_fallback_raises(one_provider):
    """`fallback=None` means "no fallback — raise", here as everywhere else in this module.

    Raising `LLMUnavailableError` is the improvement: the caller can tell "the model gave me nothing" from
    "the model gave me something I could not parse", which an empty string could not express.
    """
    one_provider.install([""])

    with pytest.raises(LLMUnavailableError):
        await llm_resilient.generate_content("p", max_retries=1)


async def test_a_blank_reply_no_longer_reaches_the_json_parser(one_provider):
    """The end-to-end shape of the original bug, through the wrapper the diagram route uses."""
    one_provider.install(["", '{"mermaid": "flowchart TD\\n  A-->B"}'])

    parsed = await llm_resilient.generate_content_json("p", max_retries=2, fallback={})

    assert parsed == {"mermaid": "flowchart TD\n  A-->B"}
