"""Bug Hunt — API routes.

Two routers, mounted at different prefixes by `src/app.py`:

- `router` → `/api/v1/bug-hunt`. `GET /program` and `GET /seasons` take no token, because the landing
  page renders the reward table from them and a marketing page behind auth is a marketing page nobody
  reads. Everything else is `CurrentUser`.
- `admin_router` → `/api/v1/admin/bug-hunt`. `StaffUser` for reads, `SuperAdminUser` for anything that
  changes a season or moves money, matching the split the rest of `/api/v1/admin` uses.

Phase 1 scope: seasons and standing. Applications, submissions, triage, the ledger and payouts land in
Phases 2–6 — see `docs/implementation/bug-hunt-program-plan.md`.

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, File, HTTPException, Query, UploadFile, status

from src.domains.admin.services.audit_service import log_admin_action
from src.domains.identity.db_models import User
from src.shared.auth import CurrentUser, StaffUser, SuperAdminUser
from src.shared.exceptions import ValidationError
from src.shared.infrastructure.storage import StorageError, storage_service

from . import attachments as attachment_rules
from . import models
from .db_models import (
    BugHuntAttachment,
    BugHuntParticipant,
    BugHuntProgram,
    BugHuntSubmission,
)
from .exceptions import (
    CountryNotEligibleError,
    CountryNotSetError,
    NoOpenSeasonError,
)
from .services import (
    eligibility_service,
    program_service,
    submission_service,
    triage_service,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["bug-hunt"])
admin_router = APIRouter(tags=["bug-hunt-admin"])


# ===========================================================================
# Serialisation
# ===========================================================================


def _reward_tiers(program: BugHuntProgram) -> list[models.RewardTier]:
    """Flatten a season's matrix into the published table.

    Sorted by amount, descending, because that is how the landing page reads it — the thing worth the
    most first. Deriving the table from the row rather than from a constant is what stops the page
    advertising amounts the server will not pay.
    """
    flat = [
        (amount, category, severity)
        for category, row in (program.reward_matrix or {}).items()
        if isinstance(row, dict)
        for severity, amount in row.items()
        if isinstance(amount, int) and not isinstance(amount, bool)
    ]
    return [
        models.RewardTier(category=category, severity=severity, amountKobo=amount)
        for amount, category, severity in sorted(flat, key=lambda t: (-t[0], t[1], t[2]))
    ]


def _season_summary(program: BugHuntProgram) -> models.SeasonSummary:
    return models.SeasonSummary(
        id=program.id,
        seasonNumber=program.season_number,
        slug=program.slug,
        name=program.name,
        status=program.status,
        startsAt=program.starts_at,
        endsAt=program.ends_at,
        rewards=_reward_tiers(program),
        countryAllowlist=list(program.country_allowlist or []),
        perParticipantCapKobo=program.per_participant_cap_kobo,
        minWithdrawalKobo=program.min_withdrawal_kobo,
        passUpliftPercent=program.pass_uplift_percent,
        rulesVersion=program.rules_version,
        submissionDailyLimit=program.submission_daily_limit,
    )


async def _season_admin_view(program: BugHuntProgram) -> models.SeasonAdminView:
    summary = _season_summary(program)
    return models.SeasonAdminView(
        **summary.model_dump(),
        budgetKobo=program.budget_kobo,
        awardedKobo=program.awarded_kobo,
        remainingBudgetKobo=program_service.remaining_budget_kobo(program),
        participantCounts=await program_service.participant_counts(program.id),
        rewardMatrix=dict(program.reward_matrix or {}),
    )


def _attachment_view(row: BugHuntAttachment) -> models.AttachmentView:
    return models.AttachmentView(
        id=row.id,
        url=row.url,
        contentType=row.content_type,
        sizeBytes=row.size_bytes,
        createdAt=row.created_at,
    )


def _submission_view(
    submission: BugHuntSubmission,
    *,
    season_number: int,
    award_kobo: int | None = None,
) -> models.SubmissionView:
    """A submission as its reporter sees it.

    `adminNotes` is not passed through, and cannot be: it is not a field on `SubmissionView`. Building this
    explicitly rather than with `from_attributes` is what makes that guarantee readable — a triager's
    private note reaching the person it is about is the kind of leak that only happens through a generic
    serialiser.
    """
    return models.SubmissionView(
        id=submission.id,
        programId=submission.program_id,
        seasonNumber=season_number,
        platform=submission.platform,
        appVersion=submission.app_version,
        buildNumber=submission.build_number,
        deviceModel=submission.device_model,
        osVersion=submission.os_version,
        route=submission.route,
        title=submission.title,
        stepsToReproduce=submission.steps_to_reproduce,
        expectedResult=submission.expected_result,
        actualResult=submission.actual_result,
        reportedSeverity=submission.reported_severity,
        category=submission.category,
        type=submission.type,
        severity=submission.severity,
        status=submission.status,
        isApplication=submission.is_application,
        duplicateOfId=submission.duplicate_of_id,
        publicResponse=submission.public_response,
        awardKobo=award_kobo,
        attachments=[_attachment_view(a) for a in submission.attachments],
        createdAt=submission.created_at,
        triagedAt=submission.triaged_at,
    )


def _submission_admin_view(
    submission: BugHuntSubmission,
    *,
    season_number: int,
    email: str | None,
    name: str | None,
    award_kobo: int | None,
) -> models.SubmissionAdminView:
    """The staff view: everything the reporter sees, plus `adminNotes` and who they are.

    A separate function from `_submission_view` rather than a flag on it, because the difference between the
    two is a private note reaching the person it is about. A boolean parameter is one inverted condition away
    from leaking it; two functions are not.
    """
    return models.SubmissionAdminView(
        id=submission.id,
        programId=submission.program_id,
        seasonNumber=season_number,
        participantId=submission.participant_id,
        userId=submission.user_id,
        email=email,
        name=name,
        platform=submission.platform,
        appVersion=submission.app_version,
        buildNumber=submission.build_number,
        deviceModel=submission.device_model,
        osVersion=submission.os_version,
        route=submission.route,
        title=submission.title,
        stepsToReproduce=submission.steps_to_reproduce,
        expectedResult=submission.expected_result,
        actualResult=submission.actual_result,
        reportedSeverity=submission.reported_severity,
        category=submission.category,
        type=submission.type,
        severity=submission.severity,
        status=submission.status,
        isApplication=submission.is_application,
        duplicateOfId=submission.duplicate_of_id,
        publicResponse=submission.public_response,
        adminNotes=submission.admin_notes,
        awardKobo=award_kobo,
        attachments=[_attachment_view(a) for a in submission.attachments],
        createdAt=submission.created_at,
        triagedAt=submission.triaged_at,
        triagedByUserId=submission.triaged_by_user_id,
    )


def _participant_admin_view(
    participant: BugHuntParticipant, email: str, name: str | None, season_number: int
) -> models.ParticipantAdminView:
    return models.ParticipantAdminView(
        id=participant.id,
        programId=participant.program_id,
        seasonNumber=season_number,
        userId=participant.user_id,
        email=email,
        name=name,
        status=participant.status,
        attemptCount=participant.attempt_count,
        carriedForward=participant.carried_from_program_id is not None,
        acceptedRulesVersion=participant.accepted_rules_version,
        rejectionReason=participant.rejection_reason,
        createdAt=participant.created_at,
        decidedAt=participant.decided_at,
    )


def _participation_view(
    participant: BugHuntParticipant, program: BugHuntProgram
) -> models.ParticipationView:
    return models.ParticipationView(
        id=participant.id,
        programId=participant.program_id,
        seasonNumber=program.season_number,
        seasonName=program.name,
        seasonStatus=program.status,
        status=participant.status,
        attemptCount=participant.attempt_count,
        carriedForward=participant.carried_from_program_id is not None,
        acceptedRulesVersion=participant.accepted_rules_version,
        rejectionReason=participant.rejection_reason,
        createdAt=participant.created_at,
        decidedAt=participant.decided_at,
    )


def _eligibility(program: BugHuntProgram | None, user: User) -> models.EligibilityView:
    """The caller's standing, as a value rather than an exception.

    `GET /me` reports eligibility instead of raising it: the app asks this to decide which screen to
    render, and a 403 for the ordinary case of "you are not in Nigeria" would make the boot path an
    error path. The refusals in `exceptions.py` cover the *write* attempts, and the codes are shared so
    both routes explain themselves the same way.
    """
    if program is None:
        return models.EligibilityView(
            eligible=False, country=user.country, reasonCode="NO_OPEN_SEASON"
        )
    if not user.country:
        return models.EligibilityView(eligible=False, country=None, reasonCode="COUNTRY_NOT_SET")
    if not eligibility_service.country_allowed(program, user.country):
        return models.EligibilityView(
            eligible=False, country=user.country, reasonCode="COUNTRY_NOT_ELIGIBLE"
        )
    return models.EligibilityView(eligible=True, country=user.country)


# ===========================================================================
# Public — the programme itself
# ===========================================================================


@router.get("/program", response_model=models.ProgramStateResponse)
async def get_program_state() -> models.ProgramStateResponse:
    """The current state of the programme. **Unauthenticated.**

    Answers all three states the landing page has to render, and says which one it is in `state` rather
    than leaving the client to infer a page from a null season. The between-seasons case is not an edge
    case: it is the state the programme spends most of the year in, and it goes live the day a season
    closes.
    """
    open_season = await program_service.current()
    upcoming = await program_service.next_scheduled()
    latest = await program_service.latest_any()

    if open_season is not None:
        state = "open"
    elif upcoming is not None:
        state = "scheduled"
    else:
        state = "between"

    return models.ProgramStateResponse(
        state=state,
        season=_season_summary(open_season) if open_season is not None else None,
        nextSeason=_season_summary(upcoming) if upcoming is not None else None,
        latestSeasonNumber=latest.season_number if latest is not None else None,
    )


@router.get("/seasons", response_model=models.SeasonListResponse)
async def list_seasons() -> models.SeasonListResponse:
    """Seasons that have run or are running, newest first. **Unauthenticated.**

    Drafts are excluded: a draft's amounts are still being decided, and publishing a reward table we
    might revise is worse than publishing nothing. A *scheduled* season is still announced — by
    `GET /program`, which carries its dates without implying its numbers are final.
    """
    seasons = await program_service.list_all(include_draft=False)
    return models.SeasonListResponse(seasons=[_season_summary(s) for s in seasons])


@router.get("/me", response_model=models.MeResponse)
async def get_me(current_user: CurrentUser) -> models.MeResponse:
    """Everything the participant app needs to choose a screen, in one request.

    Reports rather than refuses (see `_eligibility`). The wallet is `null` until Phase 4; the field is in
    the contract from the start so adding it later is not a shape change for the client.
    """
    open_season = await program_service.current()
    upcoming = await program_service.next_scheduled()
    state = "open" if open_season else ("scheduled" if upcoming else "between")

    # Seasons are fetched once and indexed rather than looked up per participation. There are only ever
    # a handful of them, and a query per row would make the app's boot call scale with how loyal the
    # tester is — the people we most want a fast first paint for.
    seasons = {season.id: season for season in await program_service.list_all(include_draft=True)}
    participations = await program_service.participations(user_id=current_user.id)

    history = [
        _participation_view(participant, seasons[participant.program_id])
        for participant in participations
        if participant.program_id in seasons
    ]

    current_participation = None
    needs_terms = False
    if open_season is not None:
        # Matched on the ORM rows, not on the serialised views, so the terms comparison reads the same
        # attributes `eligibility_service.assert_terms_accepted` does. Two ways of deciding "do they owe
        # an acknowledgement" is how the boot response and the write refusal come to disagree.
        row = next((p for p in participations if p.program_id == open_season.id), None)
        if row is not None:
            current_participation = _participation_view(row, open_season)
            if row.status == "approved":
                needs_terms = (
                    row.accepted_rules_version is None
                    or row.accepted_rules_version < open_season.rules_version
                )

    return models.MeResponse(
        state=state,
        season=_season_summary(open_season) if open_season is not None else None,
        eligibility=_eligibility(open_season, current_user),
        participation=current_participation,
        history=history,
        wallet=None,
        needsTermsAcceptance=needs_terms,
    )


# ===========================================================================
# Participant — applying and submitting
# ===========================================================================


@router.post(
    "/applications",
    response_model=models.ApplicationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_application(
    body: models.ApplicationCreateRequest, current_user: CurrentUser
) -> models.ApplicationResponse:
    """Register for the open season by submitting a first finding.

    **The application is the submission.** One transaction writes the participation and the finding, and
    nothing downstream treats that finding specially — it is triaged and paid like any other. An
    application form whose content is read and discarded would ask a tester to do the work of finding a
    bug and then decline to pay for it, which is a poor first impression for a programme whose whole
    proposition is that findings are worth money.

    Refused for anyone already `approved`, including a carried-forward participant: their route is
    straight to `POST /submissions`, and showing them a form they passed last season would read as though
    we had lost their record.
    """
    if not body.acceptTerms:
        raise ValidationError("You need to accept the programme terms to take part.")

    participant, submission = await submission_service.create_application(
        user=current_user,
        fields=body.model_dump(),
        accepted_rules_version=body.acceptedRulesVersion,
    )
    program = await program_service.get(participant.program_id)
    return models.ApplicationResponse(
        participation=_participation_view(participant, program),
        submission=_submission_view(submission, season_number=program.season_number),
    )


@router.post("/terms-acceptance", response_model=models.ParticipationView)
async def accept_terms(
    body: models.TermsAcceptanceRequest, current_user: CurrentUser
) -> models.ParticipationView:
    """Accept this season's terms as an already-approved participant.

    The one screen a returning tester sees before submitting. Carry-forward seeds them `approved` with no
    accepted version on purpose — this season's amounts, dates and possibly country scope differ, and
    consent to the last season is not consent to this one.
    """
    if not body.accept:
        raise ValidationError("You need to accept this season's terms to carry on.")

    participant = await submission_service.accept_terms(
        user=current_user, rules_version=body.rulesVersion
    )
    program = await program_service.get(participant.program_id)
    return _participation_view(participant, program)


@router.post(
    "/submissions", response_model=models.SubmissionView, status_code=status.HTTP_201_CREATED
)
async def create_submission(
    body: models.SubmissionCreateRequest, current_user: CurrentUser
) -> models.SubmissionView:
    """File a finding. Approved participants, open season, within the season's daily limit."""
    submission = await submission_service.create_submission(
        user=current_user, fields=body.model_dump()
    )
    program = await program_service.get(submission.program_id)
    return _submission_view(submission, season_number=program.season_number)


