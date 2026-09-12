"""Turning a grading into money.

One function does the work, and it is the most carefully ordered thing in this domain, because it has four
obligations that all have to hold at once:

1. Pay **the submission's own season's** rate, so a late triage is never an unfair one.
2. Pay **once**, however many times it is called.
3. Not exceed the tester's per-season cap.
4. Not exceed the season's budget.

(2) is a partial unique index on `(submissionId) WHERE kind = 'award'`, not a check-then-insert: two staff
clicking the triage button at the same moment both pass a Python check and one loses at the database. (4)
is `awardedKobo` incremented **in the same transaction as the entry it counts**, under a lock on the
season row, which is why the budget cannot drift by one and why two simultaneous awards cannot both fit
into the last ₦2,000.

**What happens when there is no room.** The cap and the budget are handled differently on purpose:

- **Cap reached** is about this tester and this season, and it is permanent for the season. The finding is
  still accepted — it has value, and the reporter deserves the record — and no entry is written. The
  console says so.
- **Budget exhausted** is about us, not them, and it is fixable in thirty seconds by raising the budget.
  So the award is *refused rather than reduced*. Paying a tester less than the published amount because we
  ran out of money is the one failure mode that would actually damage the programme, and clamping to the
  remaining budget is exactly that failure wearing a friendly face.

Neither case rolls back the grading. The finding is accepted, the money is owed, and the operator can see
it — which is better than an accepted finding silently worth nothing.

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from src.shared.database import get_session_factory
from src.shared.exceptions import NotFoundError, ValidationError

from .. import rewards
from ..db_models import (
    BugHuntLedgerEntry,
    BugHuntProgram,
    BugHuntSubmission,
    BugHuntWallet,
)
from . import ledger_service

logger = logging.getLogger(__name__)

#: Why an accepted finding was not paid its full matrix amount.
BLOCK_ALREADY_AWARDED = "already_awarded"
BLOCK_CAP_REACHED = "cap_reached"
BLOCK_BUDGET_EXHAUSTED = "budget_exhausted"
BLOCK_NOT_PRICED = "not_priced"


@dataclass(frozen=True)
class AwardResult:
    """What happened when a grading met the ledger.

    `matrix_kobo` is what the grading is worth; `credited_kobo` is what was actually written. They differ
    only when something blocked the award, and `blocked` names which — never both silently.
    """

    #: What this season's table says the grading is worth.
    matrix_kobo: int
    #: What reached the ledger. `0` when blocked.
    credited_kobo: int
    #: `None` when the award went through cleanly, or was legitimately worth nothing.
    blocked: str | None
    entry_id: str | None
    season_number: int
    #: Human-readable, for the triage console. `None` when nothing is wrong.
    message: str | None = None

    @property
    def awarded(self) -> bool:
        return self.credited_kobo > 0


async def award_submission(*, submission_id: str, staff_user_id: str | None = None) -> AwardResult:
    """Write the award for an accepted finding. Idempotent, capped, and budgeted.

    Called from `triage_service.triage` immediately after a grading is saved, and safe to call again: a
    second call returns `blocked="already_awarded"` with the original amount rather than paying twice.
    """
    factory = get_session_factory()
    async with factory() as session:
        submission = (
            await session.execute(
                select(BugHuntSubmission).where(BugHuntSubmission.id == submission_id)
            )
        ).scalar_one_or_none()
        if submission is None:
            raise NotFoundError("Submission", submission_id)

        if submission.status != "accepted":
            raise ValidationError("Only an accepted finding is awarded.")
        if submission.user_id is None:
            # The account was deleted; the finding survives (SET NULL) but there is nobody to pay.
            raise ValidationError("This finding has no reporter to pay.")

        # **The lock, and the season.** `FOR UPDATE` on the programme row serialises every award in this
        # season, which is what makes the budget check and the `awardedKobo` increment below atomic. It
        # also means two triagers cannot both fit an award into the last ₦2,000.
        program = (
            await session.execute(
                select(BugHuntProgram)
                .where(BugHuntProgram.id == submission.program_id)
                .with_for_update()
            )
        ).scalar_one()

        matrix_kobo = rewards.amount_for(
            program.reward_matrix or {}, submission.category, submission.severity
        )

        existing = (
            await session.execute(
                select(BugHuntLedgerEntry).where(
                    BugHuntLedgerEntry.submission_id == submission_id,
                    BugHuntLedgerEntry.kind == "award",
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            # Already paid, and it stays paid at the original amount — the partial unique index makes that
            # a guarantee rather than a policy. Paying once is the property worth having; a triager who
            # clicks twice, a retried request and two people working the same queue all collapse to one
            # payment.
            #
            # The cost is that **correcting a mis-grade upward cannot pay the difference**, and the honest
            # thing is to say so rather than let the console show a new amount that no ledger entry backs.
            # The remedy is a super-admin adjustment with a note, which is what that lever is for.
            paid = int(existing.amount_kobo)
            drift = matrix_kobo - paid
            return AwardResult(
                matrix_kobo=matrix_kobo,
                credited_kobo=paid,
                blocked=BLOCK_ALREADY_AWARDED,
                entry_id=existing.id,
                season_number=program.season_number,
                message=(
                    None
                    if drift == 0
                    else (
                        f"Already paid {paid // 100:,} naira. This grading is worth "
                        f"{matrix_kobo // 100:,}, so the tester is "
                        f"{'owed' if drift > 0 else 'over-paid by'} {abs(drift) // 100:,} naira. "
                        "An award is written once; settle the difference with an adjustment."
                    )
                ),
            )

        if matrix_kobo <= 0:
            # Reached only if the season's table was edited between grading and awarding — `triage`
            # refuses an unpriced grading up front. Named rather than silently paying nothing.
            return AwardResult(
                matrix_kobo=0,
                credited_kobo=0,
                blocked=BLOCK_NOT_PRICED,
                entry_id=None,
                season_number=program.season_number,
                message=(
                    f"Season {program.season_number} has no amount for "
                    f"{submission.category}/{submission.severity}."
                ),
            )

        # The cap, computed from this participant's credits in this season only. Positive entries, so an
        # adjustment counts against the cap the same way an award does — otherwise the cap is a suggestion
        # anyone with adjustment rights can walk past.
        earned_this_season = (
            await session.execute(
                select(func.coalesce(func.sum(BugHuntLedgerEntry.amount_kobo), 0)).where(
                    BugHuntLedgerEntry.user_id == submission.user_id,
                    BugHuntLedgerEntry.program_id == program.id,
                    BugHuntLedgerEntry.amount_kobo > 0,
                )
            )
        ).scalar() or 0
        cap_remaining = program.per_participant_cap_kobo - int(earned_this_season)
        if cap_remaining <= 0:
            return AwardResult(
                matrix_kobo=matrix_kobo,
                credited_kobo=0,
                blocked=BLOCK_CAP_REACHED,
                entry_id=None,
                season_number=program.season_number,
                message=(
                    "This tester has reached the season cap of "
                    f"{program.per_participant_cap_kobo // 100:,} naira. The finding is recorded and "
                    "worth keeping; it pays nothing."
                ),
            )

        # Clamped to the cap, not refused by it. A tester ₦500 short of their cap who files a critical
        # should get the ₦500 — the cap is a ceiling on what we pay one person, not a reason to pay
        # nothing.
        credited = min(matrix_kobo, cap_remaining)

        # The budget. **Refused, not reduced** — see the module docstring.
        remaining_budget = program.budget_kobo - program.awarded_kobo
        if credited > remaining_budget:
            return AwardResult(
                matrix_kobo=matrix_kobo,
                credited_kobo=0,
                blocked=BLOCK_BUDGET_EXHAUSTED,
                entry_id=None,
                season_number=program.season_number,
                message=(
                    f"Season {program.season_number} has "
                    f"{remaining_budget // 100:,} naira left and this finding is worth "
                    f"{credited // 100:,}. Raise the budget, then award it — the finding stays accepted "
                    "and the money stays owed."
                ),
            )

        wallet = await ledger_service.get_or_create_wallet(submission.user_id)
        entry = BugHuntLedgerEntry(
            wallet_id=wallet.id,
            user_id=submission.user_id,
            program_id=program.id,
            participant_id=submission.participant_id,
            kind="award",
            amount_kobo=credited,
            submission_id=submission.id,
            note=(
                f"{submission.category}/{submission.severity}"
                + (f" (capped from {matrix_kobo})" if credited < matrix_kobo else "")
            ),
            created_by_user_id=staff_user_id,
        )
        session.add(entry)

        # Same transaction as the entry, under the same lock. This is what makes `awardedKobo` unable to
        # drift from the ledger, and what makes the CHECK constraint `awardedKobo <= budgetKobo` a real
        # ceiling rather than an assertion that fires after the fact.
        program.awarded_kobo = program.awarded_kobo + credited

        try:
            await session.commit()
        except IntegrityError as e:
            # The partial unique index won a race with another triager. Theirs paid; ours must not.
            await session.rollback()
            logger.info("bug_hunt: award race lost for submission=%s", submission_id)
            paid_by_winner = await ledger_service.award_for_submission(submission_id)
            return AwardResult(
                matrix_kobo=matrix_kobo,
                credited_kobo=int(paid_by_winner or 0),
                blocked=BLOCK_ALREADY_AWARDED,
                entry_id=None,
                season_number=program.season_number,
                # An `IntegrityError` that was *not* the unique index — a budget CHECK, say — leaves nothing
                # paid, and that is worth surfacing rather than reporting as a duplicate.
                message=None if paid_by_winner else f"Could not write the award: {e}",
            )
        await session.refresh(entry)
        season_number = program.season_number

    logger.info(
        "bug_hunt: awarded %d kobo for submission=%s (season %s, matrix %d)",
        credited,
        submission_id,
        season_number,
        matrix_kobo,
    )
    return AwardResult(
        matrix_kobo=matrix_kobo,
        credited_kobo=credited,
        blocked=None,
        entry_id=entry.id,
        season_number=season_number,
        message=(
            f"Capped at the season limit — worth {matrix_kobo // 100:,} naira, paid {credited // 100:,}."
            if credited < matrix_kobo
            else None
        ),
    )


async def adjust(
    *,
    user_id: str,
    amount_kobo: int,
    note: str,
    staff_user_id: str,
    program_id: str | None = None,
) -> BugHuntLedgerEntry:
    """A super admin's correction. **The only free-typed amount in the programme.**

    Either sign: a goodwill credit, or a clawback of an over-award. It carries a mandatory note because an
    unexplained adjustment on a ledger a tester can read is worse than no adjustment — and because this is
    the one entry whose amount traces to a person rather than to a published rule.

    Attributed to a season when one is given, which means it counts against that tester's cap. Deliberate:
    an adjustment that sidestepped the cap would make the cap advisory for anyone holding this permission.

    A clawback still cannot overdraw — it goes through `debit`'s lock like any other spend, so a correction
    cannot leave a tester owing us money.
    """
    cleaned = (note or "").strip()
    if not cleaned:
        raise ValidationError("An adjustment needs a reason. The tester can read this ledger.")
    if amount_kobo == 0:
        raise ValidationError("An adjustment of nothing is not an adjustment.")

    if amount_kobo > 0:
        if program_id is None:
            # Unattributed: goodwill for somebody between seasons, counting against no budget and no cap.
            return await ledger_service.credit(
                user_id=user_id,
                kind="adjustment",
                amount_kobo=amount_kobo,
                program_id=None,
                note=cleaned,
                created_by_user_id=staff_user_id,
            )
        return await _attributed_credit(
            user_id=user_id,
            program_id=program_id,
            amount_kobo=amount_kobo,
            note=cleaned,
            staff_user_id=staff_user_id,
        )

    # Negative — a clawback. It takes the wallet lock like any other spend, so a correction cannot race a
    # redemption and leave a tester owing us money. Not routed through `ledger_service.debit`, because that
    # rejects any kind outside `LEDGER_DEBIT_KINDS` and `adjustment` is deliberately sign-agnostic.
    wallet = await ledger_service.get_or_create_wallet(user_id)
    factory = get_session_factory()
    async with factory() as session:
        locked = (
            await session.execute(
                select(BugHuntWallet).where(BugHuntWallet.id == wallet.id).with_for_update()
            )
        ).scalar_one()
        available = (
            await session.execute(
                select(func.coalesce(func.sum(BugHuntLedgerEntry.amount_kobo), 0)).where(
                    BugHuntLedgerEntry.user_id == user_id
                )
            )
        ).scalar() or 0
        if int(available) + amount_kobo < 0:
            raise ValidationError(
                f"That would take the balance below zero: balance {int(available) // 100:,} naira, "
                f"clawback {-amount_kobo // 100:,}."
            )

        entry = BugHuntLedgerEntry(
            wallet_id=locked.id,
            user_id=user_id,
            program_id=program_id,
            kind="adjustment",
            amount_kobo=amount_kobo,
            note=cleaned,
            created_by_user_id=staff_user_id,
        )
        session.add(entry)
        await session.commit()
        await session.refresh(entry)

        # A clawback attributed to a season returns its budget, for the same reason a credit spends it.
        # Clamped at zero: `awardedKobo >= 0` is a CHECK, and a clawback of money awarded in a *different*
        # season must not drive this one negative.
        if program_id is not None:
            program = (
                await session.execute(
                    select(BugHuntProgram).where(BugHuntProgram.id == program_id).with_for_update()
                )
            ).scalar_one()
            program.awarded_kobo = max(0, program.awarded_kobo + amount_kobo)
            await session.commit()

    logger.info(
        "bug_hunt: adjustment %d kobo for user=%s by %s (%s)",
        amount_kobo,
        user_id,
        staff_user_id,
        cleaned,
    )
    return entry


async def _attributed_credit(
    *, user_id: str, program_id: str, amount_kobo: int, note: str, staff_user_id: str
) -> BugHuntLedgerEntry:
    """A positive adjustment charged to a season's budget.

    **The budget has to cover an adjustment too.** Without this it covers awards only, and a super admin —
    who is also the person able to raise the budget — can spend past it silently through the one endpoint
    that takes a free-typed amount. That makes the ceiling advisory for exactly the wrong person.

    So it takes the same lock and the same check an award does. A refusal here means "raise the budget
    first", which is one extra deliberate act on the only path in the programme where money does not trace
    to a published rule.
    """
    factory = get_session_factory()
    async with factory() as session:
        program = (
            await session.execute(
                select(BugHuntProgram).where(BugHuntProgram.id == program_id).with_for_update()
            )
        ).scalar_one()

        remaining = program.budget_kobo - program.awarded_kobo
        if amount_kobo > remaining:
            raise ValidationError(
                f"Season {program.season_number} has {remaining // 100:,} naira of budget left and this "
                f"adjustment is {amount_kobo // 100:,}. Raise the budget first."
            )

        wallet = await ledger_service.get_or_create_wallet(user_id)
        entry = BugHuntLedgerEntry(
            wallet_id=wallet.id,
            user_id=user_id,
            program_id=program_id,
            kind="adjustment",
            amount_kobo=amount_kobo,
            note=note,
            created_by_user_id=staff_user_id,
        )
        session.add(entry)
        program.awarded_kobo = program.awarded_kobo + amount_kobo
        await session.commit()
        await session.refresh(entry)

    logger.info(
        "bug_hunt: adjustment %d kobo for user=%s in season %s by %s (%s)",
        amount_kobo,
        user_id,
        program.season_number,
        staff_user_id,
        note,
    )
    return entry
