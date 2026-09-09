"""Landing drafts — the service.

Four things happen here: a draft is opened, edited, previewed, and eventually claimed by a real
account. The unusual constraint running through all of it is that the first three have **no
authenticated caller**, so every protection normally provided by the account — identity, a usage
meter, a support trail — has to be provided explicitly instead.

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from src.shared.exceptions import ConflictError, NotFoundError

from ..db_models import LandingDraft
from ..models import MAX_SUBJECTS, DraftPreviewItem
from ..repository import landing_draft_repo

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# The numbers
# ---------------------------------------------------------------------------

#: How long an unclaimed draft stays usable.
#:
#: Seven days rather than a session, because the visitor this exists for is the one who reads the
#: page on a phone, thinks about it, and signs up on a laptop two days later. Longer than a week
#: mostly stores drafts nobody will ever claim: the wizard's own conversion happens in one sitting or
#: not at all, and everything after that is a bet against the data.
DRAFT_TTL_DAYS = 7

#: Total preview generations allowed per draft, ever.
#:
#: Two: the first one, and one more for the visitor who realises they typed the wrong exam. Beyond
#: that a caller is not previewing, they are iterating against our LLM budget with no account
#: attached. The per-IP limit in the route bounds the population of drafts; this bounds each one.
MAX_GENERATES_PER_DRAFT = 2

#: The operation label for cost attribution. Also added to `llm_resilient.UNCHARGED_OPERATIONS`, not
#: because it would otherwise be charged — an anonymous call has no `user_id` to charge, so it is
#: already unmetered — but so that the exemption is a stated decision in the same list as onboarding
#: and memory, rather than a side effect of there being nobody to bill.
PREVIEW_OPERATION = "landing_preview"


# ---------------------------------------------------------------------------
# Tokens
# ---------------------------------------------------------------------------


def _new_token() -> tuple[str, str]:
    """Return `(raw_token, token_hash)`.

    `token_urlsafe(32)` is 256 bits of entropy, which matters more here than for a session cookie:
    this token is the *only* authorisation on the row, it travels in a URL query parameter, and it
    cannot be revoked by signing out. Guessing has to be hopeless because nothing else is stopping it.
    """
    raw = secrets.token_urlsafe(32)
    return raw, hash_token(raw)


def hash_token(raw: str) -> str:
    """SHA-256 of a draft token.

    Plain SHA-256 rather than a password hash, deliberately. A slow KDF defends a *low-entropy*
    secret against offline guessing; this secret is 256 random bits, so there is nothing to guess and
    the only property needed is that the stored form cannot be reversed into a usable token. Making
    it slow would just make every public read slow.
    """
    return hashlib.sha256(raw.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


def _is_live(draft: LandingDraft) -> bool:
    """Whether a draft can still be read or edited.

    Expiry is computed from `expiresAt`, not read from `status`, so a draft is unusable the moment
    its week is up whether or not the sweep has run. A visitor's access must never depend on a
    background job having fired.
    """
    return draft.status == "open" and draft.expires_at > datetime.now(UTC)


async def create_draft(*, purpose: str | None = None) -> tuple[LandingDraft, str]:
    """Open a draft and return it with its one-time token."""
    raw, token_hash = _new_token()
    draft = await landing_draft_repo.create(
        {
            "token_hash": token_hash,
            "purpose": purpose,
            "status": "open",
            "expires_at": datetime.now(UTC) + timedelta(days=DRAFT_TTL_DAYS),
        }
    )
    logger.info("Landing draft created: id=%s purpose=%s", draft.id, purpose)
    return draft, raw


async def resolve_draft(*, token: str) -> LandingDraft:
    """Find a live draft by token, or explain why there isn't one.

    An unknown token, an expired one, and an already-claimed one all raise. They raise *differently*
    so the wizard can tell a visitor whose week ran out from a visitor whose token is nonsense, but
    note that the public routes flatten unknown-and-expired into the same 404 on purpose: telling an
    unauthenticated caller "that token existed once" is an oracle, and there is no legitimate visitor
    who benefits from knowing it.
    """
    draft = await landing_draft_repo.get_by_token_hash(hash_token(token))
    if draft is None:
        raise NotFoundError("Landing draft")
    if draft.status == "claimed":
        raise ConflictError(
            "This draft has already been used to set up an account",
            code="DRAFT_ALREADY_CLAIMED",
        )
    if not _is_live(draft):
        raise NotFoundError("Landing draft")
    return draft


async def update_draft(*, token: str, changes: dict[str, Any]) -> LandingDraft:
    """Apply step-2 answers.

    `changes` is already `exclude_unset`-filtered by the route, so an absent key leaves the column
    alone and an explicit `None` clears it. Editing any answer clears the preview: a sketch built for
    "MCAT" is actively misleading once the visitor changes the subject to "A-Level Chemistry", and
    showing it would be worse than showing nothing.
    """
    draft = await resolve_draft(token=token)

    values: dict[str, Any] = {}
    if "purpose" in changes:
        values["purpose"] = changes["purpose"]
    if "subjects" in changes:
        values["subjects"] = changes["subjects"]
    if "goals" in changes:
        values["goals_text"] = changes["goals"]
    if "exam_name" in changes:
        values["exam_name"] = changes["exam_name"]
    if "exam_date" in changes:
        values["exam_date"] = changes["exam_date"]

    if values and draft.preview:
        values["preview"] = None

    updated = await landing_draft_repo.update_fields(draft.id, values)
    return updated or draft


def can_generate(draft: LandingDraft) -> bool:
    """Whether this draft has a generation left."""
    return (draft.generate_count or 0) < MAX_GENERATES_PER_DRAFT


async def generate_preview(*, token: str) -> LandingDraft:
    """Build the step-3 sketch and store it on the draft.

    **Cost shape.** One call to the cheap model with reasoning off, a small output ceiling, a short
    timeout, and a deterministic fallback. `user_id=None` means `model_for_operation` returns the
    standard model unconditionally, so there is no path by which this reaches a premium model.

    **The fallback is not a nicety.** The landing page's primary CTA sits inside this flow, so an LLM
    outage must degrade to a plainer sketch rather than break the wizard. A visitor should never
    learn about our provider's availability.
    """
    draft = await resolve_draft(token=token)

    if not can_generate(draft):
        raise ConflictError(
            "This draft has already been generated",
            code="DRAFT_GENERATE_LIMIT",
        )

    items = await _generate_items(draft)
    updated = await landing_draft_repo.update_fields(
        draft.id,
        {
            "preview": [item.model_dump(by_alias=True) for item in items],
            "generate_count": (draft.generate_count or 0) + 1,
        },
    )
    return updated or draft


async def _generate_items(draft: LandingDraft) -> list[DraftPreviewItem]:
    """The LLM call, with a deterministic sketch behind it."""
    from src.domains.intelligence.reasoning.llm import THINKING_OFF
    from src.domains.personal_learning.services import llm_resilient

    subjects = list(draft.subjects or [])
    subject = subjects[0] if subjects else (draft.exam_name or "what you are studying")
    fallback = _static_preview(draft, subject)

    prompt = _preview_prompt(draft, subjects, subject)
    try:
        raw = await llm_resilient.generate_content_json(
            prompt,
            max_tokens=700,
            temperature=0.4,
            timeout_s=15,
            thinking=THINKING_OFF,
            fallback=[item.model_dump(by_alias=True) for item in fallback],
            user_id=None,
            operation=PREVIEW_OPERATION,
        )
    except Exception as e:  # pragma: no cover - provider behaviour
        # Belt as well as braces: `fallback` already covers provider failure inside
        # `generate_content_json`, but this endpoint is on the conversion path and must not be able
        # to 500 for any reason the visitor cannot act on.
        logger.warning("Landing preview generation failed, using static sketch: %s", e)
        return fallback

    return _coerce_items(raw, fallback)


def _preview_prompt(draft: LandingDraft, subjects: list[str], subject: str) -> str:
    """The prompt. Small, closed, and asking for prose rather than product objects.

    It asks for labels and sentences instead of a course or plan structure so that a bad reply is a
    bad sentence rather than a malformed object we would then have to validate against the real
    schema. The preview's job is to be recognisable, not to be data.
    """
    parts = [
        "A prospective learner has told us what they want help with on our marketing site.",
        "Sketch what their starting setup in Maigie would contain.",
        "",
        f"Goal: {_purpose_phrase(draft.purpose)}",
        f"Subject(s): {', '.join(subjects) if subjects else subject}",
    ]
    if draft.exam_name:
        parts.append(f"Exam: {draft.exam_name}")
    if draft.exam_date:
        parts.append(f"Target date: {draft.exam_date.isoformat()}")
    if draft.goals_text:
        parts.append(f"In their words: {draft.goals_text[:300]}")
    parts += [
        "",
        "Return JSON only: an array of exactly 3 objects with keys 'label' and 'detail'.",
        "'label' is at most 5 words naming one concrete thing they would have "
        "(for example 'A study plan', 'Practice sets', 'Weak-spot review').",
        "'detail' is one sentence, at most 20 words, describing it in terms of their own subject.",
        "Describe only what the setup contains. Do not promise outcomes, grades, or scores.",
        "Do not mention AI, models, or Maigie's internals.",
    ]
    return "\n".join(parts)


def _purpose_phrase(purpose: str | None) -> str:
    return {
        "exam_prep": "preparing for an exam",
        "skill_building": "learning a new skill",
        "course_completion": "staying on top of their courses",
        "professional_certification": "preparing for a professional certification",
        "general_learning": "remembering what they learn",
    }.get(purpose or "", "learning something new")


def _static_preview(draft: LandingDraft, subject: str) -> list[DraftPreviewItem]:
    """The sketch shown when generation is unavailable.

    Kept in step with the client-side preview in `maigie-public/src/components/landing/
    StarterWizard.tsx`, which is what a visitor sees when the API itself is unreachable. Two copies
    is one more than ideal, but the alternative is the public site importing backend copy at build
    time, and a marketing page that cannot render without the API is a worse trade.
    """
    if draft.purpose == "exam_prep":
        target = f" by {draft.exam_date.isoformat()}" if draft.exam_date else ""
        return [
            DraftPreviewItem(label="A study plan", detail=f"Milestones for {subject}{target}."),
            DraftPreviewItem(
                label="Practice sets", detail=f"Questions on {subject}, harder as you improve."
            ),
            DraftPreviewItem(
                label="Weak-spot review", detail="Timed review focused on what you miss most."
            ),
        ]
    if draft.purpose == "skill_building":
        return [
            DraftPreviewItem(label="A learning path", detail=f"{subject}, broken into stages."),
            DraftPreviewItem(label="Practice as you go", detail="Exercises after each new idea."),
            DraftPreviewItem(label="Checkpoints", detail="Short reviews that show what stuck."),
        ]
    if draft.purpose == "course_completion":
        return [
            DraftPreviewItem(label="A unified view", detail=f"Everything for {subject} in one place."),
            DraftPreviewItem(
                label='"What matters now"', detail="The next priority stays visible."
            ),
            DraftPreviewItem(
                label="Smart review", detail="Retention checks arranged around your workload."
            ),
        ]
    return [
        DraftPreviewItem(label="A course outline", detail=f"{subject}, in a sensible order."),
        DraftPreviewItem(label="Flashcards", detail="Made from what you read, reviewed on time."),
        DraftPreviewItem(label="Spaced review", detail="Topics return before you forget them."),
    ]


def _coerce_items(raw: Any, fallback: list[DraftPreviewItem]) -> list[DraftPreviewItem]:
    """Turn whatever the model returned into at most three valid items.

    A model that answers with a dict wrapping the array, or with four items, or with a missing
    `detail`, should cost the visitor nothing — so anything unusable falls back rather than raising.
    """
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError):
            return fallback
    if isinstance(raw, dict):
        for key in ("items", "preview", "setup"):
            if isinstance(raw.get(key), list):
                raw = raw[key]
                break
    if not isinstance(raw, list):
        return fallback

    items: list[DraftPreviewItem] = []
    for entry in raw[:3]:
        if not isinstance(entry, dict):
            continue
        label = str(entry.get("label") or "").strip()
        detail = str(entry.get("detail") or "").strip()
        if not label or not detail:
            continue
        items.append(DraftPreviewItem(label=label[:80], detail=detail[:280]))
    return items or fallback


# ---------------------------------------------------------------------------
# Claim
# ---------------------------------------------------------------------------


async def claim_draft(*, token: str, user_id: str) -> dict[str, Any]:
    """Apply a draft to a freshly created account.

    **Every failure here is soft.** The caller is a learner who has just signed up, and the worst
    possible outcome is that a stale marketing draft stops them reaching their account. So an
    unknown token, an expired draft, a second claim, and an account that already has a profile all
    return `applied=False` with a reason, and the client carries on into normal onboarding.

    **Why it goes through `onboarding_service` rather than writing the profile.** The in-app signup
    path already sequences purpose → details → content generation, and `set_exam_details` /
    `set_skill_details` spawn auto-setup themselves. Calling `auto_setup_for_learner` here as well
    would run it twice, and it is not idempotent: the learner would get two preparations, two topic
    sets and two study plans. Reusing the existing sequence is the whole point of §4.4's "don't
    invent a second onboarding pipeline".
    """
    from src.domains.personal_learning.repository import personal_learning_repo
    from src.domains.personal_learning.services import onboarding_service

    draft = await landing_draft_repo.get_by_token_hash(hash_token(token))
    if draft is None:
        return {"applied": False, "reason": "not_found"}
    if draft.status == "claimed":
        return {"applied": False, "reason": "already_claimed"}
    if not _is_live(draft):
        return {"applied": False, "reason": "expired"}
    if not draft.purpose:
        # A draft opened and abandoned before the visitor said anything. Nothing to apply, and
        # claiming it would burn the token for no benefit.
        return {"applied": False, "reason": "empty"}

    # Don't walk over an account that has already been set up. `set_purpose` would rewind
    # `onboardingState`, and the details calls would spawn a second auto-setup on top of content the
    # learner already has.
    existing = await personal_learning_repo.get_profile_by_user(user_id)
    if existing is not None and existing.purpose:
        logger.info("Landing draft %s not applied: user %s already onboarded", draft.id, user_id)
        return {"applied": False, "reason": "profile_exists"}

    # Take the draft first. The claim is what makes this single-use, and it has to happen before the
    # side effects rather than after: if content generation fails halfway, the safe outcome is a
    # consumed draft and a partially set-up account, not a live token that will run generation again
    # on the next retry.
    if not await landing_draft_repo.mark_claimed(draft.id, user_id):
        return {"applied": False, "reason": "already_claimed"}

    subjects = _subjects_for_setup(draft)
    goals = draft.goals_text

    await onboarding_service.set_purpose(user_id=user_id, purpose=draft.purpose)

    if draft.purpose == "exam_prep" and (draft.exam_name or subjects):
        await onboarding_service.set_exam_details(
            user_id=user_id,
            exam_name=draft.exam_name or subjects[0],
            exam_date=draft.exam_date,
            subjects=subjects,
            goals=goals,
        )
    elif draft.purpose == "skill_building" and subjects:
        await onboarding_service.set_skill_details(
            user_id=user_id,
            skill_name=subjects[0],
            subjects=subjects,
            goals=goals,
        )
    elif subjects:
        await onboarding_service.set_subjects(user_id=user_id, subjects=subjects, goals=goals)
    else:
        # Purpose only. Worth applying — it shapes the first question the app asks — but there is
        # nothing for auto-setup to build from, and calling it would be a no-op that logs a skip.
        logger.info("Landing draft %s applied as purpose-only for user %s", draft.id, user_id)

    logger.info(
        "Landing draft claimed: id=%s user=%s purpose=%s subjects=%d",
        draft.id,
        user_id,
        draft.purpose,
        len(subjects),
    )
    return {
        "applied": True,
        "reason": None,
        "purpose": draft.purpose,
        "subjects": subjects,
    }


def _subjects_for_setup(draft: LandingDraft) -> list[str]:
    """The subject list auto-setup will build from.

    `auto_setup_for_learner` refuses a profile with no subjects, so a draft that only named an exam
    would silently produce no content. The exam name is a reasonable primary subject in that case —
    "MCAT" is what the learner would have typed anyway — and it is better than the alternative of a
    claimed draft that generates nothing.
    """
    subjects = [s for s in (draft.subjects or []) if isinstance(s, str) and s.strip()]
    if not subjects and draft.exam_name:
        subjects = [draft.exam_name]
    return subjects[:MAX_SUBJECTS]