@router.get("/submissions", response_model=models.SubmissionListResponse)
async def list_submissions(
    current_user: CurrentUser,
    programId: str | None = None,
    status_filter: str | None = Query(default=None, alias="status"),
    platform: str | None = None,
    page: int = Query(default=1, ge=1),
    pageSize: int = Query(default=20, ge=1, le=100),
) -> models.SubmissionListResponse:
    """The caller's own submissions, newest first.

    No `programId` means **every season**, not the current one. The dashboard passes the open season when
    there is one; a tester reading their history between seasons would otherwise be shown an empty page
    because the filter defaulted to a season that does not exist.
    """
    rows, total = await submission_service.list_own(
        user_id=current_user.id,
        program_id=programId,
        status=status_filter,
        platform=platform,
        page=page,
        page_size=pageSize,
    )
    seasons = {s.id: s for s in await program_service.list_all(include_draft=True)}
    awards = await submission_service.awards_for([r.id for r in rows])
    return models.SubmissionListResponse(
        submissions=[
            _submission_view(
                row,
                season_number=seasons[row.program_id].season_number,
                award_kobo=awards.get(row.id),
            )
            for row in rows
            if row.program_id in seasons
        ],
        total=total,
        page=page,
        pageSize=pageSize,
        hasMore=(page * pageSize) < total,
    )


