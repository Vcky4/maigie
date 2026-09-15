"""Referral list shaping — names, stages, and the day-count clamp. No database."""

from datetime import UTC, datetime

from src.domains.billing.models import ReferralsResponse
from src.domains.billing.services.referral_rewards_service import (
    referral_display_name,
    referral_progress,
)


class TestReferralDisplayName:
    def test_uses_the_first_name(self):
        assert referral_display_name("Ada Lovelace") == "Ada"

    def test_falls_back_when_there_is_no_name(self):
        assert referral_display_name(None) == "Friend"
        assert referral_display_name("   ") == "Friend"


class TestReferralProgress:
    def test_pending_caps_at_the_required_days(self):
        status, days = referral_progress(distinct_days=12, qualified=False, required=7)
        assert status == "pending"
        assert days == 7

    def test_qualified_reports_the_required_days_even_without_usage(self):
        status, days = referral_progress(distinct_days=0, qualified=True, required=7)
        assert status == "qualified"
        assert days == 7

    def test_a_partial_week_stays_pending(self):
        status, days = referral_progress(distinct_days=3, qualified=False, required=7)
        assert status == "pending"
        assert days == 3


class TestReferralsResponseShape:
    def test_accepts_the_stats_payload_the_route_returns(self):
        now = datetime.now(UTC)
        body = ReferralsResponse.model_validate(
            {
                "referralCode": "ABC12345",
                "totalReferrals": 1,
                "pendingReferrals": 1,
                "qualifiedReferrals": 0,
                "requiredStudyDays": 7,
                "pointsPerQualifiedReferral": 100,
                "referrals": [
                    {
                        "id": "r1",
                        "displayName": "Ada",
                        "status": "pending",
                        "studyDays": 3,
                        "requiredStudyDays": 7,
                        "joinedAt": now,
                        "qualifiedAt": None,
                        "pointsAwarded": None,
                    }
                ],
            }
        )
        assert body.referrals[0].display_name == "Ada"
        assert body.pending_referrals == 1
