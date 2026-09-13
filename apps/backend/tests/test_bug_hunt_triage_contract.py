"""The triage surface's contract and its permission split.

The claim worth pinning above all others: **`TriageRequest` has no amount field.** A triager sets category
and severity, the season's matrix decides the kobo, and there is nowhere in the request to put a figure. If
somebody later adds one "for flexibility", every amount in the programme stops tracing to a published rule
and this test is what says so.

The second is the staff/super-admin line. Triage is deliberately **staff**, because the amount is not
theirs to choose (Decision D) and a two-click money flow across a 14-day season would not get used.
Suspension is **super admin**, because it is the one participation decision a later approval cannot undo.
Those two guards differ by one word in a type alias, which is exactly the kind of thing worth asserting
against the wired dependency rather than reading.

Run with: pytest tests/test_bug_hunt_triage_contract.py -v
"""

import pytest

from src.app import create_app
from src.domains.bug_hunt import rewards
from src.domains.bug_hunt.db_models import SUBMISSION_STATUSES
from src.domains.bug_hunt.services import triage_service

ADMIN = "/api/v1/admin/bug-hunt"


@pytest.fixture(scope="module")
def app():
    return create_app()


@pytest.fixture(scope="module")
def schema(app) -> dict:
    return app.openapi()


TRIAGE_PATHS = [
    f"{ADMIN}/stats",
    f"{ADMIN}/participants",
    f"{ADMIN}/participants/{{participant_id}}",
    f"{ADMIN}/participants/{{participant_id}}/decision",
    f"{ADMIN}/participants/{{participant_id}}/suspend",
    f"{ADMIN}/submissions",
    f"{ADMIN}/submissions/{{submission_id}}",
    f"{ADMIN}/submissions/{{submission_id}}/triage",
    f"{ADMIN}/known-issues",
]


class TestTheTriageSurfaceIsMounted:
    @pytest.mark.parametrize("path", TRIAGE_PATHS)
    def test_endpoint_is_mounted(self, path, app):
        assert path in {route.path for route in app.routes}

    def test_known_issues_is_not_swallowed_by_the_submission_id_route(self, app):
        """`/known-issues` sits under a different prefix from `/submissions/{id}`, so there is no collision
        to order around — asserted so that moving it under `/submissions/` later does not silently become a
        lookup for a finding called "known-issues"."""
        paths = {route.path for route in app.routes}
        assert f"{ADMIN}/known-issues" in paths
        assert f"{ADMIN}/submissions/known-issues" not in paths


class TestNoAmountCrossesTheTriageBoundary:
    """The single most important property of this surface."""

    @pytest.mark.parametrize(
        "forbidden", ["awardKobo", "amountKobo", "amount", "award", "rewardKobo", "payKobo"]
    )
    def test_the_triage_request_has_no_amount_field(self, forbidden, schema):
        properties = schema["components"]["schemas"]["TriageRequest"]["properties"]
        assert forbidden not in properties

    def test_the_triage_request_carries_only_a_grading_and_words(self, schema):
        """Enumerated rather than checked for absences, so adding *any* new field to this model fails here
        and gets a second look. Money paths should be tedious to widen."""
        properties = set(schema["components"]["schemas"]["TriageRequest"]["properties"])
        assert properties == {
            "status",
            "category",
            "type",
            "severity",
            "duplicateOfId",
            "publicResponse",
            "adminNotes",
        }

    def test_the_response_reports_the_amount_the_matrix_decided(self, schema):
        properties = schema["components"]["schemas"]["TriageResponse"]["properties"]
        assert "awardKobo" in properties
        assert "matrixKobo" in properties

    def test_the_response_can_express_owed_but_unpaid(self, schema):
        """Two amounts, because they can differ and the difference matters.

        `matrixKobo` is what the grading is worth; `awardKobo` is what reached the ledger. A single number
        could not express "accepted, owed 2,000, paid nothing because the season is out of budget", and that
        is exactly the state an operator has to see and act on.
        """
        properties = schema["components"]["schemas"]["TriageResponse"]["properties"]
        assert "awardBlocked" in properties
        assert "awardMessage" in properties
        for nullable in ("awardBlocked", "awardMessage"):
            variants = {v.get("type") for v in properties[nullable].get("anyOf", [])}
            assert "null" in variants, nullable


class TestThePermissionSplit:
    @pytest.mark.parametrize("path", TRIAGE_PATHS)
    def test_every_triage_endpoint_requires_a_token(self, path, schema):
        methods = schema["paths"][path]
        for method, operation in methods.items():
            assert operation.get("security"), f"{method.upper()} {path}"

    def test_triage_is_staff_not_super_admin(self, app):
        """Decision D. A content manager can work the queue because the amount is not theirs to choose, and
        a queue that needs a super admin across a 14-day season is a queue that goes unworked."""
        from src.shared.auth.dependencies import get_staff_user, get_super_admin_user

        staff_only = {
            (f"{ADMIN}/submissions/{{submission_id}}/triage", "POST"),
            (f"{ADMIN}/participants/{{participant_id}}/decision", "POST"),
            (f"{ADMIN}/stats", "GET"),
            (f"{ADMIN}/known-issues", "GET"),
        }
        seen = set()
        for route in app.routes:
            for method in getattr(route, "methods", None) or set():
                key = (getattr(route, "path", None), method)
                if key not in staff_only:
                    continue
                seen.add(key)
                calls = [d.call for d in getattr(route.dependant, "dependencies", []) if d.call]
                assert get_staff_user in calls, f"{key} should be staff-guarded"
                assert get_super_admin_user not in calls, f"{key} should not need a super admin"
        assert seen == staff_only, f"missing: {staff_only - seen}"

    def test_suspension_is_super_admin(self, app):
        """The one participation decision a later approval cannot undo — and confiscation is the obvious
        next thing somebody would ask for, so the guard is set where that conversation has to happen."""
        from src.shared.auth.dependencies import get_super_admin_user

        target = (f"{ADMIN}/participants/{{participant_id}}/suspend", "POST")
        found = False
        for route in app.routes:
            for method in getattr(route, "methods", None) or set():
                if (getattr(route, "path", None), method) != target:
                    continue
                found = True
                calls = [d.call for d in getattr(route.dependant, "dependencies", []) if d.call]
                assert get_super_admin_user in calls
        assert found


