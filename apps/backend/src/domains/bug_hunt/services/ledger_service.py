"""The money. An append-only ledger of signed kobo, and the only thing that decides a balance.

There is no cached total, no `awardKobo` on the submission and no reconciliation job, because there is
nothing to reconcile against: a balance is `SUM(amountKobo)` over a wallet and that is the whole
definition. Entries are written and never updated or deleted — a reversal is a new row with the opposite
sign, so the history reads as what happened rather than as what we currently believe.

**Debits take a row lock. This is the part that matters.**

`points_service`, which this module otherwise mirrors, reads the spendable balance in one session and
writes the redemption in another. That is a check-then-act with no lock between the two steps: two
concurrent redemptions each see the same balance and both succeed, and the learner spends the same points
twice. It has survived because points are cheap and low-volume.

Kobo are not cheap. This programme has two independent spend rails — a pass redemption and a cash
withdrawal — and a tester with ₦1,500 could plausibly fire both at once, on purpose. So every debit here
opens one transaction, takes `SELECT … FOR UPDATE` on the `BugHuntWallet` row, sums the ledger *inside*
that transaction, and writes the entry before releasing. The second concurrent debit waits, re-reads a
balance that already reflects the first, and is refused.

The wallet row exists solely to be that lock target. Locking a participation would be locking the wrong
thing for a debit that spans seasons; locking `User` would reach outside this domain and serialise
unrelated writes.

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.shared.database import get_session_factory
from src.shared.exceptions import ConflictError, ValidationError

from ..db_models import (
    LEDGER_CREDIT_KINDS,
    LEDGER_DEBIT_KINDS,
    LEDGER_KINDS,
    WITHDRAWAL_OPEN_STATUSES,
    BugHuntLedgerEntry,
    BugHuntProgram,
    BugHuntWallet,
    BugHuntWithdrawal,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WalletSummary:
    """Everything the wallet screen shows, in one read.

    Lifetime, not per season — the wallet belongs to the person (§6.1). `earned_this_season_kobo` and
    `cap_remaining_kobo` are the only season-scoped figures, and they are `0` and the full cap when no
    season is open, which is the honest answer between seasons rather than a null the client has to guess
    at.
    """

    balance_kobo: int
    lifetime_awarded_kobo: int
    open_withdrawal_kobo: int
    earned_this_season_kobo: int
    cap_remaining_kobo: int


# ===========================================================================
# Wallet
# ===========================================================================


async def get_or_create_wallet(user_id: str) -> BugHuntWallet:
    """The user's wallet, created on first need.

    Created lazily rather than at signup, because most accounts will never take part in the programme and
    a row per learner would be a table of empty wallets. The unique index on `userId` is what makes the
    race benign: two concurrent creates leave one row and one `IntegrityError`, which is caught and
    re-read rather than surfaced.
    """
    factory = get_session_factory()
    async with factory() as session:
        existing = (
            await session.execute(select(BugHuntWallet).where(BugHuntWallet.user_id == user_id))
        ).scalar_one_or_none()
        if existing is not None:
            return existing

        wallet = BugHuntWallet(user_id=user_id)
        session.add(wallet)
        try:
            await session.commit()
        except IntegrityError:
            # Somebody else created it between the read and the insert. Theirs is as good as ours.
            await session.rollback()
            return (
                await session.execute(select(BugHuntWallet).where(BugHuntWallet.user_id == user_id))
            ).scalar_one()
        await session.refresh(wallet)

    logger.info("bug_hunt: wallet created for user=%s", user_id)
    return wallet


# ===========================================================================
# Reading
# ===========================================================================


async def balance(user_id: str) -> int:
    """Spendable kobo. `SUM(amountKobo)`, and nothing else.

    Cheap enough to call freely: a season produces tens of entries per tester, not thousands, which is
    exactly why this domain has no cached balance to go stale.
    """
    factory = get_session_factory()
    async with factory() as session:
        return await _balance_in(session, user_id)


async def _balance_in(session: AsyncSession, user_id: str) -> int:
    """The balance, inside a caller's transaction. Used by `debit` while it holds the wallet lock."""
    total = (
        await session.execute(
            select(func.coalesce(func.sum(BugHuntLedgerEntry.amount_kobo), 0)).where(
                BugHuntLedgerEntry.user_id == user_id
            )
        )
    ).scalar()
    return int(total or 0)


