"""Cash out. Requested by a tester, transferred by hand, then recorded.

**There is no transfer API here, and that is the design, not a gap.** Season 1 pays by bank transfer made by
a person, so `mark_paid` records a movement that has *already happened*. Every name in this module is chosen
to keep that straight: `decide` approves a request, `mark_paid` records evidence of a completed transfer, and
`revert_to_approved` exists for the case where the bank refused it. Nothing here moves money, and the admin
control is labelled *Record payment* rather than *Pay* for the same reason — a button that sounds like it
pays invites somebody to click it first and transfer afterwards, which is how a tester ends up marked paid
with no money.

**The debit happens at request time.** Not at approval, and not at payment. A pending request must not be
spendable twice over, and the alternative — holding the balance "logically" and debiting later — is two
sources of truth for one number. A rejection writes a compensating credit; it does not delete the debit.

Three invariants live in the database rather than here:

- One open request per tester, by partial unique index on `userId WHERE status IN ('requested','approved')`.
  A count-then-insert has no lock between its steps.
- A `paid` row must carry a bank reference and a `paidAt`. A payout that cannot be matched to a statement
  line is indistinguishable from one that never happened.
- A `rejected` row must carry a reason. The tester reads it.

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from src.shared.database import get_session_factory, ilike_any
from src.shared.exceptions import ConflictError, NotFoundError, ValidationError

from .. import payout_crypto, rewards
from ..db_models import (
    WITHDRAWAL_OPEN_STATUSES,
    BugHuntLedgerEntry,
    BugHuntPayoutAccount,
    BugHuntWithdrawal,
)
from . import ledger_service, program_service

logger = logging.getLogger(__name__)

#: How long new withdrawals are held after the bank details change.
#:
#: An account takeover that can redirect a payout *instantly* is worth much more to an attacker than one that
#: cannot, and a day is long enough for the real owner to notice the "your bank details changed" mail. The
#: cost is a day's delay for a tester who legitimately switched banks mid-season, which is the right trade.
PAYOUT_ACCOUNT_CHANGE_HOLD = timedelta(hours=24)


@dataclass(frozen=True)
class PayoutAccountView:
    """A payout account as anyone other than the person making the transfer sees it.

    No field here can be used to send money anywhere. `account_number_last4` identifies the account without
    disclosing it, which is what lets a tester confirm they entered the right one.
    """

    id: str
    bank_code: str
    bank_name: str
    account_name: str
    account_number_last4: str
    verified_at: datetime | None
    changed_at: datetime | None
    held_until: datetime | None


def _view(account: BugHuntPayoutAccount) -> PayoutAccountView:
    held_until = account.changed_at + PAYOUT_ACCOUNT_CHANGE_HOLD if account.changed_at else None
    return PayoutAccountView(
        id=account.id,
        bank_code=account.bank_code,
        bank_name=account.bank_name,
        account_name=account.account_name,
        account_number_last4=account.account_number_last4,
        verified_at=account.verified_at,
        changed_at=account.changed_at,
        held_until=held_until if held_until and held_until > datetime.now(UTC) else None,
    )


# ===========================================================================
# Payout account
# ===========================================================================


async def get_account(user_id: str) -> PayoutAccountView | None:
    factory = get_session_factory()
    async with factory() as session:
        row = (
            await session.execute(
                select(BugHuntPayoutAccount).where(BugHuntPayoutAccount.user_id == user_id)
            )
        ).scalar_one_or_none()
    return _view(row) if row is not None else None


async def set_account(
    *,
    user_id: str,
    bank_code: str,
    bank_name: str,
    account_number: str,
    account_name: str,
) -> PayoutAccountView:
    """Store or replace a tester's bank details. One account per person, reused every season.

    The number is normalised and shape-checked before encryption: a mistyped account number is discovered
    either here or by a transfer to a stranger, and the first is very much better. `changed_at` is stamped on
    every write, which starts the 24-hour hold on new withdrawals.
    """
    try:
        cleaned = payout_crypto.normalise_account_number(account_number)
    except payout_crypto.InvalidAccountNumber as e:
        raise ValidationError(str(e)) from e

    for label, value in (("bank", bank_name), ("account name", account_name)):
        if not (value or "").strip():
            raise ValidationError(f"Tell us the {label} on the account.")

    now = datetime.now(UTC)
    factory = get_session_factory()
    async with factory() as session:
        row = (
            await session.execute(
                select(BugHuntPayoutAccount).where(BugHuntPayoutAccount.user_id == user_id)
            )
        ).scalar_one_or_none()

        if row is None:
            row = BugHuntPayoutAccount(
                user_id=user_id,
                bank_code=bank_code.strip(),
                bank_name=bank_name.strip(),
                account_number_enc=payout_crypto.encrypt(cleaned),
                account_number_last4=payout_crypto.last4(cleaned),
                account_name=account_name.strip(),
                # No hold on first entry: there is no previous account for an attacker to be redirecting
                # money away from, and holding a tester's first withdrawal for a day would be friction with
                # nothing behind it.
                changed_at=None,
            )
            session.add(row)
        else:
            unchanged = (
                row.account_number_last4 == payout_crypto.last4(cleaned)
                and row.bank_code == bank_code.strip()
            )
            row.bank_code = bank_code.strip()
            row.bank_name = bank_name.strip()
            row.account_number_enc = payout_crypto.encrypt(cleaned)
            row.account_number_last4 = payout_crypto.last4(cleaned)
            row.account_name = account_name.strip()
            # Correcting a spelling in the account name is not a redirection, so it does not restart the
            # hold. Changing the bank or the number is.
            if not unchanged:
                row.changed_at = now
            row.verified_at = None
        await session.commit()
        await session.refresh(row)

    logger.info("bug_hunt: payout account set for user=%s bank=%s", user_id, row.bank_code)
    return _view(row)


async def reveal_account_number(user_id: str) -> str:
    """The full account number, for the one person about to make a transfer.

    Called from a single super-admin endpoint which audits the read. Separated into its own function so that
    every disclosure of a full account number is one call site somebody can find by grepping.
    """
    factory = get_session_factory()
    async with factory() as session:
        row = (
            await session.execute(
                select(BugHuntPayoutAccount).where(BugHuntPayoutAccount.user_id == user_id)
            )
        ).scalar_one_or_none()
    if row is None:
        raise NotFoundError("Payout account", user_id)
    return payout_crypto.decrypt(row.account_number_enc)


# ===========================================================================
# Requesting
# ===========================================================================


def minimum_kobo(program: Any | None) -> int:
    """The smallest cash request this season accepts, or the default between seasons."""
    return int(
        program.min_withdrawal_kobo if program is not None else rewards.DEFAULT_MIN_WITHDRAWAL_KOBO
    )


async def request(*, user_id: str, amount_kobo: int) -> BugHuntWithdrawal:
    """Ask for cash. Debits immediately, so the balance cannot be spent twice.

    Order of operations, and the one awkward bit: the ledger entry has to name the withdrawal it belongs to
    (a CHECK enforces it), so the request row is written first and the debit second. If the debit is refused —
    insufficient balance, or a lost race — the request row is **deleted**. That is not destroying a financial
    record: no money moved, nothing was debited, and leaving it would occupy the one-open-request slot with a
    request that was never funded.
    """
    program = await program_service.current()
    minimum = minimum_kobo(program)
    if amount_kobo < minimum:
        raise ValidationError(
            f"The smallest cash request is {minimum // 100:,} naira. "
            "A Plus pass has no minimum, if you would rather take one of those."
        )

    account = await get_account(user_id)
    if account is None:
        raise ConflictError(
            message="Add your bank details before requesting cash.",
            code="PAYOUT_ACCOUNT_MISSING",
        )
    if account.held_until is not None:
        raise ConflictError(
            message="Your bank details changed recently, so cash requests are paused for a day.",
            detail=f"held_until={account.held_until.isoformat()}",
            code="PAYOUT_ACCOUNT_HELD",
        )

    balance = await ledger_service.balance(user_id)
    if balance < amount_kobo:
        raise ConflictError(
            message="That is more than your balance.",
            detail=f"available={balance} requested={amount_kobo}",
            code="INSUFFICIENT_BALANCE",
        )

    wallet = await ledger_service.get_or_create_wallet(user_id)
    row = BugHuntWithdrawal(
        wallet_id=wallet.id,
        user_id=user_id,
        amount_kobo=amount_kobo,
        status="requested",
        payout_account_id=account.id,
        # Snapshotted now, so a historic payout stays reconcilable against a bank statement after the
        # retention sweep deletes the account row — and without keeping the full number for years.
        bank_name_snapshot=account.bank_name,
        account_name_snapshot=account.account_name,
        account_last4_snapshot=account.account_number_last4,
    )

    factory = get_session_factory()
    async with factory() as session:
        session.add(row)
        try:
            await session.commit()
        except IntegrityError as e:
            await session.rollback()
            # The partial unique index. One open request at a time, enforced where it cannot be raced.
            raise ConflictError(
                message="You already have a cash request open. It has to be settled first.",
                detail=str(e),
                code="WITHDRAWAL_ALREADY_OPEN",
            ) from e
        await session.refresh(row)

    try:
        await ledger_service.debit(
            user_id=user_id,
            kind="withdrawal",
            amount_kobo=-amount_kobo,
            withdrawal_id=row.id,
            note=f"Cash to {account.bank_name} ••••{account.account_number_last4}",
        )
    except Exception:
        # Unfunded, so the request never existed as far as the money is concerned. Deleting it frees the
        # one-open-request slot; leaving it would block every future request with a row that holds nothing.
        async with factory() as session:
            stale = (
                await session.execute(
                    select(BugHuntWithdrawal).where(BugHuntWithdrawal.id == row.id)
                )
            ).scalar_one_or_none()
            if stale is not None:
                await session.delete(stale)
                await session.commit()
        logger.warning("bug_hunt: withdrawal %s rolled back — debit refused", row.id)
        raise

    logger.info(
        "bug_hunt: withdrawal %s requested by user=%s for %d kobo", row.id, user_id, amount_kobo
    )
    return row


async def list_own(*, user_id: str) -> list[BugHuntWithdrawal]:
    """A tester's own requests, newest first. What turns the dashboard row into Requested → Approved → Paid."""
    factory = get_session_factory()
    async with factory() as session:
        return list(
            (
                await session.execute(
                    select(BugHuntWithdrawal)
                    .where(BugHuntWithdrawal.user_id == user_id)
                    .order_by(BugHuntWithdrawal.created_at.desc())
                )
            )
            .scalars()
            .all()
        )


