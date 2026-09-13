"""The reward ledger, against a real database — including the races it exists to lose safely.

Most of this file is arithmetic, and arithmetic about money is worth being boring about. Four claims carry
the weight:

- **A balance is a `SUM` and nothing else.** No cached total to drift, so the test is that every read
  agrees with the entries, in every state, including mid-withdrawal.
- **Concurrent debits cannot overdraw.** Run for real with `asyncio.gather` against Postgres, not asserted
  from a docstring — a lock you have not raced is a lock you are hoping for. This is the test that would
  fail if `with_for_update` were removed, and nothing else in the suite would.
- **An award is written once.** Two simultaneous triages of the same finding pay one of them.
- **The cap clamps and the budget refuses.** Two limits, two deliberately different behaviours: paying a
  tester less than the published amount because *we* ran out of money is the failure that would damage the
  programme, so it is refused rather than reduced.

Run with:

    RUN_DB_TESTS=1 DATABASE_URL=postgresql://localhost/scratch pytest tests/test_bug_hunt_ledger.py
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, func, select, update

from src.domains.bug_hunt.db_models import (
    BugHuntAttachment,
    BugHuntLedgerEntry,
    BugHuntParticipant,
    BugHuntProgram,
    BugHuntSubmission,
    BugHuntWallet,
    BugHuntWithdrawal,
)
from src.domains.bug_hunt.services import (
    ledger_service,
    program_service,
    reward_service,
    submission_service,
    triage_service,
)
from src.domains.identity.db_models import User
from src.shared.database import get_session_factory
from src.shared.exceptions import ConflictError, ValidationError

pytestmark = pytest.mark.usefixtures("db")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
async def clean_slate():
    async def wipe():
        from src.domains.admin.db_models import AuditLog

        factory = get_session_factory()
        async with factory() as session:
            for model in (
                BugHuntLedgerEntry,
                BugHuntWithdrawal,
                BugHuntAttachment,
                BugHuntSubmission,
                BugHuntWallet,
                BugHuntParticipant,
                BugHuntProgram,
            ):
                await session.execute(delete(model))
            stale = select(User.id).where(User.email.like("bughunt-ledger-%"))
            await session.execute(delete(AuditLog).where(AuditLog.admin_user_id.in_(stale)))
            await session.execute(delete(User).where(User.email.like("bughunt-ledger-%")))
            await session.commit()

    await wipe()
    yield
    await wipe()


async def make_user(staff: bool = False) -> User:
    user = User(
        email=f"bughunt-ledger-{uuid.uuid4().hex[:12]}@example.com",
        country="NG",
        role="ADMIN" if staff else "USER",
        admin_staff_role="SUPER_ADMIN" if staff else None,
    )
    factory = get_session_factory()
    async with factory() as session:
        session.add(user)
        await session.commit()
        await session.refresh(user)
    return user


async def make_season(*, number: int = 1, status: str = "open", **overrides) -> BugHuntProgram:
    starts = datetime.now(UTC)
    program = await program_service.create(
        name=f"Season {number}",
        slug=f"ledger-s{number}-{uuid.uuid4().hex[:6]}",
        starts_at=starts,
        ends_at=starts + timedelta(days=14),
        season_number=number,
        **overrides,
    )
    if status == "open":
        program = await program_service.open_season(program.id)
    elif status == "closed":
        await program_service.open_season(program.id)
        program = await program_service.close_season(program.id)
    return program


def finding(**overrides) -> dict:
    fields = {
        "platform": "android",
        "title": "Crash when opening Learn on a cold start",
        "stepsToReproduce": "Force-stop the app, reopen it, tap Learn.",
        "expectedResult": "Learn renders.",
        "actualResult": "The app closes.",
    }
    fields.update(overrides)
    return fields


async def accepted_finding(
    program: BugHuntProgram, staff: User, *, severity: str = "critical", user: User | None = None
) -> tuple[User, BugHuntSubmission]:
    """A tester with one accepted, graded finding. Returns them and the finding."""
    tester = user or await make_user()
    participant, submission = await submission_service.create_application(
        user=tester, fields=finding(), accepted_rules_version=program.rules_version
    )
    await triage_service.decide_application(
        participant_id=participant.id, decision="approve", reason=None, staff_user_id=staff.id
    )
    await triage_service.triage(
        submission_id=submission.id,
        status="accepted",
        category="bug",
        type_="crash",
        severity=severity,
        duplicate_of_id=None,
        public_response=None,
        admin_notes=None,
        staff_user_id=staff.id,
    )
    return tester, submission


async def make_withdrawal(user: User, amount_kobo: int, status: str = "requested") -> str:
    """A withdrawal row for the ledger entry to point at.

    Phase 6 owns the request flow; the ledger only needs something real to reference, because a
    `withdrawal` entry that names no request is refused — by a CHECK and by `ledger_service`.
    """
    wallet = await ledger_service.get_or_create_wallet(user.id)
    row = BugHuntWithdrawal(
        wallet_id=wallet.id, user_id=user.id, amount_kobo=amount_kobo, status=status
    )
    factory = get_session_factory()
    async with factory() as session:
        session.add(row)
        await session.commit()
        await session.refresh(row)
    return row.id


async def entry_count(user_id: str) -> int:
    factory = get_session_factory()
    async with factory() as session:
        return int(
            (
                await session.execute(
                    select(func.count())
                    .select_from(BugHuntLedgerEntry)
                    .where(BugHuntLedgerEntry.user_id == user_id)
                )
            ).scalar()
            or 0
        )


# ---------------------------------------------------------------------------
# The wallet
# ---------------------------------------------------------------------------


class TestWallet:
    async def test_a_wallet_is_created_on_first_need(self):
        """Lazily, not at signup. Most accounts never take part, and a row per learner would be a table of
        empty wallets."""
        user = await make_user()
        wallet = await ledger_service.get_or_create_wallet(user.id)
        assert wallet.user_id == user.id

    async def test_get_or_create_is_idempotent(self):
        user = await make_user()
        first = await ledger_service.get_or_create_wallet(user.id)
        second = await ledger_service.get_or_create_wallet(user.id)
        assert first.id == second.id

    async def test_concurrent_creation_leaves_one_wallet(self):
        """The unique index makes the race benign; the `IntegrityError` is caught and the row re-read."""
        user = await make_user()
        wallets = await asyncio.gather(
            *[ledger_service.get_or_create_wallet(user.id) for _ in range(5)]
        )
        assert len({w.id for w in wallets}) == 1

    async def test_an_empty_wallet_has_a_zero_balance_not_an_error(self):
        user = await make_user()
        assert await ledger_service.balance(user.id) == 0


# ---------------------------------------------------------------------------
# Awards
# ---------------------------------------------------------------------------


class TestAwards:
    async def test_grading_a_finding_credits_the_seasons_amount(self):
        program = await make_season()
        staff = await make_user(staff=True)
        tester, submission = await accepted_finding(program, staff, severity="critical")

        assert await ledger_service.balance(tester.id) == 200_000
        assert await ledger_service.award_for_submission(submission.id) == 200_000

    async def test_the_award_is_attributed_to_its_season_and_participation(self):
        """Which is what the per-season cap and the budget are computed from. Without the attribution both
        are summed over an incomplete set and nobody notices until the numbers are wrong."""
        program = await make_season()
        staff = await make_user(staff=True)
        tester, submission = await accepted_finding(program, staff)

        factory = get_session_factory()
        async with factory() as session:
            entry = (
                await session.execute(
                    select(BugHuntLedgerEntry).where(
                        BugHuntLedgerEntry.submission_id == submission.id
                    )
                )
            ).scalar_one()
        assert entry.program_id == program.id
        assert entry.participant_id is not None
        assert entry.kind == "award"

    async def test_the_seasons_awarded_total_moves_with_the_ledger(self):
        """`awardedKobo` is the one deliberate denormalisation in this domain, incremented in the same
        transaction as the entry it counts — so it cannot drift by one."""
        program = await make_season()
        staff = await make_user(staff=True)
        await accepted_finding(program, staff, severity="critical")
        await accepted_finding(program, staff, severity="medium")

        refreshed = await program_service.get(program.id)
        assert refreshed.awarded_kobo == 300_000

        factory = get_session_factory()
        async with factory() as session:
            ledger_total = (
                await session.execute(
                    select(func.coalesce(func.sum(BugHuntLedgerEntry.amount_kobo), 0)).where(
                        BugHuntLedgerEntry.program_id == program.id,
                        BugHuntLedgerEntry.kind == "award",
                    )
                )
            ).scalar()
        assert int(ledger_total) == refreshed.awarded_kobo

    async def test_awarding_twice_pays_once(self):
        program = await make_season()
        staff = await make_user(staff=True)
        tester, submission = await accepted_finding(program, staff)

        again = await reward_service.award_submission(
            submission_id=submission.id, staff_user_id=staff.id
        )
        assert again.blocked == "already_awarded"
        assert again.credited_kobo == 200_000
        assert await ledger_service.balance(tester.id) == 200_000
        assert await entry_count(tester.id) == 1

    async def test_two_simultaneous_awards_pay_once(self):
        """**The race, run for real.**

        Two triagers working the same queue, or one clicking twice on a slow connection. The partial unique
        index on `(submissionId) WHERE kind = 'award'` is what makes this safe; a check-then-insert in Python
        holds no lock between its two steps and would pay twice.
        """
        program = await make_season()
        staff = await make_user(staff=True)
        tester, submission = await accepted_finding(program, staff)

        # The first award already landed via `accepted_finding`; fire four more at once.
        results = await asyncio.gather(
            *[
                reward_service.award_submission(submission_id=submission.id, staff_user_id=staff.id)
                for _ in range(4)
            ],
            return_exceptions=True,
        )
        assert all(not isinstance(r, Exception) for r in results), results
        assert await entry_count(tester.id) == 1
        assert await ledger_service.balance(tester.id) == 200_000

    async def test_only_an_accepted_finding_is_awarded(self):
        program = await make_season()
        staff = await make_user(staff=True)
        tester = await make_user()
        _, submission = await submission_service.create_application(
            user=tester, fields=finding(), accepted_rules_version=program.rules_version
        )
        with pytest.raises(ValidationError, match="accepted"):
            await reward_service.award_submission(
                submission_id=submission.id, staff_user_id=staff.id
            )

    async def test_a_finding_whose_reporter_is_gone_cannot_be_paid(self):
        """The finding survives account deletion (`SET NULL`) because the bug outlives the reporter — but
        there is nobody to pay, and pretending otherwise would write an award with no owner."""
        program = await make_season()
        staff = await make_user(staff=True)
        _, submission = await accepted_finding(program, staff)

        factory = get_session_factory()
        async with factory() as session:
            await session.execute(
                update(BugHuntSubmission)
                .where(BugHuntSubmission.id == submission.id)
                .values(user_id=None)
            )
            await session.execute(
                delete(BugHuntLedgerEntry).where(BugHuntLedgerEntry.submission_id == submission.id)
            )
            await session.commit()

        with pytest.raises(ValidationError, match="no reporter"):
            await reward_service.award_submission(
                submission_id=submission.id, staff_user_id=staff.id
            )

    async def test_a_late_triage_is_paid_at_its_own_seasons_rate(self):
        """The reason the matrix lives on the season row, verified all the way to the ledger."""
        first = await make_season(number=1)
        staff = await make_user(staff=True)
        tester = await make_user()
        participant, submission = await submission_service.create_application(
            user=tester, fields=finding(), accepted_rules_version=first.rules_version
        )
        await triage_service.decide_application(
            participant_id=participant.id, decision="approve", reason=None, staff_user_id=staff.id
        )
        await program_service.close_season(first.id)
        await make_season(
            number=2,
            reward_matrix={
                "bug": {"critical": 999_900, "high": 1, "medium": 1, "low": 1},
                "feedback": {"high_value": 1, "standard": 1},
            },
        )

        await triage_service.triage(
            submission_id=submission.id,
            status="accepted",
            category="bug",
            type_="crash",
            severity="critical",
            duplicate_of_id=None,
            public_response=None,
            admin_notes=None,
            staff_user_id=staff.id,
        )
        assert await ledger_service.balance(tester.id) == 200_000, "Season 1's rate, not Season 2's"
        assert (await program_service.get(first.id)).awarded_kobo == 200_000


class TestTheCapClamps:
    async def test_an_award_is_clamped_to_what_is_left_of_the_cap(self):
        """Clamped, not refused. A tester ₦500 short of their cap who files a critical should get the ₦500 —
        the cap is a ceiling on what we pay one person, not a reason to pay nothing."""
        program = await make_season(per_participant_cap_kobo=250_000)
        staff = await make_user(staff=True)
        tester, _ = await accepted_finding(program, staff, severity="critical")  # 200 000
        assert await ledger_service.balance(tester.id) == 200_000

        # A second critical is worth 200 000 but only 50 000 of cap remains.
        second = await submission_service.create_submission(user=tester, fields=finding())
        result = await triage_service.triage(
            submission_id=second.id,
            status="accepted",
            category="bug",
            type_="crash",
            severity="critical",
            duplicate_of_id=None,
            public_response=None,
            admin_notes=None,
            staff_user_id=staff.id,
        )
        assert result["matrixKobo"] == 200_000
        assert result["awardKobo"] == 50_000
        assert await ledger_service.balance(tester.id) == 250_000

    async def test_once_the_cap_is_spent_further_findings_pay_nothing_but_are_recorded(self):
        program = await make_season(per_participant_cap_kobo=200_000)
        staff = await make_user(staff=True)
        tester, _ = await accepted_finding(program, staff, severity="critical")

        third = await submission_service.create_submission(user=tester, fields=finding())
        result = await triage_service.triage(
            submission_id=third.id,
            status="accepted",
            category="bug",
            type_="crash",
            severity="high",
            duplicate_of_id=None,
            public_response=None,
            admin_notes=None,
            staff_user_id=staff.id,
        )
        assert result["awardBlocked"] == "cap_reached"
        assert result["awardKobo"] == 0
        assert result["matrixKobo"] == 150_000
        assert "cap" in (result["awardMessage"] or "")
        # The finding is still accepted. It has value, and the reporter deserves the record.
        assert result["submission"].status == "accepted"
        assert await ledger_service.balance(tester.id) == 200_000

    async def test_the_cap_is_per_season_not_lifetime(self):
        """Earning is season-scoped even though holding is not. A tester who maxed out Season 1 starts Season
        2 with a full cap, and their Season 1 balance is untouched by that."""
        first = await make_season(number=1, per_participant_cap_kobo=200_000)
        staff = await make_user(staff=True)
        tester, _ = await accepted_finding(first, staff, severity="critical")
        await program_service.close_season(first.id)

        second = await make_season(number=2, per_participant_cap_kobo=200_000)
        await program_service.carry_forward(into_program_id=second.id, from_program_id=first.id)
        await submission_service.accept_terms(user=tester, rules_version=second.rules_version)
        new_finding = await submission_service.create_submission(user=tester, fields=finding())
        result = await triage_service.triage(
            submission_id=new_finding.id,
            status="accepted",
            category="bug",
            type_="crash",
            severity="critical",
            duplicate_of_id=None,
            public_response=None,
            admin_notes=None,
            staff_user_id=staff.id,
        )
        assert result["awardKobo"] == 200_000, "a fresh cap in the new season"
        assert (
            await ledger_service.balance(tester.id) == 400_000
        ), "and the old balance is still theirs"

    async def test_an_adjustment_counts_against_the_cap(self):
        """Otherwise the cap is advisory for anyone holding the adjustment permission."""
        program = await make_season(per_participant_cap_kobo=200_000)
        staff = await make_user(staff=True)
        tester = await make_user()
        participant, submission = await submission_service.create_application(
            user=tester, fields=finding(), accepted_rules_version=program.rules_version
        )
        await triage_service.decide_application(
            participant_id=participant.id, decision="approve", reason=None, staff_user_id=staff.id
        )
        await reward_service.adjust(
            user_id=tester.id,
            amount_kobo=150_000,
            note="Goodwill for the security disclosure.",
            staff_user_id=staff.id,
            program_id=program.id,
        )
        result = await triage_service.triage(
            submission_id=submission.id,
            status="accepted",
            category="bug",
            type_="crash",
            severity="critical",
            duplicate_of_id=None,
            public_response=None,
            admin_notes=None,
            staff_user_id=staff.id,
        )
        assert result["awardKobo"] == 50_000, "only 50 000 of the cap was left"


class TestTheBudgetRefuses:
    async def test_an_award_beyond_the_budget_is_refused_not_reduced(self):
        """**The one place clamping would be wrong.**

        Paying a tester less than the published amount because *we* ran out of money is the failure that
        would actually damage the programme. So the award is refused, the finding stays accepted, the money
        stays owed, and the operator is told to raise the budget.
        """
        program = await make_season(budget_kobo=250_000)
        staff = await make_user(staff=True)
        tester, _ = await accepted_finding(
            program, staff, severity="critical"
        )  # 200 000 of 250 000

        second_tester = await make_user()
        participant, submission = await submission_service.create_application(
            user=second_tester, fields=finding(), accepted_rules_version=program.rules_version
        )
        await triage_service.decide_application(
            participant_id=participant.id, decision="approve", reason=None, staff_user_id=staff.id
        )
        result = await triage_service.triage(
            submission_id=submission.id,
            status="accepted",
            category="bug",
            type_="crash",
            severity="critical",
            duplicate_of_id=None,
            public_response=None,
            admin_notes=None,
            staff_user_id=staff.id,
        )
        assert result["awardBlocked"] == "budget_exhausted"
        assert result["awardKobo"] == 0
        assert result["matrixKobo"] == 200_000
        assert "Raise the budget" in (result["awardMessage"] or "")
        assert result["submission"].status == "accepted", "the grading stands on its own"
        assert await ledger_service.balance(second_tester.id) == 0

    async def test_raising_the_budget_then_awarding_pays_it(self):
        """The operator's remedy, end to end."""
        program = await make_season(budget_kobo=250_000)
        staff = await make_user(staff=True)
        await accepted_finding(program, staff, severity="critical")

        second_tester = await make_user()
        participant, submission = await submission_service.create_application(
            user=second_tester, fields=finding(), accepted_rules_version=program.rules_version
        )
        await triage_service.decide_application(
            participant_id=participant.id, decision="approve", reason=None, staff_user_id=staff.id
        )
        blocked = await triage_service.triage(
            submission_id=submission.id,
            status="accepted",
            category="bug",
            type_="crash",
            severity="critical",
            duplicate_of_id=None,
            public_response=None,
            admin_notes=None,
            staff_user_id=staff.id,
        )
        assert blocked["awardBlocked"] == "budget_exhausted"

        await program_service.edit(program.id, {"budgetKobo": 1_000_000})
        settled = await reward_service.award_submission(
            submission_id=submission.id, staff_user_id=staff.id
        )
        assert settled.blocked is None
        assert settled.credited_kobo == 200_000
        assert await ledger_service.balance(second_tester.id) == 200_000

    async def test_the_budget_can_never_be_exceeded_by_concurrent_awards(self):
        """Two awards racing for the last ₦2,000 — only one fits.

        The lock is `FOR UPDATE` on the season row, held across the budget check and the `awardedKobo`
        increment. Without it both awards read the same remaining budget and both commit, and the CHECK
        constraint `awardedKobo <= budgetKobo` turns a business rule into a 500.
        """
        program = await make_season(budget_kobo=200_000)
        staff = await make_user(staff=True)
        submissions = []
        for _ in range(3):
            tester = await make_user()
            participant, submission = await submission_service.create_application(
                user=tester, fields=finding(), accepted_rules_version=program.rules_version
            )
            await triage_service.decide_application(
                participant_id=participant.id,
                decision="approve",
                reason=None,
                staff_user_id=staff.id,
            )
            factory = get_session_factory()
            async with factory() as session:
                await session.execute(
                    update(BugHuntSubmission)
                    .where(BugHuntSubmission.id == submission.id)
                    .values(
                        status="accepted",
                        category="bug",
                        severity="critical",
                        triaged_at=datetime.now(UTC),
                    )
                )
                await session.commit()
            submissions.append(submission.id)

        results = await asyncio.gather(
            *[
                reward_service.award_submission(submission_id=sid, staff_user_id=staff.id)
                for sid in submissions
            ],
            return_exceptions=True,
        )
        assert all(not isinstance(r, Exception) for r in results), results

        paid = [r for r in results if r.awarded]  # type: ignore[union-attr]
        assert len(paid) == 1, "exactly one award fitted in the budget"
        refreshed = await program_service.get(program.id)
        assert refreshed.awarded_kobo == 200_000
        assert refreshed.awarded_kobo <= refreshed.budget_kobo


