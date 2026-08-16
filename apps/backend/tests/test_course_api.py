"""Course routes, exercised end to end against Postgres.

Written before touching the course library, because every one of these endpoints answered `500`
until the defect sweep and **none of them has ever been executed**. They map camelCase columns onto
snake_case attributes and the routes read the column names; the fix is a one-line change per site
and looks obviously right, which is exactly the kind of fix that should not be trusted unrun. The
integration plan says to treat these routes as unproven rather than working.

Needs Postgres, so these skip unless ``RUN_DB_TESTS=1``. Run the file on its own::

    RUN_DB_TESTS=1 DATABASE_URL=... pytest tests/test_course_api.py
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient

BASE = "/api/v1/knowledge"


async def _login(client: AsyncClient) -> dict[str, str]:
    """Sign up, activate and log in a second learner, for the ownership checks."""
    from sqlalchemy import update as sa_update

    from src.domains.identity.db_models import User
    from src.shared.database.session import get_session_factory

    email = f"course_{uuid.uuid4()}@example.com"
    password = "StrongPassword123!"
    signup = await client.post(
        "/api/v1/auth/signup",
        json={"email": email, "password": password, "name": "Course Learner"},
    )
    # `fail`, not `skip`. The `auth_headers` fixture has already proved the database is reachable,
    # so a failure here is a broken helper — and a helper that skips turns every ownership test in
    # the file green without running any of them.
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


async def _create_course(client: AsyncClient, headers: dict[str, str], **overrides) -> dict:
    body = {"title": f"Course {uuid.uuid4().hex[:8]}", "difficulty": "BEGINNER", **overrides}
    response = await client.post(f"{BASE}/courses", json=body, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


# ---------------------------------------------------------------------------
# The routes answer at all
# ---------------------------------------------------------------------------


async def test_create_course_returns_the_created_course(client: AsyncClient, auth_headers):
    created = await _create_course(client, auth_headers, description="Made by a test")

    assert created["id"]
    assert created["description"] == "Made by a test"
    # Every one of these is a camelCase field over a snake_case attribute, and each was a `500`.
    assert created["userId"]
    assert created["isAIGenerated"] is False
    assert created["archived"] is False
    assert created["totalTopics"] == 0
    assert created["modules"] == []


async def test_list_courses_includes_a_created_course(client: AsyncClient, auth_headers):
    created = await _create_course(client, auth_headers)

    response = await client.get(f"{BASE}/courses", headers=auth_headers)

    assert response.status_code == 200, response.text
    payload = response.json()
    ids = [course["id"] for course in payload["items"]]
    assert created["id"] in ids
    assert payload["total"] >= 1


async def test_get_course_returns_it_with_modules_and_topics(client: AsyncClient, auth_headers):
    created = await _create_course(client, auth_headers)
    module = await client.post(
        f"{BASE}/courses/{created['id']}/modules",
        json={"title": "Module one", "order": 1},
        headers=auth_headers,
    )
    assert module.status_code == 201, module.text
    module_id = module.json()["id"]

    topic = await client.post(
        f"{BASE}/courses/{created['id']}/modules/{module_id}/topics",
        json={"title": "Topic one", "order": 1},
        headers=auth_headers,
    )
    assert topic.status_code == 201, topic.text

    response = await client.get(f"{BASE}/courses/{created['id']}", headers=auth_headers)

    assert response.status_code == 200, response.text
    payload = response.json()
    assert len(payload["modules"]) == 1
    assert payload["modules"][0]["title"] == "Module one"
    assert len(payload["modules"][0]["topics"]) == 1
    # `calculate_module_progress` read camelCase attributes and took every module and topic route
    # down with it. These two keys are what it computes.
    assert payload["modules"][0]["topicCount"] == 1
    assert payload["modules"][0]["completedTopicCount"] == 0


async def test_completing_a_topic_moves_course_progress(client: AsyncClient, auth_headers):
    created = await _create_course(client, auth_headers)
    module_id = (
        await client.post(
            f"{BASE}/courses/{created['id']}/modules",
            json={"title": "Module", "order": 1},
            headers=auth_headers,
        )
    ).json()["id"]
    topic_id = (
        await client.post(
            f"{BASE}/courses/{created['id']}/modules/{module_id}/topics",
            json={"title": "Topic", "order": 1},
            headers=auth_headers,
        )
    ).json()["id"]

    complete = await client.patch(
        f"{BASE}/courses/{created['id']}/modules/{module_id}/topics/{topic_id}/complete",
        params={"completed": True},
        headers=auth_headers,
    )
    assert complete.status_code == 200, complete.text
    assert complete.json()["completed"] is True
    # `estimatedHours` has a default, so a broken alias returned null here rather than failing.
    assert "estimatedHours" in complete.json()

    reread = (await client.get(f"{BASE}/courses/{created['id']}", headers=auth_headers)).json()
    assert reread["completedTopics"] == 1
    assert reread["progress"] == pytest.approx(100.0)


# ---------------------------------------------------------------------------
# Ownership
# ---------------------------------------------------------------------------


async def test_another_learners_course_is_a_404(client: AsyncClient, auth_headers):
    """Not a 403. The same answer as a missing course, so an id cannot be probed for existence."""
    other = await _login(client)
    theirs = await _create_course(client, other)

    response = await client.get(f"{BASE}/courses/{theirs['id']}", headers=auth_headers)

    assert response.status_code == 404, response.text


async def test_another_learners_course_is_not_in_my_list(client: AsyncClient, auth_headers):
    other = await _login(client)
    theirs = await _create_course(client, other)

    payload = (await client.get(f"{BASE}/courses", headers=auth_headers)).json()

    assert theirs["id"] not in [course["id"] for course in payload["items"]]


async def test_cannot_add_a_module_to_another_learners_course(client: AsyncClient, auth_headers):
    other = await _login(client)
    theirs = await _create_course(client, other)

    response = await client.post(
        f"{BASE}/courses/{theirs['id']}/modules",
        json={"title": "Intruding", "order": 1},
        headers=auth_headers,
    )

    assert response.status_code == 404, response.text


async def test_a_module_id_from_another_course_is_refused(client: AsyncClient, auth_headers):
    """Both ids are checked against each other, not just each against the caller.

    Same class as the study-plan cross-user item write: owning the course in the path is not the
    same as the module in the path belonging to it.
    """
    mine = await _create_course(client, auth_headers)
    other_course = await _create_course(client, auth_headers)
    module_id = (
        await client.post(
            f"{BASE}/courses/{other_course['id']}/modules",
            json={"title": "Elsewhere", "order": 1},
            headers=auth_headers,
        )
    ).json()["id"]

    response = await client.put(
        f"{BASE}/courses/{mine['id']}/modules/{module_id}",
        json={"title": "Moved"},
        headers=auth_headers,
    )

    assert response.status_code == 400, response.text


# ---------------------------------------------------------------------------
# Filtering
# ---------------------------------------------------------------------------


async def test_search_filters_the_list(client: AsyncClient, auth_headers):
    """The defect this file was written to catch.

    The route builds a Prisma-style ``where["OR"]`` for `search`, and
    `_build_course_conditions` has no `OR` branch — so the parameter was accepted, ignored, and the
    caller was handed the unfiltered library while believing it had searched.
    """
    marker = uuid.uuid4().hex[:10]
    match = await _create_course(client, auth_headers, title=f"Searchable {marker}")
    other = await _create_course(client, auth_headers, title="Nothing to do with it")

    payload = (
        await client.get(f"{BASE}/courses", params={"search": marker}, headers=auth_headers)
    ).json()

    ids = [course["id"] for course in payload["items"]]
    assert match["id"] in ids
    assert other["id"] not in ids
    assert payload["total"] == 1


async def test_search_also_matches_the_description(client: AsyncClient, auth_headers):
    marker = uuid.uuid4().hex[:10]
    match = await _create_course(
        client, auth_headers, title="Plain title", description=f"About {marker}"
    )

    payload = (
        await client.get(f"{BASE}/courses", params={"search": marker}, headers=auth_headers)
    ).json()

    assert [course["id"] for course in payload["items"]] == [match["id"]]


async def test_search_is_case_insensitive(client: AsyncClient, auth_headers):
    marker = uuid.uuid4().hex[:8]
    await _create_course(client, auth_headers, title=f"Uppercase {marker.upper()}")

    payload = (
        await client.get(
            f"{BASE}/courses", params={"search": marker.lower()}, headers=auth_headers
        )
    ).json()

    assert payload["total"] == 1


async def test_archived_courses_are_out_of_the_default_library(client: AsyncClient, auth_headers):
    """Archiving is the learner saying "not now". A default list that still shows it ignores that."""
    kept = await _create_course(client, auth_headers)
    shelved = await _create_course(client, auth_headers)
    archive = await client.post(f"{BASE}/courses/{shelved['id']}/archive", headers=auth_headers)
    assert archive.status_code == 200, archive.text

    payload = (await client.get(f"{BASE}/courses", headers=auth_headers)).json()

    ids = [course["id"] for course in payload["items"]]
    assert kept["id"] in ids
    assert shelved["id"] not in ids


async def test_archived_true_returns_the_archive(client: AsyncClient, auth_headers):
    shelved = await _create_course(client, auth_headers)
    await client.post(f"{BASE}/courses/{shelved['id']}/archive", headers=auth_headers)

    payload = (
        await client.get(f"{BASE}/courses", params={"archived": True}, headers=auth_headers)
    ).json()

    assert shelved["id"] in [course["id"] for course in payload["items"]]


async def test_difficulty_filter_is_case_insensitive_on_input(client: AsyncClient, auth_headers):
    course = await _create_course(client, auth_headers, difficulty="ADVANCED")

    payload = (
        await client.get(
            f"{BASE}/courses", params={"difficulty": "advanced"}, headers=auth_headers
        )
    ).json()

    assert course["id"] in [item["id"] for item in payload["items"]]
    assert all(item["difficulty"] == "ADVANCED" for item in payload["items"])
