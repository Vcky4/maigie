"""Seasons: resolution, creation, the two status transitions, and carry-forward.

This module is where Decision 12 is either honoured or quietly abandoned. Every function here exists so
that opening a season is data entry rather than a deploy — `create` defaults a new season from the last
one, `open` and `close` are the only status transitions, and `carry_forward` moves a proven cohort into
the next run without asking them to re-audition.

**`current` is the season, singular.** At most one row can be `open`, guaranteed by a partial unique
index rather than by a check here, so every read in this domain has one unambiguous answer to "what is
running". Anything that needs intake to be open calls `require_open` and gets a named refusal that the
participant app can render as a landing state.

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select, update

from src.shared.database import get_session_factory
from src.shared.exceptions import ConflictError, NotFoundError, ValidationError

from .. import rewards
from ..db_models import BugHuntParticipant, BugHuntProgram
from ..exceptions import NoOpenSeasonError, SeasonStateError

logger = logging.getLogger(__name__)


# ===========================================================================
# Resolution
# ===========================================================================


async def current() -> BugHuntProgram | None:
    """The open season, or `None` if the programme is between seasons.

    Keyed on `status`, not on the date window. The dates are what the landing page promises; the status
    is what the admin controls. Deriving "open" from `startsAt <= now <= endsAt` would mean a season
    opens itself at midnight whether or not anyone is ready to triage it, and closes itself while a
    tester is mid-submission. A human decides, and `endsAt` is the commitment they are working to.
    """
    factory = get_session_factory()
    async with factory() as session:
        return (
            await session.execute(select(BugHuntProgram).where(BugHuntProgram.status == "open"))
        ).scalar_one_or_none()


async def next_scheduled() -> BugHuntProgram | None:
    """The soonest `draft` season, if one is on the books.

    Read by `GET /program` when nothing is open, because "the next season starts on the 14th" and "there
    is no next season" are different pages, and the second one should not be shown to someone we have
    already scheduled a date for.
    """
    factory = get_session_factory()
    async with factory() as session:
        return (
            await session.execute(
                select(BugHuntProgram)
                .where(BugHuntProgram.status == "draft")
                .order_by(BugHuntProgram.starts_at.asc())
                .limit(1)
            )
        ).scalar_one_or_none()


async def latest_closed() -> BugHuntProgram | None:
    """The most recently closed season. Used for defaults when creating the next one."""
    factory = get_session_factory()
    async with factory() as session:
        return (
            await session.execute(
                select(BugHuntProgram)
                .where(BugHuntProgram.status == "closed")
                .order_by(BugHuntProgram.season_number.desc())
                .limit(1)
            )
        ).scalar_one_or_none()


async def latest_any() -> BugHuntProgram | None:
    """The highest-numbered season in any state. What `create` copies its defaults from."""
    factory = get_session_factory()
    async with factory() as session:
        return (
            await session.execute(
                select(BugHuntProgram).order_by(BugHuntProgram.season_number.desc()).limit(1)
            )
        ).scalar_one_or_none()


async def get(program_id: str) -> BugHuntProgram:
    factory = get_session_factory()
    async with factory() as session:
        row = (
            await session.execute(select(BugHuntProgram).where(BugHuntProgram.id == program_id))
        ).scalar_one_or_none()
    if row is None:
        raise NotFoundError("Season", program_id)
    return row


async def list_all(*, include_draft: bool = True) -> list[BugHuntProgram]:
    """Every season, newest first. `include_draft=False` for the public `/seasons` read.

    A draft season is an internal plan with numbers that may still change, so the public list shows only
    what has actually run — while `GET /program` still announces a *scheduled* one, because a date is a
    promise worth making and an unfinalised reward table is not.
    """
    factory = get_session_factory()
    async with factory() as session:
        stmt = select(BugHuntProgram).order_by(BugHuntProgram.season_number.desc())
        if not include_draft:
            stmt = stmt.where(BugHuntProgram.status != "draft")
        return list((await session.execute(stmt)).scalars().all())


async def require_open() -> BugHuntProgram:
    """The open season, or a refusal the app can render.

    Called by every write in this domain. Centralised so that "is intake open" is asked in exactly one
    place — a second copy of this check is a second place to forget the between-seasons case.
    """
    program = await current()
    if program is not None:
        return program
    upcoming = await next_scheduled()
    raise NoOpenSeasonError(
        next_starts_at=upcoming.starts_at.isoformat() if upcoming is not None else None
    )


def remaining_budget_kobo(program: BugHuntProgram) -> int:
    """What is left of the season's budget. Never negative — a CHECK constraint guarantees that."""
    return max(0, program.budget_kobo - program.awarded_kobo)