# ---------------------------------------------------------------------------
# Spending
# ---------------------------------------------------------------------------


class TestDebits:
    async def test_a_debit_reduces_the_balance(self):
        program = await make_season()
        staff = await make_user(staff=True)
        tester, _ = await accepted_finding(program, staff)

        await ledger_service.debit(
            user_id=tester.id, kind="pass_redemption", amount_kobo=-120_000, pass_id="pass_x"
        )
        assert await ledger_service.balance(tester.id) == 80_000

    async def test_a_debit_beyond_the_balance_is_refused(self):
        program = await make_season()
        staff = await make_user(staff=True)
        tester, _ = await accepted_finding(program, staff, severity="low")  # 50 000

        with pytest.raises(ConflictError) as e:
            await ledger_service.debit(
                user_id=tester.id, kind="pass_redemption", amount_kobo=-60_000, pass_id="p"
            )
        assert e.value.code == "INSUFFICIENT_BALANCE"
        assert await ledger_service.balance(tester.id) == 50_000

    async def test_a_debit_may_spend_the_balance_exactly(self):
        """The boundary. An off-by-one here refuses a legitimate redemption of everything they have."""
        program = await make_season()
        staff = await make_user(staff=True)
        tester, _ = await accepted_finding(program, staff, severity="low")

        await ledger_service.debit(
            user_id=tester.id, kind="pass_redemption", amount_kobo=-50_000, pass_id="p"
        )
        assert await ledger_service.balance(tester.id) == 0

    async def test_concurrent_debits_cannot_overdraw(self):
        """**The test this module exists for.**

        A tester with ₦2,000 firing a redemption and a withdrawal at once. Both read the same balance if the
        lock is missing, and both succeed — leaving a negative balance and a pass we gave away. Run for real
        against Postgres, because a lock you have not raced is a lock you are hoping for.
        """
        program = await make_season()
        staff = await make_user(staff=True)
        tester, _ = await accepted_finding(program, staff, severity="critical")  # 200 000

        results = await asyncio.gather(
            *[
                ledger_service.debit(
                    user_id=tester.id,
                    kind="pass_redemption",
                    amount_kobo=-150_000,
                    pass_id=f"pass_{n}",
                )
                for n in range(4)
            ],
            return_exceptions=True,
        )
        succeeded = [r for r in results if not isinstance(r, Exception)]
        refused = [r for r in results if isinstance(r, ConflictError)]

        assert len(succeeded) == 1, f"only one 150 000 debit fits in 200 000: {results}"
        assert len(refused) == 3
        balance = await ledger_service.balance(tester.id)
        assert balance == 50_000
        assert balance >= 0

    async def test_a_credit_and_a_debit_of_the_same_size_net_to_nothing(self):
        program = await make_season()
        staff = await make_user(staff=True)
        tester, _ = await accepted_finding(program, staff, severity="low")

        withdrawal_id = await make_withdrawal(tester, 50_000)
        entry = await ledger_service.debit(
            user_id=tester.id,
            kind="withdrawal",
            amount_kobo=-50_000,
            withdrawal_id=withdrawal_id,
        )
        assert await ledger_service.balance(tester.id) == 0
        await ledger_service.reverse(entry=entry, note="Bank rejected the transfer.")
        assert await ledger_service.balance(tester.id) == 50_000
        # Two rows, not a deletion. The history is the explanation.
        assert await entry_count(tester.id) == 3

    async def test_a_withdrawal_entry_must_name_its_request(self):
        """Refused with a sentence rather than an `IntegrityError`.

        A cash movement that names no request cannot be reconciled against one, which makes it
        indistinguishable from a debit nobody asked for. The CHECK constraint would also catch this; this is
        the readable half.
        """
        program = await make_season()
        staff = await make_user(staff=True)
        tester, _ = await accepted_finding(program, staff, severity="low")
        with pytest.raises(ValidationError, match="must name the withdrawal"):
            await ledger_service.debit(
                user_id=tester.id, kind="withdrawal", amount_kobo=-50_000, withdrawal_id=None
            )

    async def test_a_reversal_is_a_new_row_never_a_deletion(self):
        program = await make_season()
        staff = await make_user(staff=True)
        tester, _ = await accepted_finding(program, staff, severity="low")
        entry = await ledger_service.debit(
            user_id=tester.id, kind="pass_redemption", amount_kobo=-50_000, pass_id="p"
        )
        reversal = await ledger_service.reverse(entry=entry, note="Grant failed.")
        assert reversal.kind == "pass_redemption_reversal"
        assert reversal.amount_kobo == 50_000
        assert reversal.pass_id == "p"

    @pytest.mark.parametrize(
        ("kind", "amount"),
        [("pass_redemption", 100), ("award", -100), ("withdrawal", 100)],
    )
    async def test_the_wrong_sign_for_a_kind_is_refused(self, kind, amount):
        user = await make_user()
        with pytest.raises(ValidationError):
            if amount > 0:
                await ledger_service.credit(user_id=user.id, kind=kind, amount_kobo=amount)
            else:
                await ledger_service.debit(user_id=user.id, kind=kind, amount_kobo=amount)

    async def test_a_zero_entry_is_refused(self):
        user = await make_user()
        with pytest.raises(ValidationError, match="worth nothing"):
            await ledger_service.credit(user_id=user.id, kind="adjustment", amount_kobo=0)

    async def test_a_credit_kind_cannot_be_used_as_a_debit(self):
        user = await make_user()
        with pytest.raises(ValidationError, match="not a spend"):
            await ledger_service.debit(user_id=user.id, kind="award", amount_kobo=-100)


