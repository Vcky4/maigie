"""Unit tests for circle_gates — plan-aware feature gate predicate.

Covers all gate features with both free and plan-active states.

Run with: ``SKIP_DB_FIXTURE=1 pytest tests/test_circle_gates.py -v``
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("SKIP_DB_FIXTURE", "1")

from src.services.circle_gates import (  # noqa: E402
    CircleFeature,
    CircleGateError,
    CircleGateState,
    check_pinned_resource_limit,
    gate,
)


# ---------------------------------------------------------------------------
# Chat Group Create
# ---------------------------------------------------------------------------


class TestChatGroupCreateGate:
    def test_allows_first_group_on_free(self):
        state = CircleGateState(circle_plan_active=False, chat_group_count=0)
        assert gate(CircleFeature.CHAT_GROUP_CREATE, state) is True

    def test_rejects_second_group_on_free(self):
        state = CircleGateState(circle_plan_active=False, chat_group_count=1)
        with pytest.raises(CircleGateError) as exc_info:
            gate(CircleFeature.CHAT_GROUP_CREATE, state)
        assert exc_info.value.code == "CHAT_GROUPS_REQUIRE_CIRCLE_PLAN"
        assert exc_info.value.status_code == 402

    def test_allows_up_to_10_groups_on_plan(self):
        state = CircleGateState(circle_plan_active=True, chat_group_count=9)
        assert gate(CircleFeature.CHAT_GROUP_CREATE, state) is True

    def test_rejects_11th_group_on_plan(self):
        state = CircleGateState(circle_plan_active=True, chat_group_count=10)
        with pytest.raises(CircleGateError) as exc_info:
            gate(CircleFeature.CHAT_GROUP_CREATE, state)
        assert exc_info.value.code == "CHAT_GROUP_LIMIT_REACHED"
        assert exc_info.value.status_code == 409


# ---------------------------------------------------------------------------
# Group Session Start
# ---------------------------------------------------------------------------


class TestGroupSessionStartGate:
    def test_allows_up_to_3_on_free(self):
        state = CircleGateState(circle_plan_active=False, group_session_count=2)
        assert gate(CircleFeature.GROUP_SESSION_START, state) is True

    def test_rejects_4th_on_free(self):
        state = CircleGateState(circle_plan_active=False, group_session_count=3)
        with pytest.raises(CircleGateError) as exc_info:
            gate(CircleFeature.GROUP_SESSION_START, state)
        assert exc_info.value.code == "GROUP_SESSION_LIMIT_REACHED"
        assert exc_info.value.status_code == 402

    def test_allows_unlimited_on_plan(self):
        state = CircleGateState(circle_plan_active=True, group_session_count=100)
        assert gate(CircleFeature.GROUP_SESSION_START, state) is True


# ---------------------------------------------------------------------------
# DM Open
# ---------------------------------------------------------------------------


class TestDmOpenGate:
    def test_rejects_on_free(self):
        state = CircleGateState(circle_plan_active=False)
        with pytest.raises(CircleGateError) as exc_info:
            gate(CircleFeature.DM_OPEN, state)
        assert exc_info.value.code == "DMS_REQUIRE_CIRCLE_PLAN"

    def test_allows_on_plan(self):
        state = CircleGateState(circle_plan_active=True)
        assert gate(CircleFeature.DM_OPEN, state) is True


# ---------------------------------------------------------------------------
# Banner / Theme
# ---------------------------------------------------------------------------


class TestBannerThemeGate:
    def test_rejects_on_free(self):
        state = CircleGateState(circle_plan_active=False)
        with pytest.raises(CircleGateError) as exc_info:
            gate(CircleFeature.BANNER_THEME, state)
        assert exc_info.value.code == "BANNER_THEME_REQUIRES_CIRCLE_PLAN"

    def test_allows_on_plan(self):
        state = CircleGateState(circle_plan_active=True)
        assert gate(CircleFeature.BANNER_THEME, state) is True


# ---------------------------------------------------------------------------
# Moderator Role
# ---------------------------------------------------------------------------


class TestModeratorRoleGate:
    def test_rejects_on_free(self):
        state = CircleGateState(circle_plan_active=False)
        with pytest.raises(CircleGateError) as exc_info:
            gate(CircleFeature.MODERATOR_ROLE, state)
        assert exc_info.value.code == "MODERATOR_REQUIRES_CIRCLE_PLAN"

    def test_allows_on_plan(self):
        state = CircleGateState(circle_plan_active=True)
        assert gate(CircleFeature.MODERATOR_ROLE, state) is True


# ---------------------------------------------------------------------------
# AI Tutor / Group AI
# ---------------------------------------------------------------------------


class TestAiGate:
    def test_rejects_on_free_without_addon(self):
        state = CircleGateState(circle_plan_active=False, has_any_active_addon=False)
        with pytest.raises(CircleGateError) as exc_info:
            gate(CircleFeature.AI_TUTOR, state)
        assert exc_info.value.code == "AI_REQUIRES_CIRCLE_PLAN_OR_ADDON"

    def test_allows_with_plan(self):
        state = CircleGateState(circle_plan_active=True, has_any_active_addon=False)
        assert gate(CircleFeature.AI_TUTOR, state) is True

    def test_allows_with_addon_only(self):
        state = CircleGateState(circle_plan_active=False, has_any_active_addon=True)
        assert gate(CircleFeature.GROUP_AI, state) is True


# ---------------------------------------------------------------------------
# Version History
# ---------------------------------------------------------------------------


class TestVersionHistoryGate:
    def test_rejects_on_free(self):
        state = CircleGateState(circle_plan_active=False)
        with pytest.raises(CircleGateError) as exc_info:
            gate(CircleFeature.VERSION_HISTORY, state)
        assert exc_info.value.code == "VERSION_HISTORY_REQUIRES_CIRCLE_PLAN"

    def test_allows_on_plan(self):
        state = CircleGateState(circle_plan_active=True)
        assert gate(CircleFeature.VERSION_HISTORY, state) is True


# ---------------------------------------------------------------------------
# Detailed Analytics
# ---------------------------------------------------------------------------


class TestDetailedAnalyticsGate:
    def test_rejects_on_free(self):
        state = CircleGateState(circle_plan_active=False)
        with pytest.raises(CircleGateError) as exc_info:
            gate(CircleFeature.DETAILED_ANALYTICS, state)
        assert exc_info.value.code == "DETAILED_ANALYTICS_REQUIRES_CIRCLE_PLAN"

    def test_allows_on_plan(self):
        state = CircleGateState(circle_plan_active=True)
        assert gate(CircleFeature.DETAILED_ANALYTICS, state) is True


# ---------------------------------------------------------------------------
# Featured Eligibility
# ---------------------------------------------------------------------------


class TestFeaturedEligibilityGate:
    def test_rejects_on_free(self):
        state = CircleGateState(circle_plan_active=False)
        with pytest.raises(CircleGateError) as exc_info:
            gate(CircleFeature.FEATURED_ELIGIBILITY, state)
        assert exc_info.value.code == "FEATURED_REQUIRES_CIRCLE_PLAN"

    def test_allows_on_plan(self):
        state = CircleGateState(circle_plan_active=True)
        assert gate(CircleFeature.FEATURED_ELIGIBILITY, state) is True


# ---------------------------------------------------------------------------
# Pinned Resource Limit
# ---------------------------------------------------------------------------


class TestPinnedResourceLimit:
    def test_allows_under_limit_on_free(self):
        state = CircleGateState(circle_plan_active=False)
        assert check_pinned_resource_limit(state, 4) is True

    def test_rejects_at_limit_on_free(self):
        state = CircleGateState(circle_plan_active=False)
        with pytest.raises(CircleGateError) as exc_info:
            check_pinned_resource_limit(state, 5)
        assert exc_info.value.code == "PINNED_RESOURCE_LIMIT_REACHED"

    def test_allows_unlimited_on_plan(self):
        state = CircleGateState(circle_plan_active=True)
        assert check_pinned_resource_limit(state, 100) is True


# ---------------------------------------------------------------------------
# Unknown feature (fail open)
# ---------------------------------------------------------------------------


class TestUnknownFeature:
    def test_allows_unknown_feature(self):
        state = CircleGateState()
        assert gate("some_future_feature", state) is True
