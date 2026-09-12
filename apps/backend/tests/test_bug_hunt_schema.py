"""The Bug Hunt schema encodes the invariants, and this asserts it does.

Every claim here is about table metadata rather than about a running query, so it needs no database.
That is deliberate: these are the constraints that make a class of bug *impossible*, and the way they
get lost is not a failing test — it is somebody removing a CHECK during a refactor and nothing noticing
because no test ever exercised the row it forbade.

Two groups of claim matter most:

- **The three partial unique indexes.** One open season, one award per submission, one open withdrawal
  per tester. Each replaces a check-then-write that has no lock between its two steps, and each guards
  either money or the meaning of "the current season".
- **The wallet/season split.** The ledger must be keyed on `userId` and must *not* be reachable only
  through a participation, because that is what lets a balance survive a closed season.

Run with: pytest tests/test_bug_hunt_schema.py -v
"""

import pytest

from src.domains.bug_hunt import db_models as m
from src.shared.database.base import Base

TABLES = [
    "BugHuntProgram",
    "BugHuntParticipant",
    "BugHuntSubmission",
    "BugHuntAttachment",
    "BugHuntWallet",
    "BugHuntLedgerEntry",
    "BugHuntPayoutAccount",
    "BugHuntWithdrawal",
]

SEASON_SCOPED = ["BugHuntParticipant", "BugHuntSubmission"]
WALLET_SCOPED = ["BugHuntWallet", "BugHuntLedgerEntry", "BugHuntPayoutAccount", "BugHuntWithdrawal"]


def table(name: str):
    return Base.metadata.tables[name]


def check_names(name: str) -> set[str]:
    return {c.name for c in table(name).constraints if type(c).__name__ == "CheckConstraint"}


def index_names(name: str) -> set[str]:
    return {i.name for i in table(name).indexes}


class TestTablesExist:
    @pytest.mark.parametrize("name", TABLES)
    def test_table_is_registered(self, name):
        assert name in Base.metadata.tables

    def test_the_migration_declares_the_same_eight_tables(self):
        """Metadata and migration `083` must agree, or `alembic upgrade` produces a schema the ORM
        cannot read. Autogenerate only sees these models because `alembic/env.py` imports
        `bug_hunt.db_models`; without that line the migration would silently drift from the models.
        """
        source = (
            __import__("pathlib").Path(__file__).parents[1] / "alembic/versions/083_bug_hunt.py"
        ).read_text()
        for name in TABLES:
            assert f'"{name}"' in source, f"{name} is in the models but not in migration 083"

    def test_alembic_env_imports_the_domain(self):
        env = (__import__("pathlib").Path(__file__).parents[1] / "alembic/env.py").read_text()
        assert "src.domains.bug_hunt.db_models" in env


class TestOnlyOneSeasonCanBeOpen:
    """Two open seasons would make "the current season" ambiguous for every read in the domain,
    including the ones that decide what a finding is worth and which cap applies."""

    def test_the_partial_unique_index_exists(self):
        assert "BugHuntProgram_one_open_key" in index_names("BugHuntProgram")

    def test_it_is_unique_and_restricted_to_open_rows(self):
        idx = next(
            i for i in table("BugHuntProgram").indexes if i.name == "BugHuntProgram_one_open_key"
        )
        assert idx.unique is True
        where = str(idx.dialect_options["postgresql"]["where"])
        assert "open" in where, "the index must be partial, or no season could ever be a draft"


class TestOneAwardPerSubmission:
    """What makes triage idempotent: a double-submitted form, a retried request, or two staff working
    the queue at once all collapse to one payment."""

    def test_the_partial_unique_index_exists(self):
        assert "BugHuntLedgerEntry_award_once_key" in index_names("BugHuntLedgerEntry")

    def test_it_is_unique_and_scoped_to_award_entries(self):
        idx = next(
            i
            for i in table("BugHuntLedgerEntry").indexes
            if i.name == "BugHuntLedgerEntry_award_once_key"
        )
        assert idx.unique is True
        where = str(idx.dialect_options["postgresql"]["where"])
        assert "award" in where, (
            "without the partial predicate this would allow only one ledger entry of any kind per "
            "submission, which would block the reversal entries the spend rails depend on"
        )


