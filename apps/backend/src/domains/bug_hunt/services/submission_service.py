"""Applying, submitting, and reading back what you submitted.

Two things in here are less obvious than they look.

**An application is a submission.** There is one `POST` that creates a `BugHuntParticipant` and a
`BugHuntSubmission` with `isApplication = true`, in one transaction, and nothing downstream treats that
submission specially: it is triaged and paid like any other. The alternative — an application form whose
content is thrown away once a human has read it — would ask a tester to do the work of finding a bug and
then decline to pay for it, which is the wrong first impression for a programme whose entire proposition
is that findings are worth money.

**The daily limit is counted in the database, not in Redis.** It is a published rule of the season
(`BugHuntProgram.submissionDailyLimit`), not abuse control, so it has to hold when the cache is down —
`check_rate_limit` degrades *open*, and a limit that quietly stops applying during a Redis blip is not a
limit. Counting rows is authoritative, cheap at this volume, and lets the refusal say when the window
frees up.

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.domains.identity.db_models import User
from src.shared.database import get_session_factory
from src.shared.exceptions import ConflictError, NotFoundError, ValidationError

from ..db_models import (
    BugHuntAttachment,
    BugHuntLedgerEntry,
    BugHuntParticipant,
    BugHuntProgram,
    BugHuntSubmission,
)
from ..exceptions import (
    AlreadyParticipatingError,
    AttemptLimitReachedError,
    ReapplyTooSoonError,
    SubmissionLimitReachedError,
)
from . import eligibility_service, program_service

logger = logging.getLogger(__name__)

#: How long a rejected applicant waits before their one permitted retry (Decision E).
#:
#: The cooldown is the point, not the retry count. Without it, "you may reapply once" becomes "resubmit
#: immediately with the same report and hope for a different triager", which wastes the queue's time and
#: teaches the applicant nothing. Two days is long enough to find a different bug.
REAPPLY_COOLDOWN = timedelta(hours=48)

#: The window the daily limit counts over. Rolling rather than calendar, because a calendar day needs the
#: tester's timezone and we do not reliably have it — `User.timezone` defaults to UTC and was never
#: prompted for, so a calendar rule would silently give Lagos testers a limit that resets mid-afternoon.
#: A rolling window is the same for everyone and lets the refusal name a real retry time.
SUBMISSION_WINDOW = timedelta(hours=24)

#: Statuses in which a tester may still add attachments. Once a triager has ruled, the evidence is part
#: of the record: letting the reporter add to it afterwards would mean a decision could be argued against
#: material the decision was not made on.
ATTACHABLE_STATUSES = frozenset({"submitted", "in_review"})


# ===========================================================================
# Applying
# ===========================================================================


async def create_application(
    *,
    user: User,
    fields: dict,
    accepted_rules_version: int,
) -> tuple[BugHuntParticipant, BugHuntSubmission]:
    """Register for the open season by submitting a first finding.

    Refuses an already-`approved` participant rather than creating a second application, which is what
    keeps a carried-forward tester out of a form they passed last season. A `pending` one is told to wait;
    a `rejected` one may retry once, after the cooldown, and that retry **reuses the participation row**
    so the unique constraint on `(programId, userId)` stays the thing preventing duplicates.
    """
    program = await eligibility_service.require_eligible_applicant(user)

    if accepted_rules_version != program.rules_version:
        # A stale version means the applicant read terms we have since changed — possibly the amounts.
        # Accepting it would record consent to something they never saw.
        raise ConflictError(
            message="These terms have been updated. Please read them again.",
            detail=f"sent={accepted_rules_version} current={program.rules_version}",
            code="RULES_VERSION_STALE",
        )

    existing = await program_service.participation(user_id=user.id, program_id=program.id)
    if existing is not None:
        if existing.status in ("approved", "pending", "suspended"):
            raise AlreadyParticipatingError(participant_status=existing.status)
        # Rejected, so a retry is on the table.
        if existing.attempt_count >= 2:
            raise AttemptLimitReachedError()
        if existing.decided_at is not None:
            ready_at = existing.decided_at + REAPPLY_COOLDOWN
            if datetime.now(UTC) < ready_at:
                raise ReapplyTooSoonError(ready_at=ready_at.isoformat())

    now = datetime.now(UTC)
    factory = get_session_factory()
    async with factory() as session:
        if existing is None:
            participant = BugHuntParticipant(
                program_id=program.id,
                user_id=user.id,
                status="pending",
                attempt_count=1,
                accepted_rules_version=accepted_rules_version,
                terms_accepted_at=now,
            )
            session.add(participant)
            await session.flush()
        else:
            participant = (
                await session.execute(
                    select(BugHuntParticipant).where(BugHuntParticipant.id == existing.id)
                )
            ).scalar_one()
            participant.status = "pending"
            participant.attempt_count = existing.attempt_count + 1
            participant.accepted_rules_version = accepted_rules_version
            participant.terms_accepted_at = now
            # Cleared, because they are pending again and a stale reason on a pending row would be shown
            # to them as though it were current.
            participant.rejection_reason = None
            participant.decided_at = None
            participant.decided_by_user_id = None

        submission = BugHuntSubmission(
            program_id=program.id,
            participant_id=participant.id,
            user_id=user.id,
            is_application=True,
            **_submission_fields(fields),
        )
        session.add(submission)
        try:
            await session.commit()
        except Exception as e:  # pragma: no cover - the unique constraint winning a race
            await session.rollback()
            raise ConflictError(
                message="You are already signed up for this season.",
                detail=str(e),
                code="ALREADY_PARTICIPATING",
            ) from e
        await session.refresh(participant)
        await _refresh_with_attachments(session, submission)

    logger.info(
        "bug_hunt: application from user=%s season=%s attempt=%d",
        user.id,
        program.season_number,
        participant.attempt_count,
    )
    return participant, submission


async def accept_terms(*, user: User, rules_version: int) -> BugHuntParticipant:
    """Record a returning participant's acceptance of this season's terms.

    Exists because carry-forward seeds an `approved` participation with no accepted version, on purpose:
    the amounts, dates and possibly the country scope differ from the season they agreed to. This is the
    one screen a returning tester sees before submitting — not an application, and not a wait.
    """
    program = await eligibility_service.require_eligible_applicant(user)
    if rules_version != program.rules_version:
        raise ConflictError(
            message="These terms have been updated. Please read them again.",
            detail=f"sent={rules_version} current={program.rules_version}",
            code="RULES_VERSION_STALE",
        )

    # Approval is a precondition: accepting terms is not a route into the season, only a step for someone
    # already in it. A pending applicant already accepted at application time.
    participant = eligibility_service.assert_approved(
        await program_service.participation(user_id=user.id, program_id=program.id)
    )

    factory = get_session_factory()
    async with factory() as session:
        await session.execute(
            update(BugHuntParticipant)
            .where(BugHuntParticipant.id == participant.id)
            .values(accepted_rules_version=rules_version, terms_accepted_at=datetime.now(UTC))
        )
        await session.commit()

    logger.info(
        "bug_hunt: terms v%d accepted by user=%s season=%s",
        rules_version,
        user.id,
        program.season_number,
    )
    refreshed = await program_service.participation(user_id=user.id, program_id=program.id)
    assert refreshed is not None
    return refreshed


# ===========================================================================
# Submitting
# ===========================================================================


async def create_submission(*, user: User, fields: dict) -> BugHuntSubmission:
    """File a finding in the open season. Approved participants only.

    The full gate runs first — open season, eligible country, approved, terms accepted — and returns both
    the season and the participation, so the row written below cannot belong to a different season than
    the one the caller was authorised against.
    """
    program, participant = await eligibility_service.require_participant(user)
    await _assert_within_daily_limit(program=program, participant_id=participant.id)

    submission = BugHuntSubmission(
        program_id=program.id,
        participant_id=participant.id,
        user_id=user.id,
        is_application=False,
        **_submission_fields(fields),
    )
    factory = get_session_factory()
    async with factory() as session:
        session.add(submission)
        await session.commit()
        await _refresh_with_attachments(session, submission)

    logger.info(
        "bug_hunt: submission %s from user=%s platform=%s season=%s",
        submission.id,
        user.id,
        submission.platform,
        program.season_number,
    )
    return submission


async def _refresh_with_attachments(session: AsyncSession, submission: BugHuntSubmission) -> None:
    """Reload a just-written submission **with its `attachments` collection populated.**

    Without this the collection is unloaded, and the caller — a route serialising the row after the session
    has closed — triggers a lazy load against a detached instance and raises `DetachedInstanceError`. The
    result is a 500 on the first application anybody files, which is as visible a failure as this domain
    has and is invisible in the service tests, because they never serialise.

    A new submission always has no attachments, so this is loading an empty collection. That is the point:
    "loaded and empty" and "not loaded" are the same value and different behaviour.
    """
    await session.refresh(submission)
    await session.refresh(submission, attribute_names=["attachments"])


def _submission_fields(fields: dict) -> dict:
    """Map the wire payload onto ORM attributes, trimming and normalising.

    Explicit rather than `**fields`, so a client cannot set `status`, `severity`, `category` or
    `publicResponse` by adding a key to its JSON. Those are triage's to write, and a submitter who could
    set their own severity could set their own payment.
    """
    title = (fields.get("title") or "").strip()
    steps = (fields.get("stepsToReproduce") or "").strip()
    expected = (fields.get("expectedResult") or "").strip()
    actual = (fields.get("actualResult") or "").strip()
    if not (title and steps and expected and actual):
        raise ValidationError(
            "A finding needs a title, steps to reproduce, what you expected, and what happened."
        )

    return {
        "platform": fields["platform"],
        "app_version": _clean(fields.get("appVersion")),
        "build_number": _clean(fields.get("buildNumber")),
        "device_model": _clean(fields.get("deviceModel")),
        "os_version": _clean(fields.get("osVersion")),
        "route": _clean(fields.get("route")),
        "title": title,
        "steps_to_reproduce": steps,
        "expected_result": expected,
        "actual_result": actual,
        "reported_severity": _clean(fields.get("reportedSeverity")),
    }


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    trimmed = value.strip()
    return trimmed or None


async def _assert_within_daily_limit(*, program: BugHuntProgram, participant_id: str) -> None:
    """Refuse a tester who has filed the season's daily maximum in the last 24 hours.

    Counted from rows rather than from a Redis counter on purpose (see the module docstring). The refusal
    carries the moment the window frees up, because "try again later" without a time invites a retry loop.
    """
    since = datetime.now(UTC) - SUBMISSION_WINDOW
    factory = get_session_factory()
    async with factory() as session:
        recent = (
            await session.execute(
                select(BugHuntSubmission.created_at)
                .where(
                    BugHuntSubmission.participant_id == participant_id,
                    BugHuntSubmission.created_at >= since,
                )
                .order_by(BugHuntSubmission.created_at.asc())
            )
        ).scalars()
        timestamps = list(recent.all())

    if len(timestamps) < program.submission_daily_limit:
        return

    # The oldest submission in the window is the one whose expiry frees a slot.
    oldest = timestamps[0]
    if oldest.tzinfo is None:  # pragma: no cover - defensive; the column is tz-aware
        oldest = oldest.replace(tzinfo=UTC)
    raise SubmissionLimitReachedError(
        limit=program.submission_daily_limit,
        retry_at=(oldest + SUBMISSION_WINDOW).isoformat(),
    )


# ===========================================================================
# Reading
# ===========================================================================


async def get_own(*, user_id: str, submission_id: str) -> BugHuntSubmission:
    """One of the caller's own submissions, from any season, with its attachments loaded.

    Scoped in the query rather than checked afterwards, so another tester's id is indistinguishable from
    one that does not exist. A 404 that confirms a row exists is a small enumeration oracle, and in a
    programme where submissions describe unfixed security bugs it is not a small one.
    """
    factory = get_session_factory()
    async with factory() as session:
        row = (
            await session.execute(
                select(BugHuntSubmission)
                .options(selectinload(BugHuntSubmission.attachments))
                .where(
                    BugHuntSubmission.id == submission_id,
                    BugHuntSubmission.user_id == user_id,
                )
            )
        ).scalar_one_or_none()
    if row is None:
        raise NotFoundError("Submission", submission_id)
    return row


async def list_own(
    *,
    user_id: str,
    program_id: str | None = None,
    status: str | None = None,
    platform: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[BugHuntSubmission], int]:
    """The caller's submissions, newest first, with a total for pagination.

    `program_id` is optional and unset means **every season**, not the current one. The dashboard defaults
    to the open season, but a tester between seasons still has a history worth reading, and a filter that
    defaults to "the season that does not exist right now" would show them an empty page.
    """
    offset = max(0, (page - 1) * page_size)
    factory = get_session_factory()
    async with factory() as session:
        conditions = [BugHuntSubmission.user_id == user_id]
        if program_id:
            conditions.append(BugHuntSubmission.program_id == program_id)
        if status:
            conditions.append(BugHuntSubmission.status == status)
        if platform:
            conditions.append(BugHuntSubmission.platform == platform)

        total = (
            await session.execute(
                select(func.count()).select_from(BugHuntSubmission).where(*conditions)
            )
        ).scalar() or 0

        rows = (
            (
                await session.execute(
                    select(BugHuntSubmission)
                    .options(selectinload(BugHuntSubmission.attachments))
                    .where(*conditions)
                    .order_by(BugHuntSubmission.created_at.desc())
                    .offset(offset)
                    .limit(page_size)
                )
            )
            .scalars()
            .all()
        )
    return list(rows), int(total)


async def awards_for(submission_ids: list[str]) -> dict[str, int]:
    """What each of these submissions was awarded, in kobo, read from the ledger.

    **Joined rather than mirrored.** `BugHuntSubmission` carries no award column, so this is the only
    place an amount comes from and there is nothing to reconcile. One query for a page of submissions
    rather than one per row.

    Returns only submissions that were actually awarded; a caller reads a missing key as "nothing yet",
    which is different from "awarded ₦0" and the wallet screen shows them differently.
    """
    if not submission_ids:
        return {}
    factory = get_session_factory()
    async with factory() as session:
        rows = (
            await session.execute(
                select(BugHuntLedgerEntry.submission_id, BugHuntLedgerEntry.amount_kobo).where(
                    BugHuntLedgerEntry.kind == "award",
                    BugHuntLedgerEntry.submission_id.in_(submission_ids),
                )
            )
        ).all()
    return {submission_id: int(amount) for submission_id, amount in rows if submission_id}


# ===========================================================================
# Attachments
# ===========================================================================


async def attachable_submission(*, user_id: str, submission_id: str) -> BugHuntSubmission:
    """The caller's submission, if it can still take an attachment.

    Authorised by **ownership**, not by approval — the application submission belongs to a `pending`
    applicant, who by definition is not yet an approved participant, and they must still be able to
    attach the screenshot that supports it.

    Refused once a triager has ruled: after that the evidence is part of the record, and material added
    afterwards would let a decision be argued against something it was not made on.
    """
    submission = await get_own(user_id=user_id, submission_id=submission_id)
    if submission.status not in ATTACHABLE_STATUSES:
        raise ConflictError(
            message="This finding has already been reviewed, so it cannot be changed.",
            detail=f"status={submission.status}",
            code="SUBMISSION_CLOSED",
        )
    return submission


async def add_attachment(
    *, submission: BugHuntSubmission, url: str, content_type: str, size_bytes: int
) -> BugHuntAttachment:
    """Record a stored file against a submission, refusing once the per-finding ceiling is reached.

    Counted here rather than trusted from the client, and counted *before* the row is written. The upload
    itself has already happened by this point — storage is the slow, external step and doing it inside a
    transaction would hold a connection open on a network call — so a fourth file can end up in the bucket
    without a row. That is a small orphan with an identifiable path (`attachments.upload_path`) rather
        than a fourth attachment on the finding, which is the right way round.
    """
    from .. import attachments as attachment_rules

    factory = get_session_factory()
    async with factory() as session:
        count = (
            await session.execute(
                select(func.count())
                .select_from(BugHuntAttachment)
                .where(BugHuntAttachment.submission_id == submission.id)
            )
        ).scalar() or 0
        if count >= attachment_rules.MAX_PER_SUBMISSION:
            raise ConflictError(
                message=(
                    f"A finding can carry {attachment_rules.MAX_PER_SUBMISSION} attachments. "
                    "If you have more to show, it is probably a second finding — which is also how "
                    "it gets paid separately."
                ),
                detail=f"count={count}",
                code="ATTACHMENT_LIMIT",
            )

        row = BugHuntAttachment(
            submission_id=submission.id,
            url=url,
            content_type=content_type,
            size_bytes=size_bytes,
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)

    logger.info("bug_hunt: attachment %s on submission %s", row.id, submission.id)
    return row