async def summary(*, user_id: str, program: BugHuntProgram | None) -> WalletSummary:
    """The wallet screen, in one read.

    `open_withdrawal_kobo` is reported separately from the balance rather than subtracted from it, even
    though the debit was already written when the request was made. A tester who has asked for ₦1,500 and
    is waiting on the transfer should see a balance of ₦0 *and* ₦1,500 on its way — not one number that
    could mean either.
    """
    factory = get_session_factory()
    async with factory() as session:
        total = await _balance_in(session, user_id)

        lifetime_awarded = (
            await session.execute(
                select(func.coalesce(func.sum(BugHuntLedgerEntry.amount_kobo), 0)).where(
                    BugHuntLedgerEntry.user_id == user_id,
                    BugHuntLedgerEntry.amount_kobo > 0,
                )
            )
        ).scalar() or 0

        open_withdrawal = (
            await session.execute(
                select(func.coalesce(func.sum(BugHuntWithdrawal.amount_kobo), 0)).where(
                    BugHuntWithdrawal.user_id == user_id,
                    BugHuntWithdrawal.status.in_(WITHDRAWAL_OPEN_STATUSES),
                )
            )
        ).scalar() or 0

        earned_this_season = 0
        if program is not None:
            earned_this_season = (
                await session.execute(
                    select(func.coalesce(func.sum(BugHuntLedgerEntry.amount_kobo), 0)).where(
                        BugHuntLedgerEntry.user_id == user_id,
                        BugHuntLedgerEntry.program_id == program.id,
                        BugHuntLedgerEntry.amount_kobo > 0,
                    )
                )
            ).scalar() or 0

    cap_remaining = (
        max(0, program.per_participant_cap_kobo - int(earned_this_season))
        if program is not None
        else 0
    )
    return WalletSummary(
        balance_kobo=total,
        lifetime_awarded_kobo=int(lifetime_awarded),
        open_withdrawal_kobo=int(open_withdrawal),
        earned_this_season_kobo=int(earned_this_season),
        cap_remaining_kobo=cap_remaining,
    )


async def history(
    *, user_id: str, page: int = 1, page_size: int = 50
) -> tuple[list[tuple[BugHuntLedgerEntry, int | None]], int]:
    """The whole ledger, newest first, each entry with the season number it belongs to.

    Every entry, not a filtered view: the wallet explains its own number, and a balance a tester cannot
    account for line by line is a support ticket. Season numbers come along so the client can group
    awards by season without a request per row — a spend carries `None`, because it belongs to no season.
    """
    offset = max(0, (page - 1) * page_size)
    factory = get_session_factory()
    async with factory() as session:
        total = (
            await session.execute(
                select(func.count())
                .select_from(BugHuntLedgerEntry)
                .where(BugHuntLedgerEntry.user_id == user_id)
            )
        ).scalar() or 0
        rows = (
            await session.execute(
                select(BugHuntLedgerEntry, BugHuntProgram.season_number)
                .outerjoin(BugHuntProgram, BugHuntProgram.id == BugHuntLedgerEntry.program_id)
                .where(BugHuntLedgerEntry.user_id == user_id)
                .order_by(BugHuntLedgerEntry.created_at.desc())
                .offset(offset)
                .limit(page_size)
            )
        ).all()
    return [(row[0], row[1]) for row in rows], int(total)


async def award_for_submission(submission_id: str) -> int | None:
    """What one submission was awarded, or `None` if it has not been.

    `None` and `0` are different answers and both are reachable: nothing awarded yet, versus a grading
    that pays nothing. The wallet and the queue render them differently.
    """
    factory = get_session_factory()
    async with factory() as session:
        return (
            await session.execute(
                select(BugHuntLedgerEntry.amount_kobo).where(
                    BugHuntLedgerEntry.submission_id == submission_id,
                    BugHuntLedgerEntry.kind == "award",
                )
            )
        ).scalar()


# ===========================================================================
# Writing
# ===========================================================================


#: Kinds that must name the withdrawal they belong to. Mirrors
#: `BugHuntLedgerEntry_withdrawal_link_check`; checked here so a caller gets a sentence instead of an
#: `IntegrityError`, which is what the constraint alone produces.
_WITHDRAWAL_LINKED_KINDS = frozenset({"withdrawal", "withdrawal_reversal"})


def _validate(kind: str, amount_kobo: int, *, withdrawal_id: str | None = None) -> None:
    """Refuse an incoherent entry before Postgres has to.

    The CHECK constraints are the guarantee; these are the readable messages. A caller that trips one of
    these has a bug, so the message is written for whoever is reading the traceback rather than for a tester.
    """
    if kind not in LEDGER_KINDS:
        raise ValidationError(f"Unknown ledger kind {kind!r}.")
    if amount_kobo == 0:
        raise ValidationError("A ledger entry worth nothing is a bug, not a note.")
    if kind in LEDGER_CREDIT_KINDS and amount_kobo < 0:
        raise ValidationError(f"{kind} is a credit and must be positive.")
    if kind in LEDGER_DEBIT_KINDS and amount_kobo > 0:
        raise ValidationError(f"{kind} is a debit and must be negative.")
    if kind in _WITHDRAWAL_LINKED_KINDS and not withdrawal_id:
        # A cash movement that names no request cannot be reconciled against one, which makes it
        # indistinguishable from a debit nobody asked for.
        raise ValidationError(f"{kind} must name the withdrawal it belongs to.")


