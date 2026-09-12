"""The reward matrix: the amounts, the validator, and what an award reads.

These are the tests that guard money without needing a database, so they run in every suite. The
validator is worth this much attention because it is the only thing standing between an admin form and
a JSONB column that an award reads: a matrix missing a tier does not fail loudly at triage, it pays ₦0
for a real bug while telling the tester they were accepted.

Run with: pytest tests/test_bug_hunt_rewards.py -v
"""

import pytest

from src.domains.bug_hunt import rewards
from src.domains.bug_hunt.db_models import (
    BUG_SEVERITIES,
    FEEDBACK_TIERS,
    LEDGER_CREDIT_KINDS,
    LEDGER_DEBIT_KINDS,
    LEDGER_KINDS,
    UNPAID_SUBMISSION_STATUSES,
)


class TestTheSignedOffAmounts:
    """§5.2 of the plan, signed off 2026-09-12. Pinned so a refactor cannot drift them."""

    @pytest.mark.parametrize(
        ("category", "severity", "naira"),
        [
            ("bug", "critical", 2_000),
            ("bug", "high", 1_500),
            ("bug", "medium", 1_000),
            ("bug", "low", 500),
            ("feedback", "high_value", 1_500),
            ("feedback", "standard", 500),
        ],
    )
    def test_amount_matches_the_published_table(self, category, severity, naira):
        assert rewards.DEFAULT_REWARD_MATRIX[category][severity] == naira * 100

    def test_every_amount_is_an_integer_number_of_kobo(self):
        """No floats on any money path. A float naira amount is how ₦0.01 goes missing per award."""
        for tiers in rewards.DEFAULT_REWARD_MATRIX.values():
            for amount in tiers.values():
                assert isinstance(amount, int)
                assert not isinstance(amount, bool)

    def test_the_ladder_is_strictly_descending_for_bugs(self):
        """A critical must be worth more than a high, and so on down.

        Not pedantry: the severity ladder is the only mechanism steering testers towards quality over
        volume. Two adjacent tiers paying the same amount would make grading them apart pointless.
        """
        ladder = ["critical", "high", "medium", "low"]
        amounts = [rewards.DEFAULT_REWARD_MATRIX["bug"][s] for s in ladder]
        assert amounts == sorted(amounts, reverse=True)
        assert len(set(amounts)) == len(amounts)

    def test_there_is_no_platform_bonus(self):
        """Decision F was dropped, and this records why so it is not reintroduced by instinct.

        A flat ₦500 mobile bonus was in an earlier draft. Against a ₦500 low-severity floor it would
        have doubled the award for *choosing a platform* rather than for finding anything — paying for
        a dropdown selection. Mobile coverage is a copy and monitoring problem instead.
        """
        assert not hasattr(rewards, "PLATFORM_BONUS_KOBO")
        assert not hasattr(rewards, "MOBILE_BONUS_KOBO")

    def test_the_derived_guards_are_coherent_with_the_amounts(self):
        """The cap must buy several criticals and the budget must buy several capped testers.

        A cap below one critical would mean a tester's best possible finding is clipped, and a budget
        below one cap would mean the season cannot pay a single tester in full.
        """
        critical = rewards.DEFAULT_REWARD_MATRIX["bug"]["critical"]
        assert rewards.DEFAULT_PER_PARTICIPANT_CAP_KOBO >= critical * 5
        assert rewards.DEFAULT_BUDGET_KOBO >= rewards.DEFAULT_PER_PARTICIPANT_CAP_KOBO * 5

    def test_the_minimum_withdrawal_is_reachable_from_one_finding(self):
        """₦1,000, not ₦2,000.

        With a critical at ₦2,000, a ₦2,000 minimum would put cash out of reach of anyone with fewer
        than two top-grade findings and make "or redeem cash" a promise the product mostly refuses.
        The floor has to be clearable by a single medium bug.
        """
        assert rewards.DEFAULT_MIN_WITHDRAWAL_KOBO <= rewards.DEFAULT_REWARD_MATRIX["bug"]["medium"]

    def test_the_pass_uplift_cannot_make_a_pass_free(self):
        assert 0 <= rewards.DEFAULT_PASS_UPLIFT_PERCENT <= 90


