"""The wiring between a domain event and the email it sends, against a real database.

`test_bug_hunt_emails.py` proves each message says the right thing. This file proves the messages are
actually reached from the code paths that should reach them, which is the half a unit test cannot see:
an email module with perfect copy and no call site sends nothing.

The claims that carry the weight:

- **A provider outage cannot fail a triage or a payment record.** Both happen after money has moved, so
  a raised exception would surface as a failed action, the operator would repeat it, and the repeat is
  the one that double-pays. Tested by making the transport raise, not by reading the `except` block.
- **The amount in the email is the amount the ledger credited.** The award lands in a second
  transaction after grading commits, so anything reading the caller's stale submission object would
  report ₦0 for a finding that was in fact paid.
- **Idempotent admin actions do not re-send.** Approving an already-approved withdrawal and re-recording
  an already-paid one both return early, and a second email about the same money is worse than none.
- **A blocked award still tells the tester.** Silence would leave them with no word on the outcome.

Database-dependent, so opt in:

    RUN_DB_TESTS=1 DATABASE_URL=postgresql://localhost/scratch pytest tests/test_bug_hunt_notify.py

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, select, update

from src.config import settings
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
    notify_service,
    program_service,
    submission_service,
    triage_service,
    withdrawal_service,
)
from src.domains.identity.db_models import User
from src.shared.database import get_session_factory
from src.shared.infrastructure import email as em

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
                BugHuntPayoutAccount,
                BugHuntAttachment,
                BugHuntSubmission,
                BugHuntWallet,
                BugHuntParticipant,
                BugHuntProgram,
            ):
                await session.execute(delete(model))
            # Same constraint dance as the other Bug Hunt suites: `AuditLog.adminUserId` is NOT NULL
            # behind ON DELETE SET NULL, so a staff user cannot be deleted while a row references it.
            stale = select(User.id).where(User.email.like("bughunt-notify-%"))
            await session.execute(delete(AuditLog).where(AuditLog.admin_user_id.in_(stale)))
            await session.execute(delete(User).where(User.email.like("bughunt-notify-%")))
            await session.commit()

    await wipe()
    yield
    await wipe()


@pytest.fixture(autouse=True)
def transport(monkeypatch):
    """A usable provider that records instead of sending.

    Autouse, because without it these tests would exercise the no-provider-configured path and every
    assertion about what was sent would pass vacuously against an empty list.
    """
    monkeypatch.setattr(settings, "SMTP_HOST", "smtp.example.com", raising=False)
    monkeypatch.setattr(settings, "SMTP_USER", "mailer@example.com", raising=False)
    monkeypatch.setattr(settings, "SMTP_PASSWORD", "secret", raising=False)
    monkeypatch.setattr(settings, "EMAIL_OUTBOUND_STRATEGY", "smtp_only", raising=False)
    monkeypatch.setattr(settings, "BUG_HUNT_BASE_URL", "https://issues.maigie.com", raising=False)

    sent: list[dict] = []

    def fake_smtp(to_email, subject, html_body, text_body, headers=None):
        sent.append({"to": to_email, "subject": subject, "html": html_body, "text": text_body})

    monkeypatch.setattr(em, "_send_multipart_email_sync", fake_smtp)
    return sent


async def make_user(country: str | None = "NG", staff: bool = False) -> User:
    user = User(
        email=f"bughunt-notify-{uuid.uuid4().hex[:12]}@example.com",
        name="Ada",
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
        slug=f"notify-s{number}-{uuid.uuid4().hex[:6]}",
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


# ---------------------------------------------------------------------------
# Application decisions
# ---------------------------------------------------------------------------


class TestApplicationDecisions:
    async def test_approval_emails_the_applicant(self, transport):
        program = await make_season()
        staff = await make_user(staff=True)
        user, participant, _ = await applicant(program)

        await triage_service.decide_application(
            participant_id=participant.id, decision="approve", reason=None, staff_user_id=staff.id
        )

        assert len(transport) == 1
        assert transport[0]["to"] == user.email
        assert "You're in" in transport[0]["subject"]
        # The season's own limits, read from the row rather than hardcoded in the template.
        assert "₦15,000" in transport[0]["html"]

    async def test_rejection_carries_the_reason_verbatim(self, transport):
        program = await make_season()
        staff = await make_user(staff=True)
        _, participant, _ = await applicant(program)

        await triage_service.decide_application(
            participant_id=participant.id,
            decision="reject",
            reason="We could not reproduce this on a Tecno Spark 10.",
            staff_user_id=staff.id,
        )

        assert "Tecno Spark 10" in transport[0]["html"]

    async def test_a_second_rejection_does_not_offer_a_third_attempt(self, transport):
        """Two attempts is the season limit, so the second refusal must not invite another.

        Inviting a retry the API will refuse wastes the applicant's evening and teaches them the
        programme does not know its own rules.
        """
        program = await make_season()
        staff = await make_user(staff=True)
        _, participant, _ = await applicant(program)

        factory = get_session_factory()
        async with factory() as session:
            await session.execute(
                update(BugHuntParticipant)
                .where(BugHuntParticipant.id == participant.id)
                .values(attempt_count=2)
            )
            await session.commit()

        await triage_service.decide_application(
            participant_id=participant.id,
            decision="reject",
            reason="Still not reproducible.",
            staff_user_id=staff.id,
        )

        html = transport[0]["html"]
        assert "used both attempts" in html
        assert "/apply" not in html

    async def test_suspension_sends_nothing(self, transport):
        """Deliberately silent.

        A suspension needs wording a person chooses. An automated "your participation is suspended"
        with no route to reply is the worst possible version of that message, and the dashboard already
        shows the reason.
        """
        program = await make_season()
        staff = await make_user(staff=True)
        _, participant, _ = await applicant(program)
        transport.clear()

        await triage_service.suspend_participant(
            participant_id=participant.id, reason="Multiple accounts.", staff_user_id=staff.id
        )

        assert transport == []


# ---------------------------------------------------------------------------
# Triage
# ---------------------------------------------------------------------------


class TestTriageEmail:
    async def test_accepted_email_reports_what_the_ledger_credited(self, transport):
        """The award lands in a second transaction, and the email must read it after that.

        Reporting from the submission object the grading loaded would say ₦0 for a finding that was
        paid ₦2,000, which is the one error on this surface a tester will certainly notice.
        """
        program = await make_season()
        staff = await make_user(staff=True)
        user, participant, _ = await applicant(program)
        await triage_service.decide_application(
            participant_id=participant.id, decision="approve", reason=None, staff_user_id=staff.id
        )
        submission = await submission_service.create_submission(user=user, fields=finding())
        transport.clear()

        await triage_service.triage(
            submission_id=submission.id,
            status="accepted",
            category="bug",
            type_="crash",
            severity="critical",
            duplicate_of_id=None,
            public_response="Reproduced. Thank you.",
            admin_notes="Internal: same root cause as the caching bug.",
            staff_user_id=staff.id,
        )

        assert len(transport) == 1
        message = transport[0]
        assert message["to"] == user.email
        assert "₦2,000" in message["subject"]
        assert await ledger_service.balance(user.id) == 200_000
        assert "Reproduced. Thank you." in message["html"]
        # The half a tester never sees.
        assert "same root cause" not in message["html"]
        assert "same root cause" not in message["text"]

    async def test_known_issue_and_duplicate_send_different_messages(self, transport):
        program = await make_season()
        staff = await make_user(staff=True)
        user, participant, _ = await applicant(program)
        await triage_service.decide_application(
            participant_id=participant.id, decision="approve", reason=None, staff_user_id=staff.id
        )
        first = await submission_service.create_submission(user=user, fields=finding())
        await triage_service.triage(
            submission_id=first.id,
            status="accepted",
            category="bug",
            type_="crash",
            severity="high",
            duplicate_of_id=None,
            public_response=None,
            admin_notes=None,
            staff_user_id=staff.id,
        )
        second = await submission_service.create_submission(
            user=user, fields=finding(title="Same crash, reported again")
        )
        transport.clear()

        await triage_service.triage(
            submission_id=second.id,
            status="duplicate",
            category=None,
            type_=None,
            severity=None,
            duplicate_of_id=first.id,
            public_response=None,
            admin_notes=None,
            staff_user_id=staff.id,
        )

        html = transport[0]["html"]
        assert "reported this first" in html
        # The known-issue wording would blame the reporter for our backlog.
        assert "already knew" not in html

    async def test_moving_a_finding_into_review_sends_nothing(self, transport):
        """An internal step with no action for the tester in it."""
        program = await make_season()
        staff = await make_user(staff=True)
        user, participant, _ = await applicant(program)
        await triage_service.decide_application(
            participant_id=participant.id, decision="approve", reason=None, staff_user_id=staff.id
        )
        submission = await submission_service.create_submission(user=user, fields=finding())
        transport.clear()

        await triage_service.triage(
            submission_id=submission.id,
            status="in_review",
            category=None,
            type_=None,
            severity=None,
            duplicate_of_id=None,
            public_response=None,
            admin_notes=None,
            staff_user_id=staff.id,
        )

        assert transport == []

    async def test_a_blocked_award_still_emails_and_says_the_money_is_owed(self, transport):
        """An exhausted budget accepts the finding and cannot pay it. The tester still hears.

        Suppressing the email until the award landed would leave them with no word on the outcome at
        all, which is worse than an accepted message carrying an honest caveat.
        """
        program = await make_season(budget_kobo=100_000)
        staff = await make_user(staff=True)
        user, participant, _ = await applicant(program)
        await triage_service.decide_application(
            participant_id=participant.id, decision="approve", reason=None, staff_user_id=staff.id
        )
        submission = await submission_service.create_submission(user=user, fields=finding())
        transport.clear()

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

        assert result["awardBlocked"]
        assert len(transport) == 1
        html = transport[0]["html"]
        assert "₦2,000" in html
        assert "owed" in html

    async def test_a_provider_outage_does_not_fail_the_triage(self, monkeypatch, transport):
        """The property the whole design rests on.

        The grading has committed and the money has moved by the time the email is attempted. If this
        raised, the triager would see a failure, grade it again, and the second grading is the one that
        produces a second award.
        """
        program = await make_season()
        staff = await make_user(staff=True)
        user, participant, _ = await applicant(program)
        await triage_service.decide_application(
            participant_id=participant.id, decision="approve", reason=None, staff_user_id=staff.id
        )
        submission = await submission_service.create_submission(user=user, fields=finding())

        def boom(*_args, **_kwargs):
            raise RuntimeError("provider down")

        monkeypatch.setattr(em, "_send_multipart_email_sync", boom)

        result = await triage_service.triage(
            submission_id=submission.id,
            status="accepted",
            category="bug",
            type_="crash",
            severity="high",
            duplicate_of_id=None,
            public_response=None,
            admin_notes=None,
            staff_user_id=staff.id,
        )

        # Graded, paid, and the caller never knew the email failed.
        assert result["awardKobo"] == 150_000
        assert await ledger_service.balance(user.id) == 150_000


# ---------------------------------------------------------------------------
# Cash
# ---------------------------------------------------------------------------


async def funded_requester(program: BugHuntProgram, staff: User) -> tuple[User, BugHuntWithdrawal]:
    """A tester with an accepted critical finding, bank details, and an open cash request."""
    user, participant, _ = await applicant(program)
    await triage_service.decide_application(
        participant_id=participant.id, decision="approve", reason=None, staff_user_id=staff.id
    )
    submission = await submission_service.create_submission(user=user, fields=finding())
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
    await withdrawal_service.set_account(
        user_id=user.id,
        bank_code="058",
        bank_name="Guaranty Trust Bank",
        account_number="0123456789",
        account_name="Ada Lovelace",
    )
    # First entry is never held, so the request can be made immediately.
    row = await withdrawal_service.request(user_id=user.id, amount_kobo=200_000)
    return user, row


class TestCashEmail:
    async def test_approval_emails_but_does_not_claim_payment(self, transport):
        program = await make_season()
        staff = await make_user(staff=True)
        user, row = await funded_requester(program, staff)
        transport.clear()

        await withdrawal_service.decide(
            withdrawal_id=row.id, decision="approve", reason=None, staff_user_id=staff.id
        )

        assert len(transport) == 1
        assert transport[0]["to"] == user.email
        assert "approved" in transport[0]["subject"]
        assert "two working days" in transport[0]["html"]
        assert "have sent" not in transport[0]["html"]

    async def test_marking_paid_emails_the_bank_reference(self, transport):
        program = await make_season()
        staff = await make_user(staff=True)
        user, row = await funded_requester(program, staff)
        await withdrawal_service.decide(
            withdrawal_id=row.id, decision="approve", reason=None, staff_user_id=staff.id
        )
        transport.clear()

        await withdrawal_service.mark_paid(
            withdrawal_id=row.id, provider_reference="TRF-9F2K1D", staff_user_id=staff.id
        )

        assert len(transport) == 1
        assert "TRF-9F2K1D" in transport[0]["html"]
        # The last four only. The full number exists nowhere on this path.
        assert "6789" in transport[0]["html"]
        assert "0123456789" not in transport[0]["html"]

    async def test_re_recording_the_same_payment_does_not_email_twice(self, transport):
        """`mark_paid` returns early for an already-paid request, and the email sits past that.

        An operator double-clicking must not tell somebody twice that the same money was sent, because
        the obvious reading of two receipts is two payments.
        """
        program = await make_season()
        staff = await make_user(staff=True)
        _, row = await funded_requester(program, staff)
        await withdrawal_service.decide(
            withdrawal_id=row.id, decision="approve", reason=None, staff_user_id=staff.id
        )
        await withdrawal_service.mark_paid(
            withdrawal_id=row.id, provider_reference="TRF-9F2K1D", staff_user_id=staff.id
        )
        transport.clear()

        await withdrawal_service.mark_paid(
            withdrawal_id=row.id, provider_reference="TRF-9F2K1D", staff_user_id=staff.id
        )

        assert transport == []

    async def test_re_approving_does_not_email_twice(self, transport):
        program = await make_season()
        staff = await make_user(staff=True)
        _, row = await funded_requester(program, staff)
        await withdrawal_service.decide(
            withdrawal_id=row.id, decision="approve", reason=None, staff_user_id=staff.id
        )
        transport.clear()

        await withdrawal_service.decide(
            withdrawal_id=row.id, decision="approve", reason=None, staff_user_id=staff.id
        )

        assert transport == []

    async def test_a_refusal_sends_no_automated_email(self, transport):
        """A refused payout needs wording from a person.

        The money is returned by a compensating credit and the wallet screen shows both the reason and
        the reversal, so nobody is left uninformed while we get the sentence right.
        """
        program = await make_season()
        staff = await make_user(staff=True)
        user, row = await funded_requester(program, staff)
        transport.clear()

        await withdrawal_service.decide(
            withdrawal_id=row.id,
            decision="reject",
            reason="The account name does not match your Maigie account.",
            staff_user_id=staff.id,
        )

        assert transport == []
        # The money came back, which is what makes the silence acceptable.
        assert await ledger_service.balance(user.id) == 200_000

    async def test_a_provider_outage_does_not_fail_recording_a_payment(self, monkeypatch):
        """The transfer already left the bank. A failure here must not invite a second one."""
        program = await make_season()
        staff = await make_user(staff=True)
        _, row = await funded_requester(program, staff)
        await withdrawal_service.decide(
            withdrawal_id=row.id, decision="approve", reason=None, staff_user_id=staff.id
        )

        def boom(*_args, **_kwargs):
            raise RuntimeError("provider down")

        monkeypatch.setattr(em, "_send_multipart_email_sync", boom)

        paid = await withdrawal_service.mark_paid(
            withdrawal_id=row.id, provider_reference="TRF-1", staff_user_id=staff.id
        )
        assert paid.status == "paid"
        assert paid.provider_reference == "TRF-1"


# ---------------------------------------------------------------------------
# The season announcement
# ---------------------------------------------------------------------------


class TestAnnouncement:
    async def test_a_draft_season_cannot_be_announced(self):
        """The programme's most valuable list must not be sent to a season the API will refuse."""
        program = await make_season(status="draft")
        result = await notify_service.announce_season(program_id=program.id)
        assert result["reason"] == "SEASON_NOT_OPEN"
        assert result["sent"] == 0

    async def test_the_audience_is_past_participants_and_balance_holders(self):
        program = await make_season()
        staff = await make_user(staff=True)
        approved_user, participant, _ = await applicant(program)
        await triage_service.decide_application(
            participant_id=participant.id, decision="approve", reason=None, staff_user_id=staff.id
        )
        pending_user, _, _ = await applicant(program)

        audience = await notify_service._announce_audience(exclude_program_id="not-a-season")

        assert approved_user.id in audience
        # Never approved and holding nothing: no reason to hear about a new season.
        assert pending_user.id not in audience

    async def test_a_suspended_participant_is_not_invited(self):
        """Inviting somebody to a season they cannot file in is worse than silence."""
        program = await make_season()
        staff = await make_user(staff=True)
        user, participant, _ = await applicant(program)
        await triage_service.decide_application(
            participant_id=participant.id, decision="approve", reason=None, staff_user_id=staff.id
        )
        await triage_service.suspend_participant(
            participant_id=participant.id, reason="Multiple accounts.", staff_user_id=staff.id
        )

        audience = await notify_service._announce_audience(exclude_program_id="not-a-season")
        assert user.id not in audience

    async def test_a_carried_forward_participant_is_the_audience_until_they_accept(self):
        """The Season 2 path, which is the only one this send exists for.

        Applying to a season records an accepted rules version, so within one season every participant
        has already accepted and the audience is empty. Carry-forward deliberately does *not* copy that
        version across, because Season 2 sets its own amounts, dates and country scope and consent to
        Season 1 is not consent to Season 2. That gap is precisely who the announcement is for, and it
        closes the moment they accept.
        """
        staff = await make_user(staff=True)
        season_one = await make_season(number=1)
        user, participant, _ = await applicant(season_one)
        await triage_service.decide_application(
            participant_id=participant.id, decision="approve", reason=None, staff_user_id=staff.id
        )

        # Within Season 1 they have already accepted, so they are not the audience for it.
        assert user.id not in await notify_service._announce_audience(
            exclude_program_id=season_one.id
        )

        await program_service.close_season(season_one.id)
        season_two = await make_season(number=2)
        await program_service.carry_forward(
            into_program_id=season_two.id, from_program_id=season_one.id
        )

        # Seeded approved, with no accepted version for the new season: exactly the person to tell.
        assert user.id in await notify_service._announce_audience(exclude_program_id=season_two.id)

        await submission_service.accept_terms(user=user, rules_version=season_two.rules_version)

        assert user.id not in await notify_service._announce_audience(
            exclude_program_id=season_two.id
        )

    async def test_audience_size_matches_the_audience(self):
        """The console shows this number before the button does anything irreversible."""
        program = await make_season()
        staff = await make_user(staff=True)
        _, participant, _ = await applicant(program)
        await triage_service.decide_application(
            participant_id=participant.id, decision="approve", reason=None, staff_user_id=staff.id
        )

        size = await notify_service.announce_audience_size(program_id=program.id)
        audience = await notify_service._announce_audience(exclude_program_id=program.id)
        assert size == len(audience)