# ===========================================================================
# Creation and edit
# ===========================================================================


async def defaults_for_next() -> dict[str, Any]:
    """The season editor's initial values, copied from the most recent season.

    The point of defaulting rather than blanking: the person opening Season 2 should be adjusting last
    season's numbers, not retyping a reward matrix from a document. A blank form is how a season opens
    with a budget of zero, or with amounts that quietly differ from what was announced.
    """
    previous = await latest_any()
    if previous is None:
        return {
            "seasonNumber": 1,
            "budgetKobo": rewards.DEFAULT_BUDGET_KOBO,
            "perParticipantCapKobo": rewards.DEFAULT_PER_PARTICIPANT_CAP_KOBO,
            "minWithdrawalKobo": rewards.DEFAULT_MIN_WITHDRAWAL_KOBO,
            "passUpliftPercent": rewards.DEFAULT_PASS_UPLIFT_PERCENT,
            "submissionDailyLimit": rewards.DEFAULT_SUBMISSION_DAILY_LIMIT,
            "countryAllowlist": ["NG"],
            "rewardMatrix": rewards.default_matrix(),
            "rulesVersion": 1,
            "previousSeasonNumber": None,
        }
    return {
        "seasonNumber": previous.season_number + 1,
        "budgetKobo": previous.budget_kobo,
        "perParticipantCapKobo": previous.per_participant_cap_kobo,
        "minWithdrawalKobo": previous.min_withdrawal_kobo,
        "passUpliftPercent": previous.pass_uplift_percent,
        "submissionDailyLimit": previous.submission_daily_limit,
        "countryAllowlist": list(previous.country_allowlist or ["NG"]),
        "rewardMatrix": dict(previous.reward_matrix or {}),
        "rulesVersion": previous.rules_version,
        "previousSeasonNumber": previous.season_number,
    }