# ===========================================================================
# Staff: the payout queue
# ===========================================================================


async def list_all(
    *,
    status: str | None = None,
    search: str | None = None,
    page: int = 1,
    page_size: int = 25,
) -> tuple[list[tuple[BugHuntWithdrawal, str | None, str | None]], int]:
    """The payout queue, **oldest first**, with each payee's email and name.

    Oldest first because this is a queue of people waiting for money, and it is the one queue where being
    pushed down the page has a direct cost to somebody.
    """
    from src.domains.identity.db_models import User

    conditions: list[Any] = []
    if status:
        conditions.append(BugHuntWithdrawal.status == status)
    if search:
        conditions.append(
            ilike_any(search, User.email, User.name, BugHuntWithdrawal.account_name_snapshot)
        )
    offset = max(0, (page - 1) * page_size)

    factory = get_session_factory()
    async with factory() as session:
        total = (
            await session.execute(
                select(func.count())
                .select_from(BugHuntWithdrawal)
                .outerjoin(User, User.id == BugHuntWithdrawal.user_id)
                .where(*conditions)
            )
        ).scalar() or 0
        rows = (
            await session.execute(
                select(BugHuntWithdrawal, User.email, User.name)
                .outerjoin(User, User.id == BugHuntWithdrawal.user_id)
                .where(*conditions)
                .order_by(BugHuntWithdrawal.created_at.asc())
                .offset(offset)
                .limit(page_size)
            )
        ).all()
    return [(row[0], row[1], row[2]) for row in rows], int(total)


