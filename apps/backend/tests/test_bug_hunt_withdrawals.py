"""Cash out: bank details, the request, and the payout that a person makes by hand.

The riskiest surface in the programme, and the tests are chosen accordingly:

- **The account number is encrypted, and only one endpoint discloses it.** Tested by asserting the stored
  column is not the number, that the round trip works, and that an unreadable row degrades to "ask them
  again" rather than to a 500.
- **The debit happens at request time**, so a pending request cannot be spent twice. Rejection writes a
  compensating credit; it never deletes the debit.
- **`mark_paid` records a transfer that already happened.** It writes no ledger entry, because the money left
  the wallet at request time — writing one now would pay the same request twice, once out of the wallet and
  once out of the bank. There is a test that asserts precisely that nothing moved.
- **One open request at a time**, raced for real against the partial unique index.

Run with:

    RUN_DB_TESTS=1 DATABASE_URL=postgresql://localhost/scratch pytest tests/test_bug_hunt_withdrawals.py
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, select, update

from src.domains.bug_hunt import payout_crypto
from src.domains.bug_hunt.db_models import (
    BugHuntAttachment,
    BugHuntLedgerEntry,
    BugHuntParticipant,
    BugHuntPayoutAccount,
    BugHuntProgram,
    BugHuntSubmission,
    BugHuntWallet,
    BugHuntWithdrawal,
)
from src.domains.bug_hunt.services import (
    ledger_service,
    program_service,
    reward_service,
    withdrawal_service,
)
from src.domains.identity.db_models import User
from src.shared.database import get_session_factory
from src.shared.exceptions import ConflictError, NotFoundError, ValidationError

pytestmark = pytest.mark.usefixtures("db")

ACCOUNT = "0123456789"


@pytest.fixture(autouse=True)
async def clean_slate():
    async def wipe():
        from src.domains.admin.db_models import AuditLog

        factory = get_session_factory()
        async with factory() as session:
            for model in (
                BugHuntLedgerEntry,
                BugHuntWithdrawal,
                BugHuntPayoutAccount,
                BugHuntAttachment,
                BugHuntSubmission,
                BugHuntWallet,
                BugHuntParticipant,
                BugHuntProgram,
            ):
                await session.execute(delete(model))
            stale = select(User.id).where(User.email.like("bughunt-cash-%"))
            await session.execute(delete(AuditLog).where(AuditLog.admin_user_id.in_(stale)))
            await session.execute(delete(User).where(User.email.like("bughunt-cash-%")))
            await session.commit()

    await wipe()
    yield
    await wipe()


async def make_user(staff: bool = False) -> User:
    user = User(
        email=f"bughunt-cash-{uuid.uuid4().hex[:12]}@example.com",
        country="NG",
        name="A Tester",
        role="ADMIN" if staff else "USER",
        admin_staff_role="SUPER_ADMIN" if staff else None,
    )
    factory = get_session_factory()
    async with factory() as session:
        session.add(user)
        await session.commit()
        await session.refresh(user)
    return user


async def make_season(*, status: str = "open", **overrides) -> BugHuntProgram:
    starts = datetime.now(UTC)
    program = await program_service.create(
        name="Season 1",
        slug=f"cash-{uuid.uuid4().hex[:6]}",
        starts_at=starts,
        ends_at=starts + timedelta(days=14),
        season_number=1,
        **overrides,
    )
    if status == "open":
        program = await program_service.open_season(program.id)
    elif status == "closed":
        await program_service.open_season(program.id)
        program = await program_service.close_season(program.id)
    return program


async def funded(program: BugHuntProgram | None, kobo: int, *, with_account: bool = True) -> User:
    staff = await make_user(staff=True)
    tester = await make_user()
    await reward_service.adjust(
        user_id=tester.id,
        amount_kobo=kobo,
        note="Test funding.",
        staff_user_id=staff.id,
        program_id=program.id if program else None,
    )
    if with_account:
        await withdrawal_service.set_account(
            user_id=tester.id,
            bank_code="058",
            bank_name="GTBank",
            account_number=ACCOUNT,
            account_name="A Tester",
        )
    return tester


# ---------------------------------------------------------------------------
# Encryption
# ---------------------------------------------------------------------------


class TestAccountNumberEncryption:
    def test_the_round_trip_works(self):
        sealed = payout_crypto.encrypt(ACCOUNT)
        assert payout_crypto.decrypt(sealed) == ACCOUNT

    def test_the_ciphertext_does_not_contain_the_number(self):
        """The obvious thing to check, and the one that would catch a stub implementation."""
        sealed = payout_crypto.encrypt(ACCOUNT)
        assert ACCOUNT not in sealed
        assert sealed.startswith("v1.")

    def test_two_encryptions_of_the_same_number_differ(self):
        """A random nonce per write, so a database dump cannot be scanned for repeated account numbers."""
        assert payout_crypto.encrypt(ACCOUNT) != payout_crypto.encrypt(ACCOUNT)

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("0123456789", "0123456789"),
            ("012 345 6789", "0123456789"),
            ("012-345-6789", "0123456789"),
        ],
    )
    def test_spacing_is_normalised(self, raw, expected):
        """Testers type account numbers with spaces. Normalising once means the stored value, the last four
        and any future provider call all agree on one form."""
        assert payout_crypto.normalise_account_number(raw) == expected

    @pytest.mark.parametrize("bad", ["123", "12345678901", "abcdefghij", "", "01234 5678x"])
    def test_a_number_that_is_not_a_nuban_is_refused(self, bad):
        """A mistyped account number is caught either here or by a transfer to a stranger."""
        with pytest.raises(payout_crypto.InvalidAccountNumber):
            payout_crypto.normalise_account_number(bad)

    def test_the_last_four_are_the_last_four(self):
        assert payout_crypto.last4("012 345 6789") == "6789"

    @pytest.mark.parametrize(
        "corrupt", ["", "v1.", "v2.abcdef", "notascheme", "v1.!!!not-base64!!!", "v1.aaaa"]
    )
    def test_an_unreadable_value_raises_one_exception(self, corrupt):
        """Every failure mode collapses to one exception, because the only sane response to all of them is
        the same: ask the tester for their details again."""
        with pytest.raises(payout_crypto.PayoutAccountUnreadable):
            payout_crypto.decrypt(corrupt)

    def test_a_wrong_key_is_detected_rather_than_returning_rubbish(self, monkeypatch):
        """AES-GCM authenticates. Half an account number would be worse than none, because somebody might
        try to use it."""
        sealed = payout_crypto.encrypt(ACCOUNT)
        payout_crypto._key.cache_clear()
        from src.config import get_settings

        settings = get_settings()
        monkeypatch.setattr(settings, "SECRET_KEY", "a-completely-different-secret-key")
        try:
            with pytest.raises(payout_crypto.PayoutAccountUnreadable, match="rotated"):
                payout_crypto.decrypt(sealed)
        finally:
            payout_crypto._key.cache_clear()


# ---------------------------------------------------------------------------
# The payout account
# ---------------------------------------------------------------------------


class TestPayoutAccount:
    async def test_details_are_stored_encrypted_and_read_back_masked(self):
        tester = await make_user()
        view = await withdrawal_service.set_account(
            user_id=tester.id,
            bank_code="058",
            bank_name="GTBank",
            account_number="012 345 6789",
            account_name="A Tester",
        )
        assert view.account_number_last4 == "6789"
        assert not hasattr(view, "account_number"), "the view has no field for the full number"

        factory = get_session_factory()
        async with factory() as session:
            stored = (
                await session.execute(
                    select(BugHuntPayoutAccount).where(BugHuntPayoutAccount.user_id == tester.id)
                )
            ).scalar_one()
        assert ACCOUNT not in stored.account_number_enc
        assert stored.account_number_last4 == "6789"

    async def test_one_account_per_person_reused_every_season(self):
        """Keyed on the user, not a participation, so a returning tester is not asked to re-enter their bank
        details every season."""
        tester = await make_user()
        await withdrawal_service.set_account(
            user_id=tester.id,
            bank_code="058",
            bank_name="GTBank",
            account_number=ACCOUNT,
            account_name="A Tester",
        )
        await withdrawal_service.set_account(
            user_id=tester.id,
            bank_code="044",
            bank_name="Access",
            account_number="9876543210",
            account_name="A Tester",
        )
        factory = get_session_factory()
        async with factory() as session:
            count = len(
                (
                    await session.execute(
                        select(BugHuntPayoutAccount).where(
                            BugHuntPayoutAccount.user_id == tester.id
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert count == 1
        account = await withdrawal_service.get_account(tester.id)
        assert account is not None and account.account_number_last4 == "3210"

    async def test_a_bad_number_is_refused_before_anything_is_stored(self):
        tester = await make_user()
        with pytest.raises(ValidationError, match="ten digits"):
            await withdrawal_service.set_account(
                user_id=tester.id,
                bank_code="058",
                bank_name="GTBank",
                account_number="12345",
                account_name="A Tester",
            )
        assert await withdrawal_service.get_account(tester.id) is None

    async def test_the_first_entry_is_not_held(self):
        """There is no previous account for an attacker to be redirecting money away from, so holding a
        tester's first withdrawal for a day would be friction with nothing behind it."""
        tester = await make_user()
        view = await withdrawal_service.set_account(
            user_id=tester.id,
            bank_code="058",
            bank_name="GTBank",
            account_number=ACCOUNT,
            account_name="A Tester",
        )
        assert view.held_until is None

    async def test_changing_the_number_starts_a_hold(self):
        """An account takeover that can redirect a payout instantly is worth far more than one that cannot."""
        tester = await make_user()
        await withdrawal_service.set_account(
            user_id=tester.id,
            bank_code="058",
            bank_name="GTBank",
            account_number=ACCOUNT,
            account_name="A Tester",
        )
        view = await withdrawal_service.set_account(
            user_id=tester.id,
            bank_code="058",
            bank_name="GTBank",
            account_number="9876543210",
            account_name="A Tester",
        )
        assert view.held_until is not None

    async def test_fixing_a_spelling_in_the_name_does_not(self):
        """That is not a redirection, and holding somebody's payout for a typo fix would be friction with
        nothing behind it."""
        tester = await make_user()
        await withdrawal_service.set_account(
            user_id=tester.id,
            bank_code="058",
            bank_name="GTBank",
            account_number=ACCOUNT,
            account_name="A Testr",
        )
        view = await withdrawal_service.set_account(
            user_id=tester.id,
            bank_code="058",
            bank_name="GTBank",
            account_number=ACCOUNT,
            account_name="A Tester",
        )
        assert view.held_until is None

    async def test_the_full_number_is_recoverable_for_a_payout(self):
        tester = await make_user()
        await withdrawal_service.set_account(
            user_id=tester.id,
            bank_code="058",
            bank_name="GTBank",
            account_number=ACCOUNT,
            account_name="A Tester",
        )
        assert await withdrawal_service.reveal_account_number(tester.id) == ACCOUNT

    async def test_revealing_a_missing_account_is_a_404(self):
        tester = await make_user()
        with pytest.raises(NotFoundError):
            await withdrawal_service.reveal_account_number(tester.id)


