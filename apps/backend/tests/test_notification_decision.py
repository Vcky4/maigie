"""The deterministic decision engine.

These pin the three rules the engine must never break — it never expands the budget, never relaxes
consent, never sends in quiet hours — and the exact precedence between them, because a later
learned layer will be compared against this baseline and can only be trusted if the baseline is
itself trustworthy and unchanging. They are deliberately about the decision, not the wiring: the
engine is pure, so it can be exercised directly with no database.
"""

from __future__ import annotations

from datetime import UTC, datetime, time
from zoneinfo import ZoneInfo

from src.domains.notifications.decision import (
    PRIORITY_TIME_CRITICAL,
    DecisionInput,
    PlanDecision,
    decide,
    resolve,
)
from src.shared.time import LearnerTimezone

LAGOS = LearnerTimezone(
    zone=ZoneInfo("Africa/Lagos"), name="Africa/Lagos", is_known=True, source="MANUAL"
)
# 12:00 UTC is 13:00 in Lagos — the middle of the day, outside any normal quiet window.
NOON = datetime(2026, 9, 3, 12, tzinfo=UTC)


def _input(**overrides) -> DecisionInput:
    base = {
        "notification_type": "learning.study_session_reminder",
        "priority": 5,
        "urgency": "HIGH",
        "category": "LEARNING",
        "intelligence_scope": "TIMING_CHANNEL",
        "moment": NOON,
        "timezone": LAGOS,
        "quiet_from": None,
        "quiet_to": None,
        "allowance_spent": False,
        "default_channels": ("IN_APP", "MOBILE_PUSH", "WEB_PUSH"),
        "allowed_channels": ("IN_APP", "MOBILE_PUSH", "WEB_PUSH", "EMAIL"),
        "email_planned": False,
        "group_key": None,
        "groupable": False,
    }
    base.update(overrides)
    return DecisionInput(**base)


class TestTiming:
    def test_a_clear_notification_goes_now(self) -> None:
        decision = decide(_input())
        assert decision.status == "PENDING"
        assert decision.eligible_at == NOON
        assert "IMMEDIATE" in decision.reason_codes

    def test_quiet_hours_defer_to_the_end_of_the_window(self) -> None:
        # A 22:00–07:00 Lagos window; NOON is outside it, so use a night moment.
        night = datetime(2026, 9, 3, 23, tzinfo=UTC)  # 00:00 Lagos, inside the window
        decision = decide(_input(moment=night, quiet_from=time(22, 0), quiet_to=time(7, 0)))
        assert decision.status == "QUEUED"
        assert decision.eligible_at > night
        assert "QUIET_HOURS_DEFERRED" in decision.reason_codes

    def test_a_spent_budget_defers_to_the_next_local_day(self) -> None:
        decision = decide(_input(allowance_spent=True, priority=5))
        assert decision.status == "QUEUED"
        assert decision.eligible_at > NOON
        assert "ATTENTION_BUDGET_DEFERRED" in decision.reason_codes

    def test_a_time_critical_message_bypasses_a_spent_budget(self) -> None:
        decision = decide(_input(allowance_spent=True, priority=PRIORITY_TIME_CRITICAL))
        assert decision.status == "PENDING"
        assert decision.eligible_at == NOON
        assert "TIME_CRITICAL_BYPASSES_BUDGET" in decision.reason_codes

    def test_quiet_hours_outrank_a_time_critical_message(self) -> None:
        # Precedence is load-bearing: a 3am push helps no one, even an urgent one.
        night = datetime(2026, 9, 3, 23, tzinfo=UTC)
        decision = decide(
            _input(
                moment=night,
                quiet_from=time(22, 0),
                quiet_to=time(7, 0),
                priority=PRIORITY_TIME_CRITICAL,
                allowance_spent=True,
            )
        )
        assert decision.status == "QUEUED"
        assert "QUIET_HOURS_DEFERRED" in decision.reason_codes


