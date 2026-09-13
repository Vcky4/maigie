"""Seasons and intake, exercised against a real database.

These are the tests the constraint-level suite cannot cover: the *service* behaviour above the schema.
`test_bug_hunt_schema.py` proves Postgres refuses two open seasons; this proves `open_season` refuses it
with a readable message before Postgres has to, which is the difference between a tester seeing an
explanation and a triager seeing an `IntegrityError`.

The set is chosen around the two claims the programme rests on:

- **Decision 12 — a season is a row, not a deploy.** Creating Season 2, defaulting it from Season 1,
  opening it, and carrying a cohort into it must all work through these functions alone.
- **The application is the submission**, and a returning participant must never be shown an application
  form again.

Database-dependent, so opt in:

    RUN_DB_TESTS=1 DATABASE_URL=postgresql://localhost/scratch pytest tests/test_bug_hunt_lifecycle.py

The `db` fixture in `conftest.py` skips the file otherwise. It writes to whatever `DATABASE_URL` names,
so point it at a scratch database.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, select, update

from src.domains.bug_hunt import rewards
from src.domains.bug_hunt.db_models import (
    BugHuntAttachment,
    BugHuntLedgerEntry,
    BugHuntParticipant,
    BugHuntProgram,
    BugHuntSubmission,
    BugHuntWallet,
)
from src.domains.bug_hunt.exceptions import (
    AlreadyParticipatingError,
    AttemptLimitReachedError,
    CountryNotEligibleError,
    CountryNotSetError,
    NoOpenSeasonError,
    NotApprovedError,
    ReapplyTooSoonError,
    SeasonStateError,
    SubmissionLimitReachedError,
    TermsNotAcceptedError,
)
from src.domains.bug_hunt.services import program_service, submission_service
from src.domains.identity.db_models import User
from src.shared.database import get_session_factory
from src.shared.exceptions import ConflictError, NotFoundError, ValidationError

pytestmark = pytest.mark.usefixtures("db")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
async def clean_slate():
    """Empty the Bug Hunt tables around each test.

    Deleted in dependency order, and only these tables: the suite shares a database with everything else
    and truncating broadly would take other domains' fixtures with it.
    """

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
            # Audit rows first, and this is not tidiness. `AuditLog.adminUserId` is `NOT NULL` with an
            # `ON DELETE SET NULL` foreign key, so deleting a staff user makes Postgres attempt a null it
            # forbids — the delete below fails, and because this fixture is autouse the failure lands on
            # every test in the file rather than on the one that wrote the row. Left over from a previous
            # run it also poisons the *next* run's setup, which is how a green suite turns entirely red
            # without a code change.
            stale = select(User.id).where(User.email.like("bughunt-test-%"))
            await session.execute(delete(AuditLog).where(AuditLog.admin_user_id.in_(stale)))
            await session.execute(delete(User).where(User.email.like("bughunt-test-%")))
            await session.commit()

    await wipe()
    yield
    await wipe()


async def make_user(country: str | None = "NG") -> User:
    user = User(email=f"bughunt-test-{uuid.uuid4().hex[:12]}@example.com", country=country)
    factory = get_session_factory()
    async with factory() as session:
        session.add(user)
        await session.commit()
        await session.refresh(user)
    return user


async def make_season(
    *, number: int = 1, status: str = "open", days_from_now: int = 0, **overrides
) -> BugHuntProgram:
    starts = datetime.now(UTC) + timedelta(days=days_from_now)
    program = await program_service.create(
        name=f"Season {number}",
        slug=f"season-{number}-{uuid.uuid4().hex[:6]}",
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


async def set_participant(participant_id: str, **values) -> None:
    factory = get_session_factory()
    async with factory() as session:
        await session.execute(
            update(BugHuntParticipant)
            .where(BugHuntParticipant.id == participant_id)
            .values(**values)
        )
        await session.commit()


def finding(**overrides) -> dict:
    fields = {
        "platform": "android",
        "title": "Crash when opening Learn on a cold start",
        "stepsToReproduce": "Force-stop the app, reopen it, tap Learn within two seconds.",
        "expectedResult": "The Learn tab renders.",
        "actualResult": "The app closes with no message.",
        "reportedSeverity": "high",
    }
    fields.update(overrides)
    return fields


async def approved_participant(program: BugHuntProgram, user: User) -> BugHuntParticipant:
    """A participant who applied and was accepted, with this season's terms on file."""
    participant, _ = await submission_service.create_application(
        user=user, fields=finding(), accepted_rules_version=program.rules_version
    )
    await set_participant(participant.id, status="approved", decided_at=datetime.now(UTC))
    refreshed = await program_service.participation(user_id=user.id, program_id=program.id)
    assert refreshed is not None
    return refreshed


# ---------------------------------------------------------------------------
# Season resolution
# ---------------------------------------------------------------------------


class TestSeasonResolution:
    async def test_no_season_at_all_is_a_named_refusal(self):
        """The between-seasons state, which is where the programme spends most of the year."""
        with pytest.raises(NoOpenSeasonError) as e:
            await program_service.require_open()
        assert e.value.code == "NO_OPEN_SEASON"
        assert e.value.next_starts_at is None

    async def test_a_scheduled_season_is_named_in_the_refusal(self):
        """So the landing page can say "the next one starts on the 14th" rather than "nothing here".

        The refusal carries the date because a client that has just been refused should not have to make a
        second request to find out what to tell the person in front of it.
        """
        await make_season(number=2, status="draft", days_from_now=30)
        with pytest.raises(NoOpenSeasonError) as e:
            await program_service.require_open()
        assert e.value.next_starts_at is not None

    async def test_the_open_season_is_resolved_by_status_not_by_date(self):
        """A season whose window has not begun is still the open one if an admin opened it.

        Deriving "open" from the dates would mean a season opens itself at midnight whether or not anyone
        is ready to triage it, and closes itself while a tester is mid-submission.
        """
        program = await make_season(number=1, status="open", days_from_now=5)
        current = await program_service.current()
        assert current is not None
        assert current.id == program.id


