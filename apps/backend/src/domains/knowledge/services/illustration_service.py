"""Diagrams and equations kept from a study session.

Two producers, one store. The voice tutor pushes a visual by calling `study_show_visual` mid-conversation,
and the learner can ask for one directly through `POST /gemini-live/study/diagram`. Before this module both
handed their result to the browser and kept nothing, so a diagram cost credits, existed for as long as the
tab did, and could not be shown on the lesson it explained.

`study_voice` writes through here rather than reaching for the repository, so the ownership check and the
"a row must contain something drawable" rule are applied once, on both paths, by the domain that owns the
table.

## Failing to save never fails the thing being saved

`record` swallows its own errors and returns `None`. That is unusual in this codebase and deliberate here:
its callers are a live voice relay and a route that has already spent a model call and charged the learner.
A diagram that reached the screen and failed to persist is a diagram the learner got; raising would replace
a working visual with an error, and in the tool path it would break the conversation mid-turn. The failure
is logged with the topic id, and the learner keeps what they paid for.

That is the opposite of the rule everywhere else in this programme, so it is worth being precise about why
it does not contradict it: nothing is being *accepted and discarded*. The value is delivered to the caller
in full either way. What can be lost is the copy kept for later, and losing that is strictly better than
losing both.
"""

from __future__ import annotations

import logging

from src.domains.knowledge.repository import knowledge_repo
from src.domains.knowledge.services.course_service import check_topic_ownership

logger = logging.getLogger(__name__)

#: What a learner can accumulate on one topic before the list is trimmed on read.
MAX_PER_TOPIC = 50

#: `tutor` — the model chose to show it. `learner` — it was asked for.
SOURCE_TUTOR = "tutor"
SOURCE_LEARNER = "learner"


async def record(
    user_id: str,
    *,
    topic_id: str,
    mermaid: str | None,
    display_math: str | None,
    caption: str | None,
    source: str = SOURCE_TUTOR,
) -> str | None:
    """Keep one visual, returning its id, or `None` if it could not be kept.

    Ownership is not re-checked. Both callers have already resolved the topic through
    `check_topic_ownership` — the diagram route to generate from it, the tool dispatch to build the session
    brief — so a second check would be a second query for an answer already held. `record_checked` is the
    entry point for a caller that has not.
    """
    mermaid_clean = (mermaid or "").strip()
    math_clean = (display_math or "").strip()
    if not mermaid_clean and not math_clean:
        # Refused rather than stored empty. A row with neither renders as a blank panel, which reads as a
        # broken feature; the database constraint says the same thing, and this says it with a log line
        # naming the topic instead of an IntegrityError.
        logger.warning("Refusing to store an empty illustration for topic %s", topic_id)
        return None

    try:
        row = await knowledge_repo.create_topic_illustration(
            {
                "topicId": topic_id,
                "userId": user_id,
                "mermaid": mermaid_clean or None,
                "displayMath": math_clean or None,
                "caption": (caption or "").strip() or None,
                "source": source if source in (SOURCE_TUTOR, SOURCE_LEARNER) else SOURCE_TUTOR,
            }
        )
    except Exception as error:
        # See the module docstring: the visual has already reached the learner and been paid for.
        logger.warning("Could not store an illustration for topic %s: %s", topic_id, error)
        return None

    return row.id


async def record_checked(
    user_id: str,
    *,
    topic_id: str,
    mermaid: str | None,
    display_math: str | None,
    caption: str | None,
    source: str = SOURCE_TUTOR,
) -> str | None:
    """`record`, for a caller that has not established the topic is this learner's.

    Raises `NotFoundError` / `ForbiddenError` from `check_topic_ownership`, which is the one failure that
    must not be swallowed — writing a row against another learner's topic is worse than losing it.
    """
    await check_topic_ownership(topic_id, user_id)
    return await record(
        user_id,
        topic_id=topic_id,
        mermaid=mermaid,
        display_math=display_math,
        caption=caption,
        source=source,
    )


async def list_for_topic(user_id: str, *, topic_id: str) -> list:
    """This learner's visuals for one topic, newest first.

    Ownership checked, because the caller is a route taking a topic id from the URL. A topic that is not
    theirs answers `404` through `check_topic_ownership`, the same as every other topic read.
    """
    await check_topic_ownership(topic_id, user_id)
    return await knowledge_repo.list_topic_illustrations(topic_id, user_id, take=MAX_PER_TOPIC)


async def delete(user_id: str, *, illustration_id: str) -> bool:
    """Remove one. Returns False when it is not this learner's, or does not exist.

    Deliberately does not resolve the topic first. The row carries its own `userId`, so scoping the delete
    to it is one statement — and "not yours" and "not there" answer identically, which is what keeps an id
    from being probed.
    """
    return await knowledge_repo.delete_topic_illustration(illustration_id, user_id)
