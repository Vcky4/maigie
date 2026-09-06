"""Tests for trial eligibility, which the commercial surface reads and got wrong.

`GET /trial/status` returned bare dicts whose keys differed between branches. In
particular `trialAvailable` appeared **only** in the "no status at all" fallback, so
a learner whose trial had expired and whose cooldown had since elapsed got a
response with no such key. The client read `undefined`, treated it as false, and hid
the trial offer from someone who was eligible — immediately after a paywall had told
them a trial existed.

`trial_available` is now derived on `TrialStatus` from the same rules `start_trial`
enforces, so what the UI offers matches what pressing it would do. These tests are
that agreement, stated as cases.
"""

import os

os.environ.setdefault("SKIP_DB_FIXTURE", "1")

from datetime import UTC, datetime, timedelta

from src.domains.personal_learning import models
from src.domains.personal_learning.services import trial_service

NOW = datetime.now(UTC)


class TestTrialAvailability:
    def test_a_learner_who_never_trialled_is_eligible(self):
        assert trial_service.TrialStatus(is_active=False).trial_available is True

    def test_an_active_trial_is_not_a_second_one(self):
        status = trial_service.TrialStatus(
            is_active=True,
            day_number=3,
            days_remaining=4,
            ends_at=NOW + timedelta(days=4),
        )
        assert status.trial_available is False

    def test_inside_the_cooldown_is_not_eligible(self):
        status = trial_service.TrialStatus(
            is_active=False,
            expired=True,
            next_trial_available_at=NOW + timedelta(days=90),
        )
        assert status.trial_available is False

    def test_past_the_cooldown_is_eligible_again(self):
        """The bug. `get_trial_status` leaves `next_trial_available_at` as None once
        the cooldown has elapsed, and that learner is eligible — but the route used
        to omit `trialAvailable` on this branch entirely.
        """
        status = trial_service.TrialStatus(
            is_active=False,
            expired=True,
            started_at=NOW - timedelta(days=200),
            ends_at=NOW - timedelta(days=193),
            next_trial_available_at=None,
        )
        assert status.trial_available is True

    def test_availability_never_disagrees_with_being_active(self):
        """Both true at once would offer a trial to someone already on one."""
        for active in (True, False):
            status = trial_service.TrialStatus(is_active=active)
            assert not (status.is_active and status.trial_available)

    def test_the_trial_terms_are_the_documented_ones(self):
        """Three days, once a quarter.

        Three rather than seven because a free 7-day trial sitting beside a paid 7-day pass
        is one product at two prices, and the paid one looks like a trick. `config.
        TRIAL_DAYS_MAIGIE_PLUS` holds the same number for the Stripe subscription's own
        trial period; `test_subscription_catalog.py` asserts the two agree.
        """
        assert trial_service.TRIAL_COOLDOWN_DAYS == 90
        assert trial_service.TRIAL_DURATION_DAYS == 3


class TestTrialStatusResponse:
    """The wire model. Typed at all now, which is what lets the client stop guessing."""

    def test_a_never_trialled_learner_gets_zeroes_not_a_fabricated_day(self):
        """`COMMERCIAL_TRIAL_DEMO` announced "Day 3 of your Plus trial" to Free
        learners. Day 0 with `isActive: false` is the honest shape, and there is no
        branch that produces a day number for someone not on a trial.
        """
        response = models.TrialStatusResponse(
            is_active=False,
            trial_available=True,
            total_days=trial_service.TRIAL_DURATION_DAYS,
        )
        assert (response.day_number, response.days_remaining) == (0, 0)
        assert response.starts_at is None
        assert response.ends_at is None
        assert response.showcase_suggestions == []

    def test_total_days_comes_from_the_service_not_the_client(self):
        response = models.TrialStatusResponse(
            is_active=True,
            trial_available=False,
            day_number=3,
            days_remaining=4,
            total_days=trial_service.TRIAL_DURATION_DAYS,
        )
        assert response.total_days == trial_service.TRIAL_DURATION_DAYS
        # The progress a UI would draw is derivable without a hardcoded denominator.
        assert response.day_number <= response.total_days

    def test_it_serialises_camel_case_for_the_client(self):
        response = models.TrialStatusResponse(
            is_active=False,
            trial_available=True,
            total_days=trial_service.TRIAL_DURATION_DAYS,
            next_trial_available_at=NOW,
        )
        payload = response.model_dump(by_alias=True)
        assert "trialAvailable" in payload
        assert "nextTrialAvailableAt" in payload
        assert "totalDays" in payload

    def test_trial_available_is_required(self):
        """No default, so a future branch cannot omit it the way the old dicts did."""
        assert models.TrialStatusResponse.model_fields["trial_available"].is_required()

    def test_suggestions_validate(self):
        response = models.TrialStatusResponse(
            is_active=True,
            trial_available=False,
            day_number=2,
            days_remaining=5,
            total_days=7,
            showcase_suggestions=[
                models.TrialShowcaseSuggestion(
                    capability_id="adaptive-study-plans",
                    title="Let your plan adapt",
                    description="Schedules more time on your weakest topics.",
                    action_url="/prepare",
                    reason="You have a preparation with 3 focus topics.",
                )
            ],
        )
        assert response.showcase_suggestions[0].capability_id == "adaptive-study-plans"
        # The reason is required: a suggestion without one is an advert.
        assert models.TrialShowcaseSuggestion.model_fields["reason"].is_required()