# ---------------------------------------------------------------------------
# Decision 12: opening Season 2 without a deploy
# ---------------------------------------------------------------------------


class TestSeasonTwoNeedsNoCode:
    async def test_defaults_for_a_first_season_come_from_the_module(self):
        defaults = await program_service.defaults_for_next()
        assert defaults["seasonNumber"] == 1
        assert defaults["rewardMatrix"] == rewards.DEFAULT_REWARD_MATRIX
        assert defaults["previousSeasonNumber"] is None

    async def test_season_two_defaults_from_season_one(self):
        """The point of the season editor: adjust last season's numbers, do not retype them.

        A blank form is how a season opens with a budget of zero, or with amounts that quietly differ from
        what was announced.
        """
        await make_season(
            number=1,
            status="closed",
            budget_kobo=12_345_600,
            per_participant_cap_kobo=999_900,
            country_allowlist=["NG", "GH"],
        )
        defaults = await program_service.defaults_for_next()
        assert defaults["seasonNumber"] == 2
        assert defaults["budgetKobo"] == 12_345_600
        assert defaults["perParticipantCapKobo"] == 999_900
        assert defaults["countryAllowlist"] == ["NG", "GH"]
        assert defaults["previousSeasonNumber"] == 1

    async def test_a_second_open_season_is_refused_with_a_message(self):
        """Before Postgres has to refuse it. The partial unique index is the backstop; this is the
        explanation, and it names which season is in the way."""
        first = await make_season(number=1, status="open")
        second = await make_season(number=2, status="draft", days_from_now=30)
        with pytest.raises(SeasonStateError) as e:
            await program_service.open_season(second.id)
        assert e.value.code == "SEASON_ALREADY_OPEN"
        assert str(first.season_number) in e.value.message

    async def test_a_season_with_no_budget_cannot_open(self):
        """It would accept findings it cannot pay for, and the tester would not know until triage."""
        program = await make_season(number=1, status="draft", budget_kobo=0)
        with pytest.raises(SeasonStateError) as e:
            await program_service.open_season(program.id)
        assert e.value.code == "SEASON_NO_BUDGET"

    async def test_a_season_with_an_incomplete_reward_table_cannot_open(self):
        """The failure mode this prevents: a missing tier awards ₦0 for a real bug and reports success."""
        program = await make_season(number=1, status="draft")
        factory = get_session_factory()
        async with factory() as session:
            await session.execute(
                update(BugHuntProgram)
                .where(BugHuntProgram.id == program.id)
                .values(reward_matrix={"bug": {"critical": 200_000}})
            )
            await session.commit()
        with pytest.raises(SeasonStateError) as e:
            await program_service.open_season(program.id)
        assert e.value.code == "SEASON_MATRIX_INVALID"

    async def test_closing_season_one_lets_season_two_open(self):
        """The whole handover, through these functions and nothing else."""
        first = await make_season(number=1, status="open")
        await program_service.close_season(first.id)
        second = await make_season(number=2, status="open", days_from_now=1)
        current = await program_service.current()
        assert current is not None
        assert current.season_number == second.season_number

    async def test_a_closed_season_cannot_be_reopened(self):
        program = await make_season(number=1, status="closed")
        with pytest.raises(SeasonStateError) as e:
            await program_service.open_season(program.id)
        assert e.value.code == "SEASON_CLOSED"

    async def test_a_closed_season_cannot_be_edited(self):
        """Its reward matrix is the record of what it paid. Editing it would make the public season
        history lie about what testers were promised."""
        program = await make_season(number=1, status="closed")
        with pytest.raises(SeasonStateError) as e:
            await program_service.edit(program.id, {"budgetKobo": 1})
        assert e.value.code == "SEASON_CLOSED"

    async def test_an_open_season_can_have_its_budget_raised(self):
        """Sometimes necessary mid-run. Lowering it below what is already awarded is refused by a CHECK."""
        program = await make_season(number=1, status="open", budget_kobo=1_000_000)
        edited = await program_service.edit(program.id, {"budgetKobo": 5_000_000})
        assert edited.budget_kobo == 5_000_000

    async def test_an_incomplete_matrix_is_refused_at_edit_time(self):
        program = await make_season(number=1, status="open")
        with pytest.raises(ValidationError):
            await program_service.edit(program.id, {"rewardMatrix": {"bug": {"critical": 1}}})

    async def test_a_duplicate_season_number_is_refused(self):
        await make_season(number=1, status="closed")
        with pytest.raises(ConflictError) as e:
            await make_season(number=1, status="draft", days_from_now=30)
        assert e.value.code == "SEASON_DUPLICATE"


# ---------------------------------------------------------------------------
# Carry-forward
# ---------------------------------------------------------------------------