@router.get("/submissions/{submission_id}", response_model=models.SubmissionView)
async def get_submission(submission_id: str, current_user: CurrentUser) -> models.SubmissionView:
    """One of the caller's own submissions, from any season.

    Someone else's id answers 404, not 403 — scoped in the query rather than checked after the fact, so a
    foreign id is indistinguishable from one that does not exist. In a programme where findings describe
    unfixed security bugs, a 403 that confirms a row exists is not a small oracle.
    """
    submission = await submission_service.get_own(
        user_id=current_user.id, submission_id=submission_id
    )
    program = await program_service.get(submission.program_id)
    awards = await submission_service.awards_for([submission.id])
    return _submission_view(
        submission,
        season_number=program.season_number,
        award_kobo=awards.get(submission.id),
    )


@router.post(
    "/submissions/{submission_id}/attachments",
    response_model=models.AttachmentView,
    status_code=status.HTTP_201_CREATED,
)
async def upload_attachment(
    submission_id: str, current_user: CurrentUser, file: UploadFile = File(...)
) -> models.AttachmentView:
    """Attach a screenshot or a short screen recording to one of the caller's own submissions.

    Authorised by **ownership rather than approval**, because the application's own finding belongs to a
    `pending` applicant who is not yet an approved participant, and they must still be able to attach the
    screenshot that supports it.

    Validated before it is stored, then stored, then recorded. Storage is the slow external step, so it
    happens outside any transaction: the cost is that a refused fourth file can leave an object in the
    bucket with no row. That is a small orphan at an identifiable path rather than a fourth attachment on
    the finding, which is the right way round.
    """
    content = await file.read()
    rejection = attachment_rules.validate(content_type=file.content_type, size=len(content))
    if rejection is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": rejection.code, "message": rejection.message},
        )

    submission = await submission_service.attachable_submission(
        user_id=current_user.id, submission_id=submission_id
    )

    await file.seek(0)
    try:
        stored = await storage_service.upload_upload_file(
            file,
            path_prefix=attachment_rules.upload_path(
                user_id=current_user.id, submission_id=submission.id
            ),
        )
    except StorageError as error:
        # A 503, because nothing the tester did is wrong and a retry is the correct response. Returning a
        # 400 here would tell them their screenshot was invalid when our CDN was down.
        logger.error("bug_hunt: attachment upload failed for %s: %s", current_user.id, error)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="That upload did not go through. Please try again.",
        ) from error

    row = await submission_service.add_attachment(
        submission=submission,
        url=stored["url"],
        content_type=(file.content_type or "application/octet-stream"),
        size_bytes=int(stored.get("size") or len(content)),
    )
    return _attachment_view(row)


