"""Tests for the three restored Celery task implementations.

All three were broken by imports of `src.tasks.*`, a package that contained nothing but
`__pycache__`. Because the imports sat *inside* the task bodies, the module-import guard
could not see them: the worker imported fine and the task died when beat fired it.

These cover the decision logic rather than the database plumbing, since that is where the
judgement calls are: who is eligible for a reminder, whether a week is worth emailing
about, and how a comparison reads when there is no baseline.
"""

import os

os.environ.setdefault("SKIP_DB_FIXTURE", "1")

from datetime import UTC, datetime  # noqa: E402
from types import SimpleNamespace  # noqa: E402

import pytest  # noqa: E402

from src.domains.progress.services import schedule_reminders as sr  # noqa: E402
from src.domains.progress.services import weekly_summary as ws  # noqa: E402


def _user(tier="PREMIUM_MONTHLY", email="a@b.c", name="Ada Lovelace", is_active=True):
    return SimpleNamespace(id="user_1", tier=tier, email=email, name=name, is_active=is_active)


def _entitled(monkeypatch, tier):
    """Stub the one resolver.

    `_should_remind` used to compare `user.tier` to `"FREE"`, so these tests passed tier strings
    and asserted on them. Reminder eligibility now goes through
    `entitlement_service.resolve` (Decision B), because a tier string cannot see a trial or a
    pass — the old comparison excluded every trialling learner and admitted five retired tiers.
    Stubbing the resolver rather than the tier is the point: this function no longer has an
    opinion about what "paid" means, and these tests should not either.
    """
    from src.domains.billing.services import entitlement_service

    async def _resolve(user_id):
        return entitlement_service._compose(
            subscription_tier=tier,
            subscription_period_end=None,
            active_pass=None,
            active_trial=None,
        )

    monkeypatch.setattr(entitlement_service, "resolve", _resolve)


def _prefs(
    notifications=True,
    email_schedule_reminder=True,
    email_weekly_tips=True,
    timezone="UTC",
):
    return SimpleNamespace(
        notifications=notifications,
        email_schedule_reminder=email_schedule_reminder,
        email_weekly_tips=email_weekly_tips,
        timezone=timezone,
    )


# ---------------------------------------------------------------------------
# Schedule reminder eligibility
# ---------------------------------------------------------------------------


async def test_paying_user_with_defaults_is_reminded(monkeypatch):
    _entitled(monkeypatch, "PREMIUM_MONTHLY")
    assert await sr._should_remind(_user(), _prefs()) is True


async def test_missing_preferences_row_means_defaults_not_opted_out(monkeypatch):
    """Absence of a row must not read as a refusal; every column defaults to true."""
    _entitled(monkeypatch, "PREMIUM_MONTHLY")
    assert await sr._should_remind(_user(), None) is True


async def test_free_tier_is_not_reminded(monkeypatch):
    _entitled(monkeypatch, "FREE")
    assert await sr._should_remind(_user(tier="FREE"), _prefs()) is False


async def test_a_trialling_learner_is_reminded(monkeypatch):
    """The case the tier comparison got wrong and nobody would have noticed.

    A trial is supposed to be indistinguishable from a subscription — that is what makes it a
    trial of the product rather than of a subset. Under `tier != "FREE"` a trialling learner's
    row still says `FREE`, so they silently lost the one feature whose absence they cannot see:
    a reminder that never arrives leaves no trace.
    """
    from datetime import timedelta

    from src.domains.billing.services import entitlement_service

    async def _resolve(user_id):
        return entitlement_service._compose(
            subscription_tier="FREE",
            subscription_period_end=None,
            active_pass=None,
            active_trial=entitlement_service.ActiveTrial(
                ends_at=datetime.now(UTC) + timedelta(days=2), days_remaining=2
            ),
        )

    monkeypatch.setattr(entitlement_service, "resolve", _resolve)
    assert await sr._should_remind(_user(tier="FREE"), _prefs()) is True


async def test_a_deactivated_account_is_not_reminded(monkeypatch):
    """Checked before the resolver, so a deactivated account costs no read."""
    _entitled(monkeypatch, "PREMIUM_MONTHLY")
    assert await sr._should_remind(_user(is_active=False), _prefs()) is False


async def test_channel_consent_is_no_longer_this_functions_business(monkeypatch):
    """Eligibility here decides whether the reminder *exists*, not how it is sent.

    These three used to assert that a missing address, the notification master switch, and the
    schedule-reminder email preference each stopped the reminder — correct when this producer
    sent the email itself. It does not any more: it creates a canonical notification, and the
    orchestrator decides the channels and rechecks consent immediately before sending.

    Keeping the old assertions would have frozen the bug they were written to prevent in
    place: a learner who turns *email* off would lose the reminder from the notification
    centre too, and the reminder would remain unsendable by push forever. So the checks moved
    rather than disappeared — `tests/test_notification_email.py` asserts each of them at the
    point where they now decide something.
    """
    _entitled(monkeypatch, "PREMIUM_MONTHLY")
    assert await sr._should_remind(_user(email=None), _prefs(notifications=False)) is True
    assert await sr._should_remind(_user(), _prefs(email_schedule_reminder=False)) is True


