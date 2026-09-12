"""What a finding is worth — the default matrix, and how to read one off a season.

**These constants are a seed, not the answer.** A season's authoritative matrix lives on
`BugHuntProgram.rewardMatrix`, and `amount_for` reads it from there. This module supplies the numbers
used to populate a *new* season, and nothing else consults it at award time.

That indirection is the whole point. Held as a module constant, editing Season 2's amounts would
silently rewrite what every closed season claims it paid: the `/seasons` page would show Season 1
paying Season 2's rates, and a submission triaged late — after the next season opened — would be paid
at rates that did not exist when it was reported. On the row, a closed season keeps telling the truth.

Amounts are **kobo**, always, and integers, always. ₦2,000 is `200_000`.

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

from __future__ import annotations

from typing import Any

from .db_models import BUG_SEVERITIES, CATEGORIES, FEEDBACK_TIERS

#: Signed off 2026-09-12. See §5.2 of `docs/implementation/bug-hunt-program-plan.md`, including the
#: note on what a ₦500 floor implies for submission volume, and why the per-platform bonus that an
#: earlier draft carried was dropped: at this floor it would have doubled an award for *choosing a
#: platform* rather than for finding anything.
#:
#: Feedback is graded on value delivered rather than severity, so it carries its own two tiers.
DEFAULT_REWARD_MATRIX: dict[str, dict[str, int]] = {
    "bug": {
        "critical": 200_000,  # ₦2,000
        "high": 150_000,  # ₦1,500
        "medium": 100_000,  # ₦1,000
        "low": 50_000,  # ₦500
    },
    "feedback": {
        "high_value": 150_000,  # ₦1,500 — acted on
        "standard": 50_000,  # ₦500
    },
}

#: Season 1's derived guards, also seeds. Both are columns, so Season 2 can differ freely.
DEFAULT_BUDGET_KOBO = 30_000_000  # ₦300,000
DEFAULT_PER_PARTICIPANT_CAP_KOBO = 1_500_000  # ₦15,000
DEFAULT_MIN_WITHDRAWAL_KOBO = 100_000  # ₦1,000
#: Percent off a pass's catalogue price when paid for from a Bug Hunt balance. 25 means ₦1,500 of
#: balance buys a ₦2,000 pass — it costs us COGS rather than cash, and it seeds Plus usage.
DEFAULT_PASS_UPLIFT_PERCENT = 25
DEFAULT_SUBMISSION_DAILY_LIMIT = 10

#: Which severities are valid for which category. Mirrors the pairing CHECK constraint on
#: `BugHuntSubmission`; kept here so a route can refuse a bad pair with a sentence rather than let
#: Postgres raise an `IntegrityError` nobody can read.
SEVERITIES_BY_CATEGORY: dict[str, frozenset[str]] = {
    "bug": BUG_SEVERITIES,
    "feedback": FEEDBACK_TIERS,
}


def default_matrix() -> dict[str, dict[str, int]]:
    """A fresh, mutable copy of the default matrix, for seeding a new season."""
    return {category: dict(tiers) for category, tiers in DEFAULT_REWARD_MATRIX.items()}


def validate_matrix(matrix: Any) -> dict[str, dict[str, int]]:
    """Check a caller-supplied matrix and return it normalised, or raise `ValueError`.

    Validated on the way in rather than trusted, because this is a JSONB column an admin form writes
    and an award reads. A matrix missing a tier would pay ₦0 for a real finding while telling the
    tester it was accepted; a matrix with a stray key would look like it configured something. Both
    are worth refusing at the season editor rather than discovering at triage.
    """
    if not isinstance(matrix, dict):
        raise ValueError("rewardMatrix must be an object keyed by category")

    normalised: dict[str, dict[str, int]] = {}
    for category in sorted(CATEGORIES):
        tiers = matrix.get(category)
        if not isinstance(tiers, dict):
            raise ValueError(f"rewardMatrix.{category} must be an object keyed by severity")

        expected = SEVERITIES_BY_CATEGORY[category]
        unknown = set(tiers) - expected
        if unknown:
            raise ValueError(
                f"rewardMatrix.{category} has unknown severities: {', '.join(sorted(unknown))}"
            )
        missing = expected - set(tiers)
        if missing:
            raise ValueError(
                f"rewardMatrix.{category} is missing severities: {', '.join(sorted(missing))}"
            )

        row: dict[str, int] = {}
        for severity, amount in tiers.items():
            # `bool` is an `int` in Python, and `True` would sail through an `isinstance(int)` check to
            # become an award of one kobo.
            if isinstance(amount, bool) or not isinstance(amount, int):
                raise ValueError(
                    f"rewardMatrix.{category}.{severity} must be an integer number of kobo"
                )
            if amount < 0:
                raise ValueError(f"rewardMatrix.{category}.{severity} cannot be negative")
            row[severity] = amount
        normalised[category] = row

    unknown_categories = set(matrix) - CATEGORIES
    if unknown_categories:
        raise ValueError(
            f"rewardMatrix has unknown categories: {', '.join(sorted(unknown_categories))}"
        )
    return normalised


def amount_for(matrix: dict, category: str | None, severity: str | None) -> int:
    """What this season pays for this grading, in kobo. `0` for anything ungraded or unknown.

    `matrix` is the season's own `rewardMatrix`, not the module default — pass
    `submission.program.reward_matrix`, so a late triage pays the rates that were published when the
    finding was reported.
    """
    if not category or not severity:
        return 0
    tiers = matrix.get(category)
    if not isinstance(tiers, dict):
        return 0
    amount = tiers.get(severity)
    if isinstance(amount, bool) or not isinstance(amount, int) or amount < 0:
        return 0
    return amount
