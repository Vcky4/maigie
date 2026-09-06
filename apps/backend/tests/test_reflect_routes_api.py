"""Phase 6.5's new routes over real HTTP, against a real database.

Every other test for this phase calls the services directly, which cannot see the layer these check:
FastAPI's own path matching, the `response_model` serialisation, query validation, and the ownership
`404`s. Three of those have already gone wrong in this programme — a route declared in the wrong order,
a model referenced before it was defined, and a naive datetime reaching a comparison — and none of them
is visible from a service-level test.

Needs Postgres, so these skip unless `RUN_DB_TESTS=1` and `DATABASE_URL` are both set. Run the file on
its own; several other modules set `SKIP_DB_FIXTURE` at import time.

**A brand-new learner is the point, not a limitation.** Every empty-state path — `headline: "none"`, an
`averageProgress` of `null` rather than `0`, an evidence list of `[]` for an unlinked goal — is a rule
this phase argued for explicitly, and a fresh account is the only way to reach them honestly.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient

PROGRESS = "/api/v1/progress"
LEARNING = "/api/v1/learning"


@pytest.fixture
async def goal_id(client: AsyncClient, auth_headers: dict[str, str]) -> str:
    """A manual goal with a deadline, owned by the fixture learner."""
    target = datetime.now(UTC) + timedelta(days=14)
    created = await client.post(
        f"{PROGRESS}/goals",
        headers=auth_headers,
        json={
            "title": "Reach a steady weekly rhythm",
            "description": "Built by the route contract tests.",
            "targetDate": target.isoformat(),
            "metricKind": "manual",
            "targetValue": 100.0,
            "unit": "percent",
        },
    )
    assert created.status_code == 201, created.text
    return created.json()["id"]


class TestGoalsSummaryRoute:
    async def test_summary_is_not_matched_as_a_goal_id(
        self, client: AsyncClient, auth_headers: dict[str, str]
    ):
        """FastAPI matches in declaration order, so the wrong order answers `404` for this path.

        The unit test for this reads the source; this one proves the running application agrees.
        """
        response = await client.get(f"{PROGRESS}/goals/summary", headers=auth_headers)

        assert response.status_code == 200, response.text
        body = response.json()
        assert set(body) >= {
            "active",
            "completed",
            "atRisk",
            "dueSoon",
            "overdue",
            "averageProgress",
            "headline",
            "momentum",
            "momentumTracked",
        }

    async def test_a_learner_with_no_goals_reads_none_and_a_null_average(
        self, client: AsyncClient, auth_headers: dict[str, str]
    ):
        """No goals is not the same as no progress (Decision I), and not "steady at 0%"."""
        body = (await client.get(f"{PROGRESS}/goals/summary", headers=auth_headers)).json()

        assert body["headline"] == "none"
        assert body["averageProgress"] is None
        assert body["active"] == 0

    async def test_creating_a_goal_moves_the_headline_off_none(
        self, client: AsyncClient, auth_headers: dict[str, str], goal_id: str
    ):
        body = (await client.get(f"{PROGRESS}/goals/summary", headers=auth_headers)).json()

        assert body["active"] == 1
        assert body["headline"] != "none"
        # A fortnight away and freshly created, so it is neither overdue nor yet inside the
        # due-soon window's seven days.
        assert body["headline"] in ("steady", "at_risk", "due_soon")
        assert body["averageProgress"] == 0.0

    async def test_the_momentum_axis_is_drawn_even_with_no_plan(
        self, client: AsyncClient, auth_headers: dict[str, str], goal_id: str
    ):
        """An empty chart renders as empty rather than as absent."""
        body = (await client.get(f"{PROGRESS}/goals/summary", headers=auth_headers)).json()

        assert len(body["momentum"]) == 4
        assert all(week["planned"] == 0 for week in body["momentum"])
        assert body["momentumTracked"] is False
        assert all(set(week) == {"weekStart", "planned", "completed"} for week in body["momentum"])

    async def test_the_momentum_window_is_a_bounded_query_parameter(
        self, client: AsyncClient, auth_headers: dict[str, str]
    ):
        wide = await client.get(
            f"{PROGRESS}/goals/summary", headers=auth_headers, params={"momentumWeeks": 12}
        )
        assert wide.status_code == 200
        assert len(wide.json()["momentum"]) == 12

        too_wide = await client.get(
            f"{PROGRESS}/goals/summary", headers=auth_headers, params={"momentumWeeks": 27}
        )
        # `400`, not `422`: this application converts `RequestValidationError` to a bad request in
        # `shared.exceptions.handlers.validation_error_handler`, so every route answers that way.
        assert too_wide.status_code == 400

    async def test_it_requires_a_caller(self, client: AsyncClient):
        assert (await client.get(f"{PROGRESS}/goals/summary")).status_code in (401, 403)


class TestGoalSubResourceRoutes:
    """The four per-goal reads this phase added, and their ownership behaviour."""

    #: The four per-goal reads Phase 6.5 added.
    PATHS = ("history", "momentum", "evidence", "insight")

    # Looped rather than parametrized, deliberately. `auth_headers` signs up a fresh learner per test
    # and skips the test if that signup fails, so twelve parametrized cases are twelve accounts and
    # twelve chances to skip silently — which is how the first run of this file lost eighteen tests.
    # One account, four assertions.

    async def test_each_answers_for_its_owner(
        self, client: AsyncClient, auth_headers: dict[str, str], goal_id: str
    ):
        for leaf in self.PATHS:
            response = await client.get(f"{PROGRESS}/goals/{goal_id}/{leaf}", headers=auth_headers)

            assert response.status_code == 200, f"{leaf}: {response.text}"
            assert response.json()["goalId"] == goal_id, leaf

    async def test_an_unknown_goal_is_404_not_500(
        self, client: AsyncClient, auth_headers: dict[str, str]
    ):
        """`404` rather than `403`, so a goal id is not probeable."""
        for leaf in self.PATHS:
            response = await client.get(
                f"{PROGRESS}/goals/does-not-exist/{leaf}", headers=auth_headers
            )

            assert response.status_code == 404, f"{leaf}: {response.status_code} {response.text}"

    async def test_each_requires_a_caller(self, client: AsyncClient):
        for leaf in self.PATHS:
            response = await client.get(f"{PROGRESS}/goals/some-goal/{leaf}")

            assert response.status_code in (401, 403), f"{leaf}: {response.status_code}"

    async def test_history_starts_empty_and_says_so(
        self, client: AsyncClient, auth_headers: dict[str, str], goal_id: str
    ):
        """Decision Y: the table fills from the day the nightly task first runs, with no backfill.

        `firstCapturedOn` is what lets an empty `points` be told apart — nothing recorded yet, versus
        history that falls outside the requested window.
        """
        body = (
            await client.get(f"{PROGRESS}/goals/{goal_id}/history", headers=auth_headers)
        ).json()

        assert body["points"] == []
        assert body["capturedDays"] == 0
        assert body["firstCapturedOn"] is None

    async def test_evidence_for_an_unlinked_goal_is_empty_not_general_activity(
        self, client: AsyncClient, auth_headers: dict[str, str], goal_id: str
    ):
        """Falling back to everything the learner did would attach unrelated work to a goal."""
        body = (
            await client.get(f"{PROGRESS}/goals/{goal_id}/evidence", headers=auth_headers)
        ).json()

        assert body["items"] == []
        assert body["linkedCourseId"] is None
        assert body["linkedTopicId"] is None
        assert body["linkedPrepId"] is None

    async def test_evidence_limit_is_bounded(
        self, client: AsyncClient, auth_headers: dict[str, str], goal_id: str
    ):
        ok = await client.get(
            f"{PROGRESS}/goals/{goal_id}/evidence", headers=auth_headers, params={"limit": 50}
        )
        assert ok.status_code == 200

        too_many = await client.get(
            f"{PROGRESS}/goals/{goal_id}/evidence", headers=auth_headers, params={"limit": 51}
        )
        assert too_many.status_code == 400

    async def test_momentum_reports_completion_as_untracked_rather_than_as_zero_done(
        self, client: AsyncClient, auth_headers: dict[str, str], goal_id: str
    ):
        """`completedAt` reads null for every learner until they mark a block.

        "You completed nothing" and "nothing is being tracked yet" are indistinguishable from the
        numbers alone, and only one of them is true (Decision Y).
        """
        body = (
            await client.get(f"{PROGRESS}/goals/{goal_id}/momentum", headers=auth_headers)
        ).json()

        assert body["completionTracked"] is False
        assert all(week["completed"] == 0 for week in body["points"])

    async def test_a_goal_with_a_deadline_serialises_its_derived_pace(
        self, client: AsyncClient, auth_headers: dict[str, str], goal_id: str
    ):
        """The regression for the live `500`.

        `Goal.targetDate` is `timestamp without time zone` while the ORM declares `timezone=True`, so
        it arrives naive and every pace predicate compared it against an aware `datetime.now(UTC)`.
        This route raised `TypeError` for any goal that had a deadline at all.
        """
        response = await client.get(f"{PROGRESS}/goals/{goal_id}", headers=auth_headers)

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["targetDate"] is not None
        assert body["statusLabel"] in ("ON_TRACK", "NEEDS_ATTENTION", "COMPLETED")

    async def test_the_goals_list_serialises_too(
        self, client: AsyncClient, auth_headers: dict[str, str], goal_id: str
    ):
        """The same `500`, on the route a client actually opens first."""
        response = await client.get(f"{PROGRESS}/goals", headers=auth_headers)

        assert response.status_code == 200, response.text
        # `goals`, not `items` — this envelope predates the `items` convention the newer list
        # responses use, and the goals client will be reading this name.
        assert any(goal["id"] == goal_id for goal in response.json()["goals"])


class TestGoalInsightRoute:
    async def test_free_gets_a_notice_and_a_200_rather_than_a_403(
        self, client: AsyncClient, auth_headers: dict[str, str], goal_id: str
    ):
        """Decision Z. Every figure on the page is free and only the interpretation is paid, so the
        panel must become an upgrade card rather than an error over figures that are all fine.

        A fresh learner is on Free, which is what makes this reachable without a fixture.
        """
        response = await client.get(f"{PROGRESS}/goals/{goal_id}/insight", headers=auth_headers)

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["insight"] is None
        assert body["nextAction"] is None
        assert body["locked"] is not None
        assert body["locked"]["locked"] is True
        assert body["locked"]["capability"] == "reflection"
        assert body["locked"]["upgradeUrl"] == "/subscription"
        assert body["locked"]["reason"]
        assert body["locked"]["upgradeValue"]


class TestGrowthRoutes:
    """The two growth reads this phase added, plus the fields it added to the existing ones."""

    async def test_drivers_answers_and_is_locked_for_free(
        self, client: AsyncClient, auth_headers: dict[str, str]
    ):
        response = await client.get(
            f"{LEARNING}/growth/drivers", headers=auth_headers, params={"range": "30d"}
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["range"] == "30d"
        assert body["items"] == []
        assert body["locked"]["capability"] == "reflection"

    async def test_a_range_above_the_plan_reports_the_range_lock_not_the_prose_lock(
        self, client: AsyncClient, auth_headers: dict[str, str]
    ):
        """Two locks can apply and the range one wins: an empty series has no drivers to explain."""
        body = (
            await client.get(
                f"{LEARNING}/growth/drivers", headers=auth_headers, params={"range": "90d"}
            )
        ).json()

        assert body["locked"] is not None
        assert body["locked"]["capability"] == "behaviour_analytics"
        assert body["items"] == []

    async def test_drivers_rejects_a_range_outside_the_vocabulary(
        self, client: AsyncClient, auth_headers: dict[str, str]
    ):
        response = await client.get(
            f"{LEARNING}/growth/drivers", headers=auth_headers, params={"range": "5y"}
        )

        assert response.status_code == 400

    async def test_drivers_requires_a_caller(self, client: AsyncClient):
        assert (await client.get(f"{LEARNING}/growth/drivers")).status_code in (401, 403)

    async def test_subject_insight_404s_for_a_course_that_is_not_the_learners(
        self, client: AsyncClient, auth_headers: dict[str, str]
    ):
        """`404`, not `403` — a course id must not be probeable.

        Reached before the tier gate, because ownership is established by the detail read that
        produces the figures rather than by a second check that could drift from it.
        """
        response = await client.get(
            f"{LEARNING}/growth/subjects/not-a-course-of-mine/insight", headers=auth_headers
        )

        assert response.status_code == 404, response.text

    async def test_trends_publishes_the_milestone_list(
        self, client: AsyncClient, auth_headers: dict[str, str]
    ):
        body = (
            await client.get(
                f"{LEARNING}/growth/trends", headers=auth_headers, params={"range": "30d"}
            )
        ).json()

        assert "milestones" in body
        assert isinstance(body["milestones"], list)

    async def test_subjects_publishes_the_activity_object(
        self, client: AsyncClient, auth_headers: dict[str, str]
    ):
        response = await client.get(f"{LEARNING}/growth/subjects", headers=auth_headers)

        assert response.status_code == 200, response.text
        assert "items" in response.json()

    async def test_daily_counts_publishes_the_type_mix_and_the_vocabulary(
        self, client: AsyncClient, auth_headers: dict[str, str]
    ):
        """`byType` shares the paged read's filters, so its counts reconcile with `total`."""
        response = await client.get(f"{LEARNING}/activity-feed/daily-counts", headers=auth_headers)

        assert response.status_code == 200, response.text
        body = response.json()
        assert "byType" in body
        assert "availableTypes" in body
        assert sum(entry["count"] for entry in body["byType"]) == body["total"]
