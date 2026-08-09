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


def _user(tier="plus_monthly", email="a@b.c", name="Ada Lovelace", is_active=True):
    return SimpleNamespace(tier=tier, email=email, name=name, is_active=is_active)


def _prefs(
    notifications=True, email_schedule_reminder=True, email_weekly_tips=True, timezone="UTC"
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


def test_paying_user_with_defaults_is_reminded():
    assert sr._should_remind(_user(), _prefs()) is True


def test_missing_preferences_row_means_defaults_not_opted_out():
    """Absence of a row must not read as a refusal; every column defaults to true."""
    assert sr._should_remind(_user(), None) is True


def test_free_tier_is_not_reminded():
    assert sr._should_remind(_user(tier="FREE"), _prefs()) is False


@pytest.mark.parametrize("tier", ["plus_monthly", "plus_yearly", "circle_plan_monthly"])
def test_current_paid_tier_names_are_eligible(tier):
    """The original enumerated retired tier names, so these subscribers got nothing."""
    assert sr._should_remind(_user(tier=tier), _prefs()) is True


def test_user_without_an_address_is_not_reminded():
    assert sr._should_remind(_user(email=None), _prefs()) is False


def test_notifications_off_overrides_the_email_preference():
    assert sr._should_remind(_user(), _prefs(notifications=False)) is False


def test_reminder_preference_off_is_respected():
    assert sr._should_remind(_user(), _prefs(email_schedule_reminder=False)) is False


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
        (datetime(2026, 8, 9, 10, 0, tzinfo=UTC), datetime(2026, 8, 9, 11, 0, tzinfo=UTC)),
        (datetime(2026, 8, 9, 12, 0, tzinfo=UTC), datetime(2026, 8, 9, 12, 30, tzinfo=UTC)),
    ]
    assert ws._block_minutes(rows) == 90


def test_block_minutes_ignores_incomplete_rows_and_negative_durations():
    rows = [
        (None, datetime(2026, 8, 9, 11, 0, tzinfo=UTC)),
        (datetime(2026, 8, 9, 12, 0, tzinfo=UTC), None),
        # end before start: clamped, not subtracted
        (datetime(2026, 8, 9, 13, 0, tzinfo=UTC), datetime(2026, 8, 9, 12, 0, tzinfo=UTC)),
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


def test_rendered_summary_carries_the_figures_in_both_parts():
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
    html, text = ws.render_weekly_summary(summary)

    for body in (html, text):
        assert "Ada" in body
        assert "2.5 hours" in body
        assert "150% more than last week" in body
        assert "4" in body
        assert "23" in body
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
    html, text = ws.render_weekly_summary(summary)
    assert "streak" not in html.lower()
    assert "streak" not in text.lower()


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
    html, _ = ws.render_weekly_summary(summary)
    assert "1 day" in html
    assert "1 days" not in html
