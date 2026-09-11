"""Unauthenticated educator questionnaire API.

A high-entropy resume token authorises exactly one response. It travels in ``X-Survey-Token`` rather
than ``Authorization`` for the same reason the landing-draft token does: ``Authorization: Bearer``
means "user JWT" everywhere else in this backend, and a header that sometimes carries a user and
sometimes an anonymous respondent is how a future middleware confuses the two.

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

from __future__ import annotations

import hashlib

from fastapi import APIRouter, Header, HTTPException, Request, status

from src.shared.infrastructure.rate_limit import enforce_rate_limit

from . import instrument, models
from .db_models import EducatorSurveyResponse
from .services import survey_service

router = APIRouter(tags=["educator-survey"])

# Deliberately looser than the landing-draft limits on saves and tighter on starts. One respondent
# generates a dozen section saves over fifteen minutes, so a stingy save limit would break a
# legitimate long form; starting a response is once per person, so a burst of starts is either a bot
# or a broken client.
START_LIMIT = (6, 600)
READ_LIMIT = (120, 600)
SAVE_LIMIT = (120, 600)


def _client_fingerprint(request: Request) -> str:
    """A stable, non-identifying rate-limit key."""
    host = request.client.host if request.client is not None else "unknown"
    return hashlib.sha256(host.encode()).hexdigest()


def _require_token(token: str | None) -> str:
    if not token or not token.strip():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "SURVEY_TOKEN_REQUIRED", "message": "A survey token is required"},
        )
    return token.strip()


def _to_state(row: EducatorSurveyResponse) -> models.SurveyResponseState:
    return models.SurveyResponseState(
        id=row.id,
        status=row.status,
        instrument_version=row.instrument_version,
        last_section=row.last_section,
        answers=dict(row.answers or {}),
        submitted_at=row.submitted_at,
    )


@router.get(
    "/instrument",
    summary="The instrument version this server validates against",
)
async def get_instrument_meta() -> dict[str, object]:
    """Version, checksum and question count for the committed question bank.

    Exists for the cross-repository drift check (`npm run check:instrument` on the public site), which
    is what keeps the two copies of `instrument.json` honest. The questions themselves are not served:
    the marketing site ships its own copy so the survey renders on a static build with no API call,
    and serving them here would invite a second, divergent rendering path.
    """
    return {
        "instrumentVersion": instrument.version(),
        "checksum": instrument.checksum(),
        "questionCount": len(instrument.load()["questions"]),
        "sectionCount": len(instrument.load()["sections"]),
    }


@router.post(
    "",
    response_model=models.SurveyStartedResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Begin a response after consent",
)
async def start_survey(
    request: Request,
    body: models.SurveyStartRequest,
) -> models.SurveyStartedResponse:
    """Open a response and return its only raw resume token.

    Nothing is stored for a respondent who declines: consent is the precondition for the row existing,
    not a field on it.
    """
    await enforce_rate_limit(
        user_id=_client_fingerprint(request),
        endpoint="educator_survey_start",
        max_requests=START_LIMIT[0],
        window_seconds=START_LIMIT[1],
    )
    response_id, token = await survey_service.start_response(
        consent=body.consent, honeypot=body.honeypot
    )
    return models.SurveyStartedResponse(
        id=response_id, token=token, instrument_version=instrument.version()
    )


@router.get("", response_model=models.SurveyResponseState, summary="Resume a response")
async def get_survey(
    request: Request,
    x_survey_token: str | None = Header(None, alias="X-Survey-Token"),
) -> models.SurveyResponseState:
    """Read a response by its resume token alone.

    No id in the path: the token is both the credential and the lookup key, and accepting an id as
    well would add a value without adding authorisation.
    """
    await enforce_rate_limit(
        user_id=_client_fingerprint(request),
        endpoint="educator_survey_read",
        max_requests=READ_LIMIT[0],
        window_seconds=READ_LIMIT[1],
    )
    return _to_state(await survey_service.resolve(token=_require_token(x_survey_token)))


@router.patch("", response_model=models.SurveyResponseState, summary="Save one section")
async def save_section(
    request: Request,
    body: models.SurveySectionRequest,
    x_survey_token: str | None = Header(None, alias="X-Survey-Token"),
) -> models.SurveyResponseState:
    """Persist a section's answers. Partial by design — see the service docstring."""
    await enforce_rate_limit(
        user_id=_client_fingerprint(request),
        endpoint="educator_survey_save",
        max_requests=SAVE_LIMIT[0],
        window_seconds=SAVE_LIMIT[1],
    )
    row = await survey_service.save_section(
        token=_require_token(x_survey_token),
        section=body.section,
        changes=body.answers,
    )
    return _to_state(row)


@router.post("/submit", response_model=models.SurveySubmitResponse, summary="Submit a response")
async def submit_survey(
    request: Request,
    x_survey_token: str | None = Header(None, alias="X-Survey-Token"),
) -> models.SurveySubmitResponse:
    """Mark a response complete, or report what is still outstanding."""
    await enforce_rate_limit(
        user_id=_client_fingerprint(request),
        endpoint="educator_survey_save",
        max_requests=SAVE_LIMIT[0],
        window_seconds=SAVE_LIMIT[1],
    )
    result = await survey_service.submit(token=_require_token(x_survey_token))
    return models.SurveySubmitResponse(**result)