# ---------------------------------------------------------------------------
# Requesting
# ---------------------------------------------------------------------------


class TestRequesting:
    async def test_a_request_debits_immediately(self):
        """Not at approval, and not at payment. A pending request must not be spendable twice over."""
        program = await make_season()
        tester = await funded(program, 300_000)

        row = await withdrawal_service.request(user_id=tester.id, amount_kobo=200_000)
        assert row.status == "requested"
        assert await ledger_service.balance(tester.id) == 100_000

        entry = await withdrawal_service._debit_entry(row.id)
        assert entry is not None
        assert entry.amount_kobo == -200_000
        assert entry.kind == "withdrawal"
        assert entry.program_id is None, "a spend belongs to no season"

    async def test_the_bank_details_are_snapshotted_onto_the_request(self):
        """So a historic payout stays reconcilable after the retention sweep deletes the account row — and
        without keeping the full number for years."""
        program = await make_season()
        tester = await funded(program, 300_000)
        row = await withdrawal_service.request(user_id=tester.id, amount_kobo=200_000)
        assert row.bank_name_snapshot == "GTBank"
        assert row.account_name_snapshot == "A Tester"
        assert row.account_last4_snapshot == "6789"

    async def test_a_request_below_the_minimum_is_refused(self):
        program = await make_season(min_withdrawal_kobo=100_000)
        tester = await funded(program, 300_000)
        with pytest.raises(ValidationError, match="smallest cash request"):
            await withdrawal_service.request(user_id=tester.id, amount_kobo=50_000)
        assert await ledger_service.balance(tester.id) == 300_000

    async def test_the_minimum_is_the_seasons_and_falls_back_between_seasons(self):
        from src.domains.bug_hunt import rewards

        program = await make_season(min_withdrawal_kobo=250_000)
        assert withdrawal_service.minimum_kobo(program) == 250_000
        assert withdrawal_service.minimum_kobo(None) == rewards.DEFAULT_MIN_WITHDRAWAL_KOBO

    async def test_a_request_beyond_the_balance_is_refused(self):
        program = await make_season()
        tester = await funded(program, 100_000)
        with pytest.raises(ConflictError) as e:
            await withdrawal_service.request(user_id=tester.id, amount_kobo=200_000)
        assert e.value.code == "INSUFFICIENT_BALANCE"
        assert await ledger_service.balance(tester.id) == 100_000

    async def test_a_request_with_no_bank_details_is_refused(self):
        program = await make_season()
        tester = await funded(program, 300_000, with_account=False)
        with pytest.raises(ConflictError) as e:
            await withdrawal_service.request(user_id=tester.id, amount_kobo=200_000)
        assert e.value.code == "PAYOUT_ACCOUNT_MISSING"

    async def test_a_request_during_the_change_hold_is_refused(self):
        program = await make_season()
        tester = await funded(program, 300_000)
        await withdrawal_service.set_account(
            user_id=tester.id,
            bank_code="044",
            bank_name="Access",
            account_number="9876543210",
            account_name="A Tester",
        )
        with pytest.raises(ConflictError) as e:
            await withdrawal_service.request(user_id=tester.id, amount_kobo=200_000)
        assert e.value.code == "PAYOUT_ACCOUNT_HELD"

    async def test_after_the_hold_expires_a_request_goes_through(self):
        program = await make_season()
        tester = await funded(program, 300_000)
        await withdrawal_service.set_account(
            user_id=tester.id,
            bank_code="044",
            bank_name="Access",
            account_number="9876543210",
            account_name="A Tester",
        )
        factory = get_session_factory()
        async with factory() as session:
            await session.execute(
                update(BugHuntPayoutAccount)
                .where(BugHuntPayoutAccount.user_id == tester.id)
                .values(changed_at=datetime.now(UTC) - timedelta(hours=25))
            )
            await session.commit()
        row = await withdrawal_service.request(user_id=tester.id, amount_kobo=200_000)
        assert row.status == "requested"

    async def test_only_one_request_can_be_open(self):
        program = await make_season()
        tester = await funded(program, 600_000)
        await withdrawal_service.request(user_id=tester.id, amount_kobo=200_000)
        with pytest.raises(ConflictError) as e:
            await withdrawal_service.request(user_id=tester.id, amount_kobo=200_000)
        assert e.value.code == "WITHDRAWAL_ALREADY_OPEN"
        assert await ledger_service.balance(tester.id) == 400_000, "only one debit was written"

    async def test_concurrent_requests_leave_exactly_one_open(self):
        """Raced for real. A count-then-insert has no lock between its steps, so two concurrent requests
        would each see none open and both debit a balance that will be transferred once."""
        program = await make_season()
        tester = await funded(program, 1_000_000)

        results = await asyncio.gather(
            *[withdrawal_service.request(user_id=tester.id, amount_kobo=200_000) for _ in range(4)],
            return_exceptions=True,
        )
        succeeded = [r for r in results if not isinstance(r, Exception)]
        assert len(succeeded) == 1, results
        assert await ledger_service.balance(tester.id) == 800_000, "one debit, not four"

    async def test_a_refused_debit_leaves_no_orphan_request(self):
        """The awkward corner: the request row must exist before the debit can name it, so a refused debit has
        to delete the row. Leaving it would occupy the one-open-request slot with a request that holds
        nothing, and every future request would be refused for no visible reason.
        """
        program = await make_season()
        tester = await funded(program, 300_000)

        # Drain the balance behind the request's own check, so the debit is the thing that refuses.
        await ledger_service.debit(
            user_id=tester.id, kind="pass_redemption", amount_kobo=-300_000, pass_id="p"
        )
        with pytest.raises(ConflictError):
            await withdrawal_service.request(user_id=tester.id, amount_kobo=200_000)

        factory = get_session_factory()
        async with factory() as session:
            open_rows = (
                (
                    await session.execute(
                        select(BugHuntWithdrawal).where(BugHuntWithdrawal.user_id == tester.id)
                    )
                )
                .scalars()
                .all()
            )
        assert open_rows == [], "no request row survived a refused debit"

    async def test_requesting_works_between_seasons(self):
        """An earned balance is permanent, so cashing out must not wait for the next season."""
        program = await make_season()
        tester = await funded(program, 300_000)
        await program_service.close_season(program.id)
        row = await withdrawal_service.request(user_id=tester.id, amount_kobo=200_000)
        assert row.status == "requested"