# ===========================================================================
# Admin — seasons
#
# The endpoints below are the whole of Decision 12: if a season cannot be created, opened and populated
# from here, then opening Season 2 needs an engineer, and the multi-season design was decorative.
# ===========================================================================


@admin_router.get("/seasons", response_model=models.SeasonAdminListResponse)
async def admin_list_seasons(admin_user: StaffUser) -> models.SeasonAdminListResponse:
    """Every season including drafts, newest first, with money and cohort counts."""
    seasons = await program_service.list_all(include_draft=True)
    return models.SeasonAdminListResponse(seasons=[await _season_admin_view(s) for s in seasons])


@admin_router.get("/seasons/defaults", response_model=models.SeasonDefaultsResponse)
async def admin_season_defaults(admin_user: StaffUser) -> models.SeasonDefaultsResponse:
    """The next season's suggested configuration, copied from the last one.

    Served rather than assembled in the browser so there is one rule for what Season *n+1* starts from.
    A blank form is how a season opens with a budget of zero or with a matrix retyped from a document.
    """
    return models.SeasonDefaultsResponse(**await program_service.defaults_for_next())


@admin_router.get("/seasons/{program_id}", response_model=models.SeasonAdminView)
async def admin_get_season(program_id: str, admin_user: StaffUser) -> models.SeasonAdminView:
    return await _season_admin_view(await program_service.get(program_id))