async def detail(withdrawal_id: str) -> dict[str, Any]:
    """One request, with everything the person making the transfer needs — including the full number.

    **This is the only place a full account number leaves the database.** It is a super-admin endpoint and it
    is audited. The payee's payout history comes along because a first-time payee should be visibly a
    first-time payee.

    An unreadable ciphertext does not fail the read: `accountNumber` comes back `None` with
    `accountNumberUnreadable` set, so staff can see who it is from the snapshot and ask them to re-enter
    their details. A 500 here would make an operational problem look like an outage.
    """
    from src.domains.identity.db_models import User

    factory = get_session_factory()
    async with factory() as session:
        row = (
            await session.execute(
                select(BugHuntWithdrawal, User.email, User.name)
                .outerjoin(User, User.id == BugHuntWithdrawal.user_id)
                .where(BugHuntWithdrawal.id == withdrawal_id)
            )
        ).first()
        if row is None:
            raise NotFoundError("Withdrawal", withdrawal_id)
        withdrawal, email, name = row

        account = None
        if withdrawal.payout_account_id:
            account = (
                await session.execute(
                    select(BugHuntPayoutAccount).where(
                        BugHuntPayoutAccount.id == withdrawal.payout_account_id
                    )
                )
            ).scalar_one_or_none()

        history_count = 0
        paid_before = 0
        if withdrawal.user_id:
            history_count = (
                await session.execute(
                    select(func.count())
                    .select_from(BugHuntWithdrawal)
                    .where(BugHuntWithdrawal.user_id == withdrawal.user_id)
                )
            ).scalar() or 0
            paid_before = (
                await session.execute(
                    select(func.coalesce(func.sum(BugHuntWithdrawal.amount_kobo), 0)).where(
                        BugHuntWithdrawal.user_id == withdrawal.user_id,
                        BugHuntWithdrawal.status == "paid",
                    )
                )
            ).scalar() or 0

    account_number: str | None = None
    unreadable = False
    if account is not None:
        try:
            account_number = payout_crypto.decrypt(account.account_number_enc)
        except payout_crypto.PayoutAccountUnreadable:
            unreadable = True
            logger.warning(
                "bug_hunt: payout account for withdrawal %s is unreadable", withdrawal_id
            )

    return {
        "withdrawal": withdrawal,
        "email": email,
        "name": name,
        "accountNumber": account_number,
        "accountNumberUnreadable": unreadable,
        "payoutCount": int(history_count),
        "paidBeforeKobo": int(paid_before),
    }