# ---------------------------------------------------------------------------
# Adjustments
# ---------------------------------------------------------------------------


class TestAdjustments:
    async def test_a_positive_adjustment_credits(self):
        program = await make_season()
        staff = await make_user(staff=True)
        tester = await make_user()
        entry = await reward_service.adjust(
            user_id=tester.id,
            amount_kobo=75_000,
            note="Goodwill: the queue kept them waiting.",
            staff_user_id=staff.id,
            program_id=program.id,
        )
        assert entry.amount_kobo == 75_000
        assert entry.created_by_user_id == staff.id
        assert await ledger_service.balance(tester.id) == 75_000

    async def test_a_negative_adjustment_claws_back(self):
        program = await make_season()
        staff = await make_user(staff=True)
        tester, _ = await accepted_finding(program, staff, severity="critical")

        await reward_service.adjust(
            user_id=tester.id,
            amount_kobo=-50_000,
            note="Corrected an over-award.",
            staff_user_id=staff.id,
            program_id=program.id,
        )
        assert await ledger_service.balance(tester.id) == 150_000

    async def test_a_clawback_cannot_take_a_balance_below_zero(self):
        """A correction must not leave a tester owing us money."""
        program = await make_season()
        staff = await make_user(staff=True)
        tester, _ = await accepted_finding(program, staff, severity="low")  # 50 000

        with pytest.raises(ValidationError, match="below zero"):
            await reward_service.adjust(
                user_id=tester.id,
                amount_kobo=-60_000,
                note="Too much.",
                staff_user_id=staff.id,
                program_id=program.id,
            )
        assert await ledger_service.balance(tester.id) == 50_000

    async def test_a_season_attributed_adjustment_spends_the_budget(self):
        """**The budget has to cover an adjustment too.**

        Found by a redemption test that asserted a season's spend and got zero. Without this, the budget
        covers awards only — and a super admin, who is also the person able to raise the budget, can spend
        past it silently through the one endpoint that takes a free-typed amount. That makes the ceiling
        advisory for exactly the wrong person.
        """
        program = await make_season(budget_kobo=500_000)
        staff = await make_user(staff=True)
        tester = await make_user()

        await reward_service.adjust(
            user_id=tester.id,
            amount_kobo=200_000,
            note="Owed after a regrade.",
            staff_user_id=staff.id,
            program_id=program.id,
        )
        assert (await program_service.get(program.id)).awarded_kobo == 200_000

    async def test_an_adjustment_beyond_the_budget_is_refused(self):
        program = await make_season(budget_kobo=100_000)
        staff = await make_user(staff=True)
        tester = await make_user()
        with pytest.raises(ValidationError, match="Raise the budget"):
            await reward_service.adjust(
                user_id=tester.id,
                amount_kobo=200_000,
                note="Too generous.",
                staff_user_id=staff.id,
                program_id=program.id,
            )
        assert await ledger_service.balance(tester.id) == 0
        assert (await program_service.get(program.id)).awarded_kobo == 0

    async def test_a_clawback_returns_the_budget(self):
        """For the same reason a credit spends it. Otherwise correcting an over-award leaves the season
        permanently poorer by an amount nobody received."""
        program = await make_season(budget_kobo=1_000_000)
        staff = await make_user(staff=True)
        tester, _ = await accepted_finding(program, staff, severity="critical")
        assert (await program_service.get(program.id)).awarded_kobo == 200_000

        await reward_service.adjust(
            user_id=tester.id,
            amount_kobo=-50_000,
            note="Over-awarded.",
            staff_user_id=staff.id,
            program_id=program.id,
        )
        assert (await program_service.get(program.id)).awarded_kobo == 150_000

    async def test_an_unattributed_adjustment_touches_no_budget(self):
        """Goodwill for somebody between seasons counts against no budget and no cap, because there is no
        season for it to belong to."""
        program = await make_season(budget_kobo=1_000_000)
        staff = await make_user(staff=True)
        tester = await make_user()
        await reward_service.adjust(
            user_id=tester.id,
            amount_kobo=200_000,
            note="Goodwill between seasons.",
            staff_user_id=staff.id,
            program_id=None,
        )
        assert await ledger_service.balance(tester.id) == 200_000
        assert (await program_service.get(program.id)).awarded_kobo == 0

    async def test_an_adjustment_needs_a_reason(self):
        """The tester can read this ledger. An unexplained line on it is worse than no line."""
        staff = await make_user(staff=True)
        tester = await make_user()
        with pytest.raises(ValidationError, match="needs a reason"):
            await reward_service.adjust(
                user_id=tester.id, amount_kobo=1_000, note="   ", staff_user_id=staff.id
            )

    async def test_an_adjustment_of_nothing_is_refused(self):
        staff = await make_user(staff=True)
        tester = await make_user()
        with pytest.raises(ValidationError):
            await reward_service.adjust(
                user_id=tester.id, amount_kobo=0, note="why", staff_user_id=staff.id
            )