@admin_router.post(
    "/seasons", response_model=models.SeasonAdminView, status_code=status.HTTP_201_CREATED
)
async def admin_create_season(
    body: models.SeasonCreateRequest, admin_user: SuperAdminUser
) -> models.SeasonAdminView:
    """Create a season, in `draft`.

    Draft rather than open, because a season needs its matrix reviewed and its copy checked before
    testers can see it — and because `POST` plus an automatic open is how a half-configured season goes
    live at the moment somebody hits Save.
    """
    program = await program_service.create(
        name=body.name,
        slug=body.slug,
        starts_at=body.startsAt,
        ends_at=body.endsAt,
        budget_kobo=body.budgetKobo,
        per_participant_cap_kobo=body.perParticipantCapKobo,
        min_withdrawal_kobo=body.minWithdrawalKobo,
        pass_uplift_percent=body.passUpliftPercent,
        submission_daily_limit=body.submissionDailyLimit,
        country_allowlist=body.countryAllowlist,
        reward_matrix=body.rewardMatrix,
        rules_version=body.rulesVersion,
        season_number=body.seasonNumber,
    )
    await log_admin_action(
        admin_user_id=admin_user.id,
        action="bug_hunt_create_season",
        resource_type="bug_hunt_program",
        resource_id=program.id,
        details={
            "seasonNumber": program.season_number,
            "slug": program.slug,
            "budgetKobo": program.budget_kobo,
        },
    )
    return await _season_admin_view(program)


@admin_router.patch("/seasons/{program_id}", response_model=models.SeasonAdminView)
async def admin_update_season(
    program_id: str, body: models.SeasonUpdateRequest, admin_user: SuperAdminUser
) -> models.SeasonAdminView:
    """Edit a `draft` or `open` season. A closed one is immutable — it is a record of what it paid."""
    changes = body.model_dump(exclude_none=True)
    program = await program_service.edit(program_id, changes)
    await log_admin_action(
        admin_user_id=admin_user.id,
        action="bug_hunt_update_season",
        resource_type="bug_hunt_program",
        resource_id=program_id,
        details={"fields": ",".join(sorted(changes))},
    )
    return await _season_admin_view(program)


