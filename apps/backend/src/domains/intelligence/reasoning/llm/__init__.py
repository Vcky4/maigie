"""
Multi-provider LLM adapters — Gemini, OpenAI, Anthropic.

Provides a clean interface for other domains to use LLM capabilities
without knowing about provider specifics.
"""

import logging
from dataclasses import dataclass
from typing import Any

from .errors import GeminiError, LLMError, LLMProviderError, LLMUnavailableError
from .gemini_sdk import new_gemini_client
from .gemini_sdk import types as gemini_types
from .registry import LlmTask, default_model_for, gemini_api_key

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Thinking budgets
# ---------------------------------------------------------------------------
#
# The configured models are **thinking models**: they emit hidden reasoning before the reply, and
# those tokens are drawn from the same `max_output_tokens` allowance and billed at the same output
# rate. That is the single fact behind five separate budget escalations in this codebase — diagram
# 1200 → 2048 → 8192, grounded search 2048 → 8192, lesson 4096 → 8192, reflection title 800 → 2048,
# home guidance 500 → 1200 — every one of them a visible reply cut off mid-string while the budget
# looked generous.
#
# `max_output_tokens` is a **ceiling and not a charge**: unused headroom costs nothing. So raising it
# was the right response to those truncations, and lowering it again would save nothing while
# reopening all five. What was missing is the knob that bounds the *reasoning* rather than the
# ceiling, which is this one. Nothing set it before Phase 0.
#
# `0` disables thinking, `-1` lets the model decide, a positive integer caps it.
THINKING_OFF = 0
"""No reasoning. For work with nothing to reason about — transcribing, reformatting, summarising a
passage that is already in the prompt. Cheapest and fastest, and on these tasks no worse."""

THINKING_BOUNDED = 512
"""Enough to plan a short answer, not enough to spend a budget on. For prompts that supply every
fact and bound the output — the narrative panels ask for ≤8-word headings and ≤30-word sentences
over figures the service already computed, so the only open question is phrasing."""

THINKING_DYNAMIC = -1
"""Model's discretion. For genuinely open generation: lesson bodies, quiz sets, course outlines,
reflection narratives, diagrams. These are the operations the escalations were about."""


def thinking_config(budget: int | None):
    """A `ThinkingConfig` for `budget`, or `None` to leave the provider default alone.

    Returned rather than applied so both facades and the raw call sites can share one meaning of
    each constant, and so passing `None` is distinguishable from passing `0` — "do not express an
    opinion" and "do not think" are different instructions and only one of them is free.
    """
    if budget is None:
        return None
    return gemini_types.ThinkingConfig(thinking_budget=budget)


__all__ = [
    "generate_content",
    "generate_content_with_usage",
    "GenerationUsage",
    "generate_grounded_content",
    "GroundedResult",
    "GroundingSource",
    "THINKING_OFF",
    "THINKING_BOUNDED",
    "THINKING_DYNAMIC",
    "thinking_config",
    "new_gemini_client",
    "gemini_types",
    "LlmTask",
    "default_model_for",
    "gemini_api_key",
    "LLMError",
    "LLMProviderError",
    "LLMUnavailableError",
    "GeminiError",
]


@dataclass(frozen=True)
class GenerationUsage:
    """What a completed generation actually consumed, and which model consumed it.

    **This exists so a generation can be charged for what it cost rather than what it was
    budgeted.** Every path to a provider used to return text and drop the response object, so no
    caller could see `usage_metadata` and the only way to price an operation was a table of
    estimates derived from `max_tokens` — which is a ceiling and not a charge (Phase 0). Decision L
    calls this out as the actual work in Phase 3b: metering could not be added until the numbers
    reached the place that charges.

    `thoughts_tokens` is reported separately from `output_tokens` but **billed with it**: reasoning
    tokens are drawn from the same output allowance and are charged at the output rate. It is broken
    out because it is the number that explains a truncation — a reply cut short with a large
    `thoughts_tokens` is a thinking budget problem, not a `max_tokens` problem, and the two have
    opposite fixes.

    `model` is carried rather than re-derived because a fallback can answer on a different model
    than the one the caller asked for, and pricing the wrong model is a silent error.
    """

    model: str
    input_tokens: int
    output_tokens: int
    thoughts_tokens: int = 0

    @property
    def billable_output_tokens(self) -> int:
        """Output plus reasoning, which is what the provider charges at the output rate."""
        return self.output_tokens + self.thoughts_tokens


