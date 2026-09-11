"""Staff review surface for educator survey responses.

Mounted separately from the public router and guarded by ``StaffUser``, so the routing table shows
which half of this domain is unauthenticated. Read-mostly by design: research responses are evidence,
so the only thing staff can change is the review state.

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Query

from src.shared.auth import StaffUser
from src.shared.exceptions import NotFoundError

from . import instrument, models
from .db_models import EducatorSurveyResponse
from .repository import educator_survey_repo

logger = logging.getLogger(__name__)

router = APIRouter(tags=["educator-survey-admin"])

#: The questions the list view projects, so a reviewer can triage without opening each response.
#: Named constants rather than inline literals because they are instrument coordinates: if a revision
#: renumbers the concept-relevance question, this is the line that has to change with it.
ROLE_QUESTION = "Q2"
ORGANISATION_QUESTION = "Q3"
CONCEPT_RELEVANCE_QUESTION = "Q49"
NEXT_STEP_QUESTION = "Q58"


def _as_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [v for v in value if isinstance(v, str)]
    return [value] if isinstance(value, str) else []


def _to_list_item(row: EducatorSurveyResponse, *, has_contact: bool) -> models.AdminSurveyListItem:
    answers = dict(row.answers or {})
    relevance = answers.get(CONCEPT_RELEVANCE_QUESTION)
    return models.AdminSurveyListItem(
        id=row.id,
        status=row.status,
        admin_status=row.admin_status,
        instrument_version=row.instrument_version,
        last_section=row.last_section,
        created_at=row.created_at,
        submitted_at=row.submitted_at,
        roles=_as_list(answers.get(ROLE_QUESTION)),
        organisation=answers.get(ORGANISATION_QUESTION)
        if isinstance(answers.get(ORGANISATION_QUESTION), str)
        else None,
        concept_relevance=relevance if isinstance(relevance, int) else None,
        next_step_interest=answers.get(NEXT_STEP_QUESTION)
        if isinstance(answers.get(NEXT_STEP_QUESTION), str)
        else None,
        has_contact=has_contact,
    )


@router.get("", response_model=models.AdminSurveyListResponse, summary="List survey responses")
async def list_responses(
    admin_user: StaffUser,
    status_filter: models.SurveyStatus | None = Query(None, alias="status"),
    admin_status: models.AdminStatus | None = Query(None),
    search: str | None = Query(None, max_length=200),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> models.AdminSurveyListResponse:
    """A page of responses, newest first.

    Partial responses are listed alongside complete ones and are filterable rather than hidden. A
    respondent who stopped at Q40 has answered the pain-point sections the research is actually built
    on, so excluding them by default would hide most of the evidence.
    """
    rows, with_contact, total = await educator_survey_repo.list_for_admin(
        status=status_filter,
        admin_status=admin_status,
        search=search,
        limit=limit,
        offset=offset,
    )
    contact_ids = set(with_contact)
    return models.AdminSurveyListResponse(
        items=[_to_list_item(r, has_contact=r.id in contact_ids) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{response_id}", response_model=models.AdminSurveyDetail, summary="Read one response")
async def get_response(response_id: str, admin_user: StaffUser) -> models.AdminSurveyDetail:
    """One response in full, including its contact detail if the respondent left one."""
    row = await educator_survey_repo.get_by_id(response_id)
    if row is None:
        raise NotFoundError("Survey response", response_id)
    return models.AdminSurveyDetail(
        id=row.id,
        status=row.status,
        admin_status=row.admin_status,
        instrument_version=row.instrument_version,
        last_section=row.last_section,
        created_at=row.created_at,
        updated_at=row.updated_at,
        submitted_at=row.submitted_at,
        answers=dict(row.answers or {}),
        contact_detail=await educator_survey_repo.get_contact(row.id),
        sections=instrument.describe_answers(dict(row.answers or {})),
    )


@router.patch(
    "/{response_id}/status",
    response_model=models.AdminSurveyDetail,
    summary="Update the review state",
)
async def set_status(
    response_id: str,
    body: models.AdminStatusUpdateRequest,
    admin_user: StaffUser,
) -> models.AdminSurveyDetail:
    """Move a response through NEW → REVIEWED → ARCHIVED.

    The respondent's own `status` is untouched: a reviewer marking something reviewed must not be able
    to change whether the respondent finished it.
    """
    if not await educator_survey_repo.set_admin_status(response_id, body.admin_status):
        raise NotFoundError("Survey response", response_id)
    logger.info("Educator survey %s marked %s by %s", response_id, body.admin_status, admin_user.id)
    return await get_response(response_id, admin_user)