@admin_router.post("/seasons/{program_id}/open", response_model=models.SeasonAdminView)
async def admin_open_season(program_id: str, admin_user: SuperAdminUser) -> models.SeasonAdminView:
    """Open a season for intake.

    Its own endpoint rather than a field on `PATCH`, so opening a season is one deliberate act with one
    audit entry, and the preconditions (a budget, a complete matrix, no other open season) are checked
    at exactly the moment they matter.
    """
    program = await program_service.open_season(program_id)
    await log_admin_action(
        admin_user_id=admin_user.id,
        action="bug_hunt_open_season",
        resource_type="bug_hunt_program",
        resource_id=program_id,
        details={"seasonNumber": program.season_number},
    )
    return await _season_admin_view(program)


@admin_router.post("/seasons/{program_id}/close", response_model=models.SeasonAdminView)
async def admin_close_season(program_id: str, admin_user: SuperAdminUser) -> models.SeasonAdminView:
    """Close a season to new submissions.

    Stops intake and nothing else: triage continues and pays this season's rates, and redemption and
    withdrawal stay open indefinitely because the wallet belongs to the tester, not to the season.
    """
    program = await program_service.close_season(program_id)
    await log_admin_action(
        admin_user_id=admin_user.id,
        action="bug_hunt_close_season",
        resource_type="bug_hunt_program",
        resource_id=program_id,
        details={"seasonNumber": program.season_number},
    )
    return await _season_admin_view(program)


@admin_router.get(
    "/seasons/{program_id}/carry-forward", response_model=models.CarryForwardPreviewResponse
)
async def admin_carry_forward_preview(
    program_id: str, fromProgramId: str, admin_user: StaffUser
) -> models.CarryForwardPreviewResponse:
    """How many participants would be seeded. A count before a bulk approval, not after."""
    count = await program_service.carry_forward_preview(
        into_program_id=program_id, from_program_id=fromProgramId
    )
    return models.CarryForwardPreviewResponse(
        fromProgramId=fromProgramId, intoProgramId=program_id, count=count
    )


@admin_router.post(
    "/seasons/{program_id}/carry-forward", response_model=models.CarryForwardResponse
)
async def admin_carry_forward(
    program_id: str, body: models.CarryForwardRequest, admin_user: SuperAdminUser
) -> models.CarryForwardResponse:
    """Seed a previous season's approved participants into this one as approved.

    Idempotent, so a double-click adds nobody twice. They arrive with no accepted terms, which is what
    the app's acknowledgement screen exists for — this season's amounts and dates are not the ones they
    agreed to last time.
    """
    added = await program_service.carry_forward(
        into_program_id=program_id, from_program_id=body.fromProgramId
    )
    await log_admin_action(
        admin_user_id=admin_user.id,
        action="bug_hunt_carry_forward",
        resource_type="bug_hunt_program",
        resource_id=program_id,
        details={"fromProgramId": body.fromProgramId, "added": added},
    )
    return models.CarryForwardResponse(
        fromProgramId=body.fromProgramId, intoProgramId=program_id, added=added
    )


# ===========================================================================
# Admin — triage
#
# Staff, not super admin. The amount is not theirs to choose (Decision 6): a triager sets category and
# severity, the season's matrix decides the kobo. That is what makes it safe to let a content manager work
# the queue, and it is why a two-click money flow was not worth the friction across a 14-day season.
# ===========================================================================


@admin_router.get("/stats", response_model=models.TriageStatsResponse)
async def admin_stats(
    admin_user: StaffUser, programId: str | None = None
) -> models.TriageStatsResponse:
    """Queue depths, spend against budget, and turnaround. Defaults to the open season.

    Every figure is a live query rather than a cached number: the queue depths are the thing being managed,
    and a dashboard that says the queue is empty when it is not is worse than no dashboard.
    """
    data = await triage_service.stats(programId)
    season = data.pop("season")
    return models.TriageStatsResponse(
        seasonId=season.id if season else None,
        seasonNumber=season.season_number if season else None,
        seasonStatus=season.status if season else None,
        **data,
    )


@admin_router.get("/participants", response_model=models.ParticipantAdminListResponse)
async def admin_list_participants(
    admin_user: StaffUser,
    programId: str | None = None,
    status_filter: str | None = Query(default=None, alias="status"),
    search: str | None = None,
    page: int = Query(default=1, ge=1),
    pageSize: int = Query(default=25, ge=1, le=100),
) -> models.ParticipantAdminListResponse:
    """The application queue, **oldest first**.

    Oldest first because a review queue is worked front to back. Newest-first ordering is how the applicant
    who has waited longest keeps getting pushed down the page, which is the opposite of a 48-hour promise.
    """
    rows, total = await triage_service.list_participants(
        program_id=programId,
        status=status_filter,
        search=search,
        page=page,
        page_size=pageSize,
    )
    seasons = {s.id: s for s in await program_service.list_all(include_draft=True)}
    return models.ParticipantAdminListResponse(
        participants=[
            _participant_admin_view(
                participant, email, name, seasons[participant.program_id].season_number
            )
            for participant, email, name in rows
            if participant.program_id in seasons
        ],
        total=total,
        page=page,
        pageSize=pageSize,
        hasMore=(page * pageSize) < total,
    )