class TestCarryForward:
    async def test_approved_participants_are_seeded_into_the_next_season(self):
        first = await make_season(number=1, status="open")
        user = await make_user()
        await approved_participant(first, user)
        await program_service.close_season(first.id)

        second = await make_season(number=2, status="draft", days_from_now=1)
        assert (
            await program_service.carry_forward_preview(
                into_program_id=second.id, from_program_id=first.id
            )
            == 1
        )
        assert (
            await program_service.carry_forward(into_program_id=second.id, from_program_id=first.id)
            == 1
        )

        carried = await program_service.participation(user_id=user.id, program_id=second.id)
        assert carried is not None
        assert carried.status == "approved"
        assert carried.carried_from_program_id == first.id

    async def test_a_carried_participant_owes_this_seasons_terms(self):
        """Seeded with no accepted version, deliberately. Season 2's amounts, dates and possibly country
        scope differ, and consent to Season 1 is not consent to Season 2."""
        first = await make_season(number=1, status="open")
        user = await make_user()
        await approved_participant(first, user)
        await program_service.close_season(first.id)
        second = await make_season(number=2, status="draft", days_from_now=1)
        await program_service.carry_forward(into_program_id=second.id, from_program_id=first.id)

        carried = await program_service.participation(user_id=user.id, program_id=second.id)
        assert carried is not None
        assert carried.accepted_rules_version is None
        assert carried.terms_accepted_at is None

    async def test_carry_forward_is_idempotent(self):
        """A double-clicked button must not bulk-approve anybody twice."""
        first = await make_season(number=1, status="open")
        user = await make_user()
        await approved_participant(first, user)
        await program_service.close_season(first.id)
        second = await make_season(number=2, status="draft", days_from_now=1)

        assert (
            await program_service.carry_forward(into_program_id=second.id, from_program_id=first.id)
            == 1
        )
        assert (
            await program_service.carry_forward(into_program_id=second.id, from_program_id=first.id)
            == 0
        )
        assert (await program_service.participant_counts(second.id)) == {"approved": 1}

    async def test_suspended_and_rejected_participants_do_not_carry(self):
        """Which is the entire point of suspending somebody."""
        first = await make_season(number=1, status="open")
        suspended = await make_user()
        rejected = await make_user()
        p1 = await approved_participant(first, suspended)
        await set_participant(p1.id, status="suspended")
        p2 = await approved_participant(first, rejected)
        await set_participant(p2.id, status="rejected", rejection_reason="not reproducible")
        await program_service.close_season(first.id)

        second = await make_season(number=2, status="draft", days_from_now=1)
        assert (
            await program_service.carry_forward(into_program_id=second.id, from_program_id=first.id)
            == 0
        )

    async def test_a_closed_season_takes_no_new_participants(self):
        first = await make_season(number=1, status="closed")
        with pytest.raises(SeasonStateError) as e:
            await program_service.carry_forward(into_program_id=first.id, from_program_id=first.id)
        assert e.value.code == "SEASON_CLOSED"


# ---------------------------------------------------------------------------
# Applying
# ---------------------------------------------------------------------------


class TestApplying:
    async def test_an_application_creates_a_participation_and_a_finding(self):
        """The application *is* the submission. Nothing downstream treats it specially, so the work a
        tester did to get in is triaged and paid like any other finding."""
        program = await make_season(number=1, status="open")
        user = await make_user()
        participant, submission = await submission_service.create_application(
            user=user, fields=finding(), accepted_rules_version=program.rules_version
        )
        assert participant.status == "pending"
        assert participant.attempt_count == 1
        assert participant.accepted_rules_version == program.rules_version
        assert submission.is_application is True
        assert submission.status == "submitted"
        assert submission.program_id == program.id
        assert submission.participant_id == participant.id

    async def test_applying_with_no_open_season_is_refused(self):
        user = await make_user()
        with pytest.raises(NoOpenSeasonError):
            await submission_service.create_application(
                user=user, fields=finding(), accepted_rules_version=1
            )

    async def test_an_ineligible_country_cannot_apply(self):
        program = await make_season(number=1, status="open")
        user = await make_user(country="GH")
        with pytest.raises(CountryNotEligibleError):
            await submission_service.create_application(
                user=user, fields=finding(), accepted_rules_version=program.rules_version
            )

    async def test_an_unset_country_is_asked_rather_than_refused(self):
        program = await make_season(number=1, status="open")
        user = await make_user(country=None)
        with pytest.raises(CountryNotSetError):
            await submission_service.create_application(
                user=user, fields=finding(), accepted_rules_version=program.rules_version
            )

    async def test_stale_terms_are_refused(self):
        """The applicant read terms we have since replaced — possibly the amounts. Recording that as
        consent would be recording agreement to something they never saw."""
        program = await make_season(number=1, status="open")
        await program_service.edit(program.id, {"rulesVersion": 3})
        user = await make_user()
        with pytest.raises(ConflictError) as e:
            await submission_service.create_application(
                user=user, fields=finding(), accepted_rules_version=1
            )
        assert e.value.code == "RULES_VERSION_STALE"

    async def test_a_pending_applicant_cannot_apply_twice(self):
        program = await make_season(number=1, status="open")
        user = await make_user()
        await submission_service.create_application(
            user=user, fields=finding(), accepted_rules_version=program.rules_version
        )
        with pytest.raises(AlreadyParticipatingError) as e:
            await submission_service.create_application(
                user=user, fields=finding(), accepted_rules_version=program.rules_version
            )
        assert e.value.participant_status == "pending"

    async def test_an_approved_participant_is_never_sent_back_to_the_form(self):
        """Including a carried-forward one. Their route is straight to submitting, and showing them an
        application again would read as though we had lost their record."""
        program = await make_season(number=1, status="open")
        user = await make_user()
        await approved_participant(program, user)
        with pytest.raises(AlreadyParticipatingError) as e:
            await submission_service.create_application(
                user=user, fields=finding(), accepted_rules_version=program.rules_version
            )
        assert e.value.participant_status == "approved"