async def create(
    *,
    name: str,
    slug: str,
    starts_at: datetime,
    ends_at: datetime,
    budget_kobo: int | None = None,
    per_participant_cap_kobo: int | None = None,
    min_withdrawal_kobo: int | None = None,
    pass_uplift_percent: int | None = None,
    submission_daily_limit: int | None = None,
    country_allowlist: list[str] | None = None,
    reward_matrix: dict | None = None,
    rules_version: int | None = None,
    season_number: int | None = None,
) -> BugHuntProgram:
    """Create a season in `draft`. Every unspecified value comes from the previous season.

    Created `draft` rather than `open` on purpose: a season needs its matrix reviewed and its copy
    checked before testers can see it, and a create-and-open in one call is how a half-configured season
    goes live.
    """
    fallback = await defaults_for_next()

    matrix_input = reward_matrix if reward_matrix is not None else fallback["rewardMatrix"]
    try:
        matrix = rewards.validate_matrix(matrix_input)
    except ValueError as e:
        # A 422 rather than a 500: this is an admin form's payload, and the message names the offending
        # key so the editor can point at it.
        raise ValidationError(str(e)) from e

    allowlist = [
        c.strip().upper() for c in (country_allowlist or fallback["countryAllowlist"]) if c.strip()
    ]
    if not allowlist:
        raise ValidationError("A season must be open to at least one country")

    if ends_at <= starts_at:
        raise ValidationError("A season must end after it starts")

    row = BugHuntProgram(
        season_number=season_number or fallback["seasonNumber"],
        slug=slug.strip(),
        name=name.strip(),
        status="draft",
        starts_at=starts_at,
        ends_at=ends_at,
        budget_kobo=budget_kobo if budget_kobo is not None else fallback["budgetKobo"],
        awarded_kobo=0,
        per_participant_cap_kobo=(
            per_participant_cap_kobo
            if per_participant_cap_kobo is not None
            else fallback["perParticipantCapKobo"]
        ),
        min_withdrawal_kobo=(
            min_withdrawal_kobo
            if min_withdrawal_kobo is not None
            else fallback["minWithdrawalKobo"]
        ),
        pass_uplift_percent=(
            pass_uplift_percent
            if pass_uplift_percent is not None
            else fallback["passUpliftPercent"]
        ),
        submission_daily_limit=(
            submission_daily_limit
            if submission_daily_limit is not None
            else fallback["submissionDailyLimit"]
        ),
        country_allowlist=allowlist,
        reward_matrix=matrix,
        rules_version=rules_version if rules_version is not None else fallback["rulesVersion"],
    )

    factory = get_session_factory()
    async with factory() as session:
        session.add(row)
        try:
            await session.commit()
        except Exception as e:  # pragma: no cover - surfaced as a readable conflict
            await session.rollback()
            raise ConflictError(
                message="A season with that number or slug already exists.",
                detail=str(e),
                code="SEASON_DUPLICATE",
            ) from e
        await session.refresh(row)

    logger.info("bug_hunt: created season %s (%s) as draft", row.season_number, row.id)
    return row


#: Fields an admin may change, and the attribute each maps to. `status` is absent deliberately —
#: transitions go through `open`/`close` so that each one is a distinct, separately audited act rather
#: than a field on a bulk edit form.
_EDITABLE: dict[str, str] = {
    "name": "name",
    "slug": "slug",
    "startsAt": "starts_at",
    "endsAt": "ends_at",
    "budgetKobo": "budget_kobo",
    "perParticipantCapKobo": "per_participant_cap_kobo",
    "minWithdrawalKobo": "min_withdrawal_kobo",
    "passUpliftPercent": "pass_uplift_percent",
    "submissionDailyLimit": "submission_daily_limit",
    "countryAllowlist": "country_allowlist",
    "rewardMatrix": "reward_matrix",
    "rulesVersion": "rules_version",
}


async def edit(program_id: str, changes: dict[str, Any]) -> BugHuntProgram:
    """Update a season's configuration.

    **A closed season is immutable.** Its reward matrix is the record of what it paid, and every award it
    made was computed from that matrix; editing it afterwards would make the `/seasons` page lie about
    history. An open season *can* be edited — a budget sometimes has to be raised mid-run — but lowering
    the budget below what has already been awarded is refused by a CHECK constraint, so the money
    already promised cannot be un-promised.
    """
    program = await get(program_id)
    if program.status == "closed":
        raise SeasonStateError(
            message="A closed season cannot be edited.",
            detail=f"program_id={program_id}",
            code="SEASON_CLOSED",
        )

    values: dict[str, Any] = {}
    for wire_key, attr in _EDITABLE.items():
        if wire_key not in changes or changes[wire_key] is None:
            continue
        value = changes[wire_key]
        if wire_key == "rewardMatrix":
            try:
                value = rewards.validate_matrix(value)
            except ValueError as e:
                raise ValidationError(str(e)) from e
        elif wire_key == "countryAllowlist":
            value = [c.strip().upper() for c in value if str(c).strip()]
            if not value:
                raise ValidationError("A season must be open to at least one country")
        values[attr] = value

    if not values:
        return program

    starts_at = values.get("starts_at", program.starts_at)
    ends_at = values.get("ends_at", program.ends_at)
    if ends_at <= starts_at:
        raise ValidationError("A season must end after it starts")

    factory = get_session_factory()
    async with factory() as session:
        await session.execute(
            update(BugHuntProgram).where(BugHuntProgram.id == program_id).values(**values)
        )
        await session.commit()

    logger.info("bug_hunt: edited season %s fields=%s", program.season_number, sorted(values))
    return await get(program_id)