# ---------------------------------------------------------------------------
# Reading it back
# ---------------------------------------------------------------------------


class TestSummaryAndHistory:
    async def test_the_summary_agrees_with_the_entries(self):
        program = await make_season()
        staff = await make_user(staff=True)
        tester, _ = await accepted_finding(program, staff, severity="critical")
        await ledger_service.debit(
            user_id=tester.id, kind="pass_redemption", amount_kobo=-120_000, pass_id="p"
        )

        summary = await ledger_service.summary(user_id=tester.id, program=program)
        assert summary.balance_kobo == 80_000
        assert summary.lifetime_awarded_kobo == 200_000
        assert summary.earned_this_season_kobo == 200_000
        assert summary.cap_remaining_kobo == program.per_participant_cap_kobo - 200_000
        assert summary.open_withdrawal_kobo == 0

    async def test_an_open_withdrawal_is_reported_beside_the_balance(self):
        """Not folded into it. A tester waiting on a ₦1,500 transfer should see ₦0 available *and* ₦1,500 on
        its way, not one number that could mean either."""
        program = await make_season()
        staff = await make_user(staff=True)
        tester, _ = await accepted_finding(program, staff, severity="critical")

        withdrawal_id = await make_withdrawal(tester, 150_000)
        await ledger_service.debit(
            user_id=tester.id,
            kind="withdrawal",
            amount_kobo=-150_000,
            withdrawal_id=withdrawal_id,
        )

        summary = await ledger_service.summary(user_id=tester.id, program=program)
        assert summary.balance_kobo == 50_000
        assert summary.open_withdrawal_kobo == 150_000

    async def test_the_summary_works_with_no_season_open(self):
        """The gap between seasons, where a per-user wallet earns its keep."""
        program = await make_season()
        staff = await make_user(staff=True)
        tester, _ = await accepted_finding(program, staff, severity="critical")
        await program_service.close_season(program.id)

        summary = await ledger_service.summary(user_id=tester.id, program=None)
        assert summary.balance_kobo == 200_000
        assert summary.earned_this_season_kobo == 0
        assert summary.cap_remaining_kobo == 0

    async def test_the_history_spans_seasons_and_names_them(self):
        """A credit carries its season; a spend carries `None`, because it belongs to none."""
        first = await make_season(number=1)
        staff = await make_user(staff=True)
        tester, _ = await accepted_finding(first, staff, severity="critical")
        await ledger_service.debit(
            user_id=tester.id, kind="pass_redemption", amount_kobo=-50_000, pass_id="p"
        )

        rows, total = await ledger_service.history(user_id=tester.id)
        assert total == 2
        by_kind = {entry.kind: season for entry, season in rows}
        assert by_kind["award"] == 1
        assert by_kind["pass_redemption"] is None

    async def test_the_history_is_newest_first_and_paginates(self):
        program = await make_season(submission_daily_limit=50)
        staff = await make_user(staff=True)
        tester = await make_user()
        for index in range(4):
            await reward_service.adjust(
                user_id=tester.id,
                amount_kobo=1_000 * (index + 1),
                note=f"adjustment {index}",
                staff_user_id=staff.id,
                program_id=program.id,
            )
        page1, total = await ledger_service.history(user_id=tester.id, page=1, page_size=2)
        page2, _ = await ledger_service.history(user_id=tester.id, page=2, page_size=2)
        assert total == 4
        assert len(page1) == 2 and len(page2) == 2
        assert {e.id for e, _ in page1}.isdisjoint({e.id for e, _ in page2})

    async def test_a_balance_is_always_the_sum_of_its_entries(self):
        """The invariant, asserted directly rather than trusted. There is no cached total to drift, and this
        is what makes that a design property rather than a claim."""
        program = await make_season(per_participant_cap_kobo=10_000_000, budget_kobo=10_000_000)
        staff = await make_user(staff=True)
        tester, _ = await accepted_finding(program, staff, severity="critical")
        await reward_service.adjust(
            user_id=tester.id, amount_kobo=25_000, note="goodwill", staff_user_id=staff.id
        )
        entry = await ledger_service.debit(
            user_id=tester.id, kind="pass_redemption", amount_kobo=-60_000, pass_id="p"
        )
        await ledger_service.reverse(entry=entry, note="grant failed")
        await ledger_service.debit(
            user_id=tester.id,
            kind="withdrawal",
            amount_kobo=-100_000,
            withdrawal_id=await make_withdrawal(tester, 100_000),
        )

        rows, _ = await ledger_service.history(user_id=tester.id, page_size=100)
        assert await ledger_service.balance(tester.id) == sum(e.amount_kobo for e, _ in rows)