class TestReapplying:
    async def test_a_rejected_applicant_waits_out_the_cooldown(self):
        """Without it, "you may reapply once" becomes "resubmit the same report immediately and hope for a
        different triager", which wastes the queue and teaches the applicant nothing."""
        program = await make_season(number=1, status="open")
        user = await make_user()
        participant, _ = await submission_service.create_application(
            user=user, fields=finding(), accepted_rules_version=program.rules_version
        )
        await set_participant(
            participant.id,
            status="rejected",
            rejection_reason="Not reproducible on the current build.",
            decided_at=datetime.now(UTC),
        )
        with pytest.raises(ReapplyTooSoonError) as e:
            await submission_service.create_application(
                user=user, fields=finding(), accepted_rules_version=program.rules_version
            )
        assert e.value.ready_at

    async def test_after_the_cooldown_the_one_retry_is_allowed(self):
        program = await make_season(number=1, status="open")
        user = await make_user()
        participant, _ = await submission_service.create_application(
            user=user, fields=finding(), accepted_rules_version=program.rules_version
        )
        await set_participant(
            participant.id,
            status="rejected",
            rejection_reason="Not reproducible.",
            decided_at=datetime.now(UTC) - timedelta(hours=49),
        )
        retried, submission = await submission_service.create_application(
            user=user,
            fields=finding(title="A different finding entirely"),
            accepted_rules_version=program.rules_version,
        )
        assert retried.id == participant.id, "the retry reuses the participation row"
        assert retried.status == "pending"
        assert retried.attempt_count == 2
        assert (
            retried.rejection_reason is None
        ), "a stale reason on a pending row would be shown as current"
        assert submission.is_application is True

    async def test_a_second_rejection_ends_it(self):
        program = await make_season(number=1, status="open")
        user = await make_user()
        participant, _ = await submission_service.create_application(
            user=user, fields=finding(), accepted_rules_version=program.rules_version
        )
        await set_participant(
            participant.id,
            status="rejected",
            rejection_reason="Not reproducible.",
            decided_at=datetime.now(UTC) - timedelta(hours=49),
            attempt_count=2,
        )
        with pytest.raises(AttemptLimitReachedError):
            await submission_service.create_application(
                user=user, fields=finding(), accepted_rules_version=program.rules_version
            )

    async def test_both_application_findings_survive_the_retry(self):
        """The first report is still a report. Rejecting the applicant does not delete the evidence, and a
        triager may still find it worth something."""
        program = await make_season(number=1, status="open")
        user = await make_user()
        participant, _ = await submission_service.create_application(
            user=user, fields=finding(), accepted_rules_version=program.rules_version
        )
        await set_participant(
            participant.id,
            status="rejected",
            rejection_reason="Not reproducible.",
            decided_at=datetime.now(UTC) - timedelta(hours=49),
        )
        await submission_service.create_application(
            user=user,
            fields=finding(title="Second attempt finding"),
            accepted_rules_version=program.rules_version,
        )
        rows, total = await submission_service.list_own(user_id=user.id)
        assert total == 2


# ---------------------------------------------------------------------------
# Terms re-acceptance
# ---------------------------------------------------------------------------


class TestTermsAcceptance:
    async def test_a_carried_participant_is_blocked_until_they_accept(self):
        first = await make_season(number=1, status="open")
        user = await make_user()
        await approved_participant(first, user)
        await program_service.close_season(first.id)
        second = await make_season(number=2, status="open", days_from_now=1)
        await program_service.carry_forward(into_program_id=second.id, from_program_id=first.id)

        with pytest.raises(TermsNotAcceptedError) as e:
            await submission_service.create_submission(user=user, fields=finding())
        assert e.value.rules_version == second.rules_version

    async def test_accepting_unblocks_submitting(self):
        first = await make_season(number=1, status="open")
        user = await make_user()
        await approved_participant(first, user)
        await program_service.close_season(first.id)
        second = await make_season(number=2, status="open", days_from_now=1)
        await program_service.carry_forward(into_program_id=second.id, from_program_id=first.id)

        accepted = await submission_service.accept_terms(
            user=user, rules_version=second.rules_version
        )
        assert accepted.accepted_rules_version == second.rules_version
        assert accepted.terms_accepted_at is not None

        submission = await submission_service.create_submission(user=user, fields=finding())
        assert submission.program_id == second.id

    async def test_accepting_a_stale_version_is_refused(self):
        program = await make_season(number=1, status="open")
        user = await make_user()
        await approved_participant(program, user)
        await program_service.edit(program.id, {"rulesVersion": 4})
        with pytest.raises(ConflictError) as e:
            await submission_service.accept_terms(user=user, rules_version=1)
        assert e.value.code == "RULES_VERSION_STALE"

    async def test_accepting_terms_is_not_a_way_into_the_season(self):
        """It is a step for someone already approved, not a substitute for applying."""
        program = await make_season(number=1, status="open")
        user = await make_user()
        with pytest.raises(NotApprovedError):
            await submission_service.accept_terms(user=user, rules_version=program.rules_version)


