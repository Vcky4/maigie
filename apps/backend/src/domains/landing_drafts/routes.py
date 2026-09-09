"""Landing drafts — the public API.

**Every route in this module is unauthenticated.** That is the point of the domain: the caller is a
visitor on maigie.com who has no account and, if this works, is about to get one. It is also why the
module is unusually defensive. There is no user id to rate-limit by, no usage window to draw down, and
no support trail if something is abused, so the controls are: a hashed-IP rate limit on every route, a
fail-closed limit on the one route that spends money, a per-draft generation cap, tight schema bounds,
and a token that authorises exactly one row.

The token travels in `X-Draft-Token` rather than `Authorization`. Both would work, but `Authorization:
Bearer` means "a user JWT" everywhere else in this codebase, and a header that sometimes carries a
user and sometimes carries an anonymous draft is how a future middleware ends up treating one as the
other.

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

from __future__ import annotations

import hashlib
import logging

from fastapi import APIRouter, Header, HTTPException, Request, status

from src.shared.infrastructure.rate_limit import check_rate_limit_strict, enforce_rate_limit

from . import models
from .db_models import LandingDraft
from .services import draft_service

logger = logging.getLogger(__name__)

router = APIRouter(tags=["landing-drafts"])


# ---------------------------------------------------------------------------
# Limits
# ---------------------------------------------------------------------------
#
# Per IP, and generous enough that a real visitor on a shared campus NAT is not caught. The create
# limit is the one that bounds table growth; the generate limit is the one that bounds spend, which is
# why it is both tighter and enforced with the strict limiter below.

CREATE_LIMIT = (12, 600)  # 12 drafts per 10 minutes
READ_LIMIT = (120, 600)
UPDATE_LIMIT = (60, 600)
GENERATE_LIMIT = (6, 3600)  # 6 generations per hour


def _client_fingerprint(request: Request) -> str:
    """A stable, non-identifying key for rate limiting.

    Hashed rather than raw, following `notifications.routes`: the limiter needs to tell callers apart,
    not to know who they are, and a hash in Redis is not a log of who visited the marketing page.
    """
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
        purpose=draft.purpose,
        subjects=list(draft.subjects or []),
        goals_text=draft.goals_text,
        exam_name=draft.exam_name,
        exam_date=draft.exam_date,
        preview=[models.DraftPreviewItem(**item) for item in (draft.preview or [])],
        can_generate=draft_service.can_generate(draft),
        expires_at=draft.expires_at,
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


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
    """Start a draft for a visitor who has just picked a goal.

    Returns the only copy of the token that will ever exist. Losing it means losing the draft, which
    is correct for a credential that reads someone's data with no account behind it.
    """
    await enforce_rate_limit(
        user_id=_client_fingerprint(request),
        endpoint="landing_draft_create",
        max_requests=CREATE_LIMIT[0],
        window_seconds=CREATE_LIMIT[1],
    )
    draft, token = await draft_service.create_draft(purpose=body.purpose)
    base = _to_response(draft)
    return models.DraftCreatedResponse(**base.model_dump(by_alias=False), token=token)


@router.get("/{draft_id}", response_model=models.DraftResponse, summary="Read a draft")
async def get_draft(
    draft_id: str,
    request: Request,
    x_draft_token: str | None = Header(None, alias="X-Draft-Token"),
) -> models.DraftResponse:
    """Read a draft back, for a visitor returning to the page mid-flow.

    `draft_id` is in the path for readable URLs and logs, but it is the **token** that authorises the
    read. A mismatch between the two is treated as not found rather than as a different error, so the
    endpoint cannot be used to test whether an id exists.
    """
    await enforce_rate_limit(
        user_id=_client_fingerprint(request),
        endpoint="landing_draft_read",
        max_requests=READ_LIMIT[0],
        window_seconds=READ_LIMIT[1],
    )
    token = _require_token(x_draft_token)
    draft = await draft_service.resolve_draft(token=token)
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
    """Record step-2 answers.

    Only fields the client actually sent are applied, so a client that omits `examDate` is not
    clearing it while a client that sends `null` is.
    """
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
    updated = await draft_service.update_draft(token=token, changes=changes)
    return _to_response(updated)


@router.post(
    "/{draft_id}/generate",
    response_model=models.DraftResponse,
    summary="Generate the starter preview",
)
async def generate_preview(
    draft_id: str,
    request: Request,
    x_draft_token: str | None = Header(None, alias="X-Draft-Token"),
) -> models.DraftResponse:
    """Build the sketch shown on step 3.

    **This is the only route in the product where an anonymous caller causes an LLM call, so it is
    the only one that fails closed.** `check_rate_limit_strict` refuses when Redis is unavailable
    instead of degrading open like the shared limiter: there is no account behind this request, so
    the rate limit is the entire spending control, and a limiter that cannot count must not wave
    requests through. A visitor during a cache outage gets a 429 and the client falls back to its
    local sketch, which is a worse minute for them and a bounded one for us.
    """
    token = _require_token(x_draft_token)
    allowed, _remaining = await check_rate_limit_strict(
        f"landing_draft_generate:{_client_fingerprint(request)}",
        GENERATE_LIMIT[0],
        GENERATE_LIMIT[1],
    )
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "code": "GENERATE_RATE_LIMITED",
                "message": "Too many previews. Try again shortly.",
            },
            headers={"Retry-After": str(GENERATE_LIMIT[1])},
        )

    draft = await draft_service.resolve_draft(token=token)
    if draft.id != draft_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": "Landing draft not found"},
        )
    updated = await draft_service.generate_preview(token=token)
    return _to_response(updated)