@admin_router.get("/participants/{participant_id}", response_model=models.ParticipantAdminDetail)
async def admin_get_participant(
    participant_id: str, admin_user: StaffUser
) -> models.ParticipantAdminDetail:
    """One participant, with every season they have taken part in.

    The cross-season history is the point. "Is this a good reporter" is not answerable from the season they
    are applying to, and a returning applicant's previous record is the most useful thing on this screen.
    """
    data = await triage_service.participant_detail(participant_id)
    participant = data["participant"]
    program = await program_service.get(participant.program_id)
    history: list[models.ParticipationView] = []
    for row in data["history"]:
        history.append(_participation_view(row, await program_service.get(row.program_id)))
    return models.ParticipantAdminDetail(
        participant=_participant_admin_view(
            participant, data["email"], data["name"], program.season_number
        ),
        country=data["country"],
        submissionCounts=data["submissionCounts"],
        earnedThisSeasonKobo=data["earnedThisSeasonKobo"],
        earnedLifetimeKobo=data["earnedLifetimeKobo"],
        history=history,
    )


@admin_router.post(
    "/participants/{participant_id}/decision", response_model=models.ParticipantAdminView
)
async def admin_decide_participant(
    participant_id: str, body: models.ParticipantDecisionRequest, admin_user: StaffUser
) -> models.ParticipantAdminView:
    """Approve or reject an applicant.

    Separate from triaging their application's finding, deliberately: a report can be good enough to pay for
    while the applicant is wrong for the programme, and the reverse. Collapsing the two would make one
    judgement stand in for the other.
    """
    participant = await triage_service.decide_application(
        participant_id=participant_id,
        decision=body.decision,
        reason=body.reason,
        staff_user_id=admin_user.id,
    )
    await log_admin_action(
        admin_user_id=admin_user.id,
        action="bug_hunt_decide_application",
        resource_type="bug_hunt_participant",
        resource_id=participant_id,
        details={"decision": body.decision, "reason": body.reason},
    )
    detail = await triage_service.participant_detail(participant_id)
    program = await program_service.get(participant.program_id)
    return _participant_admin_view(
        participant, detail["email"], detail["name"], program.season_number
    )


@admin_router.post(
    "/participants/{participant_id}/suspend", response_model=models.ParticipantAdminView
)
async def admin_suspend_participant(
    participant_id: str, body: models.ParticipantSuspendRequest, admin_user: SuperAdminUser
) -> models.ParticipantAdminView:
    """Suspend a participation. Stops submitting, blocks carry-forward, **does not touch the balance.**

    Super admin rather than staff, because it is the one participation decision that cannot be undone by a
    later approval — and because confiscation is the obvious next thing somebody would ask for. Whatever
    they earned before the suspension, they earned; the ledger is append-only so that no single act can
    quietly reverse a payment.
    """
    participant = await triage_service.suspend_participant(
        participant_id=participant_id, reason=body.reason, staff_user_id=admin_user.id
    )
    await log_admin_action(
        admin_user_id=admin_user.id,
        action="bug_hunt_suspend_participant",
        resource_type="bug_hunt_participant",
        resource_id=participant_id,
        details={"reason": body.reason},
    )
    detail = await triage_service.participant_detail(participant_id)
    program = await program_service.get(participant.program_id)
    return _participant_admin_view(
        participant, detail["email"], detail["name"], program.season_number
    )


@admin_router.get("/submissions", response_model=models.SubmissionAdminListResponse)
async def admin_list_submissions(
    admin_user: StaffUser,
    programId: str | None = None,
    status_filter: str | None = Query(default=None, alias="status"),
    platform: str | None = None,
    severity: str | None = None,
    category: str | None = None,
    participantId: str | None = None,
    isApplication: bool | None = None,
    search: str | None = None,
    page: int = Query(default=1, ge=1),
    pageSize: int = Query(default=25, ge=1, le=100),
) -> models.SubmissionAdminListResponse:
    """The triage queue, oldest first, filterable by season, status, platform, severity and reporter."""
    rows, total = await triage_service.list_submissions(
        program_id=programId,
        status=status_filter,
        platform=platform,
        severity=severity,
        category=category,
        participant_id=participantId,
        is_application=isApplication,
        search=search,
        page=page,
        page_size=pageSize,
    )
    seasons = {s.id: s for s in await program_service.list_all(include_draft=True)}
    awards = await submission_service.awards_for([row[0].id for row in rows])
    return models.SubmissionAdminListResponse(
        submissions=[
            _submission_admin_view(
                submission,
                season_number=seasons[submission.program_id].season_number,
                email=email,
                name=name,
                award_kobo=awards.get(submission.id),
            )
            for submission, email, name in rows
            if submission.program_id in seasons
        ],
        total=total,
        page=page,
        pageSize=pageSize,
        hasMore=(page * pageSize) < total,
    )