# ---------------------------------------------------------------------------
# Submitting
# ---------------------------------------------------------------------------


class TestSubmitting:
    async def test_an_approved_participant_can_file_a_finding(self):
        program = await make_season(number=1, status="open")
        user = await make_user()
        await approved_participant(program, user)
        submission = await submission_service.create_submission(user=user, fields=finding())
        assert submission.is_application is False
        assert submission.status == "submitted"

    @pytest.mark.parametrize("participant_status", ["pending", "rejected", "suspended"])
    async def test_an_unapproved_participant_cannot_file(self, participant_status):
        program = await make_season(number=1, status="open")
        user = await make_user()
        participant, _ = await submission_service.create_application(
            user=user, fields=finding(), accepted_rules_version=program.rules_version
        )
        await set_participant(
            participant.id,
            status=participant_status,
            rejection_reason="reason" if participant_status == "rejected" else None,
            decided_at=datetime.now(UTC),
        )
        with pytest.raises(NotApprovedError) as e:
            await submission_service.create_submission(user=user, fields=finding())
        assert e.value.participant_status == participant_status

    async def test_a_closed_season_takes_no_submissions(self):
        """Intake stops. Everything else about the wallet stays open, but this is the line."""
        program = await make_season(number=1, status="open")
        user = await make_user()
        await approved_participant(program, user)
        await program_service.close_season(program.id)
        with pytest.raises(NoOpenSeasonError):
            await submission_service.create_submission(user=user, fields=finding())

    async def test_the_daily_limit_is_enforced_and_names_a_retry_time(self):
        """Counted from rows, not from Redis: it is a published rule of the season, so it has to hold when
        the cache is down. `check_rate_limit` degrades *open*, and a limit that stops applying during a
        blip is not a limit."""
        program = await make_season(number=1, status="open", submission_daily_limit=3)
        user = await make_user()
        await approved_participant(program, user)  # this is submission 1
        await submission_service.create_submission(user=user, fields=finding())
        await submission_service.create_submission(user=user, fields=finding())
        with pytest.raises(SubmissionLimitReachedError) as e:
            await submission_service.create_submission(user=user, fields=finding())
        assert e.value.limit == 3
        assert e.value.retry_at
        assert e.value.status_code == 429

    async def test_the_limit_is_per_person_not_per_season(self):
        """One prolific tester must not lock the queue for everybody else.

        Counted against the participation rather than the season, which is easy to get wrong when the
        limit is configured on the season row.
        """
        program = await make_season(number=1, status="open", submission_daily_limit=2)
        first, second = await make_user(), await make_user()
        await approved_participant(program, first)  # each application is submission 1 of 2
        await approved_participant(program, second)

        await submission_service.create_submission(user=first, fields=finding())
        with pytest.raises(SubmissionLimitReachedError):
            await submission_service.create_submission(user=first, fields=finding())

        # The second tester still has their own allowance, untouched by their neighbour's volume.
        theirs = await submission_service.create_submission(user=second, fields=finding())
        assert theirs.id

    async def test_submissions_outside_the_window_do_not_count(self):
        program = await make_season(number=1, status="open", submission_daily_limit=1)
        user = await make_user()
        participant = await approved_participant(program, user)
        factory = get_session_factory()
        async with factory() as session:
            await session.execute(
                update(BugHuntSubmission)
                .where(BugHuntSubmission.participant_id == participant.id)
                .values(created_at=datetime.now(UTC) - timedelta(hours=25))
            )
            await session.commit()
        submission = await submission_service.create_submission(user=user, fields=finding())
        assert submission.id


class TestASubmitterCannotGradeTheirOwnFinding:
    """The most direct route from a tester to their own payment, closed by construction."""

    @pytest.mark.parametrize(
        "field", ["category", "severity", "status", "publicResponse", "adminNotes", "isApplication"]
    )
    async def test_triage_fields_in_the_payload_are_ignored(self, field):
        program = await make_season(number=1, status="open")
        user = await make_user()
        await approved_participant(program, user)
        payload = finding()
        payload[field] = "critical" if field == "severity" else "accepted"
        submission = await submission_service.create_submission(user=user, fields=payload)
        assert submission.category is None
        assert submission.severity is None
        assert submission.status == "submitted"
        assert submission.public_response is None
        assert submission.admin_notes is None
        assert submission.is_application is False

    async def test_the_reported_severity_is_kept_but_is_not_the_graded_one(self):
        program = await make_season(number=1, status="open")
        user = await make_user()
        await approved_participant(program, user)
        submission = await submission_service.create_submission(
            user=user, fields=finding(reportedSeverity="critical")
        )
        assert submission.reported_severity == "critical"
        assert submission.severity is None, "grading is a triager's act, not a claim on the form"


class TestFieldHygiene:
    @pytest.mark.parametrize(
        "blanked", ["title", "stepsToReproduce", "expectedResult", "actualResult"]
    )
    async def test_a_whitespace_only_required_field_is_refused(self, blanked):
        """Pydantic's `min_length` counts characters, not content, so "    " passes it. A report with a
        blank reproduction is not a report."""
        program = await make_season(number=1, status="open")
        user = await make_user()
        await approved_participant(program, user)
        with pytest.raises(ValidationError):
            await submission_service.create_submission(
                user=user, fields=finding(**{blanked: "        "})
            )

    async def test_optional_fields_are_trimmed_to_null(self):
        """An empty string from a form field the tester left alone should read as absent, not as a device
        model of ""."""
        program = await make_season(number=1, status="open")
        user = await make_user()
        await approved_participant(program, user)
        submission = await submission_service.create_submission(
            user=user, fields=finding(deviceModel="   ", appVersion="  1.4.2  ")
        )
        assert submission.device_model is None
        assert submission.app_version == "1.4.2"


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


