"""Unauthenticated temporary landing-draft API.

A high-entropy token authorises exactly one short-lived draft. It travels in
``X-Draft-Token`` rather than ``Authorization`` so it can never be confused with a user JWT.

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

from __future__ import annotations

import hashlib

from fastapi import APIRouter, Header, HTTPException, Request, status

from src.shared.infrastructure.rate_limit import enforce_rate_limit

from . import models
from .db_models import LandingDraft
from .services import draft_service

router = APIRouter(tags=["landing-drafts"])

CREATE_LIMIT = (12, 600)
READ_LIMIT = (120, 600)
UPDATE_LIMIT = (60, 600)


def _client_fingerprint(request: Request) -> str:
    """Return a stable, non-identifying rate-limit key."""
    host = request.client.host if request.client is not None else "unknown"
    return hashlib.sha256(host.encode()).hexdigest()


def _require_token(x_draft_token: str | None) -> str:
    if not x_draft_token or not x_draft_token.strip():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "DRAFT_TOKEN_REQUIRED", "message": "A draft token is required"},
        )
    return x_draft_token.strip()


def _to_response(draft: LandingDraft) -> models.DraftResponse:
    return models.DraftResponse(
        id=draft.id,
        status=draft.status,
        email=draft.email,
        purpose=draft.purpose,
        subjects=list(draft.subjects or []),
        goals_text=draft.goals_text,
        exam_name=draft.exam_name,
        exam_date=draft.exam_date,
        expires_at=draft.expires_at,
    )


async def _rate_limit_read(request: Request) -> None:
    await enforce_rate_limit(
        user_id=_client_fingerprint(request),
        endpoint="landing_draft_read",
        max_requests=READ_LIMIT[0],
        window_seconds=READ_LIMIT[1],
    )


@router.post(
    "",
    response_model=models.DraftCreatedResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Open an anonymous starter draft",
)
async def create_draft(
    request: Request,
    body: models.DraftCreateRequest,
) -> models.DraftCreatedResponse:
    """Create a short-lived draft and return its only raw token."""
    await enforce_rate_limit(
        user_id=_client_fingerprint(request),
        endpoint="landing_draft_create",
        max_requests=CREATE_LIMIT[0],
        window_seconds=CREATE_LIMIT[1],
    )
    draft, token = await draft_service.create_draft(purpose=body.purpose)
    base = _to_response(draft)
    return models.DraftCreatedResponse(**base.model_dump(by_alias=False), token=token)


# This static path must be declared before /{draft_id}.
@router.get("/handoff", response_model=models.DraftResponse, summary="Read a handoff draft")
async def get_handoff_draft(
    request: Request,
    x_draft_token: str | None = Header(None, alias="X-Draft-Token"),
) -> models.DraftResponse:
    """Resolve a draft using only its opaque handoff token.

    The web app deliberately receives no database identifier. The token is the credential and the
    lookup key; exposing an internal id would add another value without adding authorization.
    """
    await _rate_limit_read(request)
    draft = await draft_service.resolve_draft(token=_require_token(x_draft_token))
    return _to_response(draft)


@router.get("/{draft_id}", response_model=models.DraftResponse, summary="Read a draft")
async def get_draft(
    draft_id: str,
    request: Request,
    x_draft_token: str | None = Header(None, alias="X-Draft-Token"),
) -> models.DraftResponse:
    """Read a draft by id and token for the originating marketing client."""
    await _rate_limit_read(request)
    draft = await draft_service.resolve_draft(token=_require_token(x_draft_token))
    if draft.id != draft_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": "Landing draft not found"},
        )
    return _to_response(draft)


@router.patch("/{draft_id}", response_model=models.DraftResponse, summary="Update a draft")
async def update_draft(
    draft_id: str,
    request: Request,
    body: models.DraftUpdateRequest,
    x_draft_token: str | None = Header(None, alias="X-Draft-Token"),
) -> models.DraftResponse:
    """Persist the latest deterministic wizard answers."""
    await enforce_rate_limit(
        user_id=_client_fingerprint(request),
        endpoint="landing_draft_update",
        max_requests=UPDATE_LIMIT[0],
        window_seconds=UPDATE_LIMIT[1],
    )
    token = _require_token(x_draft_token)
    draft = await draft_service.resolve_draft(token=token)
    if draft.id != draft_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": "Landing draft not found"},
        )
    changes = body.model_dump(exclude_unset=True, by_alias=False)
    return _to_response(await draft_service.update_draft(token=token, changes=changes))
