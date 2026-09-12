"""Triage: deciding applications, grading findings, and the queues staff work from.

**Grading is where money is decided, and no amount is typed here.** A triager sets `category` and
`severity`; the season's own matrix turns that into kobo. That is the whole reason `POST /triage` takes no
amount field: a staff member who could type a figure would eventually type the wrong one, and there would
be no published rule to check it against. Off-matrix money exists, but it is an `adjustment` and it needs
a super admin (Phase 4).

**Grading reads the submission's own season, not the current one.** A finding reported in Season 1 and
triaged after Season 2 opened is paid at Season 1's rates, because those are the rates it was reported
under. This falls out of the matrix living on the programme row rather than in code, and it means a slow
queue is never an unfair one.

**`known_issue` is not `duplicate`.** A bug found in Season 1 that we never fixed will be found again in
Season 2. Calling that a duplicate blames the reporter for our backlog; `known_issue` says we already knew
and have not fixed it. Both pay nothing and the participant-facing copy differs, which is why they are
separate statuses rather than one with a note.

The award itself is written in Phase 4 — this module records the decision and leaves a hook for it.
Nothing here touches the ledger yet, and the `award_pending` flag on the result is how a caller can tell
the difference between "graded, not yet paid" and "graded, pays nothing".

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Select, func, or_, select
from sqlalchemy.orm import selectinload

from src.shared.database import get_session_factory, ilike_any
from src.shared.exceptions import ConflictError, NotFoundError, ValidationError

from .. import rewards
from ..db_models import (
    BUG_TYPES,
    CATEGORIES,
    FEEDBACK_TYPES,
    PLATFORMS,
    SUBMISSION_STATUSES,
    UNPAID_SUBMISSION_STATUSES,
    BugHuntLedgerEntry,
    BugHuntParticipant,
    BugHuntProgram,
    BugHuntSubmission,
)
from . import program_service, reward_service

logger = logging.getLogger(__name__)

#: Statuses a triager may set. `submitted` is absent: it is the state a finding arrives in, and moving one
#: *back* to untouched would erase the fact that somebody looked at it. Reopening is `in_review`.
TRIAGEABLE_STATUSES = frozenset(SUBMISSION_STATUSES - {"submitted"})

#: Which `type` values belong to which category. A `bug` typed `suggestion` is not a typo worth storing —
#: it means the triager picked one dropdown and not the other, and the pair decides what the finding is.
TYPES_BY_CATEGORY: dict[str, frozenset[str]] = {
    "bug": BUG_TYPES,
    "feedback": FEEDBACK_TYPES,
}


# ===========================================================================
# Applications
# ===========================================================================


async def decide_application(
    *,
    participant_id: str,
    decision: str,
    reason: str | None,
    staff_user_id: str,
) -> BugHuntParticipant:
    """Approve or reject a participant.

    A rejection **requires** a reason, enforced here and again by a CHECK constraint. The reason is shown
    to the applicant verbatim, and a programme that turns people down without saying why is the kind that
    stops attracting applicants after one season.

    Deciding is separate from triaging the application's finding, deliberately. A report can be good enough
    to pay for and the applicant still wrong for the programme, and the reverse: someone worth having whose
    first report was thin. Collapsing the two would force one judgement to stand in for the other.
    """
    if decision not in ("approve", "reject"):
        raise ValidationError("A decision is either approve or reject.")

    cleaned = (reason or "").strip()
    if decision == "reject" and not cleaned:
        raise ValidationError("Tell the applicant why. They are owed a reason.")

    factory = get_session_factory()
    async with factory() as session:
        participant = (
            await session.execute(
                select(BugHuntParticipant).where(BugHuntParticipant.id == participant_id)
            )
        ).scalar_one_or_none()
        if participant is None:
            raise NotFoundError("Participant", participant_id)
        if participant.status not in ("pending", "rejected", "approved"):
            raise ConflictError(
                message="This participation cannot be decided in its current state.",
                detail=f"status={participant.status}",
                code="PARTICIPANT_NOT_DECIDABLE",
            )

        participant.status = "approved" if decision == "approve" else "rejected"
        participant.rejection_reason = cleaned or None
        participant.decided_at = datetime.now(UTC)
        participant.decided_by_user_id = staff_user_id
        await session.commit()
        await session.refresh(participant)

    logger.info(
        "bug_hunt: participant %s %s by %s", participant_id, participant.status, staff_user_id
    )
    return participant


async def suspend_participant(
    *, participant_id: str, reason: str, staff_user_id: str
) -> BugHuntParticipant:
    """Suspend a participation. Stops submitting and blocks carry-forward into the next season.

    **Does not touch their balance.** Whatever they earned before the suspension, they earned; confiscating
    it would turn a moderation decision into a fine, and the ledger is append-only precisely so that no
    single act can quietly reverse a payment.
    """
    cleaned = (reason or "").strip()
    if not cleaned:
        raise ValidationError("Record why this participation is being suspended.")

    factory = get_session_factory()
    async with factory() as session:
        participant = (
            await session.execute(
                select(BugHuntParticipant).where(BugHuntParticipant.id == participant_id)
            )
        ).scalar_one_or_none()
        if participant is None:
            raise NotFoundError("Participant", participant_id)

        participant.status = "suspended"
        participant.rejection_reason = cleaned
        participant.decided_at = datetime.now(UTC)
        participant.decided_by_user_id = staff_user_id
        await session.commit()
        await session.refresh(participant)

    logger.info("bug_hunt: participant %s suspended by %s", participant_id, staff_user_id)
    return participant


async def list_participants(
    *,
    program_id: str | None = None,
    status: str | None = None,
    search: str | None = None,
    page: int = 1,
    page_size: int = 25,
) -> tuple[list[tuple[BugHuntParticipant, str, str | None]], int]:
    """The participants queue, oldest first, with each person's email and name.

    **Oldest first**, unlike every other list in this domain. A review queue is worked front to back, and
    newest-first ordering is how the applicant who has waited longest is the one who keeps getting pushed
    down the page — which is the opposite of a 48-hour promise.

    Returns tuples rather than ORM rows because the identity join is what makes the queue usable, and
    lazy-loading a `User` per row would be a query per applicant.
    """
    from src.domains.identity.db_models import User

    offset = max(0, (page - 1) * page_size)
    conditions: list[Any] = []
    if program_id:
        conditions.append(BugHuntParticipant.program_id == program_id)
    if status:
        conditions.append(BugHuntParticipant.status == status)
    if search:
        # The raw term, not `%term%`: `ilike_any` escapes it and wraps it in wildcards itself. Passing
        # pre-wrapped input escapes the `%` into a literal percent sign, and the search silently matches
        # nothing — which looks like an empty queue rather than a broken filter.
        conditions.append(ilike_any(search, User.email, User.name))

    factory = get_session_factory()
    async with factory() as session:
        base = (
            select(BugHuntParticipant, User.email, User.name)
            .join(User, User.id == BugHuntParticipant.user_id)
            .where(*conditions)
        )
        total = (
            await session.execute(
                select(func.count())
                .select_from(BugHuntParticipant)
                .join(User, User.id == BugHuntParticipant.user_id)
                .where(*conditions)
            )
        ).scalar() or 0
        rows = (
            await session.execute(
                base.order_by(BugHuntParticipant.created_at.asc()).offset(offset).limit(page_size)
            )
        ).all()
    return [(row[0], row[1], row[2]) for row in rows], int(total)


async def participant_detail(participant_id: str) -> dict[str, Any]:
    """One participant, with the history that answers "is this a good reporter".

    Cross-season by construction: a returning applicant's Season 1 record is the most useful thing a
    triager can see, and it is not visible from the season they are applying to. Lifetime earnings come from
    the ledger rather than from a column, for the same reason every other figure does.
    """
    from src.domains.identity.db_models import User

    factory = get_session_factory()
    async with factory() as session:
        row = (
            await session.execute(
                select(BugHuntParticipant, User.email, User.name, User.country)
                .join(User, User.id == BugHuntParticipant.user_id)
                .where(BugHuntParticipant.id == participant_id)
            )
        ).first()
        if row is None:
            raise NotFoundError("Participant", participant_id)
        participant, email, name, country = row

        counts = _group_counts(
            (
                await session.execute(
                    select(BugHuntSubmission.status, func.count())
                    .where(BugHuntSubmission.participant_id == participant.id)
                    .group_by(BugHuntSubmission.status)
                )
            ).all()
        )
        earned_this_season = (
            await session.execute(
                select(func.coalesce(func.sum(BugHuntLedgerEntry.amount_kobo), 0)).where(
                    BugHuntLedgerEntry.participant_id == participant.id,
                    BugHuntLedgerEntry.amount_kobo > 0,
                )
            )
        ).scalar() or 0
        earned_lifetime = (
            await session.execute(
                select(func.coalesce(func.sum(BugHuntLedgerEntry.amount_kobo), 0)).where(
                    BugHuntLedgerEntry.user_id == participant.user_id,
                    BugHuntLedgerEntry.amount_kobo > 0,
                )
            )
        ).scalar() or 0

    history = await program_service.participations(user_id=participant.user_id)
    return {
        "participant": participant,
        "email": email,
        "name": name,
        "country": country,
        "submissionCounts": counts,
        "earnedThisSeasonKobo": int(earned_this_season),
        "earnedLifetimeKobo": int(earned_lifetime),
        "history": history,
    }


# ===========================================================================
# Triaging findings
# ===========================================================================


async def triage(
    *,
    submission_id: str,
    status: str,
    category: str | None,
    type_: str | None,
    severity: str | None,
    duplicate_of_id: str | None,
    public_response: str | None,
    admin_notes: str | None,
    staff_user_id: str,
) -> dict[str, Any]:
    """Grade a finding, and compute what the season owes for it.

    Returns the submission plus `awardKobo` — what the grading is worth under **that submission's own
    season's** matrix. The ledger write lands in Phase 4; until then `awardPending` says so, and it is not
    the same as an award of zero.

    Validation here is deliberately stricter than the CHECK constraints, because the constraints cannot see
    the season. In particular: accepting a finding requires a category *and* a severity that the season's
    matrix actually prices. Without that check, accepting a finding whose grading the matrix does not cover
    would pay ₦0 while telling the tester they were accepted — the exact failure the matrix validator exists
    to prevent at the other end.
    """
    if status not in TRIAGEABLE_STATUSES:
        raise ValidationError(
            f"Set one of: {', '.join(sorted(TRIAGEABLE_STATUSES))}. "
            "A finding cannot be moved back to untouched."
        )

    factory = get_session_factory()
    async with factory() as session:
        submission = (
            await session.execute(
                select(BugHuntSubmission)
                .options(selectinload(BugHuntSubmission.attachments))
                .where(BugHuntSubmission.id == submission_id)
            )
        ).scalar_one_or_none()
        if submission is None:
            raise NotFoundError("Submission", submission_id)

        program = (
            await session.execute(
                select(BugHuntProgram).where(BugHuntProgram.id == submission.program_id)
            )
        ).scalar_one()

        _validate_grading(status=status, category=category, type_=type_, severity=severity)

        if status in ("duplicate", "known_issue"):
            duplicate_of_id = await _resolve_duplicate_target(
                session, submission=submission, status=status, duplicate_of_id=duplicate_of_id
            )
        else:
            duplicate_of_id = None

        award_kobo = 0
        if status == "accepted":
            award_kobo = rewards.amount_for(program.reward_matrix or {}, category, severity)
            if award_kobo <= 0:
                # The season does not price this grading. Refusing is the only honest option: accepting
                # would tell the tester their finding was accepted and pay them nothing for it.
                raise ConflictError(
                    message=(
                        f"Season {program.season_number} does not have an amount for "
                        f"{category}/{severity}. Fix the season's reward table or grade it differently."
                    ),
                    detail=f"category={category} severity={severity}",
                    code="GRADING_NOT_PRICED",
                )

        submission.status = status
        submission.category = category
        submission.type = type_
        submission.severity = severity
        submission.duplicate_of_id = duplicate_of_id
        submission.public_response = (public_response or "").strip() or None
        submission.admin_notes = (admin_notes or "").strip() or None
        submission.triaged_at = datetime.now(UTC)
        submission.triaged_by_user_id = staff_user_id
        await session.commit()
        await session.refresh(submission)
        await session.refresh(submission, attribute_names=["attachments"])

        season_number = program.season_number

    # **The award is a second transaction, deliberately.**
    #
    # The grading is a fact about the finding and the award is a consequence of it, and the two can fail
    # independently: a season can be out of budget while a finding is still genuinely critical. Doing both
    # in one transaction would mean an exhausted budget silently un-accepts a real bug, and the tester
    # would see their report reopen for no reason they could discover.
    #
    # So the grading commits first and stands on its own. If the award cannot be written, the finding stays
    # accepted, the money stays owed, and `awardBlocked` says why — which the triage console shows and an
    # operator can act on.
    award: reward_service.AwardResult | None = None
    if status == "accepted":
        award = await reward_service.award_submission(
            submission_id=submission_id, staff_user_id=staff_user_id
        )

    logger.info(
        "bug_hunt: triaged %s as %s (%s/%s) worth %d kobo under season %s by %s%s",
        submission_id,
        status,
        category,
        severity,
        award_kobo,
        season_number,
        staff_user_id,
        f" — award blocked: {award.blocked}" if award and award.blocked else "",
    )
    return {
        "submission": submission,
        "seasonNumber": season_number,
        "awardKobo": award.credited_kobo if award else 0,
        # What the grading is worth under this season's table, whether or not it was paid. Shown next to
        # `awardKobo` so a blocked award reads as "owed ₦2,000, not yet credited" rather than as "₦0".
        "matrixKobo": award.matrix_kobo if award else 0,
        "awardBlocked": award.blocked if award else None,
        "awardMessage": award.message if award else None,
    }


def _validate_grading(
    *, status: str, category: str | None, type_: str | None, severity: str | None
) -> None:
    """Refuse an incoherent grading with a sentence rather than an `IntegrityError`.

    The CHECK constraints catch most of this, but a triager reading "violates constraint
    BugHuntSubmission_severity_pairing_check" learns nothing about which dropdown to change.
    """
    if category is not None and category not in CATEGORIES:
        raise ValidationError(f"Category is one of: {', '.join(sorted(CATEGORIES))}.")

    if status == "accepted":
        if not category or not severity:
            raise ValidationError(
                "An accepted finding needs a category and a severity — that is what decides the amount."
            )

    if severity is not None:
        if not category:
            raise ValidationError(
                "Set a category before a severity; the valid values depend on it."
            )
        allowed = rewards.SEVERITIES_BY_CATEGORY[category]
        if severity not in allowed:
            raise ValidationError(
                f"A {category} is graded {', '.join(sorted(allowed))} — not {severity!r}."
            )

    if type_ is not None:
        if not category:
            raise ValidationError("Set a category before a type; the valid values depend on it.")
        allowed_types = TYPES_BY_CATEGORY[category]
        if type_ not in allowed_types:
            raise ValidationError(
                f"A {category} is typed {', '.join(sorted(allowed_types))} — not {type_!r}."
            )


async def _resolve_duplicate_target(
    session: Any,
    *,
    submission: BugHuntSubmission,
    status: str,
    duplicate_of_id: str | None,
) -> str:
    """Validate the finding a duplicate or known issue points at.

    Both statuses mean "this one instead", so both need a target — enforced by a CHECK too. Checked here
    for three things the constraint cannot see: that the target exists, that it is not the submission
    itself, and that it is not *itself* a duplicate. That last one matters: a chain of duplicates pointing
    at duplicates gives a tester a "see this instead" link to a page that says the same thing, and gives us
    no canonical finding to fix.
    """
    if not duplicate_of_id:
        raise ValidationError(
            "Point this at the finding it duplicates. "
            "Use known_issue if it repeats something from an earlier season."
        )
    if duplicate_of_id == submission.id:
        raise ValidationError("A finding cannot duplicate itself.")

    target = (
        await session.execute(
            select(BugHuntSubmission).where(BugHuntSubmission.id == duplicate_of_id)
        )
    ).scalar_one_or_none()
    if target is None:
        raise NotFoundError("Submission", duplicate_of_id)
    if target.duplicate_of_id is not None:
        raise ValidationError(
            "That finding is itself marked as a duplicate. Point at the original instead."
        )

    if status == "known_issue" and target.program_id == submission.program_id:
        # Not fatal, but worth naming: `known_issue` exists for cross-season repeats, and using it inside
        # one season quietly loses the distinction that keeps the blame in the right place.
        logger.info(
            "bug_hunt: known_issue %s points within its own season %s — duplicate may be the better mark",
            submission.id,
            submission.program_id,
        )
    return duplicate_of_id


# ===========================================================================
# Queues
# ===========================================================================


def _submission_filters(
    *,
    program_id: str | None,
    status: str | None,
    platform: str | None,
    severity: str | None,
    category: str | None,
    participant_id: str | None,
    is_application: bool | None,
    search: str | None,
) -> list[Any]:
    conditions: list[Any] = []
    if program_id:
        conditions.append(BugHuntSubmission.program_id == program_id)
    if status:
        conditions.append(BugHuntSubmission.status == status)
    if platform:
        conditions.append(BugHuntSubmission.platform == platform)
    if severity:
        conditions.append(BugHuntSubmission.severity == severity)
    if category:
        conditions.append(BugHuntSubmission.category == category)
    if participant_id:
        conditions.append(BugHuntSubmission.participant_id == participant_id)
    if is_application is not None:
        conditions.append(BugHuntSubmission.is_application.is_(is_application))
    if search:
        conditions.append(
            ilike_any(search, BugHuntSubmission.title, BugHuntSubmission.actual_result)
        )
    return conditions


async def list_submissions(
    *,
    program_id: str | None = None,
    status: str | None = None,
    platform: str | None = None,
    severity: str | None = None,
    category: str | None = None,
    participant_id: str | None = None,
    is_application: bool | None = None,
    search: str | None = None,
    page: int = 1,
    page_size: int = 25,
) -> tuple[list[tuple[BugHuntSubmission, str | None, str | None]], int]:
    """The triage queue, **oldest first**, with each reporter's email and name.

    Oldest first for the same reason as the participants queue: a queue worked newest-first starves the
    person who has waited longest, which is precisely who a turnaround promise is about.
    """
    from src.domains.identity.db_models import User

    conditions = _submission_filters(
        program_id=program_id,
        status=status,
        platform=platform,
        severity=severity,
        category=category,
        participant_id=participant_id,
        is_application=is_application,
        search=search,
    )
    offset = max(0, (page - 1) * page_size)

    factory = get_session_factory()
    async with factory() as session:
        stmt: Select = (
            select(BugHuntSubmission, User.email, User.name)
            .outerjoin(User, User.id == BugHuntSubmission.user_id)
            .options(selectinload(BugHuntSubmission.attachments))
            .where(*conditions)
        )
        total = (
            await session.execute(
                select(func.count()).select_from(BugHuntSubmission).where(*conditions)
            )
        ).scalar() or 0
        rows = (
            await session.execute(
                stmt.order_by(BugHuntSubmission.created_at.asc()).offset(offset).limit(page_size)
            )
        ).all()
    return [(row[0], row[1], row[2]) for row in rows], int(total)


async def submission_detail(submission_id: str) -> dict[str, Any]:
    """One finding with everything a triager needs to grade it in one screen.

    Includes the reporter's other findings *count* and the canonical finding if this one is a duplicate,
    because the two questions a triager asks — "have I seen this before" and "is this reporter reliable" —
    are otherwise two more page loads each.
    """
    from src.domains.identity.db_models import User

    factory = get_session_factory()
    async with factory() as session:
        row = (
            await session.execute(
                select(BugHuntSubmission, User.email, User.name)
                .outerjoin(User, User.id == BugHuntSubmission.user_id)
                .options(selectinload(BugHuntSubmission.attachments))
                .where(BugHuntSubmission.id == submission_id)
            )
        ).first()
        if row is None:
            raise NotFoundError("Submission", submission_id)
        submission, email, name = row

        program = (
            await session.execute(
                select(BugHuntProgram).where(BugHuntProgram.id == submission.program_id)
            )
        ).scalar_one()

        award = (
            await session.execute(
                select(BugHuntLedgerEntry.amount_kobo).where(
                    BugHuntLedgerEntry.submission_id == submission.id,
                    BugHuntLedgerEntry.kind == "award",
                )
            )
        ).scalar()

        reporter_total = 0
        if submission.participant_id:
            reporter_total = (
                await session.execute(
                    select(func.count())
                    .select_from(BugHuntSubmission)
                    .where(BugHuntSubmission.participant_id == submission.participant_id)
                )
            ).scalar() or 0

        duplicate_of_title = None
        if submission.duplicate_of_id:
            duplicate_of_title = (
                await session.execute(
                    select(BugHuntSubmission.title).where(
                        BugHuntSubmission.id == submission.duplicate_of_id
                    )
                )
            ).scalar()

    return {
        "submission": submission,
        "seasonNumber": program.season_number,
        "email": email,
        "name": name,
        "awardKobo": int(award) if award is not None else None,
        "reporterSubmissionCount": int(reporter_total),
        "duplicateOfTitle": duplicate_of_title,
        # What this grading *would* pay if accepted, under this submission's own season. Shown next to the
        # dropdowns so a triager sees the consequence of the grade before committing to it.
        "rewardMatrix": dict(program.reward_matrix or {}),
    }


async def known_issues(
    *, platform: str | None = None, exclude_program_id: str | None = None, limit: int = 100
) -> list[tuple[BugHuntSubmission, int]]:
    """Accepted findings from previous seasons, newest first, for marking a repeat.

    This endpoint is what keeps the `known_issue` fairness rule practical rather than aspirational. Without
    it, deciding whether a Season 2 report repeats an unfixed Season 1 one is a memory test, and the
    reliable outcome of a memory test under queue pressure is `duplicate` — which puts the blame on the
    reporter for our backlog.

    Returns each finding with its season number, because "reported in Season 1 and still open" is the
    sentence a triager needs to write.
    """
    conditions: list[Any] = [BugHuntSubmission.status == "accepted"]
    if platform:
        conditions.append(BugHuntSubmission.platform == platform)
    if exclude_program_id:
        conditions.append(BugHuntSubmission.program_id != exclude_program_id)

    factory = get_session_factory()
    async with factory() as session:
        rows = (
            await session.execute(
                select(BugHuntSubmission, BugHuntProgram.season_number)
                .join(BugHuntProgram, BugHuntProgram.id == BugHuntSubmission.program_id)
                .options(selectinload(BugHuntSubmission.attachments))
                .where(*conditions)
                .order_by(BugHuntSubmission.created_at.desc())
                .limit(limit)
            )
        ).all()
    return [(row[0], int(row[1])) for row in rows]


# ===========================================================================
# Stats
# ===========================================================================


async def stats(program_id: str | None = None) -> dict[str, Any]:
    """The overview a triage owner reads each morning.

    Defaults to the open season; a `program_id` gives a closed season's final figures. Every count is a
    query rather than a cached number, because the queue depths are the thing being managed and a stale
    dashboard is worse than no dashboard — it says the queue is empty when it is not.
    """
    program = (
        await program_service.get(program_id) if program_id else await program_service.current()
    )
    if program is None:
        return {
            "season": None,
            "queues": {"applications": 0, "submissions": 0},
            "submissionsByStatus": {},
            "submissionsByPlatform": {},
            "submissionsBySeverity": {},
            "participantCounts": {},
            "budgetKobo": 0,
            "awardedKobo": 0,
            "remainingBudgetKobo": 0,
            "acceptanceRate": None,
            "medianTriageHours": None,
        }

    factory = get_session_factory()
    async with factory() as session:
        by_status = _group_counts(
            (
                await session.execute(
                    select(BugHuntSubmission.status, func.count())
                    .where(BugHuntSubmission.program_id == program.id)
                    .group_by(BugHuntSubmission.status)
                )
            ).all()
        )
        by_platform = _group_counts(
            (
                await session.execute(
                    select(BugHuntSubmission.platform, func.count())
                    .where(BugHuntSubmission.program_id == program.id)
                    .group_by(BugHuntSubmission.platform)
                )
            ).all()
        )
        by_severity = _group_counts(
            (
                await session.execute(
                    select(BugHuntSubmission.severity, func.count())
                    .where(
                        BugHuntSubmission.program_id == program.id,
                        BugHuntSubmission.severity.is_not(None),
                    )
                    .group_by(BugHuntSubmission.severity)
                )
            ).all()
        )
        pending_applications = (
            await session.execute(
                select(func.count())
                .select_from(BugHuntParticipant)
                .where(
                    BugHuntParticipant.program_id == program.id,
                    BugHuntParticipant.status == "pending",
                )
            )
        ).scalar() or 0
        untriaged = (
            await session.execute(
                select(func.count())
                .select_from(BugHuntSubmission)
                .where(
                    BugHuntSubmission.program_id == program.id,
                    or_(
                        BugHuntSubmission.status == "submitted",
                        BugHuntSubmission.status == "in_review",
                    ),
                )
            )
        ).scalar() or 0
        # Median rather than mean: one submission triaged three weeks late would drag a mean into
        # meaninglessness while the queue was in fact being worked within a day.
        median_hours = (
            await session.execute(
                select(
                    func.percentile_cont(0.5).within_group(
                        func.extract(
                            "epoch", BugHuntSubmission.triaged_at - BugHuntSubmission.created_at
                        )
                    )
                ).where(
                    BugHuntSubmission.program_id == program.id,
                    BugHuntSubmission.triaged_at.is_not(None),
                )
            )
        ).scalar()

    # Decided means a triager has ruled: accepted, or one of the outcomes that pays nothing. `submitted` and
    # `in_review` are excluded, because counting them as denominator would make the acceptance rate a
    # measure of how fast the queue is being worked rather than of how good the findings are.
    accepted = by_status.get("accepted", 0)
    decided = accepted + sum(by_status.get(st, 0) for st in UNPAID_SUBMISSION_STATUSES)

    return {
        "season": program,
        "queues": {"applications": int(pending_applications), "submissions": int(untriaged)},
        "submissionsByStatus": by_status,
        "submissionsByPlatform": by_platform,
        "submissionsBySeverity": by_severity,
        "participantCounts": await program_service.participant_counts(program.id),
        "budgetKobo": program.budget_kobo,
        "awardedKobo": program.awarded_kobo,
        "remainingBudgetKobo": program_service.remaining_budget_kobo(program),
        # `None` rather than 0 when nothing has been decided. A rate of zero reads as "we reject
        # everything", which is a different and much worse thing to display on day one.
        "acceptanceRate": (accepted / decided) if decided else None,
        # `is not None`, not a truthiness check. A median of zero means everything was triaged the instant
        # it arrived, which is the best possible answer — and a falsy check would report it as "not yet
        # measured", i.e. hide the one result worth celebrating.
        "medianTriageHours": (
            round(float(median_hours) / 3600, 1) if median_hours is not None else None
        ),
    }


def _group_counts(rows: Any) -> dict[str, int]:
    """Fold `(value, count)` rows into a plain dict, dropping nulls.

    Nulls are dropped rather than keyed as `"None"`: an ungraded submission has no severity, and a bucket
    labelled `None` on a dashboard reads as a category rather than as an absence.
    """
    return {str(value): int(count) for value, count in rows if value is not None}


def platforms() -> list[str]:
    """The testable surfaces, for a filter dropdown that cannot drift from the CHECK constraint."""
    return sorted(PLATFORMS)