def test_unknown_timezone_falls_back_to_utc_rather_than_failing():
    assert str(sr._resolve_timezone("Not/AZone")) == "UTC"
    assert str(sr._resolve_timezone(None)) == "UTC"


def test_local_time_is_rendered_in_the_users_timezone():
    moment = datetime(2026, 8, 9, 14, 30, tzinfo=UTC)
    assert "02:30 PM" in sr._format_local_time(moment, sr._resolve_timezone("UTC"))
    # Lagos is UTC+1, so the same instant is an hour later locally.
    assert "03:30 PM" in sr._format_local_time(moment, sr._resolve_timezone("Africa/Lagos"))


def test_naive_timestamps_are_treated_as_utc():
    naive = datetime(2026, 8, 9, 14, 30)
    assert "02:30 PM" in sr._format_local_time(naive, sr._resolve_timezone("UTC"))


# ---------------------------------------------------------------------------
# Weekly summary
# ---------------------------------------------------------------------------


def test_block_minutes_sums_durations():
    rows = [
        (
            datetime(2026, 8, 9, 10, 0, tzinfo=UTC),
            datetime(2026, 8, 9, 11, 0, tzinfo=UTC),
        ),
        (
            datetime(2026, 8, 9, 12, 0, tzinfo=UTC),
            datetime(2026, 8, 9, 12, 30, tzinfo=UTC),
        ),
    ]
    assert ws._block_minutes(rows) == 90


def test_block_minutes_ignores_incomplete_rows_and_negative_durations():
    rows = [
        (None, datetime(2026, 8, 9, 11, 0, tzinfo=UTC)),
        (datetime(2026, 8, 9, 12, 0, tzinfo=UTC), None),
        # end before start: clamped, not subtracted
        (
            datetime(2026, 8, 9, 13, 0, tzinfo=UTC),
            datetime(2026, 8, 9, 12, 0, tzinfo=UTC),
        ),
    ]
    assert ws._block_minutes(rows) == 0


def test_change_avoids_a_percentage_against_a_zero_baseline():
    assert ws._describe_change(120, 0) == "your first tracked week"
    assert ws._describe_change(0, 0) == "no time tracked yet"


def test_change_reports_direction_and_magnitude():
    assert ws._describe_change(120, 60) == "100% more than last week"
    assert ws._describe_change(30, 60) == "50% less than last week"
    assert ws._describe_change(60, 60) == "the same as last week"


@pytest.mark.parametrize(
    "minutes,sessions,messages,expected",
    [
        (0, 0, 0, False),
        (30, 0, 0, True),
        (0, 1, 0, True),
        (0, 0, 5, True),
    ],
)
def test_a_week_with_nothing_in_it_is_not_emailed(minutes, sessions, messages, expected):
    """An engagement email that says nothing happened teaches people to ignore it."""
    summary = {
        "minutes_this_week": minutes,
        "sessions_this_week": sessions,
        "messages_this_week": messages,
    }
    assert ws._is_worth_sending(summary) is expected


def test_rendered_summary_carries_the_figures_as_plain_text():
    summary = {
        "name": "Ada",
        "minutes_this_week": 150,
        "minutes_previous_week": 60,
        "change": "150% more than last week",
        "sessions_this_week": 4,
        "messages_this_week": 23,
        "current_streak": 3,
        "longest_streak": 9,
    }
    body = ws.render_weekly_summary(summary)

    # One plain-text body now rather than an HTML and a text part. It is stored on the
    # canonical notification and read by the notification centre, a push payload, and the
    # email template, so it must not carry markup belonging to any one of them. The greeting
    # moved to the template, which is why the learner's name is no longer expected here.
    assert "<" not in body
    assert "2.5 hours" in body
    assert "150% more than last week" in body
    assert "Study sessions: 4" in body
    assert "Questions asked: 23" in body
    assert "3 day" in body


def test_a_zero_streak_is_omitted_rather_than_shown_as_zero():
    summary = {
        "name": "Ada",
        "minutes_this_week": 60,
        "minutes_previous_week": 0,
        "change": "your first tracked week",
        "sessions_this_week": 1,
        "messages_this_week": 0,
        "current_streak": 0,
        "longest_streak": 0,
    }
    body = ws.render_weekly_summary(summary)

    assert "streak" not in body.lower()


def test_singular_day_is_not_pluralised():
    summary = {
        "name": "Ada",
        "minutes_this_week": 60,
        "minutes_previous_week": 30,
        "change": "100% more than last week",
        "sessions_this_week": 1,
        "messages_this_week": 0,
        "current_streak": 1,
        "longest_streak": 1,
    }
    body = ws.render_weekly_summary(summary)

    assert "1 day" in body
    assert "1 days" not in body