class TestChannels:
    def test_in_app_is_always_planned(self) -> None:
        decision = decide(_input(default_channels=("IN_APP",)))
        assert decision.channels == ("IN_APP",)

    def test_push_channels_come_from_the_type_default(self) -> None:
        decision = decide(_input())
        assert set(decision.channels) == {"IN_APP", "MOBILE_PUSH", "WEB_PUSH"}

    def test_email_is_planned_only_when_consent_resolved_it(self) -> None:
        assert "EMAIL" not in decide(_input(email_planned=False)).channels
        with_email = decide(_input(email_planned=True, allowed_channels=("IN_APP", "EMAIL")))
        assert "EMAIL" in with_email.channels
        assert "EMAIL_CONSENTED" in with_email.reason_codes

    def test_a_channel_outside_the_allowed_ceiling_is_never_planned(self) -> None:
        # The engine may not exceed the taxonomy's allowed set even if a default names more.
        decision = decide(
            _input(
                default_channels=("IN_APP", "MOBILE_PUSH", "WEB_PUSH"),
                allowed_channels=("IN_APP",),
            )
        )
        assert decision.channels == ("IN_APP",)

    def test_grouping_is_recorded_when_the_type_groups(self) -> None:
        decision = decide(_input(group_key="conv-1", groupable=True))
        assert "GROUPED" in decision.reason_codes


class TestShadowMode:
    def test_no_proposal_applies_the_baseline(self) -> None:
        baseline = decide(_input())
        resolved = resolve(baseline, proposal=None, shadow_only=True)
        assert resolved.applied is baseline
        assert resolved.mode == "BASELINE_ONLY"
        assert resolved.used_fallback is True
        assert resolved.divergences == ()
        assert "MODE_BASELINE_ONLY" in resolved.reason_codes()

    def test_a_shadow_proposal_is_recorded_but_not_applied(self) -> None:
        baseline = decide(_input())
        # A proposal that would send later and drop a channel — the baseline must still win.
        proposal = PlanDecision(
            status="QUEUED",
            eligible_at=NOON.replace(hour=15),
            channels=("IN_APP",),
            reason_codes=("MODEL_DELAY",),
        )
        resolved = resolve(baseline, proposal=proposal, shadow_only=True)
        assert resolved.applied is baseline, "shadow mode must never change what the learner gets"
        assert resolved.mode == "SHADOW"
        assert resolved.used_fallback is True
        assert "DIVERGE_TIMING" in resolved.divergences
        assert "DIVERGE_CHANNELS" in resolved.divergences

    def test_a_live_proposal_is_applied(self) -> None:
        baseline = decide(_input())
        proposal = PlanDecision(
            status="QUEUED",
            eligible_at=NOON.replace(hour=15),
            channels=("IN_APP",),
            reason_codes=("MODEL_DELAY",),
        )
        resolved = resolve(baseline, proposal=proposal, shadow_only=False)
        assert resolved.applied is proposal
        assert resolved.mode == "LIVE"
        assert resolved.used_fallback is False

    def test_an_identical_proposal_shows_no_divergence(self) -> None:
        baseline = decide(_input())
        # Same decision content, different reason codes — divergence is about the plan, not why.
        twin = PlanDecision(
            status=baseline.status,
            eligible_at=baseline.eligible_at,
            channels=baseline.channels,
            reason_codes=("MODEL_AGREES",),
        )
        assert resolve(baseline, proposal=twin, shadow_only=True).divergences == ()


class TestAuditShape:
    def test_the_snapshot_carries_policy_inputs_and_no_content(self) -> None:
        decision_input = _input()
        snapshot = decide(decision_input).input_snapshot(decision_input)
        assert snapshot["notificationType"] == "learning.study_session_reminder"
        assert snapshot["timezone"] == "Africa/Lagos"
        # No title, body, or entity id should ever be in the snapshot.
        text = str(snapshot).lower()
        assert "title" not in text and "body" not in text

    def test_the_decision_record_names_status_time_and_channels(self) -> None:
        decision = decide(_input())
        record = decision.decision_record()
        assert record["status"] == "PENDING"
        assert "eligibleAt" in record
        assert "IN_APP" in record["channels"]