class TestTheStaffViewIsSeparateFromTheReporterView:
    def test_the_staff_view_carries_the_private_note(self, schema):
        assert "adminNotes" in schema["components"]["schemas"]["SubmissionAdminView"]["properties"]

    def test_the_reporter_view_does_not(self, schema):
        """Two models rather than one with a flag. A boolean parameter is one inverted condition away from
        showing a triager's note to the person it is about; two models are not."""
        assert "adminNotes" not in schema["components"]["schemas"]["SubmissionView"]["properties"]

    def test_the_staff_view_carries_the_reporter_identity(self, schema):
        """A queue of opaque ids is not a queue anybody can work."""
        properties = schema["components"]["schemas"]["SubmissionAdminView"]["properties"]
        assert "email" in properties
        assert "triagedByUserId" in properties

    def test_the_reporter_view_carries_no_identity_of_anyone(self, schema):
        """A tester reads their own findings; there is nobody else to name, and naming a triager would
        personalise a decision that is the programme's, not an individual's."""
        properties = schema["components"]["schemas"]["SubmissionView"]["properties"]
        assert "email" not in properties
        assert "triagedByUserId" not in properties


class TestTheDetailScreenAnswersTheTriagersQuestions:
    def test_it_carries_the_findings_own_season_reward_table(self, schema):
        """Not the current season's. A late Season 1 finding is paid at Season 1's rates, so those are the
        rates the person grading it must be looking at."""
        assert (
            "rewardMatrix" in schema["components"]["schemas"]["SubmissionAdminDetail"]["properties"]
        )

    def test_it_answers_have_i_seen_this_and_is_this_reporter_reliable(self, schema):
        properties = schema["components"]["schemas"]["SubmissionAdminDetail"]["properties"]
        assert "duplicateOfTitle" in properties
        assert "reporterSubmissionCount" in properties

    def test_the_participant_detail_spans_seasons(self, schema):
        properties = schema["components"]["schemas"]["ParticipantAdminDetail"]["properties"]
        assert "history" in properties
        assert "earnedLifetimeKobo" in properties
        assert "earnedThisSeasonKobo" in properties


class TestKnownIssueIsDistinctFromDuplicate:
    def test_both_statuses_exist(self):
        assert {"duplicate", "known_issue"} <= SUBMISSION_STATUSES

    def test_a_known_issue_carries_the_season_it_came_from(self, schema):
        """ "Reported in Season 1 and still open" is the sentence that keeps the blame for our backlog off the
        reporter, and it cannot be written without the season number."""
        assert "seasonNumber" in schema["components"]["schemas"]["KnownIssueView"]["properties"]

    def test_a_finding_cannot_be_returned_to_untouched(self):
        """`submitted` is the state a finding arrives in. Allowing a triager to set it would erase the fact
        that somebody looked; reopening is `in_review`."""
        assert "submitted" not in triage_service.TRIAGEABLE_STATUSES
        assert "in_review" in triage_service.TRIAGEABLE_STATUSES
        assert triage_service.TRIAGEABLE_STATUSES == SUBMISSION_STATUSES - {"submitted"}


class TestGradingVocabularies:
    def test_types_are_partitioned_by_category(self):
        """A `bug` typed `suggestion` means the triager picked one dropdown and not the other, and the pair
        is what decides the finding."""
        bug_types = triage_service.TYPES_BY_CATEGORY["bug"]
        feedback_types = triage_service.TYPES_BY_CATEGORY["feedback"]
        assert bug_types.isdisjoint(feedback_types)
        assert "crash" in bug_types
        assert "suggestion" in feedback_types

    def test_severities_are_partitioned_by_category(self):
        assert rewards.SEVERITIES_BY_CATEGORY["bug"].isdisjoint(
            rewards.SEVERITIES_BY_CATEGORY["feedback"]
        )

    def test_every_category_has_both_a_type_set_and_a_severity_set(self):
        assert set(triage_service.TYPES_BY_CATEGORY) == set(rewards.SEVERITIES_BY_CATEGORY)


class TestTheStatsShape:
    def test_the_rates_are_nullable(self, schema):
        """`null` before anything is decided. A displayed acceptance rate of zero reads as "we reject
        everything", which on day one is false and is the worst possible thing to show."""
        properties = schema["components"]["schemas"]["TriageStatsResponse"]["properties"]
        for field in ("acceptanceRate", "medianTriageHours"):
            variants = {v.get("type") for v in properties[field].get("anyOf", [])}
            assert "null" in variants, field

    def test_it_reports_the_two_queues_by_name(self, schema):
        assert "queues" in schema["components"]["schemas"]["TriageStatsResponse"]["properties"]

    def test_it_reports_spend_against_budget(self, schema):
        properties = schema["components"]["schemas"]["TriageStatsResponse"]["properties"]
        for field in ("budgetKobo", "awardedKobo", "remainingBudgetKobo"):
            assert field in properties

    def test_the_platform_list_cannot_drift_from_the_check_constraint(self):
        assert triage_service.platforms() == ["android", "ios", "web"]