class TestValidateMatrix:
    def test_the_default_matrix_validates(self):
        assert rewards.validate_matrix(rewards.default_matrix()) == rewards.DEFAULT_REWARD_MATRIX

    def test_default_matrix_returns_a_copy(self):
        """Seeding a season must not be able to mutate the module default for every later season."""
        copy = rewards.default_matrix()
        copy["bug"]["critical"] = 1
        assert rewards.DEFAULT_REWARD_MATRIX["bug"]["critical"] == 200_000

    def test_a_missing_severity_is_refused(self):
        """The failure this exists to prevent: an incomplete matrix awards ₦0 and reports success."""
        matrix = rewards.default_matrix()
        del matrix["bug"]["critical"]
        with pytest.raises(ValueError, match="missing severities"):
            rewards.validate_matrix(matrix)

    def test_a_missing_category_is_refused(self):
        matrix = rewards.default_matrix()
        del matrix["feedback"]
        with pytest.raises(ValueError, match="feedback"):
            rewards.validate_matrix(matrix)

    def test_an_unknown_severity_is_refused(self):
        """A typo'd key looks like it configured something and configures nothing."""
        matrix = rewards.default_matrix()
        matrix["bug"]["kritical"] = 200_000
        with pytest.raises(ValueError, match="unknown severities"):
            rewards.validate_matrix(matrix)

    def test_a_severity_from_the_wrong_category_is_refused(self):
        """`bug` grades on severity, `feedback` on value delivered. Crossing them looks up nothing."""
        matrix = rewards.default_matrix()
        matrix["bug"]["standard"] = 50_000
        with pytest.raises(ValueError, match="unknown severities"):
            rewards.validate_matrix(matrix)

    def test_an_unknown_category_is_refused(self):
        matrix = rewards.default_matrix()
        matrix["suggestion"] = {"standard": 1}
        with pytest.raises(ValueError, match="unknown categories"):
            rewards.validate_matrix(matrix)

    def test_a_negative_amount_is_refused(self):
        matrix = rewards.default_matrix()
        matrix["bug"]["low"] = -1
        with pytest.raises(ValueError, match="cannot be negative"):
            rewards.validate_matrix(matrix)

    def test_zero_is_allowed(self):
        """A season may legitimately price a tier at nothing without removing it from the table."""
        matrix = rewards.default_matrix()
        matrix["bug"]["low"] = 0
        assert rewards.validate_matrix(matrix)["bug"]["low"] == 0

    def test_a_boolean_amount_is_refused(self):
        """`True` is an `int` in Python, and would become an award of one kobo."""
        matrix = rewards.default_matrix()
        matrix["bug"]["low"] = True
        with pytest.raises(ValueError, match="integer number of kobo"):
            rewards.validate_matrix(matrix)

    def test_a_float_amount_is_refused(self):
        matrix = rewards.default_matrix()
        matrix["bug"]["low"] = 500.0
        with pytest.raises(ValueError, match="integer number of kobo"):
            rewards.validate_matrix(matrix)

    @pytest.mark.parametrize("bad", [None, [], "matrix", 5])
    def test_a_non_object_is_refused(self, bad):
        with pytest.raises(ValueError):
            rewards.validate_matrix(bad)


class TestAmountFor:
    """What an award actually reads, off the *season's* matrix rather than the module default."""

    def test_reads_the_season_matrix_not_the_default(self):
        """The whole reason the matrix lives on the row.

        A season that pays differently from the current default must keep paying its own rates — this
        is what makes a late triage, after the next season opened, pay what was published when the
        finding was reported.
        """
        season_one = {"bug": {"critical": 200_000}}
        season_two = {"bug": {"critical": 500_000}}
        assert rewards.amount_for(season_one, "bug", "critical") == 200_000
        assert rewards.amount_for(season_two, "bug", "critical") == 500_000

    @pytest.mark.parametrize(
        ("category", "severity"),
        [
            (None, "critical"),
            ("bug", None),
            (None, None),
            ("", ""),
            ("bug", "nonsense"),
            ("x", "y"),
        ],
    )
    def test_ungraded_or_unknown_pays_nothing(self, category, severity):
        assert rewards.amount_for(rewards.default_matrix(), category, severity) == 0

    def test_a_malformed_matrix_pays_nothing_rather_than_raising(self):
        """An award path must not throw on bad configuration.

        A raise here would fail the whole triage request, leaving the submission ungraded and the
        triager with a 500. Zero is wrong but visible: the tester sees an accepted finding worth
        nothing, which is a support conversation rather than a broken queue.
        """
        assert rewards.amount_for({"bug": "not-a-dict"}, "bug", "low") == 0
        assert rewards.amount_for({"bug": {"low": "500"}}, "bug", "low") == 0
        assert rewards.amount_for({"bug": {"low": True}}, "bug", "low") == 0
        assert rewards.amount_for({}, "bug", "low") == 0


class TestVocabularies:
    """The frozensets in `db_models` mirror CHECK constraints. Drift between them is a 500."""

    def test_severities_by_category_matches_the_pairing_constraint(self):
        assert rewards.SEVERITIES_BY_CATEGORY["bug"] == BUG_SEVERITIES
        assert rewards.SEVERITIES_BY_CATEGORY["feedback"] == FEEDBACK_TIERS

    def test_bug_severities_and_feedback_tiers_do_not_overlap(self):
        """If they did, the pairing constraint could not tell a mis-graded submission from a valid one."""
        assert not (BUG_SEVERITIES & FEEDBACK_TIERS)

    def test_every_severity_in_the_default_matrix_is_a_known_one(self):
        for category, tiers in rewards.DEFAULT_REWARD_MATRIX.items():
            assert set(tiers) == set(rewards.SEVERITIES_BY_CATEGORY[category])

    def test_ledger_kinds_partition_into_credits_debits_and_signed(self):
        """Every kind has exactly one sign rule, which is what the sign CHECK constraint encodes."""
        assert LEDGER_CREDIT_KINDS.isdisjoint(LEDGER_DEBIT_KINDS)
        assert LEDGER_CREDIT_KINDS | LEDGER_DEBIT_KINDS | {"adjustment"} == LEDGER_KINDS

    def test_every_debit_kind_has_a_reversal(self):
        """A debit with no way back is a debit that strands money on a failure.

        Both spend rails can fail after the ledger was written — a pass grant can raise, a withdrawal
        can be rejected — so each needs a compensating credit rather than a deleted row.
        """
        for debit in LEDGER_DEBIT_KINDS:
            assert f"{debit}_reversal" in LEDGER_CREDIT_KINDS

    def test_the_unpaid_outcomes_include_known_issue(self):
        """`known_issue` exists only because seasons recur.

        A bug found in Season 1 and never fixed will be found again in Season 2. Marking that a
        `duplicate` blames the reporter for our backlog; `known_issue` is the same ₦0 with honest
        copy. If this set loses the distinction, that fairness rule has quietly gone.
        """
        assert "known_issue" in UNPAID_SUBMISSION_STATUSES
        assert "duplicate" in UNPAID_SUBMISSION_STATUSES
        assert "accepted" not in UNPAID_SUBMISSION_STATUSES
