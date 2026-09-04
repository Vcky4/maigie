"""Optional LLM copy for the notification digest — bounded, default-off, deterministic-first.

Phase 6, Level 2 ("LLM-assisted planning and copy"). The digest already has a deterministic title
and body (`digest._TITLES` and `render_digest_body`); this module can, when explicitly enabled,
replace them with a friendlier one-paragraph summary the model writes from the same items. It is
built to the plan's hard rules for the LLM layer, and every one of them is structural here rather
than a matter of trust:

  - **Off by default.** `NOTIFICATION_DIGEST_LLM_ENABLED` is `False`, so nothing calls a model until a
    deployment opts in, and even then only for the cohort the rollout gate admits.
  - **Shadow by default.** When enabled, `NOTIFICATION_DIGEST_LLM_SHADOW_ONLY` (default `True`) means a
    proposal is generated, validated and recorded, but the learner still receives the deterministic
    copy. This is the same discipline as `decision.py`'s shadow mode: a model is measured against live
    traffic at zero risk until a human switches shadow off.
  - **Deterministic fallback, always.** OFF, disabled cohort, timeout, provider error, malformed JSON,
    a proposal that fails validation or the content-safety pass — every one of these returns the exact
    deterministic title and body the caller computed. This function never raises: instrumenting the
    digest must not be able to break the digest.
  - **Structured output, validated.** The model is asked for a small JSON object and it is parsed into
    a Pydantic model with length bounds; anything else is rejected, not coerced.
  - **Content safety.** Generated copy is a notification a learner did not write, so it is sanitised to
    plain text and refused if it carries a link, control characters, or markup — a digest summary has
    no business containing any of those, and refusing is free because the deterministic copy is right
    there.
  - **Bounded cost and latency.** Small `max_tokens`, a short per-call timeout, and the whole call runs
    inside `proactive_scope()` so its spend lands in the learner's proactive sub-budget rather than the
    interactive one.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from pydantic import BaseModel, Field, ValidationError

from src.config import Settings, get_settings

from .feature_flags import capability_enabled_for

logger = logging.getLogger(__name__)

#: The model is asked to summarise at most this many items. A digest with more is unusual, and a
#: prompt that grows without bound is a cost and latency risk for no benefit — the tail items add
#: little to a one-paragraph summary and the deterministic body still lists every one of them.
_MAX_ITEMS_IN_PROMPT = 20

#: Copy bounds. A digest title is a heading and a body is a short paragraph; anything longer is either
#: the model ignoring the brief or padding, and a notification surface cannot show it anyway.
_MAX_TITLE_LEN = 80
_MAX_BODY_LEN = 600

#: Bounded so a slow provider cannot hold the digest task, and small because the output is short.
_TIMEOUT_S = 20.0
_MAX_TOKENS = 400

#: A generated digest summary is descriptive prose. A URL in it would be either a hallucination or an
#: injection surfaced from an item body, and either way a notification the learner did not write has
#: no reason to carry a link. Cheaper and safer to refuse the whole proposal than to strip it.
_URL_RE = re.compile(r"https?://|www\.", re.IGNORECASE)

#: Control characters other than the newlines and tabs plain text legitimately uses.
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

#: Any HTML-ish tag. The digest body is plain text read by both the notification centre and the email
#: template, so a `<...>` is markup that does not belong and is treated as a failed proposal.
_TAG_RE = re.compile(r"<[^>]+>")


class DigestCopyDraft(BaseModel):
    """The exact shape the model is required to return.

    Bounds are enforced by the schema so an over-long or empty field is a validation error, not
    something a later `if` has to remember to check. `str.strip()`-ed at the boundary via the
    validators below so whitespace-only fields do not pass the min-length check.
    """

    title: str = Field(min_length=1, max_length=_MAX_TITLE_LEN)
    body: str = Field(min_length=1, max_length=_MAX_BODY_LEN)


#: Outcome codes, recorded for the audit and returned to the caller for its counters. They name why
#: the learner got what they got, which is the whole point of running this in shadow first.
STATUS_OFF = "OFF"  # capability disabled for this learner — no model was called
STATUS_SHADOW = "SHADOW"  # a valid proposal was made and recorded, deterministic copy still sent
STATUS_APPLIED = "APPLIED"  # shadow off and a valid proposal — the learner got the LLM copy
STATUS_FALLBACK = "FALLBACK"  # a model was called but its output was unusable; deterministic sent


@dataclass(frozen=True)
class DigestCopyOutcome:
    """What the digest should actually send, and why.

    `title`/`body` are always safe to use — they are the deterministic values unless the model was
    both enabled-and-live and produced valid, safe copy. `proposed` records whether a valid proposal
    existed at all, so a shadow run can report how often the model *would* have been used.
    """

    title: str
    body: str
    status: str
    proposed: bool


def _build_prompt(*, settings_category: str, period: str, items: list[tuple[str, str]]) -> str:
    """Ask for a small JSON object summarising the digest, and only that.

    The items are the same `(title, body)` pairs the deterministic renderer uses. The instruction is
    deliberately narrow — no links, no invented facts, plain text — because the validation and the
    content-safety pass will reject anything else and a tighter prompt wastes fewer calls doing so.
    """

    horizon = "day" if period == "DAILY" else "week"
    lines = []
    for title, body in items[:_MAX_ITEMS_IN_PROMPT]:
        first = (body or "").strip().splitlines()
        summary = first[0].strip() if first else ""
        lines.append(f"- {title}" + (f": {summary}" if summary and summary != title else ""))
    listing = "\n".join(lines)

    return (
        "You write a short, warm summary of a learner's notifications for the past "
        f"{horizon}. You are given the items below. Summarise them into one encouraging "
        "paragraph a learner will read in their notification centre.\n\n"
        "Rules:\n"
        "- Use only the information in the items. Do not invent progress, numbers, or events.\n"
        "- Plain text only: no markdown, no HTML, no links or URLs, no emoji.\n"
        f"- Title at most {_MAX_TITLE_LEN} characters; body at most {_MAX_BODY_LEN} characters.\n"
        "- Return ONLY a JSON object, no prose and no code fences, shaped exactly:\n"
        '{\n  "title": "short heading",\n  "body": "one short paragraph"\n}\n\n'
        f"Items ({settings_category}, past {horizon}):\n{listing}\n"
    )


def _sanitise_plain_text(value: str) -> str | None:
    """Return `value` as safe plain text, or `None` if it must be refused.

    Refusal is not an error — it routes the caller to the deterministic copy, which is always
    correct. Refused: anything with a link, an HTML-ish tag, or a control character. Otherwise the
    text is stripped and internal runs of whitespace are collapsed to single spaces per line, since a
    notification body is not laid out and a model's stray indentation should not survive into it.
    """

    if _URL_RE.search(value) or _TAG_RE.search(value) or _CONTROL_RE.search(value):
        return None
    collapsed = "\n".join(" ".join(line.split()) for line in value.splitlines())
    collapsed = collapsed.strip()
    return collapsed or None


async def _generate_draft(
    *, user_id: str, settings_category: str, period: str, items: list[tuple[str, str]]
) -> DigestCopyDraft | None:
    """Call the model and return a validated, sanitised draft, or `None` on any failure.

    Local import of the LLM client keeps this module importable without the personal-learning stack,
    and matches how the rest of notifications reaches across domains. `fallback=None` asks the client
    to raise rather than hand back a bogus object, and the broad `except` below turns any such raise —
    provider down, budget refusal, malformed JSON — into a `None`, which the caller reads as "use the
    deterministic copy".
    """

    from src.domains.personal_learning.services.llm_resilient import (
        generate_content_json,
        proactive_scope,
    )

    prompt = _build_prompt(settings_category=settings_category, period=period, items=items)
    try:
        with proactive_scope():
            raw = await generate_content_json(
                prompt,
                max_tokens=_MAX_TOKENS,
                temperature=0.4,
                timeout_s=_TIMEOUT_S,
                fallback=None,
                user_id=user_id,
                operation="notification_digest",
            )
    except Exception:
        # Provider outage, timeout, a budget refusal, or unparseable output. None of these is the
        # learner's problem and none may break the digest, so they all become "no proposal".
        logger.warning("Digest LLM copy generation failed; using deterministic copy", exc_info=True)
        return None

    if not isinstance(raw, dict):
        return None
    try:
        draft = DigestCopyDraft.model_validate(raw)
    except ValidationError:
        logger.info("Digest LLM copy failed schema validation; using deterministic copy")
        return None

    title = _sanitise_plain_text(draft.title)
    body = _sanitise_plain_text(draft.body)
    if title is None or body is None:
        logger.info("Digest LLM copy failed content-safety check; using deterministic copy")
        return None
    # Re-validate after sanitising, because collapsing whitespace can change a length.
    try:
        return DigestCopyDraft(title=title, body=body)
    except ValidationError:
        return None


async def resolve_digest_copy(
    *,
    user_id: str,
    settings_category: str,
    period: str,
    items: list[tuple[str, str]],
    deterministic_title: str,
    deterministic_body: str,
    settings: Settings | None = None,
) -> DigestCopyOutcome:
    """Decide the copy the digest should send, falling back to the deterministic values.

    The default and the failure path are the same safe thing — the deterministic title and body the
    caller already computed — so a caller can use the result unconditionally. A model is called only
    when the capability is enabled for this learner; the learner receives its output only when shadow
    mode is also off and the output passed validation and the safety pass.
    """

    config = settings or get_settings()

    if not capability_enabled_for("DIGEST_LLM", user_id, settings=config):
        return DigestCopyOutcome(
            title=deterministic_title,
            body=deterministic_body,
            status=STATUS_OFF,
            proposed=False,
        )

    draft = await _generate_draft(
        user_id=user_id,
        settings_category=settings_category,
        period=period,
        items=items,
    )
    if draft is None:
        return DigestCopyOutcome(
            title=deterministic_title,
            body=deterministic_body,
            status=STATUS_FALLBACK,
            proposed=False,
        )

    if config.NOTIFICATION_DIGEST_LLM_SHADOW_ONLY:
        # A usable proposal exists, but shadow mode means the learner still gets the deterministic
        # copy. Recorded so a rollout can see how often the model succeeds before it is trusted.
        logger.info(
            "Digest LLM copy proposed (shadow)",
            extra={"settings_category": settings_category, "period": period},
        )
        return DigestCopyOutcome(
            title=deterministic_title,
            body=deterministic_body,
            status=STATUS_SHADOW,
            proposed=True,
        )

    return DigestCopyOutcome(
        title=draft.title,
        body=draft.body,
        status=STATUS_APPLIED,
        proposed=True,
    )