class TestOneOpenWithdrawalPerTester:
    """Two concurrent requests would each see no open request and both debit a balance that will be
    transferred once."""

    def test_the_partial_unique_index_exists(self):
        assert "BugHuntWithdrawal_one_open_key" in index_names("BugHuntWithdrawal")

    def test_it_covers_exactly_the_open_statuses(self):
        idx = next(
            i
            for i in table("BugHuntWithdrawal").indexes
            if i.name == "BugHuntWithdrawal_one_open_key"
        )
        assert idx.unique is True
        where = str(idx.dialect_options["postgresql"]["where"])
        for open_status in m.WITHDRAWAL_OPEN_STATUSES:
            assert open_status in where
        # `paid` and `rejected` must be excluded, or a tester could never request a second withdrawal.
        assert "paid" not in where.replace("'requested'", "").replace("'approved'", "")


class TestTheWalletIsNotSeasonScoped:
    """§6.1. The decision most likely to be undone by someone tidying up, so it is asserted directly."""

    @pytest.mark.parametrize("name", WALLET_SCOPED)
    def test_wallet_tables_are_keyed_on_the_user(self, name):
        assert "userId" in table(name).columns

    def test_the_ledger_has_no_required_programme(self):
        """A required `programId` is exactly the mistake: it would strand an unspent balance behind a
        closed season, or split one tester's money across two wallets with two minimums to clear."""
        assert table("BugHuntLedgerEntry").columns["programId"].nullable is True

    def test_the_payout_account_belongs_to_the_user_not_a_participation(self):
        """Entered once, reused every season. Keyed on a participation, a returning tester would be
        asked to re-enter their bank details every season."""
        assert table("BugHuntPayoutAccount").columns["userId"].unique is True
        assert "participantId" not in table("BugHuntPayoutAccount").columns
        assert "programId" not in table("BugHuntPayoutAccount").columns

    def test_a_withdrawal_belongs_to_no_season(self):
        assert "programId" not in table("BugHuntWithdrawal").columns

    @pytest.mark.parametrize("name", SEASON_SCOPED)
    def test_season_scoped_tables_require_a_programme(self, name):
        assert table(name).columns["programId"].nullable is False

    def test_the_wallet_holds_no_balance(self):
        """It exists to be locked, not to cache a number. A balance column is a second source of truth
        for the one figure in this domain that must have exactly one."""
        assert "balanceKobo" not in table("BugHuntWallet").columns
        assert "balance" not in table("BugHuntWallet").columns

    def test_a_submission_carries_no_award_amount(self):
        """The ledger is the only place a figure lives; list endpoints join it. A mirror here is one
        deploy away from disagreeing with the balance."""
        for absent in ("awardKobo", "awardedKobo", "rewardKobo"):
            assert absent not in table("BugHuntSubmission").columns


