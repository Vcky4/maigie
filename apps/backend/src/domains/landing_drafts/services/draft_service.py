"""Persistence and single-use claim behavior for temporary landing drafts.

The marketing preview is deterministic and rendered by the public client. This service stores only
bounded onboarding answers and assigns them to an authenticated account. The web app then submits
those answers through the normal onboarding endpoints, which remain responsible for setup.

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from src.shared.exceptions import ConflictError, NotFoundError

from ..db_models import LandingDraft
from ..models import MAX_SUBJECTS
from ..repository import landing_draft_repo

logger = logging.getLogger(__name__)
DRAFT_TTL_DAYS = 7


def _new_token() -> tuple[str, str]:
    """Return a 256-bit raw token and the irreversible value stored in the database."""
    raw = secrets.token_urlsafe(32)
    return raw, hash_token(raw)


def hash_token(raw: str) -> str:
    """Hash a high-entropy token for indexed lookup without storing the credential."""
    return hashlib.sha256(raw.encode()).hexdigest()


def _is_live(draft: LandingDraft) -> bool:
    """Whether a draft is still readable and claimable."""
    return draft.status == "open" and draft.expires_at > datetime.now(UTC)


async def create_draft(*, purpose: str | None = None) -> tuple[LandingDraft, str]:
    """Open a temporary draft and return its only raw token."""
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
    """Resolve a live draft by its opaque token."""
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
    """Apply the fields explicitly supplied by the wizard."""
    draft = await resolve_draft(token=token)
    values: dict[str, Any] = {}
    if "email" in changes:
        values["email"] = changes["email"]
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

    updated = await landing_draft_repo.update_fields(draft.id, values)
    if updated is None:
        raise ConflictError(
            "This draft is no longer open for updates",
            code="DRAFT_NOT_OPEN",
        )
    return updated


async def claim_draft(*, token: str, user_id: str) -> dict[str, Any]:
    """Atomically assign a draft to an account and return the answers for normal onboarding.

    Claiming establishes ownership only. The authenticated client replays these values through the
    same purpose/details endpoints as interactive onboarding, so their existing auto-setup and
    readiness behavior remain the single implementation of workspace creation.
    """
    from src.domains.personal_learning.repository import personal_learning_repo

    draft = await landing_draft_repo.get_by_token_hash(hash_token(token))
    if draft is None:
        return {"applied": False, "reason": "not_found"}
    resuming = draft.status == "claimed" and draft.claimed_by == user_id
    if draft.status == "claimed" and not resuming:
        return {"applied": False, "reason": "already_claimed"}
    if not resuming and not _is_live(draft):
        return {"applied": False, "reason": "expired"}
    if not draft.purpose:
        return {"applied": False, "reason": "empty"}

    existing = await personal_learning_repo.get_profile_by_user(user_id)
    if not resuming and existing is not None and existing.purpose:
        logger.info(
            "Landing draft %s not claimed: user %s already has a learning profile",
            draft.id,
            user_id,
        )
        return {"applied": False, "reason": "profile_exists"}

    if not resuming and not await landing_draft_repo.mark_claimed(draft.id, user_id):
        return {"applied": False, "reason": "already_claimed"}

    subjects = _subjects_for_setup(draft)
    logger.info(
        "Landing draft assigned: id=%s user=%s purpose=%s subjects=%d",
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
        "goals_text": draft.goals_text,
        "exam_name": draft.exam_name,
        "exam_date": draft.exam_date,
    }


def _subjects_for_setup(draft: LandingDraft) -> list[str]:
    """Return normalized subjects, using an exam name when it is the only topic supplied."""
    subjects = [s.strip() for s in (draft.subjects or []) if isinstance(s, str) and s.strip()]
    if not subjects and draft.exam_name:
        subjects = [draft.exam_name]
    return subjects[:MAX_SUBJECTS]