def _extract_usage(response: Any, model: str) -> GenerationUsage:
    """Read token counts off a Gemini response, tolerating their absence.

    Defensive on every field because `usage_metadata` is absent on some error and streaming shapes,
    and a missing count must read as zero rather than raise: **failing to measure an operation is
    not a reason to fail the operation.** A zero here undercharges, which is visible in aggregate
    and recoverable; an exception here loses a generation the learner already waited for.
    """
    usage = getattr(response, "usage_metadata", None)
    if usage is None:
        return GenerationUsage(model=model, input_tokens=0, output_tokens=0)
    return GenerationUsage(
        model=model,
        input_tokens=getattr(usage, "prompt_token_count", 0) or 0,
        output_tokens=getattr(usage, "candidates_token_count", 0) or 0,
        thoughts_tokens=getattr(usage, "thoughts_token_count", 0) or 0,
    )


async def generate_content_with_usage(
    prompt: str,
    *,
    max_tokens: int = 2048,
    temperature: float = 0.7,
    thinking: int | None = None,
    model: str | None = None,
) -> tuple[str, GenerationUsage]:
    """`generate_content`, plus what it consumed.

    The metered entry point. `generate_content` delegates here and drops the usage, so the two
    cannot diverge in behaviour — there is one implementation and one place a bug can live.

    **`model` is how the quality paywall reaches this path (drift 23).** Until it existed this
    function bound `CHAT_DEFAULT` unconditionally, so all 26 generation call sites behind
    `llm_resilient` ran `gemini-3.5-flash` — the Plus model — for free learners as well. The
    allowlist that gates chat is read by `router` alone and never reaches here, so the decision has
    to arrive as an argument. `None` keeps the previous default, which is correct for the callers
    that are genuinely chat-shaped and wrong for nobody: `llm_resilient` always passes one.

    Raises:
        GeminiError: If Gemini returned no usable text (e.g. safety filter blocked the response,
            MAX_TOKENS hit during the thinking phase, or RECITATION). The message includes the
            ``finish_reason`` so callers can decide whether to retry or fall back.
    """
    client = new_gemini_client(gemini_api_key() or None)
    # Bound once so the error raised below can name the model that produced nothing. An error that
    # says "gemini returned nothing" without saying which model is not actionable.
    model = model or default_model_for(LlmTask.CHAT_DEFAULT)
    response = await client.aio.models.generate_content(
        model=model,
        contents=prompt,
        config=gemini_types.GenerateContentConfig(
            max_output_tokens=max_tokens,
            temperature=temperature,
            thinking_config=thinking_config(thinking),
        ),
    )

    text = _extract_text(response)
    if not text:
        finish_reason = _extract_finish_reason(response)
        logger.warning(
            "Gemini returned no text (finish_reason=%s). Prompt length=%d, max_tokens=%d, "
            "thinking=%s.",
            finish_reason,
            len(prompt),
            max_tokens,
            thinking,
        )
        # `invalid_request` rather than `server_error`: the provider answered successfully and
        # produced nothing, which a retry of the same prompt will reproduce. Classifying it retriable
        # would spend the learner's quota three times to arrive at the same silence.
        raise GeminiError(
            model=model,
            category="invalid_request",
            message=f"empty response (finish_reason={finish_reason})",
        )
    return text, _extract_usage(response, model)


async def generate_content(
    prompt: str,
    *,
    max_tokens: int = 2048,
    temperature: float = 0.7,
    thinking: int | None = None,
) -> str:
    """Generate text content using the default LLM provider.

    `thinking` bounds hidden reasoning tokens — `THINKING_OFF`, `THINKING_BOUNDED`,
    `THINKING_DYNAMIC`, or an explicit integer. `None` leaves the provider default, which is what
    every caller got before Phase 0 and is dynamic in practice.

    This is the primary interface for domains that need simple text generation
    (topic explanations, quizzes, summaries, etc.). Callers that need to charge for what they used
    want `generate_content_with_usage` instead; this drops the token counts on the floor, which is
    the right default for the callers that have no meter to feed.

    Raises:
        GeminiError: If Gemini returned no usable text (e.g. safety filter blocked
            the response, MAX_TOKENS hit during thinking phase, or RECITATION).
            The error message includes the ``finish_reason`` so callers can
            decide whether to retry or fall back.
    """
    text, _ = await generate_content_with_usage(
        prompt, max_tokens=max_tokens, temperature=temperature, thinking=thinking
    )
    return text