class TestLedgerConstraints:
    def test_the_sign_rule_is_enforced(self):
        assert "BugHuntLedgerEntry_sign_check" in check_names("BugHuntLedgerEntry")

    def test_a_zero_entry_is_forbidden(self):
        """An entry worth nothing is either a bug or a note, and a ledger is not for notes."""
        assert "BugHuntLedgerEntry_nonzero_check" in check_names("BugHuntLedgerEntry")

    def test_an_award_must_name_its_season_and_finding(self):
        """Without this, the per-tester cap and the season budget are summed over an incomplete set and
        nobody notices until the numbers are wrong."""
        assert "BugHuntLedgerEntry_award_attribution_check" in check_names("BugHuntLedgerEntry")

    def test_a_spend_must_not_name_a_season(self):
        """The other half of the same rule. Attributing a redemption to a season would count it against
        a budget it never spent."""
        assert "BugHuntLedgerEntry_spend_unattributed_check" in check_names("BugHuntLedgerEntry")

    def test_a_withdrawal_entry_must_link_its_request(self):
        assert "BugHuntLedgerEntry_withdrawal_link_check" in check_names("BugHuntLedgerEntry")

    def test_amounts_are_integers(self):
        """Kobo, as `Integer`. A `Numeric` or `Float` column here is how a fraction of a naira is
        created and then lost."""
        from sqlalchemy import Integer

        for name, column in [
            ("BugHuntLedgerEntry", "amountKobo"),
            ("BugHuntWithdrawal", "amountKobo"),
            ("BugHuntProgram", "budgetKobo"),
            ("BugHuntProgram", "awardedKobo"),
            ("BugHuntProgram", "perParticipantCapKobo"),
            ("BugHuntProgram", "minWithdrawalKobo"),
        ]:
            assert isinstance(table(name).columns[column].type, Integer), f"{name}.{column}"


class TestProgramConstraints:
    def test_the_budget_is_a_real_ceiling(self):
        """Enforced by the database, so raising it is a deliberate edit rather than something a
        generous triage afternoon does by accident."""
        assert "BugHuntProgram_awarded_within_budget_check" in check_names("BugHuntProgram")

    def test_the_window_must_be_ordered(self):
        assert "BugHuntProgram_window_check" in check_names("BugHuntProgram")

    def test_the_uplift_cannot_reach_a_hundred_percent(self):
        """A 100% uplift is a free pass, and a redemption rail configured to charge nothing is a
        redemption rail with no balance check."""
        assert "BugHuntProgram_uplift_check" in check_names("BugHuntProgram")

    def test_every_season_varying_value_is_a_column(self):
        """Decision 12, asserted. If a value that could differ between seasons is not here, opening
        Season 2 needs a code change, and the multi-season design was decorative.
        """
        columns = set(table("BugHuntProgram").columns.keys())
        for required in (
            "startsAt",
            "endsAt",
            "budgetKobo",
            "perParticipantCapKobo",
            "minWithdrawalKobo",
            "passUpliftPercent",
            "countryAllowlist",
            "rewardMatrix",
            "rulesVersion",
            "submissionDailyLimit",
        ):
            assert required in columns

    def test_the_country_allowlist_is_a_list(self):
        """An array from the start, so widening past Nigeria is data rather than a migration."""
        from sqlalchemy.dialects.postgresql import ARRAY

        assert isinstance(table("BugHuntProgram").columns["countryAllowlist"].type, ARRAY)