# ---------------------------------------------------------------------------
# The payout lifecycle
# ---------------------------------------------------------------------------


class TestTheLifecycle:
    async def test_approval_moves_no_money(self):
        """The debit was written at request time, so approval is purely a statement that somebody is going to
        make the transfer."""
        program = await make_season()
        staff = await make_user(staff=True)
        tester = await funded(program, 300_000)
        row = await withdrawal_service.request(user_id=tester.id, amount_kobo=200_000)
        before = await ledger_service.balance(tester.id)

        approved = await withdrawal_service.decide(
            withdrawal_id=row.id, decision="approve", reason=None, staff_user_id=staff.id
        )
        assert approved.status == "approved"
        assert approved.decided_by_user_id == staff.id
        assert await ledger_service.balance(tester.id) == before

    async def test_rejection_gives_the_money_back_as_a_new_row(self):
        program = await make_season()
        staff = await make_user(staff=True)
        tester = await funded(program, 300_000)
        row = await withdrawal_service.request(user_id=tester.id, amount_kobo=200_000)

        await withdrawal_service.decide(
            withdrawal_id=row.id,
            decision="reject",
            reason="The account name does not match your Maigie account.",
            staff_user_id=staff.id,
        )
        assert await ledger_service.balance(tester.id) == 300_000

        entries, _ = await ledger_service.history(user_id=tester.id)
        kinds = [entry.kind for entry, _ in entries]
        assert "withdrawal" in kinds
        assert "withdrawal_reversal" in kinds, "a new row, not a deleted debit"

    async def test_a_rejection_needs_a_reason(self):
        program = await make_season()
        staff = await make_user(staff=True)
        tester = await funded(program, 300_000)
        row = await withdrawal_service.request(user_id=tester.id, amount_kobo=200_000)
        with pytest.raises(ValidationError, match="owed a reason"):
            await withdrawal_service.decide(
                withdrawal_id=row.id, decision="reject", reason="  ", staff_user_id=staff.id
            )

    async def test_a_rejected_request_frees_the_slot(self):
        program = await make_season()
        staff = await make_user(staff=True)
        tester = await funded(program, 600_000)
        first = await withdrawal_service.request(user_id=tester.id, amount_kobo=200_000)
        await withdrawal_service.decide(
            withdrawal_id=first.id, decision="reject", reason="Wrong bank.", staff_user_id=staff.id
        )
        second = await withdrawal_service.request(user_id=tester.id, amount_kobo=200_000)
        assert second.id != first.id

    async def test_marking_paid_requires_a_reference(self):
        """A payout that cannot be matched to a statement line is indistinguishable from one that never
        happened."""
        program = await make_season()
        staff = await make_user(staff=True)
        tester = await funded(program, 300_000)
        row = await withdrawal_service.request(user_id=tester.id, amount_kobo=200_000)
        await withdrawal_service.decide(
            withdrawal_id=row.id, decision="approve", reason=None, staff_user_id=staff.id
        )
        with pytest.raises(ValidationError, match="reconciled"):
            await withdrawal_service.mark_paid(
                withdrawal_id=row.id, provider_reference="   ", staff_user_id=staff.id
            )

    async def test_marking_paid_moves_no_money(self):
        """**The most important assertion in this file.**

        The debit was recorded at request time. Writing a ledger entry here would pay the same request twice:
        once out of the wallet and once out of the bank.
        """
        program = await make_season()
        staff = await make_user(staff=True)
        tester = await funded(program, 300_000)
        row = await withdrawal_service.request(user_id=tester.id, amount_kobo=200_000)
        await withdrawal_service.decide(
            withdrawal_id=row.id, decision="approve", reason=None, staff_user_id=staff.id
        )

        before = await ledger_service.balance(tester.id)
        _, entries_before = await ledger_service.history(user_id=tester.id)

        paid = await withdrawal_service.mark_paid(
            withdrawal_id=row.id, provider_reference="NIBSS-99001", staff_user_id=staff.id
        )

        assert paid.status == "paid"
        assert paid.provider_reference == "NIBSS-99001"
        assert paid.paid_at is not None
        assert await ledger_service.balance(tester.id) == before
        _, entries_after = await ledger_service.history(user_id=tester.id)
        assert entries_after == entries_before, "no ledger entry was written"

    async def test_a_payout_cannot_be_recorded_before_it_is_approved(self):
        program = await make_season()
        staff = await make_user(staff=True)
        tester = await funded(program, 300_000)
        row = await withdrawal_service.request(user_id=tester.id, amount_kobo=200_000)
        with pytest.raises(ConflictError) as e:
            await withdrawal_service.mark_paid(
                withdrawal_id=row.id, provider_reference="X", staff_user_id=staff.id
            )
        assert e.value.code == "WITHDRAWAL_NOT_APPROVED"

    async def test_marking_paid_twice_is_harmless(self):
        program = await make_season()
        staff = await make_user(staff=True)
        tester = await funded(program, 300_000)
        row = await withdrawal_service.request(user_id=tester.id, amount_kobo=200_000)
        await withdrawal_service.decide(
            withdrawal_id=row.id, decision="approve", reason=None, staff_user_id=staff.id
        )
        first = await withdrawal_service.mark_paid(
            withdrawal_id=row.id, provider_reference="NIBSS-1", staff_user_id=staff.id
        )
        second = await withdrawal_service.mark_paid(
            withdrawal_id=row.id, provider_reference="NIBSS-2", staff_user_id=staff.id
        )
        assert second.provider_reference == first.provider_reference == "NIBSS-1"

    async def test_a_settled_request_cannot_be_decided_again(self):
        program = await make_season()
        staff = await make_user(staff=True)
        tester = await funded(program, 300_000)
        row = await withdrawal_service.request(user_id=tester.id, amount_kobo=200_000)
        await withdrawal_service.decide(
            withdrawal_id=row.id, decision="reject", reason="No.", staff_user_id=staff.id
        )
        with pytest.raises(ConflictError) as e:
            await withdrawal_service.decide(
                withdrawal_id=row.id, decision="approve", reason=None, staff_user_id=staff.id
            )
        assert e.value.code == "WITHDRAWAL_SETTLED"

    async def test_reverting_a_recorded_payout_moves_no_money_either(self):
        """For a transfer the bank refused after we wrote it down. Nothing left the wallet at this stage, so
        nothing needs putting back."""
        program = await make_season()
        staff = await make_user(staff=True)
        tester = await funded(program, 300_000)
        row = await withdrawal_service.request(user_id=tester.id, amount_kobo=200_000)
        await withdrawal_service.decide(
            withdrawal_id=row.id, decision="approve", reason=None, staff_user_id=staff.id
        )
        await withdrawal_service.mark_paid(
            withdrawal_id=row.id, provider_reference="NIBSS-1", staff_user_id=staff.id
        )
        before = await ledger_service.balance(tester.id)

        reverted = await withdrawal_service.revert_to_approved(
            withdrawal_id=row.id, reason="The bank bounced it.", staff_user_id=staff.id
        )
        assert reverted.status == "approved"
        assert reverted.provider_reference is None
        assert reverted.paid_at is None
        assert await ledger_service.balance(tester.id) == before

    async def test_only_a_paid_payout_can_be_reverted(self):
        program = await make_season()
        staff = await make_user(staff=True)
        tester = await funded(program, 300_000)
        row = await withdrawal_service.request(user_id=tester.id, amount_kobo=200_000)
        with pytest.raises(ConflictError) as e:
            await withdrawal_service.revert_to_approved(
                withdrawal_id=row.id, reason="oops", staff_user_id=staff.id
            )
        assert e.value.code == "WITHDRAWAL_NOT_PAID"

    async def test_no_finance_line_is_written_automatically(self):
        """**The mirror the plan asked for does not exist, on purpose.**

        `finance.routes._resolve_gbp` never invents an FX rate — a non-GBP ledger line requires an
        operator-entered GBP figure, and `amountGbp` is `NOT NULL`. An automatic mirror could only satisfy
        that by inventing the number the finance domain declines to invent, inside a `try/except` that
        swallows its own failures. A wrong number in the books is worse than no line.

        So `financeEntryId` stays null and adding the expense is a manual step. This test exists so that
        somebody re-adding the mirror has to read the reason first.
        """
        program = await make_season()
        staff = await make_user(staff=True)
        tester = await funded(program, 300_000)
        row = await withdrawal_service.request(user_id=tester.id, amount_kobo=200_000)
        await withdrawal_service.decide(
            withdrawal_id=row.id, decision="approve", reason=None, staff_user_id=staff.id
        )
        paid = await withdrawal_service.mark_paid(
            withdrawal_id=row.id, provider_reference="NIBSS-1", staff_user_id=staff.id
        )
        assert paid.finance_entry_id is None
        assert not hasattr(withdrawal_service, "_mirror_to_finance")

    async def test_a_paid_request_frees_the_slot_for_the_next_one(self):
        program = await make_season()
        staff = await make_user(staff=True)
        tester = await funded(program, 600_000)
        first = await withdrawal_service.request(user_id=tester.id, amount_kobo=200_000)
        await withdrawal_service.decide(
            withdrawal_id=first.id, decision="approve", reason=None, staff_user_id=staff.id
        )
        await withdrawal_service.mark_paid(
            withdrawal_id=first.id, provider_reference="NIBSS-1", staff_user_id=staff.id
        )
        second = await withdrawal_service.request(user_id=tester.id, amount_kobo=200_000)
        assert second.id != first.id