@admin_router.get("/submissions/{submission_id}", response_model=models.SubmissionAdminDetail)
async def admin_get_submission(
    submission_id: str, admin_user: StaffUser
) -> models.SubmissionAdminDetail:
    """One finding, with the reward table of **its own** season beside it.

    Not the current season's table. A triager grading a late Season 1 finding needs to see the amounts it
    was reported under, because those are the amounts it will be paid.
    """
    data = await triage_service.submission_detail(submission_id)
    return models.SubmissionAdminDetail(
        submission=_submission_admin_view(
            data["submission"],
            season_number=data["seasonNumber"],
            email=data["email"],
            name=data["name"],
            award_kobo=data["awardKobo"],
        ),
        reporterSubmissionCount=data["reporterSubmissionCount"],
        duplicateOfTitle=data["duplicateOfTitle"],
        rewardMatrix=data["rewardMatrix"],
    )


@admin_router.post("/submissions/{submission_id}/triage", response_model=models.TriageResponse)
async def admin_triage_submission(
    submission_id: str, body: models.TriageRequest, admin_user: StaffUser
) -> models.TriageResponse:
    """Grade a finding. The season's matrix decides what the grading is worth.

    **No amount crosses this boundary.** Accepting a grading the season does not price is refused rather
    than paid as zero, because "accepted, ₦0" is the one outcome that is both wrong and hard to notice.
    """
    result = await triage_service.triage(
        submission_id=submission_id,
        status=body.status,
        category=body.category,
        type_=body.type,
        severity=body.severity,
        duplicate_of_id=body.duplicateOfId,
        public_response=body.publicResponse,
        admin_notes=body.adminNotes,
        staff_user_id=admin_user.id,
    )
    await log_admin_action(
        admin_user_id=admin_user.id,
        action="bug_hunt_triage_submission",
        resource_type="bug_hunt_submission",
        resource_id=submission_id,
        details={
            "status": body.status,
            "category": body.category,
            "severity": body.severity,
            "awardKobo": result["awardKobo"],
        },
    )
    detail = await triage_service.submission_detail(submission_id)
    return models.TriageResponse(
        submission=_submission_admin_view(
            result["submission"],
            season_number=result["seasonNumber"],
            email=detail["email"],
            name=detail["name"],
            award_kobo=detail["awardKobo"],
        ),
        awardKobo=result["awardKobo"],
        awardPending=result["awardPending"],
    )


@admin_router.get("/known-issues", response_model=models.KnownIssueListResponse)
async def admin_known_issues(
    admin_user: StaffUser,
    platform: str | None = None,
    excludeProgramId: str | None = None,
    limit: int = Query(default=100, ge=1, le=300),
) -> models.KnownIssueListResponse:
    """Accepted findings from other seasons, for marking a repeat as `known_issue`.

    This endpoint is what keeps that fairness rule practical rather than aspirational. Without it, deciding
    whether a Season 2 report repeats an unfixed Season 1 one is a memory test — and the reliable outcome of
    a memory test under queue pressure is `duplicate`, which blames the reporter for our backlog.
    """
    rows = await triage_service.known_issues(
        platform=platform, exclude_program_id=excludeProgramId, limit=limit
    )
    return models.KnownIssueListResponse(
        knownIssues=[
            models.KnownIssueView(
                id=submission.id,
                seasonNumber=season_number,
                platform=submission.platform,
                title=submission.title,
                severity=submission.severity,
                category=submission.category,
                createdAt=submission.created_at,
            )
            for submission, season_number in rows
        ]
    )


# Re-exported so later phases' routes can raise them without importing from two places, and so a reader of
# this module can see which refusals the participant surface speaks.
__all__ = [
    "router",
    "admin_router",
    "CountryNotEligibleError",
    "CountryNotSetError",
    "NoOpenSeasonError",
]
