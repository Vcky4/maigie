"""What a season cost and what it bought.

`triage_service.stats` is the dashboard a triage owner reads each morning: queue depths, and whether the
48-hour promise is being kept. This module answers a different and slower question, the one that decides
whether there is a Season 3.

**Cost per accepted finding is the number that matters**, and it is the one a submission count cannot
give you. A season with 200 accepted findings at ₦1,900 each has mostly bought duplicates of things a
week of internal testing would have found; a season with 40 at ₦2,000 has bought forty real bugs. The
whole point of recording it per season is that it should *rise* over time as the obvious bugs run out,
and a flat figure across two seasons means we are paying the same rate for progressively less value.

**The `known_issue` rate is a metric about us, not about testers.** It is the share of findings where we
already knew and had not fixed it. A rising rate does not mean the testers got worse; it means we are
collecting reports faster than we are shipping fixes, and it is the one number here that should be read
as a complaint about engineering.

Every figure is scoped to one season and computed from the ledger and the submission rows rather than
from a cached counter, because a season report is read a handful of times and being right matters more
than being fast.

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import case, func, select

from src.shared.database import get_session_factory

from ..db_models import (
    BugHuntLedgerEntry,
    BugHuntParticipant,
    BugHuntProgram,
    BugHuntSubmission,
)
from . import program_service

logger = logging.getLogger(__name__)


async def season_report(program_id: str) -> dict[str, Any]:
    """Everything §13 asks for about one season, in one read.

    Rates are `None` rather than `0` wherever the denominator is empty. A cost per accepted finding of
    ₦0 on the morning a season opens is not a triumph, and an acceptance rate of 0% reads as "we reject
    everything" rather than "nothing has been decided yet".
    """
    program = await program_service.get(program_id)

    factory = get_session_factory()
    async with factory() as session:
        # --- Findings, by outcome and by platform ---------------------------------
        #
        # One pass, grouped by both, so acceptance rate by platform comes out of the same scan rather
        # than one query per platform. The mobile figures are the ones worth watching: they get the
        # least outside attention, so a low acceptance rate there means the reports are thin, while a
        # high one means we have been shipping mobile bugs nobody was catching.
        rows = (
            await session.execute(
                select(
                    BugHuntSubmission.platform,
                    BugHuntSubmission.status,
                    func.count(),
                )
                .where(BugHuntSubmission.program_id == program_id)
                .group_by(BugHuntSubmission.platform, BugHuntSubmission.status)
            )
        ).all()

        # --- Approved cohort and how much they filed ------------------------------
        approved_count = (
            await session.execute(
                select(func.count())
                .select_from(BugHuntParticipant)
                .where(
                    BugHuntParticipant.program_id == program_id,
                    BugHuntParticipant.status == "approved",
                )
            )
        ).scalar() or 0
        applications = (
            await session.execute(
                select(func.count())
                .select_from(BugHuntParticipant)
                .where(BugHuntParticipant.program_id == program_id)
            )
        ).scalar() or 0
        decided_participants = (
            await session.execute(
                select(func.count())
                .select_from(BugHuntParticipant)
                .where(
                    BugHuntParticipant.program_id == program_id,
                    BugHuntParticipant.status.in_(("approved", "rejected")),
                )
            )
        ).scalar() or 0
        total_submissions = (
            await session.execute(
                select(func.count())
                .select_from(BugHuntSubmission)
                .where(BugHuntSubmission.program_id == program_id)
            )
        ).scalar() or 0

        # --- Where the money went ------------------------------------------------
        #
        # From the ledger rather than from `awardedKobo`, and the difference is deliberate: the
        # programme row counts what the season committed, while these rows are what individual testers
        # actually hold and spend. They should agree on credits, and the spend split only exists here.
        credited = (
            await session.execute(
                select(func.coalesce(func.sum(BugHuntLedgerEntry.amount_kobo), 0)).where(
                    BugHuntLedgerEntry.program_id == program_id,
                    BugHuntLedgerEntry.amount_kobo > 0,
                )
            )
        ).scalar() or 0

        # Redemptions and withdrawals belong to no season: a balance is permanent and somebody can spend
        # Season 1 earnings during Season 2. So the split is programme-wide and labelled as such, rather
        # than attributed to a season it cannot honestly be attributed to.
        spend = (
            await session.execute(
                select(
                    func.coalesce(
                        func.sum(
                            case(
                                (
                                    BugHuntLedgerEntry.kind == "pass_redemption",
                                    -BugHuntLedgerEntry.amount_kobo,
                                ),
                                else_=0,
                            )
                        ),
                        0,
                    ),
                    func.coalesce(
                        func.sum(
                            case(
                                (
                                    BugHuntLedgerEntry.kind == "withdrawal",
                                    -BugHuntLedgerEntry.amount_kobo,
                                ),
                                else_=0,
                            )
                        ),
                        0,
                    ),
                )
            )
        ).one()

    by_status: dict[str, int] = {}
    by_platform: dict[str, dict[str, int]] = {}
    for platform, status, count in rows:
        by_status[status] = by_status.get(status, 0) + int(count)
        bucket = by_platform.setdefault(str(platform), {})
        bucket[str(status)] = int(count)

    accepted = by_status.get("accepted", 0)
    decided = sum(
        by_status.get(status, 0) for status in ("accepted", "rejected", "duplicate", "known_issue")
    )
    known_issues = by_status.get("known_issue", 0)

    pass_spend_kobo, cash_spend_kobo = int(spend[0]), int(spend[1])
    total_spend = pass_spend_kobo + cash_spend_kobo

    return {
        "season": program,
        "applications": int(applications),
        "approvedParticipants": int(approved_count),
        # Of those actually decided. Counting pending applications in the denominator would report a
        # falling approval rate every time the queue grew.
        "approvalRate": (approved_count / decided_participants) if decided_participants else None,
        "submissions": int(total_submissions),
        "submissionsPerApprovedParticipant": (
            round(total_submissions / approved_count, 2) if approved_count else None
        ),
        "submissionsByStatus": by_status,
        "acceptanceRate": (accepted / decided) if decided else None,
        "acceptanceRateByPlatform": {
            platform: _acceptance_rate(counts) for platform, counts in sorted(by_platform.items())
        },
        "submissionsByPlatform": {
            platform: sum(counts.values()) for platform, counts in sorted(by_platform.items())
        },
        # The number that decides whether there is a Season 3, and it should rise season on season as
        # the cheap bugs run out. A flat figure means we are paying the same for less.
        "costPerAcceptedFindingKobo": round(credited / accepted) if accepted else None,
        "knownIssueRate": (known_issues / decided) if decided else None,
        "budgetKobo": program.budget_kobo,
        "awardedKobo": program.awarded_kobo,
        "creditedKobo": int(credited),
        "remainingBudgetKobo": program_service.remaining_budget_kobo(program),
        # Programme-wide, not per season, and the key names say so. A tester spending a Season 1 balance
        # during Season 2 cannot be attributed to either one.
        "lifetimePassSpendKobo": pass_spend_kobo,
        "lifetimeCashSpendKobo": cash_spend_kobo,
        "lifetimePassSharePercent": (
            round(pass_spend_kobo / total_spend * 100, 1) if total_spend else None
        ),
    }


def _acceptance_rate(counts: dict[str, int]) -> float | None:
    decided = sum(
        counts.get(status, 0) for status in ("accepted", "rejected", "duplicate", "known_issue")
    )
    return (counts.get("accepted", 0) / decided) if decided else None


async def retention_report() -> list[dict[str, Any]]:
    """Season-over-season retention: what recurring seasons are supposed to buy.

    For each season after the first, how many of the previous season's approved participants came back
    and actually filed something. **Filed, not merely carried forward** — carry-forward seeds everybody,
    so counting the seeding would report 100% retention while nobody returned.

    Returns one row per season pair, oldest first. Empty until a second season exists, which is the
    honest answer rather than a zero.
    """
    seasons = sorted(
        await program_service.list_all(include_draft=False), key=lambda p: p.season_number
    )
    if len(seasons) < 2:
        return []

    factory = get_session_factory()
    out: list[dict[str, Any]] = []
    async with factory() as session:
        for previous, current in zip(seasons, seasons[1:], strict=False):
            prior_approved = set(
                (
                    await session.execute(
                        select(BugHuntParticipant.user_id).where(
                            BugHuntParticipant.program_id == previous.id,
                            BugHuntParticipant.status == "approved",
                        )
                    )
                )
                .scalars()
                .all()
            )
            if not prior_approved:
                out.append(
                    {
                        "fromSeason": previous.season_number,
                        "toSeason": current.season_number,
                        "priorApproved": 0,
                        "returned": 0,
                        "retentionRate": None,
                        "returnerAcceptanceRate": None,
                        "newcomerAcceptanceRate": None,
                    }
                )
                continue

            # The `if row` is not belt-and-braces for the SQL filter: `BugHuntSubmission.userId` is
            # nullable behind ON DELETE SET NULL, and mypy is right that the column type admits None.
            # A deleted account's findings still exist and belong to no cohort.
            filed_in_current = {
                row
                for row in (
                    await session.execute(
                        select(BugHuntSubmission.user_id).where(
                            BugHuntSubmission.program_id == current.id,
                            BugHuntSubmission.user_id.is_not(None),
                        )
                    )
                )
                .scalars()
                .all()
                if row
            }
            returned = prior_approved & filed_in_current

            # Whether proven reporters produce better findings than newcomers. If they do not, the
            # carry-forward machinery is buying convenience rather than quality, which is worth knowing
            # before building more of it.
            returner_rate = await _acceptance_for(session, program_id=current.id, user_ids=returned)
            newcomer_rate = await _acceptance_for(
                session, program_id=current.id, user_ids=filed_in_current - prior_approved
            )

            out.append(
                {
                    "fromSeason": previous.season_number,
                    "toSeason": current.season_number,
                    "priorApproved": len(prior_approved),
                    "returned": len(returned),
                    "retentionRate": len(returned) / len(prior_approved),
                    "returnerAcceptanceRate": returner_rate,
                    "newcomerAcceptanceRate": newcomer_rate,
                }
            )
    return out


async def _acceptance_for(session: Any, *, program_id: str, user_ids: set[str]) -> float | None:
    """Acceptance rate for one cohort within one season, or `None` for an empty cohort."""
    if not user_ids:
        return None
    rows = (
        await session.execute(
            select(BugHuntSubmission.status, func.count())
            .where(
                BugHuntSubmission.program_id == program_id,
                BugHuntSubmission.user_id.in_(user_ids),
            )
            .group_by(BugHuntSubmission.status)
        )
    ).all()
    counts = {str(status): int(count) for status, count in rows}
    return _acceptance_rate(counts)


__all__ = ["season_report", "retention_report"]