# ===========================================================================
# Transitions
# ===========================================================================


async def open_season(program_id: str) -> BugHuntProgram:
    """Move a `draft` season to `open`.

    The preconditions are the ones that would otherwise fail silently at triage time or read as an
    insult to a tester. A season with no budget accepts findings it cannot pay for. A season whose
    matrix does not cover every grading pays ₦0 for a real bug while reporting it accepted. A second
    open season makes "the current season" ambiguous for every read in this domain — that one is also
    enforced by a partial unique index, so a race loses rather than corrupting.
    """
    program = await get(program_id)
    if program.status == "open":
        return program
    if program.status == "closed":
        raise SeasonStateError(
            message="A closed season cannot be reopened.",
            detail=f"program_id={program_id}",
            code="SEASON_CLOSED",
        )

    if program.budget_kobo <= 0:
        raise SeasonStateError(
            message="Set a budget before opening this season.",
            code="SEASON_NO_BUDGET",
        )
    try:
        rewards.validate_matrix(program.reward_matrix)
    except ValueError as e:
        raise SeasonStateError(
            message=f"The reward table is incomplete: {e}",
            code="SEASON_MATRIX_INVALID",
        ) from e

    running = await current()
    if running is not None:
        raise SeasonStateError(
            message=f"Season {running.season_number} is still open. Close it first.",
            detail=f"open_program_id={running.id}",
            code="SEASON_ALREADY_OPEN",
        )

    factory = get_session_factory()
    async with factory() as session:
        try:
            await session.execute(
                update(BugHuntProgram)
                .where(BugHuntProgram.id == program_id, BugHuntProgram.status == "draft")
                .values(status="open")
            )
            await session.commit()
        except Exception as e:  # pragma: no cover - the unique index winning a race
            await session.rollback()
            raise SeasonStateError(
                message="Another season was opened at the same moment.",
                detail=str(e),
                code="SEASON_ALREADY_OPEN",
            ) from e

    logger.info("bug_hunt: opened season %s", program.season_number)
    return await get(program_id)


async def close_season(program_id: str) -> BugHuntProgram:
    """Move an `open` season to `closed`.

    **Closing stops intake and nothing else.** Redemption and withdrawal stay available indefinitely,
    because the wallet belongs to the tester rather than to the season (§6.1) — there is no cutoff to
    communicate and no balance to strand. Triage of what already arrived also continues, and it pays
    this season's rates, because awards read the matrix off the submission's own season.
    """
    program = await get(program_id)
    if program.status == "closed":
        return program
    if program.status == "draft":
        raise SeasonStateError(
            message="A draft season has not opened yet.",
            detail=f"program_id={program_id}",
            code="SEASON_NOT_OPEN",
        )

    factory = get_session_factory()
    async with factory() as session:
        await session.execute(
            update(BugHuntProgram)
            .where(BugHuntProgram.id == program_id, BugHuntProgram.status == "open")
            .values(status="closed")
        )
        await session.commit()

    logger.info("bug_hunt: closed season %s", program.season_number)
    return await get(program_id)


# ===========================================================================
# Carry-forward
# ===========================================================================


async def carry_forward_preview(*, into_program_id: str, from_program_id: str) -> int:
    """How many participants `carry_forward` would seed. Shown before the button is pressed.

    A count first, because bulk-approving a cohort is not something to discover the size of afterwards.
    """
    return len(
        await _carryable_user_ids(into_program_id=into_program_id, from_program_id=from_program_id)
    )


