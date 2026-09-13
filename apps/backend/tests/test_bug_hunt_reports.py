"""Season reporting, against a real database.

The report exists to answer one question honestly: was the season worth running. So the tests here are
mostly about the ways a reporting number lies.

- **`None` is not `0`.** A cost per accepted finding of ₦0 on the morning a season opens is not a
  triumph, and an acceptance rate of 0% reads as "we reject everything" rather than "nothing decided
  yet". Every rate has an empty-denominator case, and every one of them is asserted.
- **Cost per accepted finding comes from the ledger.** Dividing the budget by the accepted count would
  report a cost the programme never paid.
- **Retention counts people who filed, not people who were carried forward.** Carry-forward seeds
  everybody, so counting the seeding would report 100% retention in a season nobody came back to. This
  is the single easiest number in the whole programme to fake by accident.
- **Spend is programme-wide and labelled as such.** A balance is permanent, so a tester spending Season 1
  earnings during Season 2 belongs to neither season, and the field names say `lifetime`.

Database-dependent, so opt in:

    RUN_DB_TESTS=1 DATABASE_URL=postgresql://localhost/scratch pytest tests/test_bug_hunt_reports.py

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, select

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
    program_service,
    report_service,
    submission_service,
    triage_service,
)
from src.domains.identity.db_models import User
from src.shared.database import get_session_factory

pytestmark = pytest.mark.usefixtures("db")


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
            stale = select(User.id).where(User.email.like("bughunt-report-%"))
            await session.execute(delete(AuditLog).where(AuditLog.admin_user_id.in_(stale)))
            await session.execute(delete(User).where(User.email.like("bughunt-report-%")))
            await session.commit()

    await wipe()
    yield
    await wipe()


async def make_user(staff: bool = False) -> User:
    user = User(
        email=f"bughunt-report-{uuid.uuid4().hex[:12]}@example.com",
        name="Ada",
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


async def make_season(*, number: int = 1, open_it: bool = True, **overrides) -> BugHuntProgram:
    starts = datetime.now(UTC)
    program = await program_service.create(
        name=f"Season {number}",
        slug=f"report-s{number}-{uuid.uuid4().hex[:6]}",
        starts_at=starts,
        ends_at=starts + timedelta(days=14),
        season_number=number,
        **overrides,
    )
    if open_it:
        program = await program_service.open_season(program.id)
    return program


def finding(**overrides) -> dict:
    fields = {
        "platform": "android",
        "title": "Crash when opening Learn on a cold start",
        "stepsToReproduce": "Force-stop the app, reopen it, tap Learn.",
        "expectedResult": "The Learn tab renders.",
        "actualResult": "The app closes.",
    }
    fields.update(overrides)
    return fields


async def approved_tester(program: BugHuntProgram, staff: User) -> User:
    user = await make_user()
    participant, _ = await submission_service.create_application(
        user=user, fields=finding(), accepted_rules_version=program.rules_version
    )
    await triage_service.decide_application(
        participant_id=participant.id, decision="approve", reason=None, staff_user_id=staff.id
    )
    return user


async def grade(submission_id: str, staff: User, *, status: str, severity: str | None = None):
    return await triage_service.triage(
        submission_id=submission_id,
        status=status,
        category="bug" if status == "accepted" else None,
        type_="crash" if status == "accepted" else None,
        severity=severity,
        duplicate_of_id=None,
        public_response=None,
        admin_notes=None,
        staff_user_id=staff.id,
    )


class TestEmptySeason:
    async def test_every_rate_is_none_rather_than_zero(self):
        """The morning a season opens, nothing has been measured.

        Reporting 0% acceptance and ₦0 per finding would be a dashboard describing a catastrophe, and
        somebody would act on it.
        """
        program = await make_season()
        report = await report_service.season_report(program.id)

        assert report["submissions"] == 0
        assert report["acceptanceRate"] is None
        assert report["approvalRate"] is None
        assert report["costPerAcceptedFindingKobo"] is None
        assert report["knownIssueRate"] is None
        assert report["submissionsPerApprovedParticipant"] is None
        assert report["lifetimePassSharePercent"] is None


class TestSeasonEconomics:
    async def test_cost_per_accepted_finding_comes_from_the_ledger(self):
        """Two accepted findings at ₦2,000 and ₦500 average ₦1,250.

        From what was actually credited, not from the budget: dividing a ₦300,000 budget by two accepted
        findings would report ₦150,000 each and make the programme look catastrophically expensive.
        """
        program = await make_season()
        staff = await make_user(staff=True)
        user = await approved_tester(program, staff)

        critical = await submission_service.create_submission(user=user, fields=finding())
        await grade(critical.id, staff, status="accepted", severity="critical")
        low = await submission_service.create_submission(
            user=user, fields=finding(title="Copy typo on the login screen")
        )
        await grade(low.id, staff, status="accepted", severity="low")

        report = await report_service.season_report(program.id)

        # The application's own finding is untriaged, so it is not in the denominator.
        assert report["submissionsByStatus"]["accepted"] == 2
        assert report["creditedKobo"] == 250_000
        assert report["costPerAcceptedFindingKobo"] == 125_000

    async def test_the_known_issue_rate_is_reported_separately(self):
        """It is a metric about our backlog, not about the testers.

        Folded into a general rejection rate it would read as testers filing badly, when what it
        actually says is that we are collecting reports faster than we ship fixes.

        Set up across two seasons because that is the only way a `known_issue` exists: the service
        resolves it against an accepted finding from an **earlier** season, and refuses when there is
        nothing to point at. That refusal is deliberate — without the lookup the distinction between
        `known_issue` and `duplicate` would be a triager's memory test under time pressure.
        """
        staff = await make_user(staff=True)
        season_one = await make_season(number=1)
        first = await approved_tester(season_one, staff)
        original = await submission_service.create_submission(
            user=first, fields=finding(title="Timer drifts on slow networks")
        )
        await grade(original.id, staff, status="accepted", severity="high")
        await program_service.close_season(season_one.id)

        season_two = await make_season(number=2)
        await program_service.carry_forward(
            into_program_id=season_two.id, from_program_id=season_one.id
        )
        await submission_service.accept_terms(user=first, rules_version=season_two.rules_version)

        accepted = await submission_service.create_submission(
            user=first, fields=finding(title="New crash on the review screen")
        )
        await grade(accepted.id, staff, status="accepted", severity="high")
        repeat = await submission_service.create_submission(
            user=first, fields=finding(title="Timer drifts on slow networks")
        )
        await triage_service.triage(
            submission_id=repeat.id,
            status="known_issue",
            category=None,
            type_=None,
            severity=None,
            duplicate_of_id=original.id,
            public_response=None,
            admin_notes=None,
            staff_user_id=staff.id,
        )

        report = await report_service.season_report(season_two.id)

        assert report["knownIssueRate"] == 0.5
        assert report["acceptanceRate"] == 0.5

    async def test_acceptance_rate_is_broken_down_by_platform(self):
        """The mobile numbers are the ones worth watching.

        They get the least outside attention, so a low acceptance rate there means the reports are thin
        while a high one means we have been shipping mobile bugs nobody was catching.
        """
        program = await make_season()
        staff = await make_user(staff=True)
        user = await approved_tester(program, staff)

        ios = await submission_service.create_submission(
            user=user, fields=finding(platform="ios", title="Crash on iOS cold start")
        )
        await grade(ios.id, staff, status="accepted", severity="high")
        web = await submission_service.create_submission(
            user=user, fields=finding(platform="web", title="Misaligned label", route="/study")
        )
        await grade(web.id, staff, status="rejected")

        report = await report_service.season_report(program.id)

        assert report["acceptanceRateByPlatform"]["ios"] == 1.0
        assert report["acceptanceRateByPlatform"]["web"] == 0.0
        # The untriaged application finding sits on android, so android has no decided submissions.
        assert report["acceptanceRateByPlatform"]["android"] is None

    async def test_a_platform_with_nothing_decided_is_none_not_zero(self):
        """An undecided platform has no rate. Zero would say we reject everything on it."""
        program = await make_season()
        staff = await make_user(staff=True)
        user = await approved_tester(program, staff)
        await submission_service.create_submission(
            user=user, fields=finding(platform="web", route="/study")
        )

        report = await report_service.season_report(program.id)
        assert report["acceptanceRateByPlatform"]["web"] is None

    async def test_approval_rate_ignores_applications_still_in_the_queue(self):
        """Otherwise the approval rate falls every time the queue grows.

        That would make a busy intake day look like a quality collapse, and the number would be reacted
        to rather than understood.
        """
        program = await make_season()
        staff = await make_user(staff=True)
        await approved_tester(program, staff)

        # A second applicant, left pending.
        pending = await make_user()
        await submission_service.create_application(
            user=pending, fields=finding(), accepted_rules_version=program.rules_version
        )

        report = await report_service.season_report(program.id)

        assert report["applications"] == 2
        assert report["approvedParticipants"] == 1
        # One decided, one approved.
        assert report["approvalRate"] == 1.0

    async def test_submissions_per_approved_participant(self):
        program = await make_season()
        staff = await make_user(staff=True)
        user = await approved_tester(program, staff)
        await submission_service.create_submission(user=user, fields=finding())

        report = await report_service.season_report(program.id)
        # The application's finding plus one more, over one approved participant.
        assert report["submissionsPerApprovedParticipant"] == 2.0


class TestRetention:
    async def test_no_second_season_reports_nothing(self):
        """Empty is the honest answer. A row of zeros would imply a measured failure."""
        await make_season(number=1)
        assert await report_service.retention_report() == []

    async def test_carry_forward_alone_is_not_retention(self):
        """**The number this whole file exists to protect.**

        Carry-forward seeds every eligible participant into the new season as `approved`. Counting that
        as a return would report 100% retention for a season nobody came back to, and it would report it
        confidently, every season, forever. Retention counts people who *filed something*.
        """
        staff = await make_user(staff=True)
        season_one = await make_season(number=1)
        returner = await approved_tester(season_one, staff)
        await approved_tester(season_one, staff)  # never comes back
        await program_service.close_season(season_one.id)

        season_two = await make_season(number=2)
        added = await program_service.carry_forward(
            into_program_id=season_two.id, from_program_id=season_one.id
        )
        assert added == 2  # both seeded

        # Nobody has filed yet, so nobody has returned.
        rows = await report_service.retention_report()
        assert rows[0]["priorApproved"] == 2
        assert rows[0]["returned"] == 0
        assert rows[0]["retentionRate"] == 0.0

        # One of them files. Now one has returned.
        await submission_service.accept_terms(user=returner, rules_version=season_two.rules_version)
        await submission_service.create_submission(
            user=returner, fields=finding(title="Regression in the new season")
        )

        rows = await report_service.retention_report()
        assert rows[0]["returned"] == 1
        assert rows[0]["retentionRate"] == 0.5

    async def test_returner_and_newcomer_acceptance_are_compared(self):
        """Whether proven reporters actually file better findings.

        If they do not, the carry-forward machinery is buying convenience rather than quality, and that
        is worth knowing before building more of it.
        """
        staff = await make_user(staff=True)
        season_one = await make_season(number=1)
        returner = await approved_tester(season_one, staff)
        await program_service.close_season(season_one.id)

        season_two = await make_season(number=2)
        await program_service.carry_forward(
            into_program_id=season_two.id, from_program_id=season_one.id
        )
        await submission_service.accept_terms(user=returner, rules_version=season_two.rules_version)
        good = await submission_service.create_submission(
            user=returner, fields=finding(title="Entitlement wrong after upgrade")
        )
        await grade(good.id, staff, status="accepted", severity="critical")

        newcomer = await approved_tester(season_two, staff)
        thin = await submission_service.create_submission(
            user=newcomer, fields=finding(title="Button colour looks off")
        )
        await grade(thin.id, staff, status="rejected")

        rows = await report_service.retention_report()
        row = rows[0]
        assert row["returnerAcceptanceRate"] == 1.0
        # The newcomer's application finding is untriaged; their filed one was rejected.
        assert row["newcomerAcceptanceRate"] == 0.0

    async def test_a_cohort_with_no_filings_reports_none_not_zero(self):
        """An absent cohort has no acceptance rate.

        Zero would say they filed and were all rejected, which is a claim about people who did nothing.
        """
        staff = await make_user(staff=True)
        season_one = await make_season(number=1)
        await approved_tester(season_one, staff)
        await program_service.close_season(season_one.id)
        await make_season(number=2)

        rows = await report_service.retention_report()
        assert rows[0]["returnerAcceptanceRate"] is None
        assert rows[0]["newcomerAcceptanceRate"] is None
