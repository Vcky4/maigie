"""Per-request AI usage records.

This replaces a stub that was `pass`, so no usage was recorded at all. Worse, the stub's
signature was `emit_ai_usage(scope, **kwargs)` while its caller passes `usage_scope=`, so
the required `scope` argument was never supplied and every call raised `TypeError`. The
call site wraps it in a bare `except Exception: pass`, which is why nothing surfaced.

The `AiUsageRecord` table and model already exist and match these fields one for one, so
this needs no migration.

Writes never raise. Usage accounting is a side effect of doing the work, and losing a row
must not fail the generation the user asked for. A failure is logged with the figures so
the gap is visible and reconstructable.
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)

PERSONAL_USAGE_SCOPE = "personal"


def build_circle_usage_scope(circle_id: str) -> str:
    """Build a usage scope string for a space."""
    return f"circle:{circle_id}"


async def emit_ai_usage(
    user_id: str,
    usage_scope: str = PERSONAL_USAGE_SCOPE,
    space_id: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    feature: str | None = None,
    input_tokens: int = 0,
    output_tokens: int = 0,
    request_count: int = 1,
    **_kwargs: Any,
) -> None:
    """Record one unit of AI usage against a user and a scope.

    Args:
        user_id: Who incurred the usage.
        usage_scope: ``personal`` or ``circle:<space_id>``; see the helpers above.
        space_id: The space, when the scope is a space.
        provider: ``gemini``, ``openai``, ``anthropic``.
        model: Model id, when known.
        feature: What the tokens were spent on, for example ``ai_course_generation``.
        input_tokens: Prompt tokens.
        output_tokens: Completion tokens.
        request_count: Number of provider calls this record covers.
    """
    try:
        from src.domains.learning_spaces.db_models import AiUsageRecord
        from src.shared.database import get_session_factory

        factory = get_session_factory()
        async with factory() as session:
            session.add(
                AiUsageRecord(
                    user_id=user_id,
                    usage_scope=usage_scope,
                    space_id=space_id,
                    provider=provider,
                    model=model,
                    feature=feature,
                    input_tokens=int(input_tokens or 0),
                    output_tokens=int(output_tokens or 0),
                    request_count=int(request_count or 1),
                )
            )
            await session.commit()
    except Exception:
        logger.error(
            "Failed to record AI usage user=%s scope=%s feature=%s provider=%s "
            "model=%s in=%s out=%s requests=%s",
            user_id,
            usage_scope,
            feature,
            provider,
            model,
            input_tokens,
            output_tokens,
            request_count,
            exc_info=True,
        )