@dataclass(frozen=True)
class GroundingSource:
    """One search result the model was actually shown.

    ``url`` is frequently a ``vertexaisearch.cloud.google.com/grounding-api-redirect/...``
    redirect rather than the destination, so it has to be resolved before it is stored or
    shown. ``title`` is usually the site name rather than the page title.
    """

    url: str
    title: str | None


@dataclass(frozen=True)
class GroundedResult:
    """Text generated with search grounding, plus the sources behind it.

    ``grounded`` is the field that matters and the reason this is not just a string. The
    search tool is a *request*, not a guarantee: Gemini decides whether to call it, and
    when it does not, the reply is ordinary generation — the model writing URLs out of its
    weights. Callers that persist anything from this must be able to tell the two apart,
    because one is a citation and the other is a guess.
    """

    text: str
    sources: list[GroundingSource]
    #: True when the model ran out of output budget mid-reply.
    #:
    #: Carried because a truncated answer and an empty one are **not the same failure**, and used to
    #: be indistinguishable to the caller. A reply cut off mid-string parses to nothing, so a parser
    #: returning zero items reported "nothing found" for what was really "the reply was cut off" —
    #: and the learner was advised to rephrase a query that had in fact worked. One is a reason to
    #: try different words; the other is a reason to raise the token budget.
    truncated: bool = False

    @property
    def grounded(self) -> bool:
        return bool(self.sources)


async def generate_grounded_content(
    prompt: str,
    *,
    # 8192, from 2048, which truncated in practice — and it is the *third* call site in this
    # codebase to arrive at this number by the same route. See `study_voice/diagram.py`, which
    # reached it from 1200 and then 2048, and the lesson and outline routes before that.
    #
    # The output here is small: eight resources is maybe 1,500 characters. This looks absurdly
    # generous until you account for where the budget goes — the configured model is a *thinking*
    # model and reasoning tokens are drawn from the same output allowance. A measured failure on
    # this route: `thoughts_token_count=1067` out of 2000, `finish_reason=MAX_TOKENS`, and a reply
    # cut off mid-string after 364 characters. The JSON never closed, so the caller's parse
    # returned nothing and the learner was told no resources existed.
    #
    # Costing nothing when unused: billing is on tokens actually produced, not on the ceiling.
    max_tokens: int = 8192,
    temperature: float = 0.3,
    thinking: int | None = None,
    model: str | None = None,
) -> GroundedResult:
    """Generate text with Google Search grounding enabled.

    Use this instead of `generate_content` whenever the output is supposed to refer to
    things that exist. Plain generation asked for a URL will produce a well-formed,
    plausible, frequently non-existent one, because a URL is a string and predicting
    strings is what the model does.

    **Structured output is not available here.** `response_mime_type="application/json"`
    and a schema cannot be combined with tools in this SDK, so the reply is prose and the
    caller parses it. Worth the trade: a schema guarantees the *shape* of a URL, and
    grounding is the only thing that speaks to whether it resolves.

    A low default temperature, because this is a retrieval-and-summarise task and there is
    nothing to be gained from creative variance in a citation.

    Never raises for an ungrounded answer — it returns one with `grounded=False` and lets
    the caller decide. Refusing would turn a degraded result into a failed request, and for
    recommendations a smaller checked list beats an error.

    **`model` carries the quality paywall onto this path (drift 23).** This is the only generation in
    the product that cannot go through `llm_resilient` — the search tool has no OpenAI or Anthropic
    equivalent, so there is nothing to fall back to — and it is also the most expensive one, so it is
    the last place that should have kept serving the Plus model to everybody. Callers get the model
    from `llm_resilient.model_for_operation` so there is still one decision. `None` keeps the previous
    default.

    **Still unmetered, and that is a separate gap.** `GroundedResult` carries no usage, so no caller
    can charge for this call however it is modelled. Recorded as a Phase 3b straggler rather than
    fixed here: metering it means changing the return shape, and the quality split does not need to
    wait behind that.
    """
    client = new_gemini_client(gemini_api_key() or None)
    model = model or default_model_for(LlmTask.CHAT_DEFAULT)
    response = await client.aio.models.generate_content(
        model=model,
        contents=prompt,
        config=gemini_types.GenerateContentConfig(
            max_output_tokens=max_tokens,
            temperature=temperature,
            thinking_config=thinking_config(thinking),
            tools=[gemini_types.Tool(google_search=gemini_types.GoogleSearch())],
        ),
    )

    text = _extract_text(response)
    if not text:
        finish_reason = _extract_finish_reason(response)
        logger.warning(
            "Gemini returned no text for a grounded request (finish_reason=%s). "
            "Prompt length=%d, max_tokens=%d.",
            finish_reason,
            len(prompt),
            max_tokens,
        )
        raise GeminiError(
            model=model,
            category="invalid_request",
            message=f"empty grounded response (finish_reason={finish_reason})",
        )

    sources = _extract_grounding_sources(response)
    if not sources:
        # Not an error, but worth seeing in logs: it means the answer is ungrounded and
        # anything the caller persists from it is unverified.
        logger.info("Grounded request returned no grounding metadata; answer is ungrounded.")

    # Truncation is reported rather than left for the caller to infer from a failed parse. It was
    # silent before, and silence made it look like the model had nothing to say.
    truncated = str(_extract_finish_reason(response) or "").upper().endswith("MAX_TOKENS")
    if truncated:
        logger.warning(
            "Grounded reply hit the output budget and was cut off (max_tokens=%d, "
            "text length=%d). Reasoning tokens draw from this same allowance, so the "
            "visible answer can be truncated while the budget looks generous.",
            max_tokens,
            len(text),
        )

    return GroundedResult(text=text, sources=sources, truncated=truncated)