async def decide(
    *, withdrawal_id: str, decision: str, reason: str | None, staff_user_id: str
) -> BugHuntWithdrawal:
    """Approve a request, or reject it and give the money back.

    Approval changes nothing about the money — the debit was written when the request was made — so it is
    purely a statement that somebody is going to make this transfer. Rejection writes a **compensating
    credit** rather than deleting the debit, so the tester's history shows the request and its refund rather
    than a balance that changed for no visible reason.
    """
    if decision not in ("approve", "reject"):
        raise ValidationError("A decision is either approve or reject.")
    cleaned = (reason or "").strip()
    if decision == "reject" and not cleaned:
        raise ValidationError("Tell them why. They are owed a reason for a refused payout.")

    factory = get_session_factory()
    async with factory() as session:
        row = (
            await session.execute(
                select(BugHuntWithdrawal).where(BugHuntWithdrawal.id == withdrawal_id)
            )
        ).scalar_one_or_none()
        if row is None:
            raise NotFoundError("Withdrawal", withdrawal_id)
        if row.status not in WITHDRAWAL_OPEN_STATUSES:
            raise ConflictError(
                message=f"This request is already {row.status}.",
                detail=f"status={row.status}",
                code="WITHDRAWAL_SETTLED",
            )
        if decision == "approve" and row.status == "approved":
            return row

        row.status = "approved" if decision == "approve" else "rejected"
        row.decided_at = datetime.now(UTC)
        row.decided_by_user_id = staff_user_id
        row.rejection_reason = cleaned or None
        await session.commit()
        await session.refresh(row)

    if row.status == "rejected":
        debit = await _debit_entry(withdrawal_id)
        if debit is not None:
            await ledger_service.reverse(
                entry=debit,
                note=f"Cash request refused: {cleaned}",
                created_by_user_id=staff_user_id,
            )

    logger.info("bug_hunt: withdrawal %s %s by %s", withdrawal_id, row.status, staff_user_id)
    return row


async def _debit_entry(withdrawal_id: str) -> BugHuntLedgerEntry | None:
    """The `withdrawal` debit for this request, if one was written."""
    factory = get_session_factory()
    async with factory() as session:
        return (
            await session.execute(
                select(BugHuntLedgerEntry).where(
                    BugHuntLedgerEntry.withdrawal_id == withdrawal_id,
                    BugHuntLedgerEntry.kind == "withdrawal",
                )
            )
        ).scalar_one_or_none()


