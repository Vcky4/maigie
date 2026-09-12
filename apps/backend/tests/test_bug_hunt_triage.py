"""Triage, against a real database.

Grading is where money is decided, so the tests that matter here are the ones that prove **no amount was
typed**: a triager sets category and severity, the season's own matrix turns that into kobo, and a grading
the season does not price is refused rather than paid as zero.

Three claims carry most of the weight:

- **A late triage pays the rates the finding was reported under.** The matrix lives on the programme row
  precisely so that a slow queue is never an unfair one, and this is where that is demonstrated rather than
  asserted in a docstring.
- **`known_issue` is not `duplicate`.** Both pay nothing, and only one of them puts the blame for our
  backlog on the reporter.
- **Accepting an unpriced grading is refused.** "Accepted, ₦0" is the single outcome that is both wrong and
  hard to notice, because every status on the tester's screen looks like success.

Database-dependent, so opt in:

    RUN_DB_TESTS=1 DATABASE_URL=postgresql://localhost/scratch pytest tests/test_bug_hunt_triage.py
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, select, update

from src.domains.bug_hunt.db_models import (
    BugHuntAttachment,
    BugHuntLedgerEntry,
    BugHuntParticipant,
    BugHuntProgram,
    BugHuntSubmission,
    BugHuntWallet,
)
from src.domains.bug_hunt.services import program_service, submission_service, triage_service
from src.domains.identity.db_models import User
from src.shared.database import get_session_factory
from src.shared.exceptions import ConflictError, NotFoundError, ValidationError

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
                BugHuntAttachment,
                BugHuntSubmission,
                BugHuntWallet,
                BugHuntParticipant,
                BugHuntProgram,
            ):
                await session.execute(delete(model))
            # `AuditLog.adminUserId` is NOT NULL behind an ON DELETE SET NULL foreign key, so deleting a
            # staff user makes Postgres attempt a null it forbids. Because this fixture is autouse, the
            # failure would land on every test in the file rather than on the one that wrote the row.
            stale = select(User.id).where(User.email.like("bughunt-triage-%"))
            await session.execute(delete(AuditLog).where(AuditLog.admin_user_id.in_(stale)))
            await session.execute(delete(User).where(User.email.like("bughunt-triage-%")))
            await session.commit()

    await wipe()
    yield
    await wipe()


async def make_user(country: str | None = "NG", staff: bool = False) -> User:
    user = User(
        email=f"bughunt-triage-{uuid.uuid4().hex[:12]}@example.com",
        country=country,
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
        slug=f"triage-s{number}-{uuid.uuid4().hex[:6]}",
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
        "stepsToReproduce": "Force-stop the app, reopen it, tap Learn within two seconds.",
        "expectedResult": "The Learn tab renders.",
        "actualResult": "The app closes with no message.",
    }
    fields.update(overrides)
    return fields


async def applicant(program: BugHuntProgram) -> tuple[User, BugHuntParticipant, BugHuntSubmission]:
    user = await make_user()
    participant, submission = await submission_service.create_application(
        user=user, fields=finding(), accepted_rules_version=program.rules_version
    )
    return user, participant, submission


async def approved(program: BugHuntProgram, staff: User) -> tuple[User, BugHuntParticipant]:
    user, participant, _ = await applicant(program)
    decided = await triage_service.decide_application(
        participant_id=participant.id, decision="approve", reason=None, staff_user_id=staff.id
    )
    return user, decided


# ---------------------------------------------------------------------------
# Deciding applications
# ---------------------------------------------------------------------------


class TestDecidingApplications:
    async def test_approval_records_who_and_when(self):
        program = await make_season()
        staff = await make_user(staff=True)
        _, participant, _ = await applicant(program)
        decided = await triage_service.decide_application(
            participant_id=participant.id, decision="approve", reason=None, staff_user_id=staff.id
        )
        assert decided.status == "approved"
        assert decided.decided_by_user_id == staff.id
        assert decided.decided_at is not None

    async def test_a_rejection_without_a_reason_is_refused(self):
        """The applicant reads this verbatim. A programme that turns people down without saying why stops
        attracting applicants after one season."""
        program = await make_season()
        staff = await make_user(staff=True)
        _, participant, _ = await applicant(program)
        with pytest.raises(ValidationError, match="owed a reason"):
            await triage_service.decide_application(
                participant_id=participant.id,
                decision="reject",
                reason="   ",
                staff_user_id=staff.id,
            )

    async def test_a_rejection_keeps_its_reason(self):
        program = await make_season()
        staff = await make_user(staff=True)
        _, participant, _ = await applicant(program)
        decided = await triage_service.decide_application(
            participant_id=participant.id,
            decision="reject",
            reason="Not reproducible on 1.4.2 — please include the build number next time.",
            staff_user_id=staff.id,
        )
        assert decided.status == "rejected"
        assert "build number" in (decided.rejection_reason or "")

    async def test_an_unknown_decision_is_refused(self):
        program = await make_season()
        staff = await make_user(staff=True)
        _, participant, _ = await applicant(program)
        with pytest.raises(ValidationError):
            await triage_service.decide_application(
                participant_id=participant.id,
                decision="maybe",
                reason=None,
                staff_user_id=staff.id,
            )

    async def test_deciding_does_not_triage_the_application_finding(self):
        """Two judgements, deliberately separate. A report can be good enough to pay for while the applicant
        is wrong for the programme, and the reverse."""
        program = await make_season()
        staff = await make_user(staff=True)
        _, participant, submission = await applicant(program)
        await triage_service.decide_application(
            participant_id=participant.id, decision="approve", reason=None, staff_user_id=staff.id
        )
        detail = await triage_service.submission_detail(submission.id)
        assert detail["submission"].status == "submitted"
        assert detail["submission"].severity is None

    async def test_suspending_does_not_touch_the_balance(self):
        """Whatever they earned before the suspension, they earned. Confiscation would turn a moderation
        decision into a fine, and the ledger is append-only so that no single act can reverse a payment."""
        program = await make_season()
        staff = await make_user(staff=True)
        user, participant = await approved(program, staff)

        factory = get_session_factory()
        async with factory() as session:
            wallet = BugHuntWallet(user_id=user.id)
            session.add(wallet)
            await session.flush()
            submission = (
                await session.execute(
                    select(BugHuntSubmission).where(
                        BugHuntSubmission.participant_id == participant.id
                    )
                )
            ).scalar_one()
            session.add(
                BugHuntLedgerEntry(
                    wallet_id=wallet.id,
                    user_id=user.id,
                    program_id=program.id,
                    participant_id=participant.id,
                    kind="award",
                    amount_kobo=150_000,
                    submission_id=submission.id,
                )
            )
            await session.commit()

        await triage_service.suspend_participant(
            participant_id=participant.id, reason="Duplicate accounts.", staff_user_id=staff.id
        )
        detail = await triage_service.participant_detail(participant.id)
        assert detail["participant"].status == "suspended"
        assert detail["earnedLifetimeKobo"] == 150_000

    async def test_suspension_requires_a_reason(self):
        program = await make_season()
        staff = await make_user(staff=True)
        _, participant = await approved(program, staff)
        with pytest.raises(ValidationError):
            await triage_service.suspend_participant(
                participant_id=participant.id, reason="", staff_user_id=staff.id
            )

    async def test_deciding_an_unknown_participant_is_a_404(self):
        with pytest.raises(NotFoundError):
            await triage_service.decide_application(
                participant_id="nope", decision="approve", reason=None, staff_user_id="x"
            )


# ---------------------------------------------------------------------------
# Queues
# ---------------------------------------------------------------------------


class TestQueues:
    async def test_the_application_queue_is_oldest_first(self):
        """A review queue is worked front to back. Newest-first ordering is how the applicant who has
        waited longest keeps getting pushed down the page, which is the opposite of a turnaround promise."""
        program = await make_season()
        first, _, _ = await applicant(program)
        second, _, _ = await applicant(program)
        rows, total = await triage_service.list_participants(program_id=program.id)
        assert total == 2
        assert [email for _, email, _ in rows] == [first.email, second.email]

    async def test_the_submission_queue_is_oldest_first(self):
        program = await make_season()
        staff = await make_user(staff=True)
        user, _ = await approved(program, staff)
        second = await submission_service.create_submission(
            user=user, fields=finding(title="Second finding")
        )
        rows, _ = await triage_service.list_submissions(program_id=program.id)
        assert [row[0].title for row in rows][-1] == second.title

    async def test_the_queue_carries_the_reporter_identity(self):
        """A queue of opaque ids is not a queue anybody can work, and lazy-loading a `User` per row would be
        a query per applicant."""
        program = await make_season()
        user, _, _ = await applicant(program)
        rows, _ = await triage_service.list_submissions(program_id=program.id)
        assert rows[0][1] == user.email

    async def test_filters(self):
        program = await make_season()
        staff = await make_user(staff=True)
        user, participant = await approved(program, staff)
        await submission_service.create_submission(user=user, fields=finding(platform="web"))
        other_user, _, _ = await applicant(program)

        _, web = await triage_service.list_submissions(program_id=program.id, platform="web")
        assert web == 1
        _, applications = await triage_service.list_submissions(
            program_id=program.id, is_application=True
        )
        assert applications == 2
        _, mine = await triage_service.list_submissions(participant_id=participant.id)
        assert mine == 2
        _, searched = await triage_service.list_submissions(search="cold start")
        assert searched >= 1
        _, missing = await triage_service.list_submissions(status="accepted")
        assert missing == 0

    async def test_participant_search_matches_email(self):
        program = await make_season()
        user, _, _ = await applicant(program)
        _, total = await triage_service.list_participants(search=user.email.split("@")[0])
        assert total == 1

    async def test_participant_detail_spans_seasons(self):
        """A returning applicant's previous record is the most useful thing on the screen, and it is not
        visible from the season they are applying to."""
        first = await make_season(number=1)
        staff = await make_user(staff=True)
        user, _ = await approved(first, staff)
        await program_service.close_season(first.id)
        second = await make_season(number=2)
        await program_service.carry_forward(into_program_id=second.id, from_program_id=first.id)

        carried = await program_service.participation(user_id=user.id, program_id=second.id)
        assert carried is not None
        detail = await triage_service.participant_detail(carried.id)
        assert len(detail["history"]) == 2
        assert detail["country"] == "NG"


# ---------------------------------------------------------------------------
# Grading
# ---------------------------------------------------------------------------


class TestGrading:
    async def test_accepting_a_bug_computes_the_seasons_amount(self):
        """No amount crossed the boundary: the triager set `critical`, the matrix said ₦2,000."""
        program = await make_season()
        staff = await make_user(staff=True)
        _, _, submission = await applicant(program)
        result = await triage_service.triage(
            submission_id=submission.id,
            status="accepted",
            category="bug",
            type_="crash",
            severity="critical",
            duplicate_of_id=None,
            public_response="Confirmed and fixed in 1.4.3. Thank you.",
            admin_notes="Reproduced on a Tecno Spark 8.",
            staff_user_id=staff.id,
        )
        assert result["awardKobo"] == 200_000
        assert result["awardPending"] is True
        assert result["submission"].status == "accepted"
        assert result["submission"].triaged_by_user_id == staff.id

    @pytest.mark.parametrize(
        ("severity", "expected"),
        [("critical", 200_000), ("high", 150_000), ("medium", 100_000), ("low", 50_000)],
    )
    async def test_the_whole_bug_ladder(self, severity, expected):
        program = await make_season()
        staff = await make_user(staff=True)
        _, _, submission = await applicant(program)
        result = await triage_service.triage(
            submission_id=submission.id,
            status="accepted",
            category="bug",
            type_="functional",
            severity=severity,
            duplicate_of_id=None,
            public_response=None,
            admin_notes=None,
            staff_user_id=staff.id,
        )
        assert result["awardKobo"] == expected

    @pytest.mark.parametrize(("tier", "expected"), [("high_value", 150_000), ("standard", 50_000)])
    async def test_the_feedback_tiers(self, tier, expected):
        program = await make_season()
        staff = await make_user(staff=True)
        _, _, submission = await applicant(program)
        result = await triage_service.triage(
            submission_id=submission.id,
            status="accepted",
            category="feedback",
            type_="usability",
            severity=tier,
            duplicate_of_id=None,
            public_response=None,
            admin_notes=None,
            staff_user_id=staff.id,
        )
        assert result["awardKobo"] == expected

    async def test_a_late_triage_pays_the_rates_the_finding_was_reported_under(self):
        """**The reason the matrix lives on the season row.**

        Season 1 priced a critical at ₦2,000. Season 2 pays ₦9,999. A Season 1 finding graded after Season 2
        opened is still worth ₦2,000, because that is what was published when it was reported — a slow queue
        must never be an unfair one.
        """
        first = await make_season(number=1)
        staff = await make_user(staff=True)
        _, _, submission = await applicant(first)
        await program_service.close_season(first.id)

        matrix = {
            "bug": {"critical": 999_900, "high": 1, "medium": 1, "low": 1},
            "feedback": {"high_value": 1, "standard": 1},
        }
        second = await make_season(number=2, reward_matrix=matrix)
        assert second.reward_matrix["bug"]["critical"] == 999_900

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
        assert result["awardKobo"] == 200_000
        assert result["seasonNumber"] == 1

    async def test_accepting_a_grading_the_season_does_not_price_is_refused(self):
        """ "Accepted, ₦0" is the one outcome that is both wrong and hard to notice — every status on the
        tester's screen looks like success. Refusing is the only honest option."""
        program = await make_season()
        staff = await make_user(staff=True)
        _, _, submission = await applicant(program)
        factory = get_session_factory()
        async with factory() as session:
            await session.execute(
                update(BugHuntProgram)
                .where(BugHuntProgram.id == program.id)
                .values(
                    reward_matrix={
                        "bug": {"critical": 0, "high": 0, "medium": 0, "low": 0},
                        "feedback": {"high_value": 0, "standard": 0},
                    }
                )
            )
            await session.commit()
        with pytest.raises(ConflictError) as e:
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
        assert e.value.code == "GRADING_NOT_PRICED"

    async def test_accepting_without_a_grade_is_refused(self):
        program = await make_season()
        staff = await make_user(staff=True)
        _, _, submission = await applicant(program)
        with pytest.raises(ValidationError, match="that is what decides the amount"):
            await triage_service.triage(
                submission_id=submission.id,
                status="accepted",
                category=None,
                type_=None,
                severity=None,
                duplicate_of_id=None,
                public_response=None,
                admin_notes=None,
                staff_user_id=staff.id,
            )

    async def test_a_severity_from_the_wrong_category_is_refused_readably(self):
        """The CHECK constraint catches this too, but a triager reading "violates constraint
        BugHuntSubmission_severity_pairing_check" learns nothing about which dropdown to change."""
        program = await make_season()
        staff = await make_user(staff=True)
        _, _, submission = await applicant(program)
        with pytest.raises(ValidationError, match="graded"):
            await triage_service.triage(
                submission_id=submission.id,
                status="accepted",
                category="bug",
                type_="crash",
                severity="standard",
                duplicate_of_id=None,
                public_response=None,
                admin_notes=None,
                staff_user_id=staff.id,
            )

    async def test_a_type_from_the_wrong_category_is_refused(self):
        program = await make_season()
        staff = await make_user(staff=True)
        _, _, submission = await applicant(program)
        with pytest.raises(ValidationError, match="typed"):
            await triage_service.triage(
                submission_id=submission.id,
                status="accepted",
                category="bug",
                type_="suggestion",
                severity="low",
                duplicate_of_id=None,
                public_response=None,
                admin_notes=None,
                staff_user_id=staff.id,
            )

    async def test_a_finding_cannot_be_moved_back_to_untouched(self):
        """`submitted` is the state a finding arrives in. Moving one back would erase the fact that somebody
        looked at it; reopening is `in_review`."""
        program = await make_season()
        staff = await make_user(staff=True)
        _, _, submission = await applicant(program)
        with pytest.raises(ValidationError, match="cannot be moved back"):
            await triage_service.triage(
                submission_id=submission.id,
                status="submitted",
                category=None,
                type_=None,
                severity=None,
                duplicate_of_id=None,
                public_response=None,
                admin_notes=None,
                staff_user_id=staff.id,
            )

    async def test_rejecting_needs_no_grade(self):
        program = await make_season()
        staff = await make_user(staff=True)
        _, _, submission = await applicant(program)
        result = await triage_service.triage(
            submission_id=submission.id,
            status="rejected",
            category=None,
            type_=None,
            severity=None,
            duplicate_of_id=None,
            public_response="We could not reproduce this on the current build.",
            admin_notes=None,
            staff_user_id=staff.id,
        )
        assert result["awardKobo"] == 0
        assert result["awardPending"] is False

    async def test_the_private_note_and_the_public_response_are_separate(self):
        program = await make_season()
        staff = await make_user(staff=True)
        user, _, submission = await applicant(program)
        await triage_service.triage(
            submission_id=submission.id,
            status="rejected",
            category=None,
            type_=None,
            severity=None,
            duplicate_of_id=None,
            public_response="Not reproducible on 1.4.2.",
            admin_notes="Reporter is filing a lot of low-value volume.",
            staff_user_id=staff.id,
        )
        # The reporter's own read carries one and not the other, and the response model has no field for it.
        theirs = await submission_service.get_own(user_id=user.id, submission_id=submission.id)
        assert theirs.public_response == "Not reproducible on 1.4.2."
        assert theirs.admin_notes is not None  # present on the ORM row…
        from src.domains.bug_hunt import models

        assert (
            "adminNotes" not in models.SubmissionView.model_fields
        )  # …and absent from their contract

    async def test_regrading_recomputes_the_amount(self):
        program = await make_season()
        staff = await make_user(staff=True)
        _, _, submission = await applicant(program)
        first = await triage_service.triage(
            submission_id=submission.id,
            status="accepted",
            category="bug",
            type_="ui",
            severity="low",
            duplicate_of_id=None,
            public_response=None,
            admin_notes=None,
            staff_user_id=staff.id,
        )
        second = await triage_service.triage(
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
        assert first["awardKobo"] == 50_000
        assert second["awardKobo"] == 200_000


class TestDuplicatesAndKnownIssues:
    async def test_a_duplicate_must_point_at_something(self):
        program = await make_season()
        staff = await make_user(staff=True)
        _, _, submission = await applicant(program)
        with pytest.raises(ValidationError, match="Point this at"):
            await triage_service.triage(
                submission_id=submission.id,
                status="duplicate",
                category=None,
                type_=None,
                severity=None,
                duplicate_of_id=None,
                public_response=None,
                admin_notes=None,
                staff_user_id=staff.id,
            )

    async def test_a_duplicate_cannot_point_at_itself(self):
        program = await make_season()
        staff = await make_user(staff=True)
        _, _, submission = await applicant(program)
        with pytest.raises(ValidationError, match="cannot duplicate itself"):
            await triage_service.triage(
                submission_id=submission.id,
                status="duplicate",
                category=None,
                type_=None,
                severity=None,
                duplicate_of_id=submission.id,
                public_response=None,
                admin_notes=None,
                staff_user_id=staff.id,
            )

    async def test_a_duplicate_cannot_point_at_a_duplicate(self):
        """A chain of duplicates gives the tester a "see this instead" link to a page saying the same thing,
        and gives us no canonical finding to fix."""
        program = await make_season()
        staff = await make_user(staff=True)
        _, _, canonical = await applicant(program)
        _, _, first_dup = await applicant(program)
        _, _, second_dup = await applicant(program)

        await triage_service.triage(
            submission_id=first_dup.id,
            status="duplicate",
            category=None,
            type_=None,
            severity=None,
            duplicate_of_id=canonical.id,
            public_response=None,
            admin_notes=None,
            staff_user_id=staff.id,
        )
        with pytest.raises(ValidationError, match="Point at the original"):
            await triage_service.triage(
                submission_id=second_dup.id,
                status="duplicate",
                category=None,
                type_=None,
                severity=None,
                duplicate_of_id=first_dup.id,
                public_response=None,
                admin_notes=None,
                staff_user_id=staff.id,
            )

    async def test_a_duplicate_pays_nothing(self):
        program = await make_season()
        staff = await make_user(staff=True)
        _, _, canonical = await applicant(program)
        _, _, dup = await applicant(program)
        result = await triage_service.triage(
            submission_id=dup.id,
            status="duplicate",
            category=None,
            type_=None,
            severity=None,
            duplicate_of_id=canonical.id,
            public_response=None,
            admin_notes=None,
            staff_user_id=staff.id,
        )
        assert result["awardKobo"] == 0

    async def test_known_issue_points_across_a_season_boundary(self):
        """The whole reason `known_issue` exists. A bug found in Season 1 and never fixed will be found again
        in Season 2, and marking that `duplicate` blames the reporter for our backlog."""
        first = await make_season(number=1)
        staff = await make_user(staff=True)
        _, _, original = await applicant(first)
        await triage_service.triage(
            submission_id=original.id,
            status="accepted",
            category="bug",
            type_="crash",
            severity="high",
            duplicate_of_id=None,
            public_response=None,
            admin_notes=None,
            staff_user_id=staff.id,
        )
        await program_service.close_season(first.id)

        second = await make_season(number=2)
        _, _, repeat = await applicant(second)
        result = await triage_service.triage(
            submission_id=repeat.id,
            status="known_issue",
            category=None,
            type_=None,
            severity=None,
            duplicate_of_id=original.id,
            public_response="We already know about this one and have not fixed it yet. That is on us.",
            admin_notes=None,
            staff_user_id=staff.id,
        )
        assert result["submission"].status == "known_issue"
        assert result["submission"].duplicate_of_id == original.id
        assert result["awardKobo"] == 0

    async def test_the_known_issues_list_is_what_makes_that_practical(self):
        """Without this lookup, deciding whether a Season 2 report repeats an unfixed Season 1 one is a
        memory test — and the reliable outcome of a memory test under queue pressure is `duplicate`."""
        first = await make_season(number=1)
        staff = await make_user(staff=True)
        _, _, accepted_finding = await applicant(first)
        _, _, rejected_finding = await applicant(first)
        await triage_service.triage(
            submission_id=accepted_finding.id,
            status="accepted",
            category="bug",
            type_="crash",
            severity="high",
            duplicate_of_id=None,
            public_response=None,
            admin_notes=None,
            staff_user_id=staff.id,
        )
        await triage_service.triage(
            submission_id=rejected_finding.id,
            status="rejected",
            category=None,
            type_=None,
            severity=None,
            duplicate_of_id=None,
            public_response="No.",
            admin_notes=None,
            staff_user_id=staff.id,
        )
        await program_service.close_season(first.id)
        second = await make_season(number=2)

        rows = await triage_service.known_issues(exclude_program_id=second.id)
        assert [row[0].id for row in rows] == [accepted_finding.id], "only accepted findings"
        assert rows[0][1] == 1, "carries the season it was reported in"

        assert await triage_service.known_issues(platform="ios") == []

    async def test_the_current_seasons_findings_are_excluded_when_asked(self):
        program = await make_season(number=1)
        staff = await make_user(staff=True)
        _, _, submission = await applicant(program)
        await triage_service.triage(
            submission_id=submission.id,
            status="accepted",
            category="bug",
            type_="ui",
            severity="low",
            duplicate_of_id=None,
            public_response=None,
            admin_notes=None,
            staff_user_id=staff.id,
        )
        assert await triage_service.known_issues(exclude_program_id=program.id) == []
        assert len(await triage_service.known_issues()) == 1


# ---------------------------------------------------------------------------
# Detail and stats
# ---------------------------------------------------------------------------


class TestSubmissionDetail:
    async def test_the_detail_carries_the_findings_own_season_matrix(self):
        first = await make_season(number=1)
        _, _, submission = await applicant(first)
        await program_service.close_season(first.id)
        await make_season(
            number=2,
            reward_matrix={
                "bug": {"critical": 1, "high": 1, "medium": 1, "low": 1},
                "feedback": {"high_value": 1, "standard": 1},
            },
        )
        detail = await triage_service.submission_detail(submission.id)
        assert (
            detail["rewardMatrix"]["bug"]["critical"] == 200_000
        ), "Season 1's table, not Season 2's"
        assert detail["seasonNumber"] == 1

    async def test_the_detail_answers_is_this_reporter_prolific(self):
        program = await make_season()
        staff = await make_user(staff=True)
        user, _ = await approved(program, staff)
        submission = await submission_service.create_submission(user=user, fields=finding())
        detail = await triage_service.submission_detail(submission.id)
        assert detail["reporterSubmissionCount"] == 2
        assert detail["email"] == user.email

    async def test_the_detail_names_the_canonical_finding(self):
        program = await make_season()
        staff = await make_user(staff=True)
        _, _, canonical = await applicant(program)
        _, _, dup = await applicant(program)
        await triage_service.triage(
            submission_id=dup.id,
            status="duplicate",
            category=None,
            type_=None,
            severity=None,
            duplicate_of_id=canonical.id,
            public_response=None,
            admin_notes=None,
            staff_user_id=staff.id,
        )
        detail = await triage_service.submission_detail(dup.id)
        assert detail["duplicateOfTitle"] == canonical.title

    async def test_an_unknown_submission_is_a_404(self):
        with pytest.raises(NotFoundError):
            await triage_service.submission_detail("nope")


class TestStats:
    async def test_with_no_season_the_dashboard_is_empty_rather_than_broken(self):
        data = await triage_service.stats()
        assert data["season"] is None
        assert data["queues"] == {"applications": 0, "submissions": 0}
        assert data["acceptanceRate"] is None

    async def test_queue_depths_and_budget(self):
        program = await make_season(budget_kobo=1_000_000)
        staff = await make_user(staff=True)
        await applicant(program)
        user, _ = await approved(program, staff)
        await submission_service.create_submission(user=user, fields=finding(platform="ios"))

        data = await triage_service.stats()
        assert data["queues"]["applications"] == 1, "one still pending"
        assert data["queues"]["submissions"] == 3
        assert data["submissionsByPlatform"] == {"android": 2, "ios": 1}
        assert data["budgetKobo"] == 1_000_000
        assert data["remainingBudgetKobo"] == 1_000_000
        assert data["participantCounts"] == {"pending": 1, "approved": 1}

    async def test_the_acceptance_rate_is_null_before_anything_is_decided(self):
        """A displayed rate of zero reads as "we reject everything", which on day one is both false and the
        worst possible thing to show."""
        program = await make_season()
        await applicant(program)
        assert (await triage_service.stats())["acceptanceRate"] is None

    async def test_the_acceptance_rate_counts_only_decided_findings(self):
        """Untriaged findings are excluded from the denominator. Counting them would make the rate a measure
        of how fast the queue is being worked rather than of how good the findings are."""
        program = await make_season()
        staff = await make_user(staff=True)
        _, _, one = await applicant(program)
        _, _, two = await applicant(program)
        await applicant(program)  # left untriaged on purpose

        await triage_service.triage(
            submission_id=one.id,
            status="accepted",
            category="bug",
            type_="crash",
            severity="high",
            duplicate_of_id=None,
            public_response=None,
            admin_notes=None,
            staff_user_id=staff.id,
        )
        await triage_service.triage(
            submission_id=two.id,
            status="rejected",
            category=None,
            type_=None,
            severity=None,
            duplicate_of_id=None,
            public_response="No.",
            admin_notes=None,
            staff_user_id=staff.id,
        )
        data = await triage_service.stats()
        assert data["acceptanceRate"] == 0.5
        assert data["submissionsByStatus"] == {"accepted": 1, "rejected": 1, "submitted": 1}

    async def test_severity_buckets_exclude_the_ungraded(self):
        """A bucket labelled `None` on a dashboard reads as a category rather than as an absence."""
        program = await make_season()
        staff = await make_user(staff=True)
        _, _, submission = await applicant(program)
        await applicant(program)
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
        assert (await triage_service.stats())["submissionsBySeverity"] == {"critical": 1}

    async def test_turnaround_is_measured_once_something_is_triaged(self):
        program = await make_season()
        staff = await make_user(staff=True)
        _, _, submission = await applicant(program)
        assert (await triage_service.stats())["medianTriageHours"] is None

        factory = get_session_factory()
        async with factory() as session:
            await session.execute(
                update(BugHuntSubmission)
                .where(BugHuntSubmission.id == submission.id)
                .values(created_at=datetime.now(UTC) - timedelta(hours=10))
            )
            await session.commit()
        await triage_service.triage(
            submission_id=submission.id,
            status="rejected",
            category=None,
            type_=None,
            severity=None,
            duplicate_of_id=None,
            public_response="No.",
            admin_notes=None,
            staff_user_id=staff.id,
        )
        hours = (await triage_service.stats())["medianTriageHours"]
        assert hours is not None and 9.0 <= hours <= 11.0

    async def test_a_closed_seasons_final_figures_are_still_readable(self):
        program = await make_season(number=1)
        await applicant(program)
        await program_service.close_season(program.id)
        data = await triage_service.stats(program.id)
        assert data["season"] is not None
        assert data["submissionsByStatus"] == {"submitted": 1}


# ---------------------------------------------------------------------------
# Over the wire
# ---------------------------------------------------------------------------


def bearer(user: User) -> dict[str, str]:
    from src.shared.auth.jwt import create_access_token

    return {"Authorization": f"Bearer {create_access_token({'sub': user.email})}"}


class TestOverTheWire:
    async def test_a_learner_cannot_reach_the_triage_surface(self, client):
        program = await make_season()
        user, participant, submission = await applicant(program)
        headers = bearer(user)
        for path in (
            "/api/v1/admin/bug-hunt/stats",
            "/api/v1/admin/bug-hunt/participants",
            "/api/v1/admin/bug-hunt/submissions",
            "/api/v1/admin/bug-hunt/known-issues",
            f"/api/v1/admin/bug-hunt/submissions/{submission.id}",
            f"/api/v1/admin/bug-hunt/participants/{participant.id}",
        ):
            assert (await client.get(path, headers=headers)).status_code == 403, path

    async def test_a_whole_triage_pass_over_http(self, client):
        """Approve the applicant, grade their finding, and read the amount back — with no figure sent."""
        program = await make_season()
        staff = await make_user(staff=True)
        headers = bearer(staff)
        user, participant, submission = await applicant(program)

        decided = await client.post(
            f"/api/v1/admin/bug-hunt/participants/{participant.id}/decision",
            headers=headers,
            json={"decision": "approve"},
        )
        assert decided.status_code == 200, decided.text
        assert decided.json()["status"] == "approved"
        assert decided.json()["email"] == user.email

        detail = await client.get(
            f"/api/v1/admin/bug-hunt/submissions/{submission.id}", headers=headers
        )
        assert detail.status_code == 200
        assert detail.json()["rewardMatrix"]["bug"]["critical"] == 200_000

        graded = await client.post(
            f"/api/v1/admin/bug-hunt/submissions/{submission.id}/triage",
            headers=headers,
            json={
                "status": "accepted",
                "category": "bug",
                "type": "crash",
                "severity": "critical",
                "publicResponse": "Confirmed. Thank you.",
                "adminNotes": "Reproduced on a Tecno Spark 8.",
            },
        )
        assert graded.status_code == 200, graded.text
        assert graded.json()["awardKobo"] == 200_000
        assert graded.json()["awardPending"] is True
        assert graded.json()["submission"]["adminNotes"] == "Reproduced on a Tecno Spark 8."

        # The reporter sees the response and not the note.
        theirs = await client.get(
            f"/api/v1/bug-hunt/submissions/{submission.id}", headers=bearer(user)
        )
        assert theirs.json()["publicResponse"] == "Confirmed. Thank you."
        assert "adminNotes" not in theirs.json()

        stats = await client.get("/api/v1/admin/bug-hunt/stats", headers=headers)
        assert stats.json()["submissionsByStatus"] == {"accepted": 1}
        assert stats.json()["acceptanceRate"] == 1.0

    async def test_the_triage_request_has_nowhere_to_put_an_amount(self, client):
        """A sent amount is ignored rather than honoured, because the field does not exist. The season's
        matrix is the only thing that decides money on this path."""
        program = await make_season()
        staff = await make_user(staff=True)
        _, _, submission = await applicant(program)
        graded = await client.post(
            f"/api/v1/admin/bug-hunt/submissions/{submission.id}/triage",
            headers=bearer(staff),
            json={
                "status": "accepted",
                "category": "bug",
                "type": "ui",
                "severity": "low",
                "awardKobo": 5_000_000,
                "amountKobo": 5_000_000,
            },
        )
        assert graded.status_code == 200, graded.text
        assert graded.json()["awardKobo"] == 50_000

    async def test_suspension_is_super_admin_only(self, client):
        """Staff triage findings. Suspension is the one participation decision a later approval cannot
        undo, and confiscation is the obvious next thing somebody would ask for."""
        program = await make_season()
        staff = await make_user(staff=True)
        _, participant = await approved(program, staff)

        content_manager = await make_user(staff=True)
        factory = get_session_factory()
        async with factory() as session:
            await session.execute(
                update(User)
                .where(User.id == content_manager.id)
                .values(admin_staff_role="CONTENT_MANAGER")
            )
            await session.commit()

        refused = await client.post(
            f"/api/v1/admin/bug-hunt/participants/{participant.id}/suspend",
            headers=bearer(content_manager),
            json={"reason": "Duplicate accounts."},
        )
        assert refused.status_code == 403

        allowed = await client.post(
            f"/api/v1/admin/bug-hunt/participants/{participant.id}/suspend",
            headers=bearer(staff),
            json={"reason": "Duplicate accounts."},
        )
        assert allowed.status_code == 200
        assert allowed.json()["status"] == "suspended"

    async def test_a_content_manager_can_still_work_the_queue(self, client):
        """The point of Decision D: the amount is not theirs to choose, so triage does not need a super
        admin — and a two-click money flow across a 14-day season would not be used."""
        program = await make_season()
        staff = await make_user(staff=True)
        factory = get_session_factory()
        async with factory() as session:
            await session.execute(
                update(User).where(User.id == staff.id).values(admin_staff_role="CONTENT_MANAGER")
            )
            await session.commit()
        _, _, submission = await applicant(program)

        graded = await client.post(
            f"/api/v1/admin/bug-hunt/submissions/{submission.id}/triage",
            headers=bearer(staff),
            json={"status": "accepted", "category": "bug", "type": "crash", "severity": "medium"},
        )
        assert graded.status_code == 200, graded.text
        assert graded.json()["awardKobo"] == 100_000

    async def test_every_triage_mutation_is_audited(self, client):
        program = await make_season()
        staff = await make_user(staff=True)
        _, participant, submission = await applicant(program)
        headers = bearer(staff)

        await client.post(
            f"/api/v1/admin/bug-hunt/participants/{participant.id}/decision",
            headers=headers,
            json={"decision": "approve"},
        )
        await client.post(
            f"/api/v1/admin/bug-hunt/submissions/{submission.id}/triage",
            headers=headers,
            json={"status": "accepted", "category": "bug", "type": "crash", "severity": "high"},
        )

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
        assert "bug_hunt_decide_application" in actions
        assert "bug_hunt_triage_submission" in actions
