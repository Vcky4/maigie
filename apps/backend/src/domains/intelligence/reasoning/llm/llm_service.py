"""Content generation over the resilient multi-provider LLM client.

This replaces a stub whose ``generate`` returned ``""`` and whose
``generate_course_outline`` returned ``{}``. An empty string is indistinguishable from a
model that chose to say nothing, and the empty outline surfaced downstream as
``ValueError("Outline contained no modules")``, which blames the model for a method that
was never implemented.

There is no need to rebuild a provider client for this: ``llm_resilient`` already
implements per-user provider selection across Gemini, OpenAI and Anthropic, per-provider
circuit breaking, retry with backoff and cross-provider fallback. This delegates to it.

Note on placement: ``llm_resilient`` currently lives under
``domains.personal_learning.services``, which is the wrong home for a shared client and
means this module imports 'upwards' into a feature domain. It is imported lazily here and
left in place deliberately, because existing tests patch
``src.domains.personal_learning.services.llm_resilient.generate_content_json`` and moving
it would silently disable those patches. Relocating it into this package is tracked as
part of the remaining LLM migration.

This does not restore the routing layer that ``adapter_registry.get_llm_router`` needs;
that is a separate and much larger piece of work covering tool calling, streaming and
usage accounting.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

_VALID_DIFFICULTIES = ("BEGINNER", "INTERMEDIATE", "ADVANCED")


class LlmGenerationError(RuntimeError):
    """Raised when generation produced nothing usable."""


def _outline_prompt(topic: str, difficulty: str, user_message: str | None) -> str:
    extra = f"\nAdditional context from the learner:\n{user_message}\n" if user_message else ""
    return (
        "You are designing a structured course outline.\n\n"
        f"Topic: {topic}\n"
        f"Target difficulty: {difficulty}\n"
        f"{extra}\n"
        "Return ONLY a JSON object, with no prose and no code fences, shaped exactly:\n"
        "{\n"
        '  "title": "concise course title",\n'
        '  "description": "two or three sentences describing the course",\n'
        f'  "difficulty": "one of {" | ".join(_VALID_DIFFICULTIES)}",\n'
        '  "modules": [\n'
        "    {\n"
        '      "title": "module title",\n'
        '      "description": "one sentence",\n'
        '      "topics": ["topic title", "topic title"]\n'
        "    }\n"
        "  ]\n"
        "}\n\n"
        "Produce between 4 and 8 modules, each with 3 to 6 topics. Order them so each "
        "module depends only on earlier ones."
    )


class LlmService:
    """Generates content through the resilient provider client."""

    async def generate(
        self,
        prompt: str,
        *,
        max_tokens: int = 2048,
        temperature: float = 0.7,
        user_id: str | None = None,
        fallback: str | None = None,
        operation: str = "unknown",
        **_kwargs: Any,
    ) -> str:
        """Generate text for a prompt.

        ``operation`` is forwarded rather than assigned, because this method is a passthrough and
        cannot know what it is generating. A caller that leaves it unset is metered as ``"unknown"``
        and runs the standard model — see ``llm_resilient.QUALITY_SPLIT_OPERATIONS``.

        **Nothing calls this today**, nor ``generate_json``; ``generate_course_outline`` is the only
        method of this class with a caller. The parameter is added anyway rather than the methods
        being deleted, because a passthrough that silently ran the Plus model is how drift 23 happened
        and an unused one is a loaded gun. Deleting them is a separate question from this change.

        Raises:
            LLMUnavailableError: If every provider is unavailable and no ``fallback``
                is supplied. Previously this returned ``""``.
        """
        from src.domains.personal_learning.services.llm_resilient import (
            generate_content,
        )

        return await generate_content(
            prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            user_id=user_id,
            fallback=fallback,
            operation=operation,
        )

    async def generate_course_outline(
        self,
        topic: str,
        difficulty: str,
        user_message: str | None = None,
        *,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        """Generate a course outline.

        Returns a dict with ``title``, ``description``, ``difficulty`` and ``modules``,
        where each module has ``title``, ``description`` and a list of topic titles.

        Raises:
            LlmGenerationError: If the model returned an outline of the wrong shape or
                one with no modules. Failing here rather than returning ``{}`` keeps the
                cause attached to the error.
            json.JSONDecodeError: If the reply was not parseable at all. ``fallback=None``
                means "no fallback — raise", so a malformed reply surfaces as the parse
                error rather than as ``LlmGenerationError``. Stated because the two are
                not interchangeable to a caller writing an ``except``: the sole caller,
                ``generate_course_content_task``, catches ``Exception`` and reports over
                its websocket, which is why this has never leaked a ``500``.
        """
        from src.domains.personal_learning.services.llm_resilient import (
            generate_content_json,
        )

        outline = await generate_content_json(
            _outline_prompt(topic, difficulty, user_message),
            max_tokens=4096,
            # Structure benefits from being less inventive than prose.
            temperature=0.4,
            user_id=user_id,
            fallback=None,
            # Above the quality threshold at ~1 020 units, so Plus generates outlines on
            # `gemini-3.5-flash` and Free on Flash-Lite.
            operation="course_outline",
        )

        if not isinstance(outline, dict):
            raise LlmGenerationError(
                f"Course outline generation returned {type(outline).__name__}, expected an object"
            )

        modules = outline.get("modules")
        if not isinstance(modules, list) or not modules:
            raise LlmGenerationError(
                "Course outline generation returned no modules for topic "
                f"{topic!r}; response keys were {sorted(outline)}"
            )

        normalised_difficulty = str(outline.get("difficulty") or difficulty or "BEGINNER").upper()
        if normalised_difficulty not in _VALID_DIFFICULTIES:
            logger.info(
                "Course outline returned unrecognised difficulty %r for topic %r; "
                "falling back to the requested %r",
                outline.get("difficulty"),
                topic,
                difficulty,
            )
            normalised_difficulty = (difficulty or "BEGINNER").upper()

        outline["difficulty"] = normalised_difficulty
        logger.info(
            "Generated course outline for %r: %d module(s)",
            topic,
            len(modules),
        )
        return outline

    async def generate_json(
        self,
        prompt: str,
        *,
        max_tokens: int = 2048,
        temperature: float = 0.4,
        user_id: str | None = None,
        fallback: Any = None,
        operation: str = "unknown",
    ) -> Any:
        """Generate and parse a JSON response.

        ``operation`` is forwarded, not assigned; see ``generate``.
        """
        from src.domains.personal_learning.services.llm_resilient import (
            generate_content_json,
        )

        return await generate_content_json(
            prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            user_id=user_id,
            fallback=fallback,
            operation=operation,
        )


llm_service = LlmService()


__all__ = ["LlmService", "LlmGenerationError", "llm_service"]