class TestReading:
    async def test_another_testers_submission_answers_not_found(self):
        """404, not 403. Scoped in the query rather than checked afterwards, so a foreign id is
        indistinguishable from one that never existed — and in a programme where findings describe unfixed
        security bugs, a 403 that confirms a row exists is not a small oracle."""
        program = await make_season(number=1, status="open")
        mine, theirs = await make_user(), await make_user()
        await approved_participant(program, mine)
        _, their_submission = await submission_service.create_application(
            user=theirs, fields=finding(), accepted_rules_version=program.rules_version
        )
        with pytest.raises(NotFoundError):
            await submission_service.get_own(user_id=mine.id, submission_id=their_submission.id)

    async def test_history_spans_seasons_by_default(self):
        """An unfiltered read means every season, not the current one. A tester between seasons still has
        a history worth reading, and defaulting to "the season that does not exist right now" would show
        them an empty page."""
        first = await make_season(number=1, status="open")
        user = await make_user()
        await approved_participant(first, user)
        await program_service.close_season(first.id)

        second = await make_season(number=2, status="open", days_from_now=1)
        await program_service.carry_forward(into_program_id=second.id, from_program_id=first.id)
        await submission_service.accept_terms(user=user, rules_version=second.rules_version)
        await submission_service.create_submission(user=user, fields=finding())

        _, total_all = await submission_service.list_own(user_id=user.id)
        _, total_second = await submission_service.list_own(user_id=user.id, program_id=second.id)
        assert total_all == 2
        assert total_second == 1

    async def test_filters_and_pagination(self):
        program = await make_season(number=1, status="open", submission_daily_limit=50)
        user = await make_user()
        await approved_participant(program, user)
        for _ in range(4):
            await submission_service.create_submission(user=user, fields=finding(platform="web"))

        rows, total = await submission_service.list_own(user_id=user.id, platform="web")
        assert total == 4
        assert {r.platform for r in rows} == {"web"}

        page1, _ = await submission_service.list_own(user_id=user.id, page=1, page_size=2)
        page2, _ = await submission_service.list_own(user_id=user.id, page=2, page_size=2)
        assert len(page1) == 2 and len(page2) == 2
        assert {r.id for r in page1}.isdisjoint({r.id for r in page2})

        _, submitted_only = await submission_service.list_own(user_id=user.id, status="accepted")
        assert submitted_only == 0

    async def test_an_award_is_joined_from_the_ledger_not_stored_on_the_finding(self):
        """`awards_for` is the only place an amount comes from, so there is nothing to reconcile.

        A missing key means "not awarded yet", which the dashboard shows differently from an award of ₦0 —
        the first is "still being reviewed", the second is "reviewed, and this one does not pay".
        """
        program = await make_season(number=1, status="open")
        user = await make_user()
        participant = await approved_participant(program, user)
        submission = await submission_service.create_submission(user=user, fields=finding())

        assert await submission_service.awards_for([submission.id]) == {}

        factory = get_session_factory()
        async with factory() as session:
            wallet = BugHuntWallet(user_id=user.id)
            session.add(wallet)
            await session.flush()
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

        assert await submission_service.awards_for([submission.id]) == {submission.id: 150_000}

    async def test_awards_for_handles_an_empty_page_without_a_query(self):
        assert await submission_service.awards_for([]) == {}


# ---------------------------------------------------------------------------
# Attachments
# ---------------------------------------------------------------------------


class TestAttachments:
    async def test_a_pending_applicant_can_attach_to_their_application(self):
        """Authorised by ownership, not approval. The application's finding belongs to someone who is by
        definition not yet an approved participant, and they must still be able to attach the screenshot
        that supports it."""
        program = await make_season(number=1, status="open")
        user = await make_user()
        _, submission = await submission_service.create_application(
            user=user, fields=finding(), accepted_rules_version=program.rules_version
        )
        attachable = await submission_service.attachable_submission(
            user_id=user.id, submission_id=submission.id
        )
        assert attachable.id == submission.id

    async def test_the_per_finding_ceiling_is_enforced(self):
        program = await make_season(number=1, status="open")
        user = await make_user()
        _, submission = await submission_service.create_application(
            user=user, fields=finding(), accepted_rules_version=program.rules_version
        )
        for index in range(3):
            await submission_service.add_attachment(
                submission=submission,
                url=f"https://cdn.example/{index}.png",
                content_type="image/png",
                size_bytes=1234,
            )
        with pytest.raises(ConflictError) as e:
            await submission_service.add_attachment(
                submission=submission,
                url="https://cdn.example/4.png",
                content_type="image/png",
                size_bytes=1234,
            )
        assert e.value.code == "ATTACHMENT_LIMIT"

    async def test_a_triaged_finding_takes_no_further_evidence(self):
        """After a ruling the evidence is part of the record. Material added afterwards would let a
        decision be argued against something it was not made on."""
        program = await make_season(number=1, status="open")
        user = await make_user()
        _, submission = await submission_service.create_application(
            user=user, fields=finding(), accepted_rules_version=program.rules_version
        )
        factory = get_session_factory()
        async with factory() as session:
            await session.execute(
                update(BugHuntSubmission)
                .where(BugHuntSubmission.id == submission.id)
                .values(
                    status="accepted",
                    category="bug",
                    severity="high",
                    triaged_at=datetime.now(UTC),
                )
            )
            await session.commit()
        with pytest.raises(ConflictError) as e:
            await submission_service.attachable_submission(
                user_id=user.id, submission_id=submission.id
            )
        assert e.value.code == "SUBMISSION_CLOSED"

    async def test_attachments_come_back_with_the_finding(self):
        program = await make_season(number=1, status="open")
        user = await make_user()
        _, submission = await submission_service.create_application(
            user=user, fields=finding(), accepted_rules_version=program.rules_version
        )
        await submission_service.add_attachment(
            submission=submission,
            url="https://cdn.example/shot.png",
            content_type="image/png",
            size_bytes=999,
        )
        reread = await submission_service.get_own(user_id=user.id, submission_id=submission.id)
        assert [a.url for a in reread.attachments] == ["https://cdn.example/shot.png"]