class TestSubmissionConstraints:
    def test_an_accepted_finding_must_be_graded(self):
        """Otherwise a triage bug accepts a submission with a null severity, pays ₦0, and tells the
        tester they were accepted."""
        assert "BugHuntSubmission_accepted_graded_check" in check_names("BugHuntSubmission")

    def test_category_and_severity_must_agree(self):
        """A `bug` graded `standard` looks up nothing in the reward matrix and awards zero silently."""
        assert "BugHuntSubmission_severity_pairing_check" in check_names("BugHuntSubmission")

    def test_a_duplicate_must_point_somewhere(self):
        """Both `duplicate` and `known_issue` mean "this one instead", so both must say which one."""
        assert "BugHuntSubmission_duplicate_target_check" in check_names("BugHuntSubmission")

    def test_a_submission_cannot_duplicate_itself(self):
        assert "BugHuntSubmission_self_duplicate_check" in check_names("BugHuntSubmission")

    def test_a_finding_outlives_its_reporter(self):
        """`SET NULL`, not `CASCADE`. Forgetting the tester must not erase the bug they found — the same
        reasoning `Feedback.userId` already follows — and cross-season `known_issue` marking depends on
        these rows still being there.
        """
        submissions = table("BugHuntSubmission")
        assert submissions.columns["userId"].nullable is True
        assert submissions.columns["participantId"].nullable is True
        for column_name in ("userId", "participantId"):
            fk = next(iter(submissions.columns[column_name].foreign_keys))
            assert fk.ondelete == "SET NULL", f"{column_name} must not cascade"

    def test_a_participation_goes_with_the_account(self):
        """The asymmetry with the submission above is the point: a participation with no person behind
        it means nothing, so it is the one thing that does cascade."""
        fk = next(iter(table("BugHuntParticipant").columns["userId"].foreign_keys))
        assert fk.ondelete == "CASCADE"

    def test_a_season_with_findings_cannot_be_dropped(self):
        """No `ondelete` on the submission's programme, so Postgres refuses. Seasons are closed, never
        deleted, and deleting one would destroy the engineering record it produced."""
        fk = next(iter(table("BugHuntSubmission").columns["programId"].foreign_keys))
        assert fk.ondelete is None

    def test_the_reported_severity_is_kept_apart_from_the_triaged_one(self):
        """The tester's guess never touches money. It is here so we can see who calibrates well."""
        columns = table("BugHuntSubmission").columns
        assert "reportedSeverity" in columns
        assert "severity" in columns
        assert columns["severity"].nullable is True, "ungraded until a human triages it"


class TestPayoutPii:
    def test_the_full_account_number_is_stored_encrypted(self):
        columns = table("BugHuntPayoutAccount").columns
        assert "accountNumberEnc" in columns
        assert "accountNumberLast4" in columns
        assert "accountNumber" not in columns, "a plaintext account-number column must not exist"

    def test_the_last_four_is_exactly_four(self):
        assert "BugHuntPayoutAccount_last4_check" in check_names("BugHuntPayoutAccount")

    def test_a_paid_withdrawal_must_carry_its_evidence(self):
        """A payout with no bank reference cannot be matched to a statement line, which makes it
        indistinguishable from one that never happened — and this is the only rail by which a tester
        actually receives cash."""
        assert "BugHuntWithdrawal_paid_evidence_check" in check_names("BugHuntWithdrawal")

    def test_a_rejected_withdrawal_must_carry_a_reason(self):
        assert "BugHuntWithdrawal_rejection_reason_check" in check_names("BugHuntWithdrawal")

    def test_a_paid_payout_outlives_the_account(self):
        """`SET NULL`, not `CASCADE`.

        Found by exercising deletion against real Postgres: with `CASCADE`, deleting a tester erased
        every record that we had transferred money to them. Real money left the business account and has
        to be accountable years later, so the payout is anonymised rather than destroyed. The wallet and
        its ledger *do* cascade — a balance with nobody to pay is not a balance.
        """
        withdrawals = table("BugHuntWithdrawal")
        for column_name in ("userId", "walletId"):
            assert withdrawals.columns[column_name].nullable is True
            fk = next(iter(withdrawals.columns[column_name].foreign_keys))
            assert fk.ondelete == "SET NULL", f"{column_name} must not cascade"

    def test_the_wallet_and_ledger_do_cascade(self):
        """The deliberate asymmetry with the payout above."""
        assert (
            next(iter(table("BugHuntWallet").columns["userId"].foreign_keys)).ondelete == "CASCADE"
        )
        assert (
            next(iter(table("BugHuntLedgerEntry").columns["walletId"].foreign_keys)).ondelete
            == "CASCADE"
        )

    def test_bank_details_are_snapshotted_onto_the_withdrawal(self):
        """So a historic payout stays reconcilable after the retention sweep deletes the account row —
        and without keeping the full number for years."""
        columns = table("BugHuntWithdrawal").columns
        for snapshot in ("bankNameSnapshot", "accountNameSnapshot", "accountLast4Snapshot"):
            assert snapshot in columns
        assert "accountNumberSnapshot" not in columns, "never snapshot the full number"
