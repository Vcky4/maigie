"""Composed prose, kept against the figures it was written about.

Three Reflect surfaces publish a written interpretation of numbers they already measured: the growth
chart's drivers, a subject's strength/focus pair, and a goal's insight and next action. Every one is
Plus (Decision Z) and every one is read on page load, so composing on each read would spend a
language model call whenever a learner opened a goal.

This module is the one place that decides whether a passage needs writing. `resolve` takes the
**measured skeleton** and a composer, hashes the skeleton, and returns the stored passage when the
hash still matches. The hash is the invalidation: prose is only ever beside the figures it was written
from, and a figure that has not moved has no new sentence owing to it.

Nothing here composes anything itself. Each caller owns its prompt, its assembly and its truncation
guard, and hands over a plain dict — so this module has no opinion about what a narrative contains
and none of the three callers can change what the others store.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any, Literal

from sqlalchemy import select

from src.shared.database import get_session_factory

from ..db_models import NarrativeCache

logger = logging.getLogger(__name__)

#: The surfaces permitted to store here. A closed set in code rather than a database enum, so a new
#: panel costs a line instead of a migration, while a typo in a caller still fails loudly.
NarrativeKind = Literal["growth_drivers", "subject_insight", "goal_insight"]

#: Bump this when any narrative prompt or assembly rule changes.
#:
#: The cache is keyed on the *measured skeleton*, which is exactly right for invalidating prose when a
#: figure moves and exactly wrong for invalidating it when the instruction changes. Without this, a
#: prompt fix would leave every learner reading the wording it replaced until their numbers happened to
#: shift — the first prompt correction here was made against prose already stored, which is how the
#: gap was found. Folded into every fingerprint, so one increment retires all three panels' prose.
NARRATIVE_REVISION = 2


def fingerprint(inputs: Any) -> str:
    """A stable hash of the measured skeleton a passage was written from.

    `sort_keys` so that two equal skeletons assembled in different orders are one cache entry, and
    `default=str` so a `date` or a `Decimal` in the figures does not raise — a serialisation error
    here would surface as a missing insight panel, which is a confusing way to learn about a type.

    Rounded floats are the caller's business: this hashes exactly what it is given, and a caller that
    passes an unrounded ratio will miss on every read as the last decimal drifts.

    `NARRATIVE_REVISION` is folded in so that a change to a prompt or an assembly rule invalidates the
    prose it was written under, which the skeleton alone cannot express.
    """
    encoded = json.dumps(
        {"revision": NARRATIVE_REVISION, "inputs": inputs},
        sort_keys=True,
        default=str,
        separators=(",", ":"),
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


#: The capability the written interpretation is gated by. Deliberately the **existing** `reflection`
#: entry rather than a new one: its upgrade copy already reads "deeper insights that identify patterns
#: across topics and provide specific next steps", which is precisely the promise these three panels
#: make, and `trial_service.record_plus_feature_used` already tracks it. A second entry would mean two
#: upsell messages for one product promise, free to drift apart.
NARRATIVE_CAPABILITY = "reflection"


async def plus_gate(user_id: str, *, reason: str):
    """The notice for a learner who may not read composed prose, or `None` when they may.

    **A locked narrative is a `200` with this attached, never a `403`** (Decision Z). Every figure on
    these pages is free and only the interpretation is paid, so the page must render with an upgrade
    card where the prose would be — an error would make a working page look broken.

    Returns `models.LockedNotice`; the annotation is omitted to keep this module free of an import
    cycle through `models`, which imports nothing from the services layer but is imported by all of it.
    """
    from .. import models
    from . import feature_tier_service

    if await feature_tier_service.get_quality_tier(user_id) == "plus":
        return None
    matrix = feature_tier_service.FEATURE_TIER_MATRIX.get(NARRATIVE_CAPABILITY, {})
    return models.LockedNotice(
        reason=reason,
        capability=NARRATIVE_CAPABILITY,
        trial_available=await feature_tier_service.trial_available(user_id),
        upgrade_value=matrix.get("upgrade_value", ""),
    )


async def resolve(
    *,
    user_id: str,
    kind: NarrativeKind,
    inputs: Any,
    compose: Callable[[], Awaitable[dict[str, Any] | None]],
    entity_id: str = "",
    scope: str = "",
) -> dict[str, Any]:
    """The stored passage for these figures, composing and storing it on a miss.

    Returns `{}` when there is no prose to show — a miss whose composer declined or failed. Callers
    publish that as `insight: null`, which the pages already render as an absent panel rather than as
    an error.

    **A failed or empty composition is never stored.** Caching `{}` would turn one language model
    timeout into a permanently blank panel, unfixable until the learner's figures happened to move.
    The cost is that a persistent outage is retried on each read; that is the right way round, because
    the alternative fails silently and forever.

    **A cache read never fails the caller.** If the table is unreachable the composer still runs, so
    the panel degrades to uncached rather than to absent.
    """
    inputs_hash = fingerprint(inputs)

    row = await _load(user_id=user_id, kind=kind, entity_id=entity_id, scope=scope)
    if row is not None and row.inputs_hash == inputs_hash:
        payload = row.payload
        # A dict is what every caller stored, but the column is JSON and a hand-edited row could hold
        # anything. Treating a non-dict as a miss recomposes rather than handing a list to `.get`.
        if isinstance(payload, dict) and payload:
            return payload

    composed = await compose()
    if not composed:
        return {}

    await _store(
        user_id=user_id,
        kind=kind,
        entity_id=entity_id,
        scope=scope,
        inputs_hash=inputs_hash,
        payload=composed,
    )
    return composed


async def _load(*, user_id: str, kind: str, entity_id: str, scope: str) -> NarrativeCache | None:
    try:
        factory = get_session_factory()
        async with factory() as session:
            result = await session.execute(
                select(NarrativeCache).where(
                    NarrativeCache.user_id == user_id,
                    NarrativeCache.kind == kind,
                    NarrativeCache.entity_id == entity_id,
                    NarrativeCache.scope == scope,
                )
            )
            return result.scalars().first()
    except Exception as exc:  # pragma: no cover - degrades to uncached
        logger.warning("Narrative cache read failed for user %s kind %s: %s", user_id, kind, exc)
        return None


async def _store(
    *,
    user_id: str,
    kind: str,
    entity_id: str,
    scope: str,
    inputs_hash: str,
    payload: dict[str, Any],
) -> None:
    """Upsert by the full key, updating in place so one entity keeps one row.

    Re-reads inside the write transaction rather than reusing the row `resolve` loaded: composition
    takes seconds, and a concurrent request for the same panel would otherwise insert a second row
    and take a unique-constraint error on the way out.
    """
    try:
        factory = get_session_factory()
        async with factory() as session:
            try:
                result = await session.execute(
                    select(NarrativeCache).where(
                        NarrativeCache.user_id == user_id,
                        NarrativeCache.kind == kind,
                        NarrativeCache.entity_id == entity_id,
                        NarrativeCache.scope == scope,
                    )
                )
                existing = result.scalars().first()
                if existing is None:
                    session.add(
                        NarrativeCache(
                            user_id=user_id,
                            kind=kind,
                            entity_id=entity_id,
                            scope=scope,
                            inputs_hash=inputs_hash,
                            payload=payload,
                        )
                    )
                else:
                    existing.inputs_hash = inputs_hash
                    existing.payload = payload
                await session.commit()
            except Exception:
                await session.rollback()
                raise
    except Exception as exc:  # pragma: no cover - the passage is still returned
        logger.warning("Narrative cache write failed for user %s kind %s: %s", user_id, kind, exc)


async def compose_json(
    *, user_id: str, prompt: str, what: str, max_tokens: int = 2048
) -> dict[str, Any] | None:
    """One generation for a narrative panel, or `None` when it produced nothing usable.

    Lives here rather than in either composer because all three panels need identical failure
    behaviour, and `resolve` depends on that behaviour being identical: it stores what it is given, so a
    caller that returned `{}` on failure instead of `None` would freeze a blank panel in place until the
    learner's figures happened to move.

    `fallback={}` rather than `None`, because passing `None` to `generate_content_json` means "raise",
    and two routes have already taken a `500` from the other reading of it.

    **`max_tokens` is a per-caller argument now, and reasoning is bounded rather than the ceiling
    lowered.** This hardcoded 8192 for every panel, justified by an 800-token budget having returned
    prose truncated mid-sentence — a real measurement, but the response overshot it tenfold and then
    applied that one number to four panels of very different sizes. Goal insight, growth drivers and
    subject insight are each 80-150 words over figures the service already computed; the reflection
    narrative scales with signals by subjects and has its own measured truncation at 4096, so it
    passes its own value.

    Lowering a ceiling saves nothing by itself: `max_output_tokens` is a ceiling and unused headroom
    is not billed. What the 8192 was compensating for is that these are thinking models and reasoning
    is drawn from the same allowance — so the fix is `THINKING_BOUNDED`, enough to plan a sentence and
    not enough to spend a budget on. 2048 then leaves ample room for output the prompts already cap at
    eight-word headings and thirty-word sentences. Phase 0 Question 1.
    """
    from src.domains.intelligence.reasoning.llm import THINKING_BOUNDED

    from .llm_resilient import generate_content_json

    try:
        reply = await generate_content_json(
            prompt,
            max_tokens=max_tokens,
            fallback={},
            user_id=user_id,
            thinking=THINKING_BOUNDED,
            # Above the quality threshold at ~770 units each. One label for all three panels: they
            # are the same operation at three prompts, and splitting the label would split the
            # threshold decision three ways for no reason.
            operation="narrative_panel",
        )
    except Exception as exc:
        logger.warning("Narrative generation failed for %s (%s): %s", user_id, what, exc)
        return None
    if not isinstance(reply, dict) or not reply:
        logger.info("Narrative generation returned nothing usable for %s (%s)", user_id, what)
        return None
    return reply