# ---------------------------------------------------------------------------
# Over the wire
# ---------------------------------------------------------------------------


def bearer(user: User) -> dict[str, str]:
    """A token for a user created directly in the database.

    The shared `auth_headers` fixture signs up through `/auth/signup`, which sends an OTP email and does
    not let the caller set a country — and country is the first thing this domain checks. Minting the token
    keeps these tests about Bug Hunt rather than about the identity flow, which has its own suite.
    """
    from src.shared.auth.jwt import create_access_token

    return {"Authorization": f"Bearer {create_access_token({'sub': user.email})}"}


class TestOverTheWire:
    """The routes, end to end, because the service tests above bypass serialisation and auth.

    Small on purpose: the contract shape is pinned without a database in
    `test_bug_hunt_intake_contract.py`, and the rules are exercised without HTTP above. What is left that
    only a request can prove is that the two halves are wired to each other.
    """

    async def test_the_programme_state_is_readable_without_a_token(self, client):
        """The landing page's first call. If this needs auth, the page is blank for everyone who has not
        signed up — which is everyone it is written for."""
        program = await make_season(number=1, status="open")
        response = await client.get("/api/v1/bug-hunt/program")
        assert response.status_code == 200
        body = response.json()
        assert body["state"] == "open"
        assert body["season"]["seasonNumber"] == program.season_number
        # The reward table comes from the season row, so the page cannot advertise amounts we will not pay.
        amounts = {
            (t["category"], t["severity"]): t["amountKobo"] for t in body["season"]["rewards"]
        }
        assert amounts[("bug", "critical")] == 200_000
        assert amounts[("feedback", "standard")] == 50_000

    async def test_the_between_seasons_state_is_a_page_rather_than_an_error(self, client):
        response = await client.get("/api/v1/bug-hunt/program")
        assert response.status_code == 200
        assert response.json()["state"] == "between"

    async def test_a_scheduled_season_is_announced_but_not_published(self, client):
        """`/program` carries a draft season's dates — a date is a promise worth making. `/seasons` does
        not list it, because its amounts may still change and publishing a table we might revise is worse
        than publishing nothing."""
        await make_season(number=1, status="draft", days_from_now=20)
        state = (await client.get("/api/v1/bug-hunt/program")).json()
        assert state["state"] == "scheduled"
        assert state["nextSeason"]["seasonNumber"] == 1
        assert (await client.get("/api/v1/bug-hunt/seasons")).json()["seasons"] == []

    async def test_me_reports_eligibility_rather_than_refusing(self, client):
        """The app's boot call. A 403 for the ordinary case of "not in Nigeria" would make the boot path an
        error path, and the client would have to parse an error to pick a screen."""
        await make_season(number=1, status="open")
        outsider = await make_user(country="GH")
        response = await client.get("/api/v1/bug-hunt/me", headers=bearer(outsider))
        assert response.status_code == 200
        body = response.json()
        assert body["eligibility"]["eligible"] is False
        assert body["eligibility"]["reasonCode"] == "COUNTRY_NOT_ELIGIBLE"
        assert body["participation"] is None
        assert body["wallet"] is None  # Phase 4 fills this in

    async def test_apply_then_read_it_back(self, client):
        program = await make_season(number=1, status="open")
        user = await make_user()
        headers = bearer(user)

        created = await client.post(
            "/api/v1/bug-hunt/applications",
            headers=headers,
            json={
                **finding(),
                "acceptTerms": True,
                "acceptedRulesVersion": program.rules_version,
            },
        )
        assert created.status_code == 201, created.text
        body = created.json()
        assert body["participation"]["status"] == "pending"
        assert body["submission"]["isApplication"] is True
        assert body["submission"]["awardKobo"] is None

        listed = await client.get("/api/v1/bug-hunt/submissions", headers=headers)
        assert listed.status_code == 200
        assert listed.json()["total"] == 1

        detail = await client.get(
            f"/api/v1/bug-hunt/submissions/{body['submission']['id']}", headers=headers
        )
        assert detail.status_code == 200
        assert "adminNotes" not in detail.json()

        me = await client.get("/api/v1/bug-hunt/me", headers=headers)
        assert me.json()["participation"]["status"] == "pending"

    async def test_applying_without_accepting_the_terms_is_refused(self, client):
        program = await make_season(number=1, status="open")
        user = await make_user()
        response = await client.post(
            "/api/v1/bug-hunt/applications",
            headers=bearer(user),
            json={**finding(), "acceptTerms": False, "acceptedRulesVersion": program.rules_version},
        )
        assert response.status_code == 422

    async def test_refusals_reach_the_client_with_their_codes(self, client):
        """The codes are the whole point of the named exceptions: the app renders a different screen for
        most of them, and a bare 403 would collapse them into one shrug."""
        user = await make_user()
        response = await client.post(
            "/api/v1/bug-hunt/applications",
            headers=bearer(user),
            json={**finding(), "acceptTerms": True, "acceptedRulesVersion": 1},
        )
        assert response.status_code == 409
        assert "NO_OPEN_SEASON" in response.text

    async def test_a_submission_needs_approval(self, client):
        await make_season(number=1, status="open")
        user = await make_user()
        response = await client.post(
            "/api/v1/bug-hunt/submissions", headers=bearer(user), json=finding()
        )
        assert response.status_code == 403
        assert "NOT_APPROVED" in response.text

    async def test_another_testers_finding_answers_404(self, client):
        program = await make_season(number=1, status="open")
        mine, theirs = await make_user(), await make_user()
        await approved_participant(program, mine)
        _, their_submission = await submission_service.create_application(
            user=theirs, fields=finding(), accepted_rules_version=program.rules_version
        )
        response = await client.get(
            f"/api/v1/bug-hunt/submissions/{their_submission.id}", headers=bearer(mine)
        )
        assert response.status_code == 404

    async def test_the_admin_surface_refuses_a_learner(self, client):
        """`StaffUser` and `SuperAdminUser` are one word apart in a type alias, so this is worth a request
        rather than a reading."""
        user = await make_user()
        for path in ("/api/v1/admin/bug-hunt/seasons", "/api/v1/admin/bug-hunt/seasons/defaults"):
            assert (await client.get(path, headers=bearer(user))).status_code == 403

    async def test_the_admin_surface_can_run_a_whole_season_handover(self, client):
        """Decision 12's acceptance test, over HTTP: create, open, close, create the next, carry the cohort.

        If this ever needs a code change, opening Season 2 needs an engineer and the multi-season design
        was decorative.
        """
        staff = await make_user()
        factory = get_session_factory()
        async with factory() as session:
            await session.execute(
                update(User)
                .where(User.id == staff.id)
                .values(role="ADMIN", admin_staff_role="SUPER_ADMIN")
            )
            await session.commit()
        headers = bearer(staff)

        first = await make_season(number=1, status="open")
        tester = await make_user()
        await approved_participant(first, tester)

        closed = await client.post(
            f"/api/v1/admin/bug-hunt/seasons/{first.id}/close", headers=headers
        )
        assert closed.status_code == 200
        assert closed.json()["status"] == "closed"

        defaults = await client.get("/api/v1/admin/bug-hunt/seasons/defaults", headers=headers)
        assert defaults.status_code == 200
        assert defaults.json()["seasonNumber"] == 2

        starts = datetime.now(UTC) + timedelta(days=7)
        created = await client.post(
            "/api/v1/admin/bug-hunt/seasons",
            headers=headers,
            json={
                "name": "Season 2",
                "slug": f"season-2-{uuid.uuid4().hex[:6]}",
                "startsAt": starts.isoformat(),
                "endsAt": (starts + timedelta(days=14)).isoformat(),
            },
        )
        assert created.status_code == 201, created.text
        second_id = created.json()["id"]
        # Defaulted from Season 1 rather than blanked, which is what stops a season opening with a budget
        # of zero or a hand-retyped reward table.
        assert created.json()["budgetKobo"] == first.budget_kobo
        assert created.json()["rewardMatrix"] == rewards.DEFAULT_REWARD_MATRIX

        opened = await client.post(
            f"/api/v1/admin/bug-hunt/seasons/{second_id}/open", headers=headers
        )
        assert opened.status_code == 200
        assert opened.json()["status"] == "open"

        preview = await client.get(
            f"/api/v1/admin/bug-hunt/seasons/{second_id}/carry-forward",
            headers=headers,
            params={"fromProgramId": first.id},
        )
        assert preview.json()["count"] == 1

        carried = await client.post(
            f"/api/v1/admin/bug-hunt/seasons/{second_id}/carry-forward",
            headers=headers,
            json={"fromProgramId": first.id},
        )
        assert carried.json()["added"] == 1

        # The returning tester is approved but owes an acknowledgement — not an application.
        me = await client.get("/api/v1/bug-hunt/me", headers=bearer(tester))
        assert me.json()["participation"]["status"] == "approved"
        assert me.json()["needsTermsAcceptance"] is True
        assert me.json()["participation"]["carriedForward"] is True
        assert len(me.json()["history"]) == 2

        blocked = await client.post(
            "/api/v1/bug-hunt/submissions", headers=bearer(tester), json=finding()
        )
        assert blocked.status_code == 409
        assert "TERMS_NOT_ACCEPTED" in blocked.text

        accepted = await client.post(
            "/api/v1/bug-hunt/terms-acceptance",
            headers=bearer(tester),
            json={"accept": True, "rulesVersion": 1},
        )
        assert accepted.status_code == 200

        filed = await client.post(
            "/api/v1/bug-hunt/submissions", headers=bearer(tester), json=finding()
        )
        assert filed.status_code == 201, filed.text
        assert filed.json()["programId"] == second_id

        # And the closed season still publishes what it paid.
        history = (await client.get("/api/v1/bug-hunt/seasons")).json()["seasons"]
        assert {s["seasonNumber"] for s in history} == {1, 2}