async def mark_paid(
    *, withdrawal_id: str, provider_reference: str, staff_user_id: str
) -> BugHuntWithdrawal:
    """Record a transfer **that has already been made.**

    This moves no money. It is the operator writing down what they just did in their banking app, and the
    reference is mandatory because a payout that cannot be matched to a statement line is indistinguishable
    from one that never happened.

    No ledger entry is written here either: the debit was recorded at request time. Anything else would pay
    the same request twice — once out of the wallet and once out of the bank.
    """
    reference = (provider_reference or "").strip()
    if not reference:
        raise ValidationError(
            "Record the bank's transaction reference. Without it this payout cannot be reconciled."
        )

    factory = get_session_factory()
    async with factory() as session:
        row = (
            await session.execute(
                select(BugHuntWithdrawal).where(BugHuntWithdrawal.id == withdrawal_id)
            )
        ).scalar_one_or_none()
        if row is None:
            raise NotFoundError("Withdrawal", withdrawal_id)
        if row.status == "paid":
            return row
        if row.status != "approved":
            raise ConflictError(
                message="Approve this request before recording a payment against it.",
                detail=f"status={row.status}",
                code="WITHDRAWAL_NOT_APPROVED",
            )

        row.status = "paid"
        row.provider_reference = reference
        row.paid_at = datetime.now(UTC)
        await session.commit()
        await session.refresh(row)

    logger.info(
        "bug_hunt: withdrawal %s recorded paid (ref=%s) by %s — the finance expense line is a "
        "separate, manual step (see the note on `_mirror_to_finance`)",
        withdrawal_id,
        reference,
        staff_user_id,
    )
    return row


async def revert_to_approved(
    *, withdrawal_id: str, reason: str, staff_user_id: str
) -> BugHuntWithdrawal:
    """Undo a *recording* mistake, or a transfer the bank refused after we wrote it down.

    Touches no ledger entry, and there is a test asserting that: nothing left the wallet at this stage, so
    nothing needs putting back. It only says the payout is not settled after all.
    """
    cleaned = (reason or "").strip()
    if not cleaned:
        raise ValidationError("Say why this payout is going back in the queue.")

    factory = get_session_factory()
    async with factory() as session:
        row = (
            await session.execute(
                select(BugHuntWithdrawal).where(BugHuntWithdrawal.id == withdrawal_id)
            )
        ).scalar_one_or_none()
        if row is None:
            raise NotFoundError("Withdrawal", withdrawal_id)
        if row.status != "paid":
            raise ConflictError(
                message="Only a payout recorded as paid can go back to approved.",
                detail=f"status={row.status}",
                code="WITHDRAWAL_NOT_PAID",
            )
        row.status = "approved"
        row.provider_reference = None
        row.paid_at = None
        row.rejection_reason = None
        await session.commit()
        await session.refresh(row)

    logger.info(
        "bug_hunt: withdrawal %s reverted to approved by %s (%s)",
        withdrawal_id,
        staff_user_id,
        cleaned,
    )
    return row


# ---------------------------------------------------------------------------
# Why there is no automatic finance mirror
# ---------------------------------------------------------------------------
#
# The plan called for a paid withdrawal to be mirrored into `finance` as an expense, so the programme's cash
# cost sits with every other expense. It was written, and then removed, because it cannot be done honestly.
#
# `finance.routes._resolve_gbp` is explicit: *"Never invents a rate: GBP lines are 1:1; everything else must
# carry an operator-entered GBP figure."* `LedgerLine.amountGbp` is `NOT NULL`, and the `fx-preview` endpoint
# exists so a human can *review* a rate before accepting it — the ledger deliberately refuses to convert on
# its own. A NGN payout therefore cannot become a ledger line without somebody supplying the GBP figure.
#
# An automatic mirror could only satisfy that by inventing the number the finance domain declines to invent,
# from inside a `try/except` that swallows its own failures. That is overriding another domain's stated
# invariant, silently, on a financial record. The wrong number in the books is worse than no line at all.
#
# So: `mark_paid` records the payout here and logs a reminder, `financeEntryId` stays null, and adding the
# expense line is a step the finance operator takes with the reference from the payout console. Recorded as an
# open item in the plan rather than left as a surprise.