# ---------------------------------------------------------------------------
# Over the wire
# ---------------------------------------------------------------------------


def bearer(user: User) -> dict[str, str]:
    from src.shared.auth.jwt import create_access_token

    return {"Authorization": f"Bearer {create_access_token({'sub': user.email})}"}


class TestOverTheWire:
    async def test_a_tester_reads_their_own_wallet_and_ledger(self, client):
        program = await make_season()
        staff = await make_user(staff=True)
        tester, _ = await accepted_finding(program, staff, severity="critical")

        wallet = await client.get("/api/v1/bug-hunt/wallet", headers=bearer(tester))
        assert wallet.status_code == 200
        assert wallet.json()["balanceKobo"] == 200_000

        ledger = await client.get("/api/v1/bug-hunt/wallet/ledger", headers=bearer(tester))
        assert ledger.status_code == 200
        assert ledger.json()["balanceKobo"] == 200_000
        assert ledger.json()["entries"][0]["kind"] == "award"
        assert ledger.json()["entries"][0]["seasonNumber"] == 1

    async def test_the_wallet_is_reachable_between_seasons(self, client):
        """The point of a per-user wallet. A wallet that four-oh-foured here would look like it had eaten
        their balance."""
        program = await make_season()
        staff = await make_user(staff=True)
        tester, _ = await accepted_finding(program, staff, severity="critical")
        await program_service.close_season(program.id)

        wallet = await client.get("/api/v1/bug-hunt/wallet", headers=bearer(tester))
        assert wallet.status_code == 200
        assert wallet.json()["balanceKobo"] == 200_000

    async def test_me_carries_the_wallet_once_they_have_taken_part(self, client):
        program = await make_season()
        staff = await make_user(staff=True)
        tester, _ = await accepted_finding(program, staff, severity="critical")

        me = await client.get("/api/v1/bug-hunt/me", headers=bearer(tester))
        assert me.json()["wallet"]["balanceKobo"] == 200_000

    async def test_me_carries_no_wallet_for_a_passer_by(self, client):
        """No row is created for a learner who wandered onto the programme site and never applied."""
        await make_season()
        stranger = await make_user()
        me = await client.get("/api/v1/bug-hunt/me", headers=bearer(stranger))
        assert me.json()["wallet"] is None

    async def test_one_tester_cannot_read_anothers_wallet(self, client):
        """There is no path that takes a user id: the wallet is always the caller's own."""
        program = await make_season()
        staff = await make_user(staff=True)
        rich, _ = await accepted_finding(program, staff, severity="critical")
        poor = await make_user()

        wallet = await client.get("/api/v1/bug-hunt/wallet", headers=bearer(poor))
        assert wallet.json()["balanceKobo"] == 0

    async def test_an_adjustment_over_http_is_super_admin_and_audited(self, client):
        program = await make_season()
        staff = await make_user(staff=True)
        tester, _ = await accepted_finding(program, staff, severity="low")
        participant = await program_service.participation(user_id=tester.id, program_id=program.id)
        assert participant is not None

        # A content manager cannot: this is the only typed amount in the programme.
        manager = await make_user(staff=True)
        factory = get_session_factory()
        async with factory() as session:
            await session.execute(
                update(User).where(User.id == manager.id).values(admin_staff_role="CONTENT_MANAGER")
            )
            await session.commit()
        refused = await client.post(
            f"/api/v1/admin/bug-hunt/participants/{participant.id}/adjustment",
            headers=bearer(manager),
            json={"amountKobo": 100_000, "note": "goodwill"},
        )
        assert refused.status_code == 403

        allowed = await client.post(
            f"/api/v1/admin/bug-hunt/participants/{participant.id}/adjustment",
            headers=bearer(staff),
            json={"amountKobo": 100_000, "note": "Owed for the regrade."},
        )
        assert allowed.status_code == 200, allowed.text
        assert allowed.json()["balanceKobo"] == 150_000
        assert allowed.json()["entry"]["note"] == "Owed for the regrade."

        from src.domains.admin.db_models import AuditLog

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
        assert "bug_hunt_adjust_balance" in actions

    async def test_an_adjustment_without_a_note_is_refused_over_http(self, client):
        program = await make_season()
        staff = await make_user(staff=True)
        tester, _ = await accepted_finding(program, staff, severity="low")
        participant = await program_service.participation(user_id=tester.id, program_id=program.id)
        assert participant is not None

        response = await client.post(
            f"/api/v1/admin/bug-hunt/participants/{participant.id}/adjustment",
            headers=bearer(staff),
            json={"amountKobo": 100_000, "note": ""},
        )
        # 400, not 422: this repo's `validation_error_handler` maps Pydantic request-shape failures to 400,
        # while a domain `ValidationError` raised from a service carries 422. Both are refusals; they arrive
        # by different paths, and the note is caught by the model here rather than by `reward_service`.
        assert response.status_code == 400
        assert await ledger_service.balance(tester.id) == 50_000, "and nothing was written"