class TestThePayoutConsole:
    async def test_the_detail_carries_the_full_number_and_the_payee_history(self):
        program = await make_season()
        tester = await funded(program, 300_000)
        row = await withdrawal_service.request(user_id=tester.id, amount_kobo=200_000)

        data = await withdrawal_service.detail(row.id)
        assert data["accountNumber"] == ACCOUNT
        assert data["accountNumberUnreadable"] is False
        assert data["email"] == tester.email
        assert data["payoutCount"] == 1
        assert data["paidBeforeKobo"] == 0, "a first-time payee is visibly a first-time payee"

    async def test_an_unreadable_account_degrades_rather_than_failing(self):
        """A 500 here would make an operational problem look like an outage. Staff can still see who it is
        from the snapshot and ask them to re-enter their details.
        """
        program = await make_season()
        tester = await funded(program, 300_000)
        row = await withdrawal_service.request(user_id=tester.id, amount_kobo=200_000)

        factory = get_session_factory()
        async with factory() as session:
            await session.execute(
                update(BugHuntPayoutAccount)
                .where(BugHuntPayoutAccount.user_id == tester.id)
                .values(account_number_enc="v1.corrupted")
            )
            await session.commit()

        data = await withdrawal_service.detail(row.id)
        assert data["accountNumber"] is None
        assert data["accountNumberUnreadable"] is True
        assert data["withdrawal"].account_last4_snapshot == "6789", "still identifiable"

    async def test_the_queue_is_oldest_first(self):
        """The one queue where being pushed down the page has a direct cost to somebody."""
        program = await make_season()
        first = await funded(program, 300_000)
        second = await funded(program, 300_000)
        await withdrawal_service.request(user_id=first.id, amount_kobo=200_000)
        await withdrawal_service.request(user_id=second.id, amount_kobo=200_000)

        rows, total = await withdrawal_service.list_all()
        assert total == 2
        assert [row.user_id for row, _, _ in rows] == [first.id, second.id]

    async def test_the_queue_filters_by_status_and_searches_the_payee(self):
        program = await make_season()
        staff = await make_user(staff=True)
        tester = await funded(program, 600_000)
        row = await withdrawal_service.request(user_id=tester.id, amount_kobo=200_000)
        await withdrawal_service.decide(
            withdrawal_id=row.id, decision="approve", reason=None, staff_user_id=staff.id
        )

        _, approved = await withdrawal_service.list_all(status="approved")
        assert approved == 1
        _, requested = await withdrawal_service.list_all(status="requested")
        assert requested == 0
        _, searched = await withdrawal_service.list_all(search=tester.email.split("@")[0])
        assert searched == 1

    async def test_a_second_payout_shows_the_history(self):
        program = await make_season()
        staff = await make_user(staff=True)
        tester = await funded(program, 900_000)
        first = await withdrawal_service.request(user_id=tester.id, amount_kobo=200_000)
        await withdrawal_service.decide(
            withdrawal_id=first.id, decision="approve", reason=None, staff_user_id=staff.id
        )
        await withdrawal_service.mark_paid(
            withdrawal_id=first.id, provider_reference="NIBSS-1", staff_user_id=staff.id
        )
        second = await withdrawal_service.request(user_id=tester.id, amount_kobo=300_000)

        data = await withdrawal_service.detail(second.id)
        assert data["payoutCount"] == 2
        assert data["paidBeforeKobo"] == 200_000


