"""Who gets told what, and when.

One module between the domain services and ``bug_hunt.emails``, for two reasons. The address lookup
belongs somewhere that may touch the database, which the email module deliberately may not. And "which
event tells whom" is policy worth reading in one place rather than reconstructing from five call sites.

**Every function here is fire-and-forget and cannot fail its caller.** They are called *after* the
transaction that matters has committed, they swallow everything, and they return ``bool`` only so a test
can assert an attempt. This is not defensive habit: ``triage_service.triage`` commits a grading and then
moves money, and an exception raised after that on the way out would tell a triager the triage failed.
They would do it again, and the second attempt is the one that double-pays.

**The season-opening announcement is the odd one out**, and goes through the notification orchestrator
rather than straight to the transport. It is the only Bug Hunt message that is genuinely marketing: a
broadcast to a warm list of people who are not currently owed anything. So it is consent-gated,
carries an unsubscribe header, honours quiet hours and gets retries, all of which come free from
``notifications.service``. The rest are statements about somebody's own money and must arrive.

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import func, select

from src.shared.database import get_session_factory

from .. import emails
from ..db_models import BugHuntProgram, BugHuntSubmission, BugHuntWithdrawal
from . import ledger_service

logger = logging.getLogger(__name__)

#: Severity value to the words a tester reads. The programme never shows a raw enum to a participant:
#: "graded high_value" is a database artefact, not an explanation of why they were paid ₦1,500.
GRADE_LABELS: dict[str, str] = {
    "critical": "critical",
    "high": "high",
    "medium": "medium",
    "low": "low",
    "high_value": "feedback we will act on",
    "standard": "useful feedback",
}


async def _recipient(user_id: str | None) -> tuple[str, str | None] | None:
    """``(email, name)`` for a user, or ``None`` when there is nobody to write to.

    ``None`` is a real case rather than an error: ``BugHuntSubmission.userId`` is nullable behind
    ``ON DELETE SET NULL``, so a finding whose author deleted their account still exists and can still
    be triaged. Nobody to email is the correct outcome there, not a failure.
    """
    if not user_id:
        return None
    from src.domains.identity.db_models import User

    factory = get_session_factory()
    async with factory() as session:
        row = (
            await session.execute(select(User.email, User.name).where(User.id == user_id))
        ).first()
    if row is None or not row[0]:
        return None
    return str(row[0]), (str(row[1]) if row[1] else None)


async def _program(program_id: str) -> BugHuntProgram | None:
    factory = get_session_factory()
    async with factory() as session:
        return (
            await session.execute(select(BugHuntProgram).where(BugHuntProgram.id == program_id))
        ).scalar_one_or_none()


# ===========================================================================
# Applications
# ===========================================================================


async def application_decided(
    *, user_id: str, program_id: str, status: str, reason: str | None, attempt_count: int
) -> bool:
    """Tell an applicant they are in, or that they are not and why.

    Only ``approved`` and ``rejected`` send. A suspension is deliberately silent here: it is a
    moderation decision whose wording needs a person, and an automated "your participation is
    suspended" with no route to reply is the worst possible version of that message.
    """
    try:
        if status not in ("approved", "rejected"):
            return False
        recipient = await _recipient(user_id)
        program = await _program(program_id)
        if recipient is None or program is None:
            return False
        to_email, name = recipient

        if status == "approved":
            return await emails.application_approved(
                to_email=to_email,
                name=name,
                season_name=program.name,
                ends_at=program.ends_at.strftime("%d %B %Y"),
                daily_limit=program.submission_daily_limit,
                cap_kobo=program.per_participant_cap_kobo,
            )

        return await emails.application_rejected(
            to_email=to_email,
            name=name,
            season_name=program.name,
            reason=(reason or "").strip()
            or "We could not reproduce the issue from the steps given.",
            # Two attempts is the season limit, so a second rejection is the last one. Getting this
            # wrong in the encouraging direction is worse than in the discouraging one: inviting a
            # retry that the API will refuse wastes their evening and teaches them not to trust us.
            can_retry=attempt_count < 2,
        )
    except Exception:
        logger.exception("bug_hunt: application decision email failed for user=%s", user_id)
        return False


# ===========================================================================
# Findings
# ===========================================================================


async def submission_triaged(
    *,
    submission_id: str,
    award_kobo: int,
    blocked_reason: str | None = None,
) -> bool:
    """Tell a tester what happened to a finding.

    Reads the submission fresh rather than taking the caller's object, because the caller's copy was
    loaded inside the transaction that graded it and the award landed in a *second* one. Trusting the
    stale object is how the email reports ₦0 for a finding that was in fact paid.

    ``submitted`` and ``in_review`` never send. Moving a finding into review is an internal step, and
    emailing "somebody is looking at it now" is a message with no action in it.
    """
    try:
        factory = get_session_factory()
        async with factory() as session:
            submission = (
                await session.execute(
                    select(BugHuntSubmission).where(BugHuntSubmission.id == submission_id)
                )
            ).scalar_one_or_none()
        if submission is None or submission.status in ("submitted", "in_review"):
            return False

        recipient = await _recipient(submission.user_id)
        if recipient is None:
            return False
        to_email, name = recipient

        if submission.status == "accepted":
            balance = await ledger_service.balance(submission.user_id or "")
            return await emails.submission_accepted(
                to_email=to_email,
                name=name,
                title=submission.title,
                grade=GRADE_LABELS.get(
                    submission.severity or "", submission.severity or "a finding"
                ),
                award_kobo=award_kobo,
                balance_kobo=balance,
                public_response=submission.public_response,
                blocked_reason=blocked_reason,
            )

        return await emails.submission_declined(
            to_email=to_email,
            name=name,
            title=submission.title,
            status=submission.status,
            public_response=submission.public_response,
        )
    except Exception:
        logger.exception("bug_hunt: triage email failed for submission=%s", submission_id)
        return False


# ===========================================================================
# Cash
# ===========================================================================


async def withdrawal_decided(*, withdrawal_id: str) -> bool:
    """Tell a tester their cash request was approved.

    A **refusal sends nothing from here**, on purpose. A rejected request has a reason written by a
    person and returns money to a balance, and that is a message worth writing by hand rather than
    templating: the wallet screen already shows the reason and the reversal, so nobody is left
    uninformed while we get the wording right.
    """
    try:
        row = await _withdrawal(withdrawal_id)
        if row is None or row.status != "approved":
            return False
        recipient = await _recipient(row.user_id)
        if recipient is None:
            return False
        to_email, name = recipient
        return await emails.withdrawal_approved(
            to_email=to_email,
            name=name,
            amount_kobo=row.amount_kobo,
            bank_name=row.bank_name_snapshot,
            account_last4=row.account_last4_snapshot,
        )
    except Exception:
        logger.exception("bug_hunt: withdrawal approval email failed for %s", withdrawal_id)
        return False


async def withdrawal_paid(*, withdrawal_id: str) -> bool:
    """The one email in this module that nobody may quietly lose.

    It carries the bank reference, which is the only thing a tester can check against their own bank
    alert. Every other message here can be reconstructed from the dashboard; this one is the receipt.
    A failure is still swallowed, because the transfer has already happened and failing the request
    would invite an operator to record the payment twice, but it is logged loudly and the evidence row
    records the provider's refusal.
    """
    try:
        row = await _withdrawal(withdrawal_id)
        if row is None or row.status != "paid" or not row.provider_reference:
            return False
        recipient = await _recipient(row.user_id)
        if recipient is None:
            return False
        to_email, name = recipient
        return await emails.withdrawal_paid(
            to_email=to_email,
            name=name,
            amount_kobo=row.amount_kobo,
            bank_name=row.bank_name_snapshot,
            account_last4=row.account_last4_snapshot,
            reference=row.provider_reference,
        )
    except Exception:
        logger.exception("bug_hunt: withdrawal paid email failed for %s", withdrawal_id)
        return False


async def _withdrawal(withdrawal_id: str) -> BugHuntWithdrawal | None:
    factory = get_session_factory()
    async with factory() as session:
        return (
            await session.execute(
                select(BugHuntWithdrawal).where(BugHuntWithdrawal.id == withdrawal_id)
            )
        ).scalar_one_or_none()


# ===========================================================================
# The season-opening announcement
# ===========================================================================


async def announce_season(*, program_id: str) -> dict[str, Any]:
    """Tell the warm list that a new season is open.

    **The highest-value send in the programme**, and the reason participation carries forward at all: a
    list of people who have already proved they can write a reproducible bug report is worth more than
    any recruitment push.

    Two properties worth defending:

    *It goes through the notification orchestrator.* This is the one Bug Hunt message that is marketing
    rather than a statement about somebody's money, so it is consent-gated, unsubscribable and honours
    quiet hours. It is also fanned out to hundreds of people, and the orchestrator's queue is what
    stops an admin's single button press waiting on hundreds of provider round trips.

    *It is a separate action from opening the season.* Opening and announcing are different decisions:
    an operator may want the season live for a few minutes of smoke-testing before the list hears about
    it. The type's dedupe window then makes a second press harmless rather than a second email.
    """
    from src.domains.notifications import service as notification_service

    program = await _program(program_id)
    if program is None or program.status != "open":
        # Announcing a draft would tell the list to go and file findings against a season the API will
        # refuse them from. Announcing a closed one is worse.
        return {"sent": 0, "skipped": 0, "reason": "SEASON_NOT_OPEN"}

    audience = await _announce_audience(exclude_program_id=program_id)
    sent = 0
    skipped = 0
    for user_id in audience:
        try:
            await notification_service.create_notification(
                user_id=user_id,
                type="bug_hunt.season_open",
                title=f"{program.name} of the Bug Hunt is open",
                body=(
                    f"You took part before, so you are already in. Accept this season's terms and you "
                    f"can start filing. {program.name} closes "
                    f"{program.ends_at.strftime('%d %B %Y')}."
                ),
                action={"version": 1, "kind": "NONE"},
                # Keyed on the season, not the moment. A second press of the announce button is then a
                # no-op rather than a second email, which matters because the console cannot know
                # whether the first press finished before the operator got impatient.
                idempotency_key=f"bug_hunt.season_open:{program.id}:{user_id}",
                source_domain="bug_hunt",
                source_entity_type="BugHuntProgram",
                source_entity_id=program.id,
            )
            sent += 1
        except Exception:
            # One learner's missing notification policy must not stop the announcement reaching the
            # rest of the list, which is exactly what a raise in the middle of this loop would do.
            logger.exception("bug_hunt: season announcement failed for user=%s", user_id)
            skipped += 1

    logger.info("bug_hunt: announced %s to %d recipients (%d skipped)", program.slug, sent, skipped)
    return {"sent": sent, "skipped": skipped, "reason": None}


async def _announce_audience(*, exclude_program_id: str) -> list[str]:
    """Who hears about a new season.

    Everyone who has ever been an approved participant, plus **anyone holding a balance** even if they
    never got approved. The second group exists because an adjustment can credit somebody who was never
    a participant, and a person we owe money to is a person entitled to hear that a rail for spending it
    just reopened.

    Suspended participants are excluded. Inviting somebody to a season they cannot file in is a worse
    message than silence.

    Anyone already in the new season is excluded too, so the operator can press the button after
    carry-forward has run without emailing the people it just seeded... except that carry-forward seeds
    everybody, which would empty the audience. So the exclusion is on *acceptance*: a carried-forward
    participant has not accepted the new terms yet, and telling them to is the entire point of the send.
    """
    from ..db_models import BugHuntLedgerEntry, BugHuntParticipant

    factory = get_session_factory()
    async with factory() as session:
        approved = set(
            (
                await session.execute(
                    select(BugHuntParticipant.user_id).where(
                        BugHuntParticipant.status == "approved"
                    )
                )
            )
            .scalars()
            .all()
        )
        suspended = set(
            (
                await session.execute(
                    select(BugHuntParticipant.user_id).where(
                        BugHuntParticipant.status == "suspended"
                    )
                )
            )
            .scalars()
            .all()
        )
        # Summed from the ledger, because there is no cached balance to read: `BugHuntWallet` holds no
        # total by design, and a wallet row exists as soon as somebody is credited once. Grouping and
        # filtering on the sum is what distinguishes "has a wallet" from "is owed something", and only
        # the second group should be told a spending rail reopened.
        holders = set(
            (
                await session.execute(
                    select(BugHuntLedgerEntry.user_id)
                    .group_by(BugHuntLedgerEntry.user_id)
                    .having(func.coalesce(func.sum(BugHuntLedgerEntry.amount_kobo), 0) > 0)
                )
            )
            .scalars()
            .all()
        )
        already_accepted = set(
            (
                await session.execute(
                    select(BugHuntParticipant.user_id).where(
                        BugHuntParticipant.program_id == exclude_program_id,
                        BugHuntParticipant.accepted_rules_version.isnot(None),
                    )
                )
            )
            .scalars()
            .all()
        )

    return sorted((approved | holders) - suspended - already_accepted)


__all__ = [
    "application_decided",
    "submission_triaged",
    "withdrawal_decided",
    "withdrawal_paid",
    "announce_season",
    "announce_audience_size",
]


async def announce_audience_size(*, program_id: str) -> int:
    """How many people the announcement would reach, for the admin's confirmation step.

    A send to a warm list is not undoable, so the console shows the number before the button does
    anything. An operator who expected 40 and sees 1,200 has caught a mistake we could not.
    """
    return len(await _announce_audience(exclude_program_id=program_id))