def _extract_grounding_sources(response: Any) -> list[GroundingSource]:
    """Pull the web sources out of a response's grounding metadata.

    Defensive throughout: grounding metadata is optional at every level, the shape has
    changed across SDK versions, and a missing attribute here should degrade to "not
    grounded" rather than raise inside a request that otherwise succeeded.
    """
    sources: list[GroundingSource] = []
    seen: set[str] = set()

    for candidate in getattr(response, "candidates", None) or []:
        metadata = getattr(candidate, "grounding_metadata", None)
        if metadata is None:
            continue
        for chunk in getattr(metadata, "grounding_chunks", None) or []:
            web = getattr(chunk, "web", None)
            if web is None:
                continue
            url = getattr(web, "uri", None)
            if not url or url in seen:
                continue
            seen.add(url)
            sources.append(GroundingSource(url=url, title=getattr(web, "title", None)))

    return sources


def _extract_text(response: Any) -> str:
    """Pull text out of a Gemini response, falling back to walking parts if needed.

    ``response.text`` is a convenience accessor that returns None whenever the
    response contains anything other than a single simple text part (thinking
    output, function calls, empty candidates). Walking ``candidates[0].content.parts``
    catches the cases where the SDK's convenience field misses valid text.
    """
    direct = getattr(response, "text", None)
    if direct:
        return direct.strip()

    candidates = getattr(response, "candidates", None) or []
    for candidate in candidates:
        content = getattr(candidate, "content", None)
        if not content:
            continue
        parts = getattr(content, "parts", None) or []
        fragments = [p.text for p in parts if getattr(p, "text", None)]
        joined = "".join(fragments).strip()
        if joined:
            return joined
    return ""


def _extract_finish_reason(response: Any) -> str:
    """Best-effort extraction of the first candidate's finish_reason for logging."""
    candidates = getattr(response, "candidates", None) or []
    if not candidates:
        return "no_candidates"
    reason = getattr(candidates[0], "finish_reason", None)
    if reason is None:
        return "unknown"
    # google-genai returns an enum whose name is what we want in logs.
    return getattr(reason, "name", str(reason))
