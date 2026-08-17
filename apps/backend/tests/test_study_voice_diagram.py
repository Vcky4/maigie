"""Drawing a diagram on request, and failing usefully when the model will not.

Written after this route answered a **`500` with a stack trace** in development. The cause was the documented
`fallback=None` trap on `generate_content_json`: `None` means "no fallback — raise", not "return None", so an
empty model reply escaped as a raw `JSONDecodeError` out of the request. That is the third time this exact
ambiguity has produced a `500` in this programme — the lesson-generation and outline routes were fixed for it
earlier and this call site was missed — which is reason enough to pin it here rather than rely on remembering.

The route charges 80 credits, so what happens on a bad reply is not cosmetic: the learner must not be billed
for a diagram that was never drawn, and must not be shown a server error for something they cannot act on.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.domains.study_voice import diagram
from src.shared.exceptions import ValidationError


@pytest.fixture
def world(monkeypatch):
    world = SimpleNamespace(
        response={"mermaid": "flowchart TD\n  A-->B", "display_math": "", "caption": "How it flows"},
        raises=None,
        max_tokens=None,
        fallback="<unset>",
    )

    topic = SimpleNamespace(id="topic-1", title="Induction")
    course = SimpleNamespace(id="course-1", title="Discrete maths", user_id="user-1")

    async def check_topic_ownership(topic_id, user_id):
        return topic, SimpleNamespace(id="module-1"), course

    async def list_notes(user_id, where=None, take=None):
        return [], 0

    async def generate_content_json(prompt, **kwargs):
        world.max_tokens = kwargs.get("max_tokens")
        world.fallback = kwargs.get("fallback", "<unset>")
        if world.raises:
            raise world.raises
        return world.response

    monkeypatch.setattr(diagram, "check_topic_ownership", check_topic_ownership)
    monkeypatch.setattr(diagram.personal_learning_repo, "list_notes", list_notes)
    monkeypatch.setattr(diagram, "generate_content_json", generate_content_json)
    return world


async def test_a_diagram_comes_back(world):
    result = await diagram.generate_for_topic("user-1", topic_id="topic-1")

    assert result["mermaid"] == "flowchart TD\n  A-->B"
    assert result["caption"] == "How it flows"


async def test_a_fallback_is_passed_so_a_bad_reply_cannot_escape_as_a_parse_error(world):
    """The fix for the `500`, asserted at the call site rather than through its effect.

    `generate_content_json` raises when `fallback` is `None` — which is the default, and what this call site
    used to get by omission. Passing a dict is what turns an unusable reply into the actionable error below
    instead of a `JSONDecodeError` leaving the request.
    """
    await diagram.generate_for_topic("user-1", topic_id="topic-1")

    assert world.fallback == {}, "generate_content_json must be given a fallback, or it re-raises"


async def test_an_empty_reply_is_an_actionable_error_not_a_crash(world):
    """What the learner actually hit: the model returned nothing.

    With the fallback in place this arrives as `{}`, which has no diagram in it, so the service raises a
    `ValidationError` the route turns into a `502`. The route test below covers that half.
    """
    world.response = {}

    with pytest.raises(ValidationError):
        await diagram.generate_for_topic("user-1", topic_id="topic-1")


async def test_a_reply_of_the_wrong_shape_is_refused(world):
    """A bare string or a list is not a diagram, and `.get` on it would be an `AttributeError`."""
    world.response = ["flowchart TD"]

    with pytest.raises(ValidationError):
        await diagram.generate_for_topic("user-1", topic_id="topic-1")


async def test_blank_strings_count_as_no_diagram(world):
    """The model fills the keys it was asked for even when it has nothing to put in them.

    Whitespace in both fields would otherwise be stored and rendered as an empty bordered panel, which reads
    as a broken feature rather than a refusal.
    """
    world.response = {"mermaid": "   ", "display_math": "\n", "caption": "nothing"}

    with pytest.raises(ValidationError):
        await diagram.generate_for_topic("user-1", topic_id="topic-1")


async def test_maths_alone_is_a_valid_answer(world):
    """Some things are an equation rather than a picture."""
    world.response = {"mermaid": "", "display_math": "O(V + E)", "caption": ""}

    result = await diagram.generate_for_topic("user-1", topic_id="topic-1")
    assert result["display_math"] == "O(V + E)"
    assert result["mermaid"] == ""


async def test_the_token_budget_leaves_room_for_the_whole_reply(world):
    """Raised from 1200.

    The reply carries up to thirty lines of mermaid, a LaTeX equation and a caption, JSON-escaped. Sizing a
    generation for the happy case and truncating on a real one has already happened twice here — the lesson
    route went 4096 → 8192 for the same reason — so the figure is pinned rather than left to drift back.
    """
    await diagram.generate_for_topic("user-1", topic_id="topic-1")
    assert world.max_tokens >= 2048


async def test_ownership_is_checked_before_anything_is_generated(world, monkeypatch):
    """A topic that is not the learner's must not reach the model, let alone their notes.

    `check_topic_ownership` raises `NotFoundError` or `ForbiddenError`, and neither is caught here — the route
    lets them become the same `404`/`403` every other topic read answers.
    """
    from src.shared.exceptions import ForbiddenError

    async def refuse(topic_id, user_id):
        raise ForbiddenError("You do not own this topic")

    monkeypatch.setattr(diagram, "check_topic_ownership", refuse)

    with pytest.raises(ForbiddenError):
        await diagram.generate_for_topic("user-1", topic_id="topic-1")

    assert world.max_tokens is None, "nothing should have been generated"
