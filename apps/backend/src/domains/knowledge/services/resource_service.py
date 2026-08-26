"""
Resource management — create, list, interact, delete, recommend.
"""

import logging
from typing import Any

from src.domains.identity.db_models import User
from src.shared.exceptions import NotFoundError

from ..events import emit_resource_added
from ..models import ResourceType
from ..repository import knowledge_repo

logger = logging.getLogger(__name__)


async def list_resources(
    *,
    user_id: str,
    space_id: str | None = None,
    topic_id: str | None = None,
    course_id: str | None = None,
    resource_type: str | None = None,
    is_recommended: bool | None = None,
    search: str | None = None,
    page: int = 1,
    page_size: int = 20,
    sort_by: str = "createdAt",
    sort_order: str = "desc",
) -> dict[str, Any]:
    """List resources with pagination and filters."""
    where: dict[str, Any] = {"userId": user_id}

    if space_id:
        where["spaceId"] = space_id
    else:
        where["spaceId"] = None

    if topic_id:
        where["topicId"] = topic_id
    if course_id:
        where["courseId"] = course_id
    if resource_type:
        where["type"] = resource_type
    # `is not None` rather than truthiness, so `isRecommended=false` filters to
    # learner-saved resources instead of being read as "no filter".
    if is_recommended is not None:
        where["isRecommended"] = is_recommended
    if search:
        where["OR"] = [
            {"title": {"contains": search, "mode": "insensitive"}},
            {"description": {"contains": search, "mode": "insensitive"}},
        ]

    skip = (page - 1) * page_size
    resources, total = await knowledge_repo.list_resources(
        where=where, skip=skip, take=page_size, order={sort_by: sort_order}
    )

    # The canonical envelope, and `pages` rather than `hasMore` — which answers strictly more, since
    # "is there another page" is `page < pages` while the reverse cannot be recovered. This was the third
    # pagination shape in the codebase for a list paginated exactly like notes, documents and saved
    # resources; `ResourceListResponse` is deleted rather than patched.
    #
    # Rows are returned as-is. `ResourceResponse` validates off the ORM row, which is what let the
    # hand-written `_format_resource` mapper go: it built a camelCase dict from snake_case reads and, when
    # the ORM moved off Prisma, silently reported `bookmarkCount: 0` for every resource because its
    # `getattr` defaults absorbed the renames.
    return {
        "items": resources,
        "total": total,
        "page": page,
        "pageSize": page_size,
        "pages": max(1, (total + page_size - 1) // page_size),
    }


async def create_resource(*, user: User, data: dict[str, Any]) -> Any:
    """Create a new resource and index it."""
    resource_data: dict[str, Any] = {
        "userId": user.id,
        "title": data["title"],
        "url": data["url"],
        "type": data.get("type", "OTHER"),
        "isRecommended": data.get("isRecommended", False),
    }
    if data.get("description"):
        resource_data["description"] = data["description"]
    if data.get("metadata"):
        resource_data["metadata"] = data["metadata"]
    # `is not None`, not truthiness. A score of `0.0` is a real value — "recommended, and rated
    # as weakly as the scale allows" — and the truthiness test dropped it, storing NULL instead
    # and making the weakest recommendation indistinguishable from an unscored one.
    if data.get("recommendationScore") is not None:
        resource_data["recommendationScore"] = data["recommendationScore"]
    if data.get("recommendationSource"):
        resource_data["recommendationSource"] = data["recommendationSource"]
    # Copied for the same reason the repository mapper now handles it: three of the four
    # recommendation fields were settable here and this one was not, so a caller could describe
    # how strongly a resource was recommended but not why.
    if data.get("recommendationReason"):
        resource_data["recommendationReason"] = data["recommendationReason"]
    if data.get("courseId"):
        resource_data["courseId"] = data["courseId"]
    if data.get("topicId"):
        resource_data["topicId"] = data["topicId"]
    if data.get("spaceId"):
        resource_data["spaceId"] = data["spaceId"]

    resource = await knowledge_repo.create_resource(resource_data)
    await emit_resource_added(user.id, resource.id, data.get("courseId"))

    # The row, not a hand-picked subset of it. This used to return six of the nineteen fields the row has —
    # no `metadata`, no `spaceId`, no `courseId`/`topicId`, no `updatedAt` — as an untyped dict, which is why
    # the route carried no `response_model`. The web client types the result as a full resource and pushes it
    # straight into its list, so every omitted field was a hole a caller had to work around or refetch for.
    return resource


async def record_interaction(*, user_id: str, resource_id: str, interaction_type: str) -> None:
    """Record a user interaction with a resource."""
    resource = await knowledge_repo.find_resource(resource_id, user_id)
    if not resource:
        raise NotFoundError("Resource", resource_id)

    # Incremented in SQL through a dedicated repository method. This used to build
    # `{"clickCount": {"increment": 1}}` — Prisma's dialect — and hand it to
    # `update_resource`, which passes its dict into `values(**data)`; binding a dict to an
    # integer column raised, so no interaction has ever been recorded and both counters
    # are still zero for every resource. `clickCount` is also the *column* name, not the
    # mapped attribute (`click_count`), which is the second half of why it could not work.
    if interaction_type == "RESOURCE_CLICK":
        await knowledge_repo.increment_resource_counter(
            resource_id, column="clickCount", touch_last_accessed=True
        )
    elif interaction_type == "RESOURCE_BOOKMARK":
        await knowledge_repo.increment_resource_counter(resource_id, column="bookmarkCount")


async def delete_resource(*, user_id: str, resource_id: str) -> None:
    """Delete a resource."""
    resource = await knowledge_repo.find_resource(resource_id, user_id)
    if not resource:
        raise NotFoundError("Resource", resource_id)
    await knowledge_repo.delete_resource(resource_id)


#: Resource kinds a recommendation may claim. Anything else becomes ``OTHER`` rather than being
#: written through: ``type`` has no constraint at the database level, so a typo from the model
#: would become a stored value no filter chip can ever match.
#:
#: Derived from the published enum rather than restated. A hand-written copy of it here had
#: already drifted — it omitted ``PODCAST``, which the enum accepts and the column stores, so a
#: correctly identified podcast was silently downgraded to ``OTHER``. A duplicated allowlist only
#: has to be updated once to be wrong.
_RECOMMENDABLE_TYPES = {member.value for member in ResourceType}


def _parse_recommendation_payload(raw: str) -> list[dict[str, Any]]:
    """Pull the JSON array out of a grounded reply.

    Parsed out of prose because grounding and structured output are mutually exclusive in
    this SDK — a response schema cannot be attached to a request that carries tools. Only
    dicts survive, so a model that returns an array of strings yields nothing rather than
    a list of items with no fields.
    """
    import json
    import re

    match = re.search(r"\[.*\]", raw, re.DOTALL)
    if not match:
        return []
    try:
        parsed = json.loads(match.group(0))
    except (json.JSONDecodeError, ValueError):
        return []
    if not isinstance(parsed, list):
        return []
    return [item for item in parsed if isinstance(item, dict)]


def _coerce_score(value: Any) -> float:
    """A recommendation score clamped to 0-1.

    The model returns whatever it likes here — `0.9`, `90`, `"high"`. Unreadable values
    become 0.5, which is the honest reading of "recommended, with nothing useful said about
    how strongly".
    """
    try:
        score = float(value)
    except (TypeError, ValueError):
        return 0.5
    if score > 1.0:
        # A percentage rather than a fraction, which the model does often enough to handle.
        score = score / 100 if score <= 100 else 1.0
    return max(0.0, min(1.0, score))


async def recommend_resources(
    *, user_id: str, query: str, limit: int = 5, context: dict | None = None
) -> dict[str, Any]:
    """Find real resources for a query and add them to the learner's library.

    This was a single ungrounded ``generate_content`` call whose output was returned and
    then thrown away. Two things were wrong, and they compounded:

    1. **It never looked anything up.** The docstring said "via RAG"; there was no retrieval
       and no search anywhere in the backend. It asked the model to produce URLs from its
       weights, which yields plausible URLs as readily as real ones, so a learner following
       one often landed on a 404.
    2. **Nothing was persisted.** ``Resource.isRecommended``, ``recommendationScore``,
       ``recommendationSource`` and ``recommendationReason`` exist for precisely this and had
       never been written by anything — one of them was not even mapped by
       ``create_resource``. So there was no recommended-resources surface to build, and every
       suggestion was lost the moment the screen changed.

    Now: search-grounded generation, every URL resolved and checked, and the survivors stored
    as real rows the learner can open, filter and keep.

    **Two model calls, and they cannot be one.** The search happens in a request with no output
    format demanded, because asking for search and "return ONLY a JSON array" in the same breath
    makes Gemini skip the search — measured, with the figures in the comment at the call site. A
    second request, carrying no tools, transcribes the grounded prose into JSON, which is allowed to
    have an output contract precisely *because* it has no tools. The first version of this rewrite did
    both in one request and was therefore never grounded at all: it had reintroduced, in its own
    prompt, the defect it existed to remove.

    **Checking is what makes storing safe.** An unverified URL in a row is worse than one in
    a response: a row is permanent, accumulates counters, and can be filed into a collection.
    So a recommendation earns its row by resolving, and the rest are counted and dropped.

    Returns rows — newly created and pre-existing together — plus ``grounded`` (whether
    search actually ran, since the tool is a request and not a guarantee) and ``discarded``
    (how many were dropped as unreachable). ``personalized`` is gone: it was hardcoded
    ``True`` including when the reply could not be parsed at all, so it never carried
    information.
    """
    from src.domains.intelligence.memory.memory_service import get_memory_context
    from src.domains.intelligence.reasoning.llm import (
        generate_content,
        generate_grounded_content,
    )

    from .url_validator import check_urls

    # `get_memory_context` returns a formatted prompt block, not a mapping. This used to call
    # `.update()` on it, which raised `AttributeError` on every request that supplied `context` — the
    # annotation on the memory service claimed `dict[str, Any]` and this call site believed it. The
    # annotation is now correct and the extra context is appended as what it is: more prose for the
    # same block.
    user_context = await get_memory_context(user_id)
    if context:
        extra = "\n".join(f"- {k}: {v}" for k, v in context.items())
        user_context = f"{user_context}\n{extra}" if user_context else extra

    # ---- Step 1: search. Described in prose, with no output format demanded. ----
    #
    # **The formatting instruction has to be absent, and this is the whole reason there are two
    # calls.** Asking for search and "return ONLY a JSON array" in one request makes Gemini skip the
    # search entirely: measured on `gemini-3.5-flash`, the same prompt with the JSON demand came back
    # with no grounding metadata and no search queries, while without it the model issued twelve
    # searches and returned twenty-three grounding chunks. The model appears to treat a strict output
    # contract as incompatible with calling a tool, and answers from its weights instead — which is
    # precisely the failure this endpoint was rewritten to eliminate. The rewrite had reintroduced it
    # in its own prompt.
    search_prompt = (
        f"Search the web for {limit} genuinely useful learning resources about: {query}\n\n"
        f"Learner context — use it to pitch the level, do not treat it as the query: "
        f"{str(user_context)[:500]}\n\n"
        "For each resource give its title, its direct URL, what it covers, and one sentence on why "
        "it helps with this specific topic.\n\n"
        "Only include resources you actually found in the search results — do not construct, guess "
        "or complete a URL. Give the direct page URL, never a search results page. Prefer primary "
        "sources, official documentation, university material and established educational sites."
    )

    # The token budget is left at the function's default. This used to pass `max_tokens=2000`, which
    # looked ample for eight resources and was not: the model is a thinking model and reasoning
    # tokens come out of the same allowance. A measured run spent 1,067 of 2,000 on thought and
    # returned a reply cut off mid-string after 364 characters — the JSON never closed, the parse
    # produced nothing, and the learner was told no resources existed for a perfectly good query.
    found = await generate_grounded_content(search_prompt, temperature=0.3)
    if not found.text.strip():
        logger.warning("Recommendation search for %r came back empty.", query)
        return {"recommendations": [], "query": query, "grounded": False, "discarded": 0}

    # ---- Step 2: format. No tools attached, so an output contract is allowed again. ----
    #
    # Transcription, not generation: everything it may say is already in the text above. Structured
    # output is available here for exactly the reason it is not available in step 1 — this request
    # carries no tools.
    format_prompt = (
        "Convert the resource list below into JSON. Copy the titles, URLs and descriptions "
        "verbatim; do not add resources, do not invent or complete URLs, and drop any entry that "
        "has no URL.\n\n"
        'Return ONLY a JSON array of objects with keys: "title", "url", "description", "type", '
        '"relevance", "score".\n'
        f"- type: one of {sorted(_RECOMMENDABLE_TYPES)}, chosen from what the resource plainly is\n"
        "- relevance: the sentence explaining why it helps, taken from the text\n"
        "- score: 0-1, how strongly the text recommends it\n\n"
        f"Resource list:\n{found.text}"
    )
    formatted = await generate_content(format_prompt, max_tokens=8192, temperature=0.0)

    candidates = _parse_recommendation_payload(formatted)[:limit]
    if not candidates:
        if found.truncated:
            # Logged apart because the two causes need different responses: rephrasing helps one and
            # only a larger budget helps the other. Reported as the same empty list to the client,
            # which is honest — nothing was found — but the server should not have to guess why.
            logger.warning(
                "Recommendation for %r produced no items because the search reply was truncated, "
                "not because nothing was found.",
                query,
            )
        else:
            logger.info("Recommendation for %r returned no parseable items.", query)
        return {
            "recommendations": [],
            "query": query,
            "grounded": found.grounded,
            "discarded": 0,
        }

    # Every proposed URL resolved and checked at once. Following redirects is required
    # rather than tidy: a grounding URI is usually a `grounding-api-redirect` indirection,
    # and storing that instead of its destination would put a Google redirect in the
    # learner's library.
    proposed = [str(item.get("url") or "") for item in candidates]
    checked = await check_urls([url for url in proposed if url])

    # Deduped against what the learner already has, so searching twice for the same thing
    # does not file the same page twice. Keyed on the *resolved* URL, because that is what
    # gets stored, and matched on URL rather than title — a title is the model's phrasing
    # and varies between runs, while the URL is the identity of the thing.
    resolved_urls = [check.resolved for check in checked.values() if check.resolved]
    existing_by_url = {
        row.url: row for row in await knowledge_repo.find_resources_by_urls(user_id, resolved_urls)
    }

    recommendations: list[Any] = []
    seen: set[str] = set()
    discarded = 0

    for item in candidates:
        proposed_url = str(item.get("url") or "")
        check = checked.get(proposed_url)
        if check is None or check.resolved is None:
            discarded += 1
            logger.info(
                "Dropped an unreachable recommendation for %r: %s (%s)",
                query,
                proposed_url or "<no url>",
                check.reason if check else "no url given",
            )
            continue

        url = check.resolved
        if url in seen:
            # Two suggestions that redirect to the same page.
            continue
        seen.add(url)

        already = existing_by_url.get(url)
        if already is not None:
            recommendations.append(already)
            continue

        raw_type = str(item.get("type") or "OTHER").upper()
        created = await knowledge_repo.create_resource(
            {
                "userId": user_id,
                "title": str(item.get("title") or "Untitled"),
                "url": url,
                "description": item.get("description"),
                "type": raw_type if raw_type in _RECOMMENDABLE_TYPES else "OTHER",
                "isRecommended": True,
                "recommendationScore": _coerce_score(item.get("score")),
                # Distinguishes a checked citation from a checked guess. The search tool is
                # a request, so an ungrounded reply can still produce URLs that happen to
                # resolve, and the row must not claim those were found by search.
                "recommendationSource": "gemini_grounded" if found.grounded else "gemini",
                "recommendationReason": item.get("relevance"),
            }
        )
        recommendations.append(created)

    if discarded:
        logger.info(
            "Recommendation for %r kept %d of %d after link checking.",
            query,
            len(recommendations),
            len(candidates),
        )

    return {
        "recommendations": recommendations,
        "query": query,
        "grounded": found.grounded,
        "discarded": discarded,
    }
