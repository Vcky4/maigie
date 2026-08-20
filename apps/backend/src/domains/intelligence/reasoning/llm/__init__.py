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

__all__ = [
    "generate_content",
    "generate_grounded_content",
    "GroundedResult",
    "GroundingSource",
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


async def generate_content(prompt: str, *, max_tokens: int = 2048, temperature: float = 0.7) -> str:
    """Generate text content using the default LLM provider.

    This is the primary interface for domains that need simple text generation
    (topic explanations, quizzes, summaries, etc.).

    Raises:
        GeminiError: If Gemini returned no usable text (e.g. safety filter blocked
            the response, MAX_TOKENS hit during thinking phase, or RECITATION).
            The error message includes the ``finish_reason`` so callers can
            decide whether to retry or fall back.
    """
    client = new_gemini_client(gemini_api_key() or None)
    response = await client.aio.models.generate_content(
        model=default_model_for(LlmTask.CHAT_DEFAULT),
        contents=prompt,
        config=gemini_types.GenerateContentConfig(
            max_output_tokens=max_tokens,
            temperature=temperature,
        ),
    )

    text = _extract_text(response)
    if not text:
        finish_reason = _extract_finish_reason(response)
        logger.warning(
            "Gemini returned no text (finish_reason=%s). Prompt length=%d, max_tokens=%d.",
            finish_reason,
            len(prompt),
            max_tokens,
        )
        raise GeminiError(
            f"empty response (finish_reason={finish_reason})",
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
    """
    client = new_gemini_client(gemini_api_key() or None)
    response = await client.aio.models.generate_content(
        model=default_model_for(LlmTask.CHAT_DEFAULT),
        contents=prompt,
        config=gemini_types.GenerateContentConfig(
            max_output_tokens=max_tokens,
            temperature=temperature,
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
        raise GeminiError(f"empty grounded response (finish_reason={finish_reason})")

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
