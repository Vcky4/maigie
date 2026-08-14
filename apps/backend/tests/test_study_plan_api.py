"""Study plan routes: contract, ownership, and the cross-user write.

Needs Postgres, so these skip when ``DATABASE_URL`` is unset. Run the file on its own —
several other modules set ``SKIP_DB_FIXTURE`` at import time, which disables the
database fixture for the whole run.

Plan creation is LLM-backed, so these tests build plans through the API only where that
is the thing under test; elsewhere they insert rows directly, which keeps them from
depending on a model response.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from httpx import AsyncClient

BASE = "/api/v1/learning"


async def _login(client: AsyncClient) -> dict[str, str]:
    """Sign up, activate and log in an additional learner, for cross-user checks."""
    from sqlalchemy import update as sa_update

    from src.domains.identity.db_models import User
    from src.shared.database.session import get_session_factory

    email = f"plan_{uuid.uuid4()}@example.com"
    password = "StrongPassword123!"
    signup = await client.post(
        "/api/v1/auth/signup",
        json={"email": email, "password": password, "name": "Plan Learner"},
    )
    # `fail`, not `skip`. The `auth_headers` fixture already proved the database is
    # reachable, so a failure here is a broken helper — and a helper that skips turns
    # every ownership test in this file green without running any of them, which is how
    # the conftest signup bug went unnoticed for as long as it did.
    assert signup.status_code in (200, 201), f"signup failed: {signup.status_code} {signup.text}"

    factory = get_session_factory()
    async with factory() as session:
        await session.execute(sa_update(User).where(User.email == email).values(is_active=True))
        await session.commit()

    login = await client.post(
        "/api/v1/auth/login/json", json={"email": email, "password": password}
    )
    assert login.status_code == 200, login.text
    token = login.json().get("access_token") or login.json().get("accessToken")
    assert token, f"no access token in login response: {login.text}"
    return {"Authorization": f"Bearer {token}"}


async def _user_id_for(headers: dict[str, str], client: AsyncClient) -> str:
    """The caller's own id.

    `/api/v1/auth/me`, not `/api/v1/users/me` — the latter exists but answers `405`,
    because it is registered for deletion-related verbs only.
    """
    response = await client.get("/api/v1/auth/me", headers=headers)
    assert response.status_code == 200, f"auth/me failed: {response.status_code} {response.text}"
    return response.json()["id"]


async def _seed_plan(user_id: str, *, title="Seeded plan", status="ACTIVE", item_count=3):
    """Insert a plan and its items directly, bypassing LLM-backed generation."""
    from src.domains.personal_learning.db_models import StudyPlan, StudyPlanItem
    from src.shared.database.session import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        plan = StudyPlan(
            user_id=user_id,
            title=title,
            goal_description="Seeded goal",
            deadline=datetime.now(UTC) + timedelta(days=21),
            status=status,
            total_items=item_count,
            completed_items=0,
        )
        session.add(plan)
        await session.flush()
        items = []
        for index in range(item_count):
            item = StudyPlanItem(
                plan_id=plan.id,
                title=f"Task {index}",
                scheduled_date=datetime.now(UTC) + timedelta(days=index),
                estimated_minutes=30,
                item_type="STUDY",
                phase="Foundations" if index < 2 else "Practice",
                status="PENDING",
            )
            session.add(item)
            items.append(item)
        await session.flush()
        result = (plan.id, [item.id for item in items])
        await session.commit()
        return result


class TestPlanListing:
    async def test_returns_the_pagination_envelope_without_items(
        self, client: AsyncClient, auth_headers
    ):
        user_id = await _user_id_for(auth_headers, client)
        await _seed_plan(user_id)

        response = await client.get(f"{BASE}/study-plans", headers=auth_headers)
        assert response.status_code == 200, response.text
        body = response.json()
        assert {"items", "total", "page", "pageSize", "pages"} <= set(body)
        assert body["total"] >= 1
        # The summary omits items on purpose; the detail route carries them.
        assert "items" not in body["items"][0]
        assert "strategy" in body["items"][0]

    async def test_status_filter_reaches_non_active_plans(
        self, client: AsyncClient, auth_headers
    ):
        """The old listing hard-filtered ACTIVE, so these tabs could never match."""
        user_id = await _user_id_for(auth_headers, client)
        await _seed_plan(user_id, title="Paused plan", status="PAUSED")

        response = await client.get(
            f"{BASE}/study-plans", params={"status": "PAUSED"}, headers=auth_headers
        )
        assert response.status_code == 200
        assert [item["title"] for item in response.json()["items"]] == ["Paused plan"]

    async def test_rejects_a_status_it_does_not_define(self, client: AsyncClient, auth_headers):
        response = await client.get(
            f"{BASE}/study-plans", params={"status": "ABANDONED"}, headers=auth_headers
        )
        assert response.status_code == 400
        assert response.json()["code"] == "VALIDATION_ERROR"

    async def test_shows_nothing_belonging_to_another_learner(
        self, client: AsyncClient, auth_headers
    ):
        intruder = await _login(client)
        intruder_id = await _user_id_for(intruder, client)
        await _seed_plan(intruder_id, title="Theirs")

        response = await client.get(f"{BASE}/study-plans", headers=auth_headers)
        assert all(item["title"] != "Theirs" for item in response.json()["items"])

    async def test_detail_carries_items_with_their_phase(
        self, client: AsyncClient, auth_headers
    ):
        user_id = await _user_id_for(auth_headers, client)
        plan_id, _ = await _seed_plan(user_id)

        response = await client.get(f"{BASE}/study-plans/{plan_id}", headers=auth_headers)
        assert response.status_code == 200
        body = response.json()
        assert len(body["items"]) == 3
        assert {item["phase"] for item in body["items"]} == {"Foundations", "Practice"}

    async def test_today_is_not_shadowed_by_the_detail_route(
        self, client: AsyncClient, auth_headers
    ):
        """`/study-plans/today` must not be read as a plan id."""
        response = await client.get(f"{BASE}/study-plans/today", headers=auth_headers)
        assert response.status_code == 200
        assert isinstance(response.json(), list)


class TestItemCompletionOwnership:
    async def test_cannot_complete_an_item_from_another_learners_plan(
        self, client: AsyncClient, auth_headers
    ):
        """The cross-user write, at the HTTP level.

        The caller owns `plan_id` and passes an item id belonging to someone else's
        plan. Before the fix this wrote status and completedAt onto that row and
        incremented the caller's own progress.
        """
        user_id = await _user_id_for(auth_headers, client)
        own_plan_id, _ = await _seed_plan(user_id, title="Mine")

        intruder = await _login(client)
        intruder_id = await _user_id_for(intruder, client)
        victim_plan_id, victim_items = await _seed_plan(intruder_id, title="Theirs")

        response = await client.post(
            f"{BASE}/study-plans/{own_plan_id}/items/{victim_items[0]}/complete",
            headers=auth_headers,
        )
        assert response.status_code == 404

        # Their item is untouched, and the caller's progress did not move.
        victim_view = await client.get(
            f"{BASE}/study-plans/{victim_plan_id}", headers=intruder
        )
        assert all(item["status"] == "PENDING" for item in victim_view.json()["items"])

        own_view = await client.get(f"{BASE}/study-plans/{own_plan_id}", headers=auth_headers)
        assert own_view.json()["completedItems"] == 0

    async def test_plan_routes_are_unreachable_for_another_learner(
        self, client: AsyncClient, auth_headers
    ):
        user_id = await _user_id_for(auth_headers, client)
        plan_id, item_ids = await _seed_plan(user_id)
        intruder = await _login(client)

        assert (
            await client.get(f"{BASE}/study-plans/{plan_id}", headers=intruder)
        ).status_code == 404
        assert (
            await client.patch(
                f"{BASE}/study-plans/{plan_id}", json={"title": "Theirs"}, headers=intruder
            )
        ).status_code == 404
        assert (
            await client.delete(f"{BASE}/study-plans/{plan_id}", headers=intruder)
        ).status_code == 404
        assert (
            await client.patch(
                f"{BASE}/study-plans/{plan_id}/items/{item_ids[0]}",
                json={"title": "Theirs"},
                headers=intruder,
            )
        ).status_code == 404


class TestProgressAccounting:
    async def test_completing_twice_does_not_exceed_the_total(
        self, client: AsyncClient, auth_headers
    ):
        """The double-count defect, at the HTTP level."""
        user_id = await _user_id_for(auth_headers, client)
        plan_id, item_ids = await _seed_plan(user_id, item_count=3)

        for _ in range(3):
            response = await client.post(
                f"{BASE}/study-plans/{plan_id}/items/{item_ids[0]}/complete",
                headers=auth_headers,
            )
            assert response.status_code == 200

        body = response.json()
        assert body["completedItems"] == 1
        assert body["completedItems"] <= body["totalItems"]

    async def test_uncomplete_returns_an_item_to_pending(
        self, client: AsyncClient, auth_headers
    ):
        user_id = await _user_id_for(auth_headers, client)
        plan_id, item_ids = await _seed_plan(user_id)

        await client.post(
            f"{BASE}/study-plans/{plan_id}/items/{item_ids[0]}/complete", headers=auth_headers
        )
        response = await client.post(
            f"{BASE}/study-plans/{plan_id}/items/{item_ids[0]}/uncomplete", headers=auth_headers
        )
        assert response.status_code == 200
        body = response.json()
        assert body["completedItems"] == 0
        item = next(row for row in body["items"] if row["id"] == item_ids[0])
        assert item["status"] == "PENDING"
        # A pending item with a completion timestamp is a row contradicting itself.
        assert item["completedAt"] is None

    async def test_completing_every_item_completes_the_plan_and_reopening_reverses_it(
        self, client: AsyncClient, auth_headers
    ):
        user_id = await _user_id_for(auth_headers, client)
        plan_id, item_ids = await _seed_plan(user_id, item_count=2)

        for item_id in item_ids:
            response = await client.post(
                f"{BASE}/study-plans/{plan_id}/items/{item_id}/complete", headers=auth_headers
            )
        assert response.json()["status"] == "COMPLETED"

        reopened = await client.post(
            f"{BASE}/study-plans/{plan_id}/items/{item_ids[0]}/uncomplete", headers=auth_headers
        )
        assert reopened.json()["status"] == "ACTIVE"

    async def test_skipping_does_not_count_as_progress(self, client: AsyncClient, auth_headers):
        user_id = await _user_id_for(auth_headers, client)
        plan_id, item_ids = await _seed_plan(user_id)

        response = await client.patch(
            f"{BASE}/study-plans/{plan_id}/items/{item_ids[0]}",
            json={"status": "SKIPPED"},
            headers=auth_headers,
        )
        assert response.status_code == 200
        assert response.json()["completedItems"] == 0
        item = next(row for row in response.json()["items"] if row["id"] == item_ids[0])
        assert item["status"] == "SKIPPED"


class TestPlanEditing:
    async def test_pauses_and_resumes(self, client: AsyncClient, auth_headers):
        user_id = await _user_id_for(auth_headers, client)
        plan_id, _ = await _seed_plan(user_id)

        paused = await client.patch(
            f"{BASE}/study-plans/{plan_id}", json={"status": "PAUSED"}, headers=auth_headers
        )
        assert paused.status_code == 200
        assert paused.json()["status"] == "PAUSED"

        # A paused plan's work is not "due today".
        today = await client.get(f"{BASE}/study-plans/today", headers=auth_headers)
        assert all(row["planId"] != plan_id for row in today.json())

        resumed = await client.patch(
            f"{BASE}/study-plans/{plan_id}", json={"status": "ACTIVE"}, headers=auth_headers
        )
        assert resumed.json()["status"] == "ACTIVE"

    async def test_rejects_a_status_a_learner_may_not_set(
        self, client: AsyncClient, auth_headers
    ):
        """`SUPERSEDED` is written by regeneration, never chosen."""
        user_id = await _user_id_for(auth_headers, client)
        plan_id, _ = await _seed_plan(user_id)

        response = await client.patch(
            f"{BASE}/study-plans/{plan_id}", json={"status": "SUPERSEDED"}, headers=auth_headers
        )
        assert response.status_code == 400

    async def test_moving_the_deadline_pulls_pending_items_inside_it(
        self, client: AsyncClient, auth_headers
    ):
        """Otherwise the schedule contradicts the date printed above it."""
        user_id = await _user_id_for(auth_headers, client)
        plan_id, _ = await _seed_plan(user_id, item_count=3)

        new_deadline = datetime.now(UTC) + timedelta(days=2)
        response = await client.patch(
            f"{BASE}/study-plans/{plan_id}",
            json={"deadline": new_deadline.isoformat()},
            headers=auth_headers,
        )
        assert response.status_code == 200
        body = response.json()
        assert body["deadline"].startswith(new_deadline.strftime("%Y-%m-%d"))
        for item in body["items"]:
            assert datetime.fromisoformat(item["scheduledDate"]) <= new_deadline + timedelta(
                days=1
            )

    async def test_adds_and_removes_an_item(self, client: AsyncClient, auth_headers):
        user_id = await _user_id_for(auth_headers, client)
        plan_id, _ = await _seed_plan(user_id, item_count=2)

        added = await client.post(
            f"{BASE}/study-plans/{plan_id}/items",
            json={
                "title": "Extra session",
                "scheduledDate": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
                "estimatedMinutes": 45,
                "phase": "Practice",
            },
            headers=auth_headers,
        )
        assert added.status_code == 201, added.text
        assert added.json()["totalItems"] == 3
        new_item = next(
            row for row in added.json()["items"] if row["title"] == "Extra session"
        )
        assert new_item["phase"] == "Practice"
        assert new_item["estimatedMinutes"] == 45

        removed = await client.delete(
            f"{BASE}/study-plans/{plan_id}/items/{new_item['id']}", headers=auth_headers
        )
        assert removed.status_code == 200
        assert removed.json()["totalItems"] == 2

    async def test_reschedules_and_regroups_an_item(self, client: AsyncClient, auth_headers):
        user_id = await _user_id_for(auth_headers, client)
        plan_id, item_ids = await _seed_plan(user_id)

        moved_to = datetime.now(UTC) + timedelta(days=5)
        response = await client.patch(
            f"{BASE}/study-plans/{plan_id}/items/{item_ids[0]}",
            json={"scheduledDate": moved_to.isoformat(), "phase": "Revision"},
            headers=auth_headers,
        )
        assert response.status_code == 200
        item = next(row for row in response.json()["items"] if row["id"] == item_ids[0])
        assert item["phase"] == "Revision"
        assert item["scheduledDate"].startswith(moved_to.strftime("%Y-%m-%d"))

    async def test_delete_removes_the_plan(self, client: AsyncClient, auth_headers):
        user_id = await _user_id_for(auth_headers, client)
        plan_id, _ = await _seed_plan(user_id)

        assert (
            await client.delete(f"{BASE}/study-plans/{plan_id}", headers=auth_headers)
        ).status_code == 204
        assert (
            await client.get(f"{BASE}/study-plans/{plan_id}", headers=auth_headers)
        ).status_code == 404


class TestTodayView:
    async def test_lists_pending_work_with_its_plan(self, client: AsyncClient, auth_headers):
        user_id = await _user_id_for(auth_headers, client)
        plan_id, _ = await _seed_plan(user_id, title="Interview prep")

        response = await client.get(f"{BASE}/study-plans/today", headers=auth_headers)
        assert response.status_code == 200
        rows = [row for row in response.json() if row["planId"] == plan_id]
        assert rows, "an item scheduled today should appear"
        assert rows[0]["planTitle"] == "Interview prep"
        assert rows[0]["item"]["status"] == "PENDING"

    async def test_excludes_another_learners_work(self, client: AsyncClient, auth_headers):
        intruder = await _login(client)
        intruder_id = await _user_id_for(intruder, client)
        intruder_plan_id, _ = await _seed_plan(intruder_id)

        response = await client.get(f"{BASE}/study-plans/today", headers=auth_headers)
        assert all(row["planId"] != intruder_plan_id for row in response.json())

    async def test_requires_authentication(self, client: AsyncClient):
        response = await client.get(f"{BASE}/study-plans/today")
        assert response.status_code in (401, 403)