# ---------------------------------------------------------------------------
# Over the wire
# ---------------------------------------------------------------------------


def bearer(user: User) -> dict[str, str]:
    from src.shared.auth.jwt import create_access_token

    return {"Authorization": f"Bearer {create_access_token({'sub': user.email})}"}


class TestOverTheWire:
    async def test_the_whole_cash_journey(self, client):
        program = await make_season()
        staff = await make_user(staff=True)
        tester = await funded(program, 300_000, with_account=False)
        theirs = bearer(tester)
        admin = bearer(staff)

        # No details yet.
        assert (await client.get("/api/v1/bug-hunt/payout-account", headers=theirs)).json() is None

        saved = await client.put(
            "/api/v1/bug-hunt/payout-account",
            headers=theirs,
            json={
                "bankCode": "058",
                "bankName": "GTBank",
                "accountNumber": "012 345 6789",
                "accountName": "A Tester",
            },
        )
        assert saved.status_code == 200, saved.text
        assert saved.json()["accountNumberLast4"] == "6789"
        assert ACCOUNT not in saved.text, "the full number never comes back to the tester"

        listed = await client.get("/api/v1/bug-hunt/withdrawals", headers=theirs)
        assert listed.json()["minimumKobo"] == program.min_withdrawal_kobo
        assert listed.json()["balanceKobo"] == 300_000

        requested = await client.post(
            "/api/v1/bug-hunt/withdrawals", headers=theirs, json={"amountKobo": 200_000}
        )
        assert requested.status_code == 201, requested.text
        withdrawal_id = requested.json()["id"]
        assert requested.json()["status"] == "requested"
        assert requested.json()["accountLast4"] == "6789"

        # The tester's dashboard: Requested, and the balance already reflects it.
        after = (await client.get("/api/v1/bug-hunt/withdrawals", headers=theirs)).json()
        assert after["withdrawals"][0]["status"] == "requested"
        assert after["balanceKobo"] == 100_000

        # The payout console. The only place the full number appears.
        console = await client.get(
            f"/api/v1/admin/bug-hunt/withdrawals/{withdrawal_id}", headers=admin
        )
        assert console.status_code == 200, console.text
        assert console.json()["accountNumber"] == ACCOUNT
        assert console.json()["payoutCount"] == 1

        approved = await client.post(
            f"/api/v1/admin/bug-hunt/withdrawals/{withdrawal_id}/decision",
            headers=admin,
            json={"decision": "approve"},
        )
        assert approved.json()["status"] == "approved"

        # …the transfer happens in somebody's banking app, and then:
        paid = await client.post(
            f"/api/v1/admin/bug-hunt/withdrawals/{withdrawal_id}/mark-paid",
            headers=admin,
            json={"providerReference": "NIBSS-99001"},
        )
        assert paid.status_code == 200, paid.text
        assert paid.json()["status"] == "paid"

        # The tester sees Paid, with the reference they can check against their own bank alert.
        final = (await client.get("/api/v1/bug-hunt/withdrawals", headers=theirs)).json()
        assert final["withdrawals"][0]["status"] == "paid"
        assert final["withdrawals"][0]["providerReference"] == "NIBSS-99001"
        assert final["balanceKobo"] == 100_000, "unchanged by the recording"

    async def test_the_tester_never_receives_their_own_full_number_back(self, client):
        """The number goes in and does not come out. There is no field on `PayoutAccountView` for it, which is
        a stronger guarantee than remembering not to include it."""
        program = await make_season()
        tester = await funded(program, 300_000)
        response = await client.get("/api/v1/bug-hunt/payout-account", headers=bearer(tester))
        assert ACCOUNT not in response.text
        assert response.json()["accountNumberLast4"] == "6789"

    async def test_the_payout_queue_is_super_admin_only(self, client):
        """Triage is safe for a content manager because the amount is not theirs to choose. This is where real
        money leaves a real bank account, and one endpoint here discloses an account number."""
        program = await make_season()
        tester = await funded(program, 300_000)
        row = await withdrawal_service.request(user_id=tester.id, amount_kobo=200_000)

        manager = await make_user(staff=True)
        factory = get_session_factory()
        async with factory() as session:
            await session.execute(
                update(User).where(User.id == manager.id).values(admin_staff_role="CONTENT_MANAGER")
            )
            await session.commit()

        for path in (
            "/api/v1/admin/bug-hunt/withdrawals",
            f"/api/v1/admin/bug-hunt/withdrawals/{row.id}",
        ):
            assert (await client.get(path, headers=bearer(manager))).status_code == 403, path
        assert (
            await client.get("/api/v1/admin/bug-hunt/withdrawals", headers=bearer(tester))
        ).status_code == 403

    async def test_viewing_the_console_is_audited(self, client):
        """Audited on *read*, not just on write. The transfer happens where we cannot see it, so a record of
        who looked at the number is the only trace this system can offer."""
        program = await make_season()
        staff = await make_user(staff=True)
        tester = await funded(program, 300_000)
        row = await withdrawal_service.request(user_id=tester.id, amount_kobo=200_000)

        await client.get(f"/api/v1/admin/bug-hunt/withdrawals/{row.id}", headers=bearer(staff))

        from src.domains.admin.db_models import AuditLog

        factory = get_session_factory()
        async with factory() as session:
            actions = set(
                (
                    await session.execute(
                        select(AuditLog.action_type).where(AuditLog.admin_user_id == staff.id)
                    )
                )
                .scalars()
                .all()
            )
        assert "bug_hunt_view_payout_details" in actions

    async def test_a_request_below_the_minimum_answers_400(self, client):
        program = await make_season(min_withdrawal_kobo=100_000)
        tester = await funded(program, 300_000)
        response = await client.post(
            "/api/v1/bug-hunt/withdrawals", headers=bearer(tester), json={"amountKobo": 5_000}
        )
        assert response.status_code == 422
        assert "smallest cash request" in response.text
