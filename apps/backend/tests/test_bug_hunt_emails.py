"""Bug Hunt programme email.

Two properties matter more than the copy, and both are about what happens when the provider is having
a bad day:

1. **A failed email never fails the caller.** Every send in this domain happens *after* a transaction
   that graded a finding or recorded a bank transfer. An exception escaping on the way out would tell
   the operator the action failed, they would repeat it, and the repeat is the one that double-pays.
2. **The amount in the subject line is the amount that was awarded.** These messages are the only
   record most testers will read, and a factor-of-100 slip here says ₦200,000 where ₦2,000 was meant.

No test opens a socket: the SMTP and Resend transports are substituted, using the same monkeypatch
targets as `test_email_infrastructure.py`.

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

import os

os.environ.setdefault("SKIP_DB_FIXTURE", "1")

import pytest  # noqa: E402

from src.config import settings  # noqa: E402
from src.domains.bug_hunt import emails  # noqa: E402
from src.shared.infrastructure import email as em  # noqa: E402


@pytest.fixture
def transport(monkeypatch):
    """Both providers usable, capturing every message handed to them."""
    monkeypatch.setattr(settings, "SMTP_HOST", "smtp.example.com", raising=False)
    monkeypatch.setattr(settings, "SMTP_USER", "mailer@example.com", raising=False)
    monkeypatch.setattr(settings, "SMTP_PASSWORD", "secret", raising=False)
    monkeypatch.setattr(settings, "RESEND_API_KEY", "re_test", raising=False)
    monkeypatch.setattr(settings, "EMAIL_OUTBOUND_STRATEGY", "smtp_only", raising=False)
    monkeypatch.setattr(settings, "BUG_HUNT_BASE_URL", "https://issues.maigie.com", raising=False)

    sent: list[dict] = []

    def fake_smtp(to_email, subject, html_body, text_body, headers=None):
        sent.append(
            {
                "to": to_email,
                "subject": subject,
                "html": html_body,
                "text": text_body,
                "headers": headers or {},
            }
        )

    monkeypatch.setattr(em, "_send_multipart_email_sync", fake_smtp)

    # Evidence writes go to the database, which this module does not have.
    async def no_evidence(**_kwargs):
        return None

    monkeypatch.setattr(em, "record_transactional_message", no_evidence)
    return sent


# ---------------------------------------------------------------------------
# Money formatting
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kobo", "expected"),
    [
        (200_000, "₦2,000"),
        (150_000, "₦1,500"),
        (50_000, "₦500"),
        (1_500_000, "₦15,000"),
        (0, "₦0"),
        (None, "₦0"),
        # Not a real award amount, but the ledger allows an adjustment of any integer kobo and a
        # rounding slip here would be visible to the person being paid.
        (150_050, "₦1,500.50"),
    ],
)
def test_naira_formatting(kobo, expected):
    assert emails._naira(kobo) == expected


def test_naira_is_the_only_converter():
    """No other function in the module divides by 100.

    Guards the property the module docstring claims. A second conversion site is how one of them ends
    up wrong, and this is the domain where wrong means underpaying somebody.
    """
    import inspect

    source = inspect.getsource(emails)
    body_after_naira = source.split("def _naira", 1)[1].split("def _evidence", 1)[0]
    assert "/ 100" in body_after_naira
    # Exactly one occurrence in the whole module, and it is the one inside `_naira`.
    assert source.count("/ 100") == 1


# ---------------------------------------------------------------------------
# What each message says
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_accepted_email_leads_with_the_amount(transport):
    assert await emails.submission_accepted(
        to_email="ada@example.com",
        name="Ada",
        title="Timer keeps running after the tab closes",
        grade="high",
        award_kobo=150_000,
        balance_kobo=200_000,
        public_response="Reproduced. Fix going out this week.",
    )
    message = transport[0]
    assert message["to"] == "ada@example.com"
    # The amount is in the subject, because the subject is what gets read on a lock screen.
    assert message["subject"] == "Accepted: ₦1,500 for your finding"
    assert "₦1,500" in message["html"]
    assert "₦2,000" in message["html"]  # the new balance
    assert "Reproduced." in message["html"]
    assert "https://issues.maigie.com/wallet" in message["html"]


@pytest.mark.asyncio
async def test_accepted_email_states_a_blocked_award_without_claiming_zero(transport):
    """A blocked award still reports the amount, and says the money is owed.

    The two-transaction design in `triage_service` lets a grading commit while the award fails on an
    exhausted budget. Suppressing the email would leave the tester with no word at all; saying ₦0
    would be false. So it says accepted, names the amount, and explains.
    """
    assert await emails.submission_accepted(
        to_email="ada@example.com",
        name="Ada",
        title="Wrong entitlement after upgrade",
        grade="critical",
        award_kobo=200_000,
        balance_kobo=0,
        blocked_reason="this season's budget is fully committed",
    )
    html = transport[0]["html"]
    assert "₦2,000" in html
    assert "budget is fully committed" in html
    assert "owed" in html


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "must_say", "must_not_say"),
    [
        ("duplicate", "reported this first", "already knew"),
        ("known_issue", "already knew", "reported this first"),
        ("rejected", "did not accept", "already knew"),
    ],
)
async def test_declined_outcomes_are_three_different_messages(
    transport, status, must_say, must_not_say
):
    """`duplicate` and `known_issue` must never collapse into one message.

    Both pay nothing, and the difference is whose fault it is: being second is another tester's speed,
    a known issue is us admitting we knew and had not fixed it. Sending the duplicate wording for a
    known issue blames the reporter for our backlog.
    """
    assert await emails.submission_declined(
        to_email="ada@example.com",
        name="Ada",
        title="Something small",
        status=status,
    )
    html = transport[0]["html"]
    assert must_say in html
    assert must_not_say not in html


@pytest.mark.asyncio
async def test_declined_email_never_carries_admin_notes(transport):
    """Only `public_response` crosses the wire.

    There is no parameter for the internal note, which is a stronger guarantee than remembering not to
    pass it. This asserts the shape rather than the discipline.
    """
    import inspect

    signature = inspect.signature(emails.submission_declined)
    assert "public_response" in signature.parameters
    assert not any("admin" in name for name in signature.parameters)


@pytest.mark.asyncio
async def test_rejected_application_offers_a_retry_only_when_one_is_left(transport):
    await emails.application_rejected(
        to_email="ada@example.com",
        name="Ada",
        season_name="Season 1",
        reason="Not reproducible from the steps given.",
        can_retry=True,
    )
    assert "one more attempt" in transport[0]["html"]
    assert "https://issues.maigie.com/apply" in transport[0]["html"]

    transport.clear()
    await emails.application_rejected(
        to_email="ada@example.com",
        name="Ada",
        season_name="Season 1",
        reason="Not reproducible from the steps given.",
        can_retry=False,
    )
    html = transport[0]["html"]
    assert "used both attempts" in html
    # No link inviting an attempt the API would refuse.
    assert "/apply" not in html


@pytest.mark.asyncio
async def test_paid_email_carries_the_bank_reference(transport):
    """The reference is the whole point of this message.

    It is the one thing a tester can check against their own bank alert, which turns "they say they
    paid me" into something verifiable.
    """
    assert await emails.withdrawal_paid(
        to_email="ada@example.com",
        name="Ada",
        amount_kobo=200_000,
        bank_name="Guaranty Trust Bank",
        account_last4="4321",
        reference="TRF-9F2K1D",
    )
    message = transport[0]
    assert "TRF-9F2K1D" in message["html"]
    assert "TRF-9F2K1D" in message["text"]
    assert "4321" in message["html"]


@pytest.mark.asyncio
async def test_approved_email_does_not_claim_the_money_has_been_sent(transport):
    """Approved is not paid, and the copy must not blur them.

    A tester told "approved" who reads it as "sent" starts checking their bank, then starts asking us
    where the money is. The gap is real and stating it is cheaper than answering the emails.
    """
    await emails.withdrawal_approved(
        to_email="ada@example.com",
        name="Ada",
        amount_kobo=200_000,
        bank_name="Guaranty Trust Bank",
        account_last4="4321",
    )
    html = transport[0]["html"]
    assert "approved" in html.lower()
    assert "two working days" in html
    assert "have sent" not in html


@pytest.mark.asyncio
async def test_full_account_number_never_appears(transport):
    """Only the last four are ever passed in, and there is no parameter for the rest."""
    import inspect

    for sender in (emails.withdrawal_approved, emails.withdrawal_paid):
        names = set(inspect.signature(sender).parameters)
        assert "account_last4" in names
        assert "account_number" not in names


# ---------------------------------------------------------------------------
# Failure never propagates
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_provider_failure_returns_false_and_does_not_raise(transport, monkeypatch):
    """The property the whole module is built around.

    Every one of these is called after a commit that moved money. A raise here would surface as a
    failed triage or a failed payment record, the operator would retry, and the retry is what
    double-pays.
    """

    def boom(*_args, **_kwargs):
        raise RuntimeError("provider down")

    monkeypatch.setattr(em, "_send_multipart_email_sync", boom)

    assert (
        await emails.submission_accepted(
            to_email="ada@example.com",
            name="Ada",
            title="Anything",
            grade="high",
            award_kobo=150_000,
            balance_kobo=150_000,
        )
        is False
    )
    assert (
        await emails.withdrawal_paid(
            to_email="ada@example.com",
            name="Ada",
            amount_kobo=200_000,
            bank_name="GTB",
            account_last4="4321",
            reference="TRF-1",
        )
        is False
    )


@pytest.mark.asyncio
async def test_missing_address_is_a_skip_not_a_crash(transport):
    """A submission whose author deleted their account has no address.

    `BugHuntSubmission.userId` is nullable behind `ON DELETE SET NULL`, so the finding survives and is
    still triageable. Nobody to write to is the correct outcome, not an error.
    """
    assert (
        await emails.submission_declined(
            to_email="",
            name=None,
            title="Orphaned finding",
            status="rejected",
        )
        is False
    )
    assert transport == []


@pytest.mark.asyncio
async def test_no_provider_configured_is_silent(monkeypatch):
    """An unconfigured environment must not raise either.

    This is the state every developer's laptop is in, and a triage that 500s locally because nobody
    set `RESEND_API_KEY` would get the email calls commented out.
    """
    monkeypatch.setattr(settings, "SMTP_HOST", "", raising=False)
    monkeypatch.setattr(settings, "RESEND_API_KEY", "", raising=False)

    async def no_evidence(**_kwargs):
        return None

    monkeypatch.setattr(em, "record_transactional_message", no_evidence)

    assert (
        await emails.application_approved(
            to_email="ada@example.com",
            name="Ada",
            season_name="Season 1",
            ends_at="26 September 2026",
            daily_limit=10,
            cap_kobo=1_500_000,
        )
        is True
    )


# ---------------------------------------------------------------------------
# Links point at the programme site
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_links_use_the_bug_hunt_origin_not_the_learner_app(transport, monkeypatch):
    """`BUG_HUNT_BASE_URL`, not `FRONTEND_BASE_URL`.

    The learner app has no `/wallet` route for this programme. A payout confirmation linking to
    `app.maigie.com/wallet` would be a broken link in an email about somebody's money.
    """
    monkeypatch.setattr(settings, "FRONTEND_BASE_URL", "https://app.maigie.com", raising=False)
    monkeypatch.setattr(settings, "BUG_HUNT_BASE_URL", "https://issues.maigie.com", raising=False)

    await emails.submission_accepted(
        to_email="ada@example.com",
        name="Ada",
        title="Anything",
        grade="high",
        award_kobo=150_000,
        balance_kobo=150_000,
    )
    html = transport[0]["html"]
    assert "https://issues.maigie.com/wallet" in html
    assert "app.maigie.com" not in html


@pytest.mark.asyncio
async def test_trailing_slash_on_the_base_url_does_not_double(transport, monkeypatch):
    monkeypatch.setattr(settings, "BUG_HUNT_BASE_URL", "https://issues.maigie.com/", raising=False)
    await emails.application_approved(
        to_email="ada@example.com",
        name="Ada",
        season_name="Season 1",
        ends_at="26 September 2026",
        daily_limit=10,
        cap_kobo=1_500_000,
    )
    assert "issues.maigie.com//" not in transport[0]["html"]


# ---------------------------------------------------------------------------
# The announcement is the one consent-gated message
# ---------------------------------------------------------------------------


def test_season_open_is_registered_and_is_not_transactional():
    """The announcement goes through the orchestrator; the money emails do not.

    `transactional=True` would be wrong here: nobody is locked out of anything by missing a marketing
    broadcast, and marking it so would be a claim that it should bypass consent.
    """
    from src.domains.notifications.taxonomy import notification_spec

    spec = notification_spec("bug_hunt.season_open")
    assert spec.category == "OPERATIONS"
    assert "EMAIL" in spec.allowed_channels
    assert spec.transactional is False
    # Within one fortnight-long season a repeat is a mistake; across seasons it is the point.
    assert spec.dedupe_window is not None


def test_no_other_bug_hunt_type_is_in_the_registry():
    """Outcome and payout email must not drift into the consent-gated path.

    If somebody later adds `bug_hunt.submission_accepted` here, a tester who switched off product
    updates stops being told what they earned. This test is the tripwire, and the fix is to read
    `bug_hunt/emails.py` first.
    """
    from src.domains.notifications.taxonomy import NOTIFICATION_SPECS

    bug_hunt_types = {t for t in NOTIFICATION_SPECS if t.startswith("bug_hunt.")}
    assert bug_hunt_types == {"bug_hunt.season_open"}