async def credit(
    *,
    user_id: str,
    kind: str,
    amount_kobo: int,
    program_id: str | None = None,
    participant_id: str | None = None,
    submission_id: str | None = None,
    withdrawal_id: str | None = None,
    pass_id: str | None = None,
    note: str | None = None,
    created_by_user_id: str | None = None,
) -> BugHuntLedgerEntry:
    """Add money. No lock needed — a credit cannot overdraw anything.

    Awards go through `reward_service.award_submission` rather than here, because an award has to check a
    cap and a budget in the same transaction as the write. This is the primitive for reversals and for
    super-admin adjustments.
    """
    _validate(kind, amount_kobo, withdrawal_id=withdrawal_id)
    wallet = await get_or_create_wallet(user_id)

    entry = BugHuntLedgerEntry(
        wallet_id=wallet.id,
        user_id=user_id,
        program_id=program_id,
        participant_id=participant_id,
        kind=kind,
        amount_kobo=amount_kobo,
        submission_id=submission_id,
        withdrawal_id=withdrawal_id,
        pass_id=pass_id,
        note=note,
        created_by_user_id=created_by_user_id,
    )
    factory = get_session_factory()
    async with factory() as session:
        session.add(entry)
        await session.commit()
        await session.refresh(entry)

    logger.info(
        "bug_hunt: credited %d kobo to user=%s as %s (entry=%s)",
        amount_kobo,
        user_id,
        kind,
        entry.id,
    )
    return entry


async def debit(
    *,
    user_id: str,
    kind: str,
    amount_kobo: int,
    withdrawal_id: str | None = None,
    pass_id: str | None = None,
    note: str | None = None,
    created_by_user_id: str | None = None,
) -> BugHuntLedgerEntry:
    """Spend money. **Serialised on the wallet row, and refuses to overdraw.**

    `amount_kobo` is negative, matching what is stored, so a reader of a call site sees the sign the
    ledger will hold rather than having to know this function flips it.

    The lock is the whole point. Sequence, all inside one transaction:

    1. `SELECT … FOR UPDATE` the wallet row. A second debit for the same tester blocks here.
    2. Sum the ledger. Because of (1), that sum already includes any debit that won the race.
    3. Refuse if the balance will not cover it.
    4. Insert, and commit — releasing the lock.

    Without step 1, two concurrent debits both read the pre-spend balance and both succeed. That is not a
    theoretical race in this domain: a pass redemption and a cash withdrawal are two separate buttons on
    the same screen, and a tester with exactly enough for one of them has an incentive to press both.

    No `programId` is set, and a CHECK enforces that: a spend belongs to no season. Attributing one would
    count it against a budget it never spent.
    """
    # The kind check comes first, before the sign check inside `_validate`. A caller passing `award` here has
    # reached for the wrong function, and "award is not a spend" says that; "award must be positive" sends
    # them off to flip a sign that was never the problem.
    if kind not in LEDGER_DEBIT_KINDS:
        raise ValidationError(f"{kind} is not a spend.")
    _validate(kind, amount_kobo, withdrawal_id=withdrawal_id)

    wallet = await get_or_create_wallet(user_id)

    factory = get_session_factory()
    async with factory() as session:
        # Step 1. `with_for_update` on the wallet, not on the ledger: the rows being counted do not exist
        # yet, so there is nothing there to lock. The wallet is the stand-in for "this tester's money".
        locked = (
            await session.execute(
                select(BugHuntWallet).where(BugHuntWallet.id == wallet.id).with_for_update()
            )
        ).scalar_one()

        available = await _balance_in(session, user_id)
        if available + amount_kobo < 0:
            raise ConflictError(
                message="That is more than your balance.",
                detail=f"available={available} requested={-amount_kobo}",
                code="INSUFFICIENT_BALANCE",
            )

        entry = BugHuntLedgerEntry(
            wallet_id=locked.id,
            user_id=user_id,
            program_id=None,
            kind=kind,
            amount_kobo=amount_kobo,
            withdrawal_id=withdrawal_id,
            pass_id=pass_id,
            note=note,
            created_by_user_id=created_by_user_id,
        )
        session.add(entry)
        await session.commit()
        await session.refresh(entry)

    logger.info(
        "bug_hunt: debited %d kobo from user=%s as %s (entry=%s, balance was %d)",
        -amount_kobo,
        user_id,
        kind,
        entry.id,
        available,
    )
    return entry


async def reverse(
    *,
    entry: BugHuntLedgerEntry,
    note: str,
    created_by_user_id: str | None = None,
) -> BugHuntLedgerEntry:
    """Undo a debit by writing its opposite. Never by deleting the original.

    A deleted row is a balance nobody can explain: a tester who requested a withdrawal, had it rejected,
    and sees their balance restored with no trace of either event has been given a number and no story.
    Two rows that cancel are the story.
    """
    reversal_kind = f"{entry.kind}_reversal"
    if reversal_kind not in LEDGER_CREDIT_KINDS:
        raise ValidationError(f"{entry.kind} has no reversal.")

    return await credit(
        user_id=entry.user_id,
        kind=reversal_kind,
        amount_kobo=-entry.amount_kobo,
        withdrawal_id=entry.withdrawal_id,
        pass_id=entry.pass_id,
        note=note,
        created_by_user_id=created_by_user_id,
    )
