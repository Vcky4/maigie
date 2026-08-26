"""Helpers the chat WebSocket handler depends on. **Most of these are still unimplemented.**

This module, `chat_greeting` and `component_response` were all created as stubs during the domain
refactor, each with a docstring reading "implementation pending migration", and the migration never
happened. Between them they supply the handler with circle-group lookup, room membership, reply
previews, role mapping, topic resources, suggestion splitting, greeting context and both component
formatters — so a great deal of what the chat pipeline appears to do is a `pass` or a `return None`.

That is the correction to the Ask Maigie plan's §2, which read this pipeline as complete and blocked only
by an unmounted router. It is not complete. It is an orchestrator over code that was never brought
across.

**What changed here, and what deliberately did not.** The stubs stay stubs — implementing circle rooms
is out of Ask Maigie's scope (plan §4.2) and inventing a suggestion-splitting heuristic would be
inventing product behaviour. What is fixed is the three ways they were actively harmful rather than
merely absent:

- `_extract_suggestion` was `async` and returned `None`, while the handler called it *without* `await`
  and unpacked the result into two names. Any turn that produced components raised
  ``TypeError: cannot unpack non-iterable coroutine object``. It is now a plain function returning the
  answer whole.
- `_serialize_reply_preview` returned `{}`, so every message claimed a reply preview that had no fields
  in it. It now returns `None` when there is nothing to preview, and a real preview when there is.
- `_map_db_role_to_client` returned the database's `"ASSISTANT"` unchanged, where both clients switch on
  `"assistant"`.

`_get_circle_group_for_session` and `_is_circle_member` are left returning `None` and `False`. Read the
note on `_is_circle_member` before implementing either.
"""

from __future__ import annotations

import re
from typing import Any

MAIGIE_MENTION_PATTERN = re.compile(r"@maigie\b", re.IGNORECASE)


async def _attach_topic_resources_context(*args, **kwargs) -> None:
    """Attach the resources saved against a topic to the prompt context.

    **Unimplemented.** Consequence: when a learner asks about a topic, the resources they saved to it do
    not reach the model, so Ask Maigie cannot refer to them. Fails silently by design — the handler
    treats context enrichment as best-effort — which is why nobody noticed.
    """
    return None


def _extract_suggestion(text: str | None) -> tuple[str, str | None]:
    """Split a trailing suggestion off an answer, so the UI can render it after components.

    **The split is unimplemented and this returns the answer whole**, with no suggestion. That is the
    deliberate choice rather than a guess: splitting means deciding by heuristic which trailing sentence
    of the model's prose is a "suggestion", and getting that wrong silently removes text the model
    produced from the answer the learner reads. Rendering a suggestion before the components instead of
    after is a cosmetic ordering issue. Losing a sentence is not.

    Was `async` and returned `None`. The handler unpacks the result into two names and did not `await`
    it, so every turn carrying components raised `TypeError: cannot unpack non-iterable coroutine
    object` — the whole tool-using path. Synchronous now, because there is nothing here to await.
    """
    return (text or "", None)


async def _get_circle_group_for_session(*args, **kwargs) -> Any:
    """The space-room chat group a session belongs to, or `None` for a personal conversation.

    **Unimplemented, returns `None` unconditionally.** So `is_circle_session` is always false and every
    space-room branch in the handler is unreachable: the `circle_message` broadcast, the room's knowledge
    context, the `@maigie` mention gate, the per-space tier resolution and all nine `send_room_json`
    calls. `subscribe` always answers "Unable to join this space room", because this returns `None`.

    Space rooms are out of scope for the Ask Maigie plan (§4.2). Recorded rather than fixed so that the
    plan's instruction not to *break* space rooms is understood correctly: there is nothing working to
    break.
    """
    return None


async def _is_circle_member(*args, **kwargs) -> bool:
    """Whether a user may participate in a space room.

    **Unimplemented, returns `False`** — deny by default, which is the safe direction.

    **Read this before implementing it.** The handler called this without `await` at one point, and
    `not <coroutine>` is always `False`, so the membership check passed for everyone. It was unreachable
    only because `_get_circle_group_for_session` returns `None` first. The missing `await` is now added,
    so a stub returning `False` denies as intended — but if you implement this function, verify the two
    call sites reject a non-member, because the guard has never once run.
    """
    return False


def _map_db_role_to_client(role: str) -> str:
    """Map a stored role onto the spelling the clients switch on.

    The column holds `USER` / `ASSISTANT` / `SYSTEM`; both clients compare against `"user"` and
    `"assistant"`. This returned the stored value unchanged, so a reply preview's role never matched and
    every previewed message was rendered with the fallback styling.
    """
    return (role or "").lower()


def _serialize_reply_preview(
    message: Any = None, *, fallback_user_name: str | None = None
) -> dict | None:
    """A short preview of the message being replied to, or `None` when there is no reply.

    Returned `{}` before, for every message whether or not it was a reply — so the clients received a
    `replyToMessage` object with no fields, which is neither a reply nor an absent one. `None` says
    "this is not a reply", which is what the clients can act on.

    Kept minimal on purpose: id, role and a truncated content excerpt are what a preview needs, and the
    author's name where it is known.
    """
    if message is None:
        return None

    content = getattr(message, "content", "") or ""
    author = getattr(message, "user", None)
    return {
        "id": getattr(message, "id", None),
        "role": _map_db_role_to_client(str(getattr(message, "role", ""))),
        "content": content[:200],
        "userId": getattr(message, "user_id", None),
        "userName": getattr(author, "name", None) if author is not None else fallback_user_name,
    }


def _strip_maigie_mention(text: str) -> str:
    """Strip an `@maigie` mention from a room message before it reaches the model."""
    return MAIGIE_MENTION_PATTERN.sub("", text).strip()