async def carry_forward(*, into_program_id: str, from_program_id: str) -> int:
    """Seed a previous season's approved participants into this one as `approved`. Returns how many.

    Decision 13: a tester who was approved and behaved has already demonstrated the thing an
    application exists to test, and asking them to re-audition is a way to lose a warm cohort. They are
    seeded `approved` with `carriedFromProgramId` set, and with **no accepted terms** — the one thing
    they must still do, because this season's amounts, dates and possibly country scope differ, and
    consent to Season 1 is not consent to Season 2.

    `suspended` participants do not carry, which is the entire point of suspending someone.

    Idempotent: re-running skips anyone already present, so a double-clicked button or a retried request
    adds nobody twice.
    """
    into = await get(into_program_id)
    if into.status == "closed":
        raise SeasonStateError(
            message="A closed season cannot take new participants.",
            code="SEASON_CLOSED",
        )

    user_ids = await _carryable_user_ids(
        into_program_id=into_program_id, from_program_id=from_program_id
    )
    if not user_ids:
        return 0

    now = datetime.now(UTC)
    factory = get_session_factory()
    async with factory() as session:
        session.add_all(
            [
                BugHuntParticipant(
                    program_id=into_program_id,
                    user_id=user_id,
                    status="approved",
                    attempt_count=1,
                    carried_from_program_id=from_program_id,
                    # No `acceptedRulesVersion`: that is what the app's acknowledgement screen fills in.
                    decided_at=now,
                )
                for user_id in user_ids
            ]
        )
        await session.commit()

    logger.info(
        "bug_hunt: carried %d participants from season %s into %s",
        len(user_ids),
        from_program_id,
        into_program_id,
    )
    return len(user_ids)


async def _carryable_user_ids(*, into_program_id: str, from_program_id: str) -> list[str]:
    """Approved users in the source season who have no participation in the target season."""
    factory = get_session_factory()
    async with factory() as session:
        source = (
            await session.execute(
                select(BugHuntParticipant.user_id).where(
                    BugHuntParticipant.program_id == from_program_id,
                    BugHuntParticipant.status == "approved",
                )
            )
        ).scalars()
        candidates = set(source.all())
        if not candidates:
            return []

        existing = (
            await session.execute(
                select(BugHuntParticipant.user_id).where(
                    BugHuntParticipant.program_id == into_program_id,
                    BugHuntParticipant.user_id.in_(candidates),
                )
            )
        ).scalars()
        return sorted(candidates - set(existing.all()))


# ===========================================================================
# Participation lookup
# ===========================================================================


async def participation(*, user_id: str, program_id: str) -> BugHuntParticipant | None:
    factory = get_session_factory()
    async with factory() as session:
        return (
            await session.execute(
                select(BugHuntParticipant).where(
                    BugHuntParticipant.program_id == program_id,
                    BugHuntParticipant.user_id == user_id,
                )
            )
        ).scalar_one_or_none()


async def participations(*, user_id: str) -> list[BugHuntParticipant]:
    """Every season this person has taken part in, newest season first.

    Read by `GET /me` and by the admin participant detail view. "Is this a good reporter" is a question
    about a history, and it is the question a returning applicant raises.
    """
    factory = get_session_factory()
    async with factory() as session:
        return list(
            (
                await session.execute(
                    select(BugHuntParticipant)
                    .join(BugHuntProgram, BugHuntProgram.id == BugHuntParticipant.program_id)
                    .where(BugHuntParticipant.user_id == user_id)
                    .order_by(BugHuntProgram.season_number.desc())
                )
            )
            .scalars()
            .all()
        )


async def participant_counts(program_id: str) -> dict[str, int]:
    """Participation counts by status for one season, for the admin overview."""
    factory = get_session_factory()
    async with factory() as session:
        rows = (
            await session.execute(
                select(BugHuntParticipant.status, func.count())
                .where(BugHuntParticipant.program_id == program_id)
                .group_by(BugHuntParticipant.status)
            )
        ).all()
    return {status: int(count) for status, count in rows}
