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

from src.domains.knowledge.repository import knowledge_repo

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
        await client.get(f"{BASE}/courses", params={"search": marker.lower()}, headers=auth_headers)
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
        await client.get(f"{BASE}/courses", params={"difficulty": "advanced"}, headers=auth_headers)
    ).json()

    assert course["id"] in [item["id"] for item in payload["items"]]
    assert all(item["difficulty"] == "ADVANCED" for item in payload["items"])


# ---------------------------------------------------------------------------
# Locating a topic, and what completion writes
# ---------------------------------------------------------------------------


async def _course_with_topic(client: AsyncClient, headers: dict[str, str]) -> tuple[dict, str, str]:
    """A course with one module and one topic. Returns ``(course, module_id, topic_id)``."""
    course = await _create_course(client, headers)
    module_id = (
        await client.post(
            f"{BASE}/courses/{course['id']}/modules",
            json={"title": "Module", "order": 1},
            headers=headers,
        )
    ).json()["id"]
    topic_id = (
        await client.post(
            f"{BASE}/courses/{course['id']}/modules/{module_id}/topics",
            json={"title": "Topic", "order": 1, "estimatedHours": 1.5},
            headers=headers,
        )
    ).json()["id"]
    return course, module_id, topic_id


async def test_a_topic_id_resolves_to_its_course(client: AsyncClient, auth_headers):
    """The endpoint that makes a topic id openable.

    A topic id used to be a dead end: the study surface needs the course as well as the topic, and
    nothing could get from one to the other. That blocked the lesson route and any deep link from a
    study plan item carrying a `topicId`.
    """
    course, module_id, topic_id = await _course_with_topic(client, auth_headers)

    response = await client.get(f"{BASE}/topics/{topic_id}", headers=auth_headers)

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["topic"]["id"] == topic_id
    assert payload["moduleId"] == module_id
    assert payload["courseId"] == course["id"]
    assert payload["courseTitle"] == course["title"]
    assert payload["position"] == 1
    assert payload["totalTopics"] == 1
    # A default-bearing field, so a broken alias returns null here rather than failing.
    assert payload["topic"]["estimatedHours"] == 1.5


async def test_another_learners_topic_cannot_be_resolved(client: AsyncClient, auth_headers):
    other = await _login(client)
    _course, _module_id, topic_id = await _course_with_topic(client, other)

    response = await client.get(f"{BASE}/topics/{topic_id}", headers=auth_headers)

    assert response.status_code in (403, 404), response.text


async def test_an_unknown_topic_is_a_404(client: AsyncClient, auth_headers):
    response = await client.get(f"{BASE}/topics/does-not-exist", headers=auth_headers)

    assert response.status_code == 404, response.text


async def test_completing_a_topic_records_when(client: AsyncClient, auth_headers):
    """`Topic.completedAt` is what the streak and the activity feed read.

    Before it existed, `completed` was a boolean with no "when" — and `updatedAt` is not a substitute,
    because it moves when a topic is renamed or has content generated into it.
    """
    course, module_id, topic_id = await _course_with_topic(client, auth_headers)
    path = f"{BASE}/courses/{course['id']}/modules/{module_id}/topics/{topic_id}/complete"

    completed = await client.patch(path, params={"completed": True}, headers=auth_headers)

    assert completed.status_code == 200, completed.text
    assert completed.json()["completed"] is True
    assert completed.json()["completedAt"] is not None


async def test_reopening_a_topic_clears_when(client: AsyncClient, auth_headers):
    """A pending topic carrying a completion time is a row that contradicts itself."""
    course, module_id, topic_id = await _course_with_topic(client, auth_headers)
    path = f"{BASE}/courses/{course['id']}/modules/{module_id}/topics/{topic_id}/complete"
    await client.patch(path, params={"completed": True}, headers=auth_headers)

    reopened = await client.patch(path, params={"completed": False}, headers=auth_headers)

    assert reopened.status_code == 200, reopened.text
    assert reopened.json()["completed"] is False
    assert reopened.json()["completedAt"] is None


async def test_completion_shows_up_on_the_courses_dashboard(client: AsyncClient, auth_headers):
    """End to end: completing a topic feeds the library's activity feed and weekly figures."""
    course, module_id, topic_id = await _course_with_topic(client, auth_headers)
    await client.patch(
        f"{BASE}/courses/{course['id']}/modules/{module_id}/topics/{topic_id}/complete",
        params={"completed": True},
        headers=auth_headers,
    )

    dashboard = await client.get(f"{BASE}/courses/dashboard", headers=auth_headers)

    assert dashboard.status_code == 200, dashboard.text
    payload = dashboard.json()
    assert payload["completedTopics"] >= 1
    # Estimated, not measured: the planned hours of the topic that got completed.
    assert payload["weeklyHours"] >= 1.5
    assert payload["weeklyTopicsCompleted"] >= 1
    titles = [entry["topicTitle"] for entry in payload["recentActivity"]]
    assert "Topic" in titles
    # The course to resume is the one whose newest completion this is.
    assert payload["featured"] is not None


# ---------------------------------------------------------------------------
# Lesson sections, and the course facts the detail page states
#
# These back the lesson workspace and the four course panels that were removed and restored. The
# rating path in particular can only be tested here: it is an upsert on a named unique constraint, so
# it is Postgres-only and the SQLite repository suite cannot reach it.
# ---------------------------------------------------------------------------


async def _course_with_lesson(client: AsyncClient, headers: dict[str, str]) -> tuple[dict, dict]:
    """A course, a module and a topic, returning the topic as its full response body.

    Deliberately **not** named `_course_with_topic`: that helper already exists above and returns
    `(course, moduleId, topicId)`. Defining a second function of the same name shadowed it — Python
    keeps the last definition — and broke five passing tests in this file that unpacked three values.
    These tests want the topic body itself, for `sections` and `moduleId`, so they get their own
    helper under a name that cannot collide.
    """
    course = await _create_course(client, headers)
    module = await client.post(
        f"{BASE}/courses/{course['id']}/modules",
        json={"title": "Module one", "order": 1},
        headers=headers,
    )
    assert module.status_code == 201, module.text
    topic = await client.post(
        f"{BASE}/courses/{course['id']}/modules/{module.json()['id']}/topics",
        json={"title": "Graph traversal", "order": 1},
        headers=headers,
    )
    assert topic.status_code == 201, topic.text
    return course, topic.json()


async def test_a_new_topic_reports_no_sections_rather_than_failing(
    client: AsyncClient, auth_headers
):
    """The reader falls back to `content` when a topic has no sections, so an empty list is a normal
    state and not an error."""
    _, topic = await _course_with_lesson(client, auth_headers)
    response = await client.get(f"{BASE}/topics/{topic['id']}/sections", headers=auth_headers)
    assert response.status_code == 200, response.text
    assert response.json() == []


async def test_a_section_round_trips_every_structured_field(client: AsyncClient, auth_headers):
    """The whole point of the section table: a title with no body, or steps that arrive as bare
    strings, is a lesson the learner cannot read."""
    _, topic = await _course_with_lesson(client, auth_headers)
    created = await client.post(
        f"{BASE}/topics/{topic['id']}/sections",
        json={
            "title": "Breadth-first search",
            "order": 10,
            "kind": "algorithm",
            "eyebrow": "Core idea",
            "summary": "How the queue advances",
            "durationMinutes": 6,
            "paragraphs": ["A queue explores in layers.", "The oldest discovery leaves first."],
            "keyIdea": "Broad before deep",
            "steps": [{"title": "Enqueue", "detail": "Add the start node"}],
            "bullets": ["Use a queue"],
            "code": "queue.push(start)",
        },
        headers=auth_headers,
    )
    assert created.status_code == 201, created.text
    section = created.json()

    assert section["kind"] == "algorithm"
    assert section["eyebrow"] == "Core idea"
    assert section["durationMinutes"] == 6
    assert len(section["paragraphs"]) == 2
    assert section["keyIdea"] == "Broad before deep"
    assert section["steps"] == [{"title": "Enqueue", "detail": "Add the start node"}]
    assert section["bullets"] == ["Use a queue"]
    assert section["code"] == "queue.push(start)"
    assert section["completed"] is False
    assert section["completedAt"] is None


async def test_sections_list_in_reading_order(client: AsyncClient, auth_headers):
    _, topic = await _course_with_lesson(client, auth_headers)
    for title, order in (("third", 30), ("first", 10), ("second", 20)):
        await client.post(
            f"{BASE}/topics/{topic['id']}/sections",
            json={"title": title, "order": order, "paragraphs": ["body"]},
            headers=auth_headers,
        )

    listed = await client.get(f"{BASE}/topics/{topic['id']}/sections", headers=auth_headers)
    assert [s["title"] for s in listed.json()] == ["first", "second", "third"]


async def test_completing_a_section_records_when_and_reopening_clears_it(
    client: AsyncClient, auth_headers
):
    _, topic = await _course_with_lesson(client, auth_headers)
    created = await client.post(
        f"{BASE}/topics/{topic['id']}/sections",
        json={"title": "Section", "order": 10, "paragraphs": ["body"]},
        headers=auth_headers,
    )
    section_id = created.json()["id"]
    path = f"{BASE}/topics/{topic['id']}/sections/{section_id}/complete"

    # No query parameter: completion is the default, because the reader sends this on every Continue.
    done = await client.patch(path, headers=auth_headers)
    assert done.status_code == 200, done.text
    assert done.json()["completed"] is True
    assert done.json()["completedAt"] is not None

    reopened = await client.patch(f"{path}?completed=false", headers=auth_headers)
    assert reopened.json()["completed"] is False
    assert reopened.json()["completedAt"] is None


async def test_completing_every_section_leaves_the_topic_and_course_untouched(
    client: AsyncClient, auth_headers
):
    """Reading a lesson through is not the same claim as finishing the topic, so course progress must
    not move here. If it did, scrolling to the end of a lesson would advance the course before the
    learner answered the check."""
    course, topic = await _course_with_lesson(client, auth_headers)
    created = await client.post(
        f"{BASE}/topics/{topic['id']}/sections",
        json={"title": "Only section", "order": 10, "paragraphs": ["body"]},
        headers=auth_headers,
    )
    await client.patch(
        f"{BASE}/topics/{topic['id']}/sections/{created.json()['id']}/complete",
        headers=auth_headers,
    )

    detail = await client.get(f"{BASE}/courses/{course['id']}", headers=auth_headers)
    assert detail.json()["progress"] == 0
    assert detail.json()["completedTopics"] == 0


async def test_replacing_sections_leaves_only_the_new_ones(client: AsyncClient, auth_headers):
    _, topic = await _course_with_lesson(client, auth_headers)
    await client.post(
        f"{BASE}/topics/{topic['id']}/sections",
        json={"title": "old", "order": 10, "paragraphs": ["old body"]},
        headers=auth_headers,
    )

    replaced = await client.put(
        f"{BASE}/topics/{topic['id']}/sections",
        json=[
            {"title": "new one", "order": 10, "paragraphs": ["a"]},
            {"title": "new two", "order": 20, "paragraphs": ["b"]},
        ],
        headers=auth_headers,
    )
    assert replaced.status_code == 200, replaced.text
    assert [s["title"] for s in replaced.json()] == ["new one", "new two"]


async def test_editing_a_section_does_not_blank_the_fields_it_omits(
    client: AsyncClient, auth_headers
):
    _, topic = await _course_with_lesson(client, auth_headers)
    created = await client.post(
        f"{BASE}/topics/{topic['id']}/sections",
        json={"title": "before", "order": 10, "paragraphs": ["kept"], "keyIdea": "also kept"},
        headers=auth_headers,
    )

    updated = await client.put(
        f"{BASE}/topics/{topic['id']}/sections/{created.json()['id']}",
        json={"title": "after"},
        headers=auth_headers,
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["title"] == "after"
    assert updated.json()["paragraphs"] == ["kept"]
    assert updated.json()["keyIdea"] == "also kept"


async def test_another_learners_sections_are_refused(client: AsyncClient, auth_headers):
    """Ownership runs through the topic, so a section in someone else's course is refused by the same
    check that refuses the topic."""
    _, topic = await _course_with_lesson(client, auth_headers)
    intruder = await _login(client)

    listed = await client.get(f"{BASE}/topics/{topic['id']}/sections", headers=intruder)
    assert listed.status_code in (403, 404), listed.text

    created = await client.post(
        f"{BASE}/topics/{topic['id']}/sections",
        json={"title": "theirs", "order": 10, "paragraphs": ["body"]},
        headers=intruder,
    )
    assert created.status_code in (403, 404), created.text


async def test_a_section_addressed_through_the_wrong_topic_is_rejected(
    client: AsyncClient, auth_headers
):
    """Both ids are in the path, so they can disagree. Accepting the mismatch would let a caller edit
    a section by naming any topic they own."""
    _, first = await _course_with_lesson(client, auth_headers)
    _, second = await _course_with_lesson(client, auth_headers)
    created = await client.post(
        f"{BASE}/topics/{first['id']}/sections",
        json={"title": "belongs to first", "order": 10, "paragraphs": ["body"]},
        headers=auth_headers,
    )

    response = await client.put(
        f"{BASE}/topics/{second['id']}/sections/{created.json()['id']}",
        json={"title": "moved"},
        headers=auth_headers,
    )
    assert response.status_code == 400, response.text


async def test_topic_resolution_carries_the_lesson_and_the_course_progress(
    client: AsyncClient, auth_headers
):
    """What `/learn/lessons/:id` needs in one request: the topic with its sections, the breadcrumb, the
    position, and how far the course has got."""
    _, topic = await _course_with_lesson(client, auth_headers)
    await client.put(
        f"{BASE}/topics/{topic['id']}/sections",
        json=[{"title": "Section one", "order": 10, "paragraphs": ["body"]}],
        headers=auth_headers,
    )

    response = await client.get(f"{BASE}/topics/{topic['id']}", headers=auth_headers)
    assert response.status_code == 200, response.text
    payload = response.json()

    assert payload["courseTitle"]
    assert payload["moduleTitle"] == "Module one"
    assert payload["position"] == 1
    assert payload["totalTopics"] == 1
    assert payload["courseProgress"] == 0
    assert [s["title"] for s in payload["topic"]["sections"]] == ["Section one"]


async def test_objectives_and_the_knowledge_check_round_trip_on_a_topic(
    client: AsyncClient, auth_headers
):
    course, topic = await _course_with_lesson(client, auth_headers)
    module_id = topic["moduleId"]

    updated = await client.put(
        f"{BASE}/courses/{course['id']}/modules/{module_id}/topics/{topic['id']}",
        json={
            "objectives": ["Trace a traversal by hand"],
            "knowledgeCheck": {
                "question": "Which traversal finds the fewest edges?",
                "explanation": "BFS explores in distance layers.",
                "choices": [
                    {"id": "dfs", "label": "Depth-first", "correct": False},
                    {"id": "bfs", "label": "Breadth-first", "correct": True},
                ],
            },
        },
        headers=auth_headers,
    )
    assert updated.status_code == 200, updated.text
    payload = updated.json()

    assert payload["objectives"] == ["Trace a traversal by hand"]
    check = payload["knowledgeCheck"]
    assert check["question"].startswith("Which traversal")
    # The correct answer is published on purpose: the check is an unscored self-test on a course the
    # learner owns, and the page grades it without a round trip.
    assert [c["id"] for c in check["choices"] if c["correct"]] == ["bfs"]


# ---------------------------------------------------------------------------
# Course facts: category, tags, outcomes, instructor, rating
# ---------------------------------------------------------------------------


async def test_the_course_panels_round_trip_on_create(client: AsyncClient, auth_headers):
    """Each of these was a panel that got deleted for having no column. `create_course` builds an
    explicit allowlist, so a field added to the request model and not to that dict is accepted and
    silently discarded — which is what this asserts against."""
    created = await _create_course(
        client,
        auth_headers,
        category="Computer Science",
        tags=["graphs", "algorithms"],
        outcomes=["Reason about traversal order"],
        instructorName="Dr Maya Chen",
        instructorRole="Computer science educator",
    )

    assert created["category"] == "Computer Science"
    assert created["tags"] == ["graphs", "algorithms"]
    assert created["outcomes"] == ["Reason about traversal order"]
    assert created["instructor"] == {"name": "Dr Maya Chen", "role": "Computer science educator"}


async def test_a_course_with_no_instructor_returns_null_not_an_empty_object(
    client: AsyncClient, auth_headers
):
    """A panel keyed on "is there an instructor" is then a single check, rather than every reader
    having to know that a nameless instructor means none."""
    created = await _create_course(client, auth_headers)
    assert created["instructor"] is None


async def test_the_library_card_carries_category_and_tags(client: AsyncClient, auth_headers):
    created = await _create_course(
        client, auth_headers, category="Mathematics", tags=["probability"]
    )
    listed = await client.get(f"{BASE}/courses?search={created['title']}", headers=auth_headers)
    card = next(c for c in listed.json()["items"] if c["id"] == created["id"])
    assert card["category"] == "Mathematics"
    assert card["tags"] == ["probability"]


async def test_an_unrated_course_reports_null_rather_than_zero(client: AsyncClient, auth_headers):
    """ "Nobody has rated this" and "everybody rated it zero" are different statements, and only one is
    ever true of a new course."""
    created = await _create_course(client, auth_headers)
    detail = await client.get(f"{BASE}/courses/{created['id']}", headers=auth_headers)
    rating = detail.json()["rating"]
    assert rating["average"] is None
    assert rating["count"] == 0
    assert rating["yourRating"] is None


async def test_rating_a_course_then_changing_it_updates_rather_than_adds(
    client: AsyncClient, auth_headers
):
    """The upsert. A read-then-write would let two submissions race into a duplicate-key failure, and
    a plain insert would let one learner weight the average by clicking repeatedly."""
    created = await _create_course(client, auth_headers)
    path = f"{BASE}/courses/{created['id']}/rating"

    first = await client.put(path, json={"value": 5}, headers=auth_headers)
    assert first.status_code == 200, first.text
    assert first.json() == {"average": 5.0, "count": 1, "yourRating": 5}

    changed = await client.put(
        path, json={"value": 3, "comment": "on reflection"}, headers=auth_headers
    )
    assert changed.json() == {"average": 3.0, "count": 1, "yourRating": 3}

    fetched = await client.get(path, headers=auth_headers)
    assert fetched.json()["yourRating"] == 3


async def test_a_rating_outside_one_to_five_is_refused(client: AsyncClient, auth_headers):
    """Bounded in the request model and again by a CHECK constraint, because the average the page
    prints has no way to notice a 40 that arrived by another path."""
    created = await _create_course(client, auth_headers)
    path = f"{BASE}/courses/{created['id']}/rating"

    for value in (0, 6, -1):
        response = await client.put(path, json={"value": value}, headers=auth_headers)
        # 400, not 422: this app installs a validation handler that reports request-model failures
        # as VALIDATION_ERROR with a 400, which the rest of this file already asserts.
        assert response.status_code == 400, f"{value} was accepted: {response.text}"


async def test_another_learner_cannot_rate_or_read_your_course_rating(
    client: AsyncClient, auth_headers
):
    created = await _create_course(client, auth_headers)
    intruder = await _login(client)
    path = f"{BASE}/courses/{created['id']}/rating"

    assert (await client.put(path, json={"value": 5}, headers=intruder)).status_code in (403, 404)
    assert (await client.get(path, headers=intruder)).status_code in (403, 404)


async def test_a_generated_course_is_credited_to_maigie(client: AsyncClient, auth_headers):
    """Written at creation rather than inferred at read time. Inferring it would mean every reader
    repeating the rule, and the first reader that forgot would show an instructor on one page and none
    on another."""
    created = await _create_course(client, auth_headers, isAIGenerated=True)
    assert created["instructor"]["name"] == "Maigie"


async def test_an_explicit_instructor_survives_ai_generation(client: AsyncClient, auth_headers):
    """The default only fills a gap. A course authored for a space keeps its author."""
    created = await _create_course(
        client, auth_headers, isAIGenerated=True, instructorName="Amara Okafor"
    )
    assert created["instructor"]["name"] == "Amara Okafor"


# ---------------------------------------------------------------------------
# Nothing is accepted and discarded
#
# These cover the defect class rather than one defect: a request field that the API accepts, reports
# success for, and never stores. `tests/test_field_mapping_completeness.py` guards it statically; these
# prove the behaviour end to end, through the routes, against Postgres.
# ---------------------------------------------------------------------------


async def test_an_explicit_null_clears_a_course_field(client: AsyncClient, auth_headers):
    """Clearing used to be impossible and to report success anyway.

    `update_course` filtered out every null, so `{"category": null}` returned `200` with the old
    category still in place. The route reads the body with `exclude_unset=True`, so a key only arrives
    when the client sent it, and that filter was the only thing collapsing "not sent" into "sent as
    null".
    """
    created = await _create_course(client, auth_headers, category="Computer Science")
    assert created["category"] == "Computer Science"

    cleared = await client.put(
        f"{BASE}/courses/{created['id']}", json={"category": None}, headers=auth_headers
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["category"] is None


async def test_omitting_a_field_leaves_it_alone(client: AsyncClient, auth_headers):
    """The other half of the same contract: an absent key must not be read as a clear, or every partial
    update would wipe everything it did not mention."""
    created = await _create_course(
        client, auth_headers, category="Mathematics", tags=["probability"]
    )

    updated = await client.put(
        f"{BASE}/courses/{created['id']}", json={"title": "Renamed"}, headers=auth_headers
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["title"] == "Renamed"
    assert updated.json()["category"] == "Mathematics"
    assert updated.json()["tags"] == ["probability"]


async def test_clearing_a_required_field_is_refused_not_attempted(
    client: AsyncClient, auth_headers
):
    """`title` is NOT NULL. Letting the write through would surface a database constraint name, which
    tells the client nothing it can act on."""
    created = await _create_course(client, auth_headers)
    response = await client.put(
        f"{BASE}/courses/{created['id']}", json={"title": None}, headers=auth_headers
    )
    assert response.status_code in (400, 422), response.text

    unchanged = await client.get(f"{BASE}/courses/{created['id']}", headers=auth_headers)
    assert unchanged.json()["title"] == created["title"]


async def test_every_field_sent_to_course_create_is_stored(client: AsyncClient, auth_headers):
    """Read back from the row rather than trusting the create response.

    This is what the original defect could hide: the create response was assembled from the ORM object,
    but a field dropped by the service allowlist never reached it, so asserting on the response alone
    would have passed while the database held nothing.
    """
    created = await _create_course(
        client,
        auth_headers,
        description="Every field",
        category="Engineering",
        tags=["systems", "scale"],
        outcomes=["Design for failure"],
        instructorName="Noah Williams",
        instructorRole="Principal systems engineer",
    )

    fetched = await client.get(f"{BASE}/courses/{created['id']}", headers=auth_headers)
    assert fetched.status_code == 200, fetched.text
    payload = fetched.json()

    assert payload["description"] == "Every field"
    assert payload["category"] == "Engineering"
    assert payload["tags"] == ["systems", "scale"]
    assert payload["outcomes"] == ["Design for failure"]
    assert payload["instructor"] == {
        "name": "Noah Williams",
        "role": "Principal systems engineer",
    }


# ---------------------------------------------------------------------------
# Authoring: bulk create, reorder, unarchive, and the wizard's own fields
# ---------------------------------------------------------------------------


async def _module_of(
    client: AsyncClient, headers: dict[str, str], course_id: str, **overrides
) -> dict:
    body = {"title": "Module", "order": 1, **overrides}
    response = await client.post(f"{BASE}/courses/{course_id}/modules", json=body, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


async def test_bulk_create_saves_a_whole_outline_in_one_request(client: AsyncClient, auth_headers):
    course = await _create_course(client, auth_headers)
    module = await _module_of(client, auth_headers, course["id"])

    response = await client.post(
        f"{BASE}/courses/{course['id']}/modules/{module['id']}/topics/bulk",
        json={
            "topics": [
                {"title": "Read", "order": 10, "kind": "Lesson", "estimatedHours": 0.5},
                {"title": "Drill", "order": 20, "kind": "Practice"},
                {"title": "Build", "order": 30, "kind": "Project"},
            ]
        },
        headers=auth_headers,
    )
    assert response.status_code == 201, response.text
    created = response.json()

    assert [t["title"] for t in created] == ["Read", "Drill", "Build"]
    assert [t["kind"] for t in created] == ["Lesson", "Practice", "Project"]
    assert created[0]["estimatedHours"] == 0.5

    # And really stored, not just echoed: the detail response counts them.
    detail = await client.get(f"{BASE}/courses/{course['id']}", headers=auth_headers)
    assert detail.json()["totalTopics"] == 3


async def test_bulk_create_refuses_an_empty_list(client: AsyncClient, auth_headers):
    """An empty outline is a caller mistake rather than a no-op worth pretending to honour."""
    course = await _create_course(client, auth_headers)
    module = await _module_of(client, auth_headers, course["id"])

    response = await client.post(
        f"{BASE}/courses/{course['id']}/modules/{module['id']}/topics/bulk",
        json={"topics": []},
        headers=auth_headers,
    )
    assert response.status_code in (400, 422), response.text


async def test_bulk_create_into_another_learners_module_is_refused(
    client: AsyncClient, auth_headers
):
    course = await _create_course(client, auth_headers)
    module = await _module_of(client, auth_headers, course["id"])
    intruder = await _login(client)

    response = await client.post(
        f"{BASE}/courses/{course['id']}/modules/{module['id']}/topics/bulk",
        json={"topics": [{"title": "Theirs", "order": 10}]},
        headers=intruder,
    )
    assert response.status_code in (403, 404), response.text


async def test_reordering_modules_changes_the_outline_order(client: AsyncClient, auth_headers):
    course = await _create_course(client, auth_headers)
    first = await _module_of(client, auth_headers, course["id"], title="first", order=1)
    second = await _module_of(client, auth_headers, course["id"], title="second", order=2)
    third = await _module_of(client, auth_headers, course["id"], title="third", order=3)

    response = await client.patch(
        f"{BASE}/courses/{course['id']}/modules/reorder",
        json={"ids": [third["id"], first["id"], second["id"]]},
        headers=auth_headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["reordered"] == 3

    detail = await client.get(f"{BASE}/courses/{course['id']}", headers=auth_headers)
    assert [m["title"] for m in detail.json()["modules"]] == ["third", "first", "second"]


async def test_reorder_is_not_shadowed_by_the_module_id_route(client: AsyncClient, auth_headers):
    """`/modules/reorder` sits under the same prefix as `/modules/{module_id}`.

    They coexist because the methods differ, but that is exactly the kind of thing that breaks silently
    when a route is added or moved — a `PATCH` to `reorder` reaching the `{module_id}` handler would try
    to load a module called "reorder" and answer `404`. This test fails if the ordering regresses.
    """
    course = await _create_course(client, auth_headers)
    module = await _module_of(client, auth_headers, course["id"])

    response = await client.patch(
        f"{BASE}/courses/{course['id']}/modules/reorder",
        json={"ids": [module["id"]]},
        headers=auth_headers,
    )
    assert response.status_code == 200, response.text
    assert response.json() == {"reordered": 1}


async def test_reordering_topics_changes_their_order_within_a_module(
    client: AsyncClient, auth_headers
):
    course = await _create_course(client, auth_headers)
    module = await _module_of(client, auth_headers, course["id"])
    created = await client.post(
        f"{BASE}/courses/{course['id']}/modules/{module['id']}/topics/bulk",
        json={
            "topics": [
                {"title": "one", "order": 10},
                {"title": "two", "order": 20},
            ]
        },
        headers=auth_headers,
    )
    ids = [t["id"] for t in created.json()]

    response = await client.patch(
        f"{BASE}/courses/{course['id']}/modules/{module['id']}/topics/reorder",
        json={"ids": [ids[1], ids[0]]},
        headers=auth_headers,
    )
    assert response.status_code == 200, response.text

    detail = await client.get(f"{BASE}/courses/{course['id']}", headers=auth_headers)
    titles = [t["title"] for t in detail.json()["modules"][0]["topics"]]
    assert titles == ["two", "one"]


async def test_reorder_ignores_ids_from_another_course(client: AsyncClient, auth_headers):
    """The count reports what moved, so a caller that sent something stale can tell."""
    mine = await _create_course(client, auth_headers)
    other = await _create_course(client, auth_headers)
    my_module = await _module_of(client, auth_headers, mine["id"])
    other_module = await _module_of(client, auth_headers, other["id"])

    response = await client.patch(
        f"{BASE}/courses/{mine['id']}/modules/reorder",
        json={"ids": [other_module["id"], my_module["id"]]},
        headers=auth_headers,
    )
    assert response.json()["reordered"] == 1


async def test_archive_then_unarchive_returns_a_course_to_the_library(
    client: AsyncClient, auth_headers
):
    """The two endpoints mirror each other, which is why unarchive is a named action rather than a field
    write: an area where one direction is an action and the other a convention invites clients to
    diverge."""
    created = await _create_course(client, auth_headers)

    archived = await client.post(f"{BASE}/courses/{created['id']}/archive", headers=auth_headers)
    assert archived.status_code == 200, archived.text
    assert archived.json()["archived"] is True

    restored = await client.post(f"{BASE}/courses/{created['id']}/unarchive", headers=auth_headers)
    assert restored.status_code == 200, restored.text
    assert restored.json()["archived"] is False

    # And it is back in the default library listing, which filters archived out.
    listed = await client.get(f"{BASE}/courses?search={created['title']}", headers=auth_headers)
    assert created["id"] in [c["id"] for c in listed.json()["items"]]


async def test_unarchiving_another_learners_course_is_refused(client: AsyncClient, auth_headers):
    created = await _create_course(client, auth_headers)
    intruder = await _login(client)
    response = await client.post(f"{BASE}/courses/{created['id']}/unarchive", headers=intruder)
    assert response.status_code in (403, 404), response.text


async def test_the_wizards_brief_and_style_round_trip(client: AsyncClient, auth_headers):
    """`sourcePrompt` is the wizard's largest input and was discarded; `teachingStyle` is scoped to the
    course rather than written to the learner's global preference."""
    created = await _create_course(
        client,
        auth_headers,
        sourcePrompt="Teach me graph algorithms with lots of diagrams",
        teachingStyle="Visual",
    )

    fetched = await client.get(f"{BASE}/courses/{created['id']}", headers=auth_headers)
    payload = fetched.json()
    assert payload["sourcePrompt"] == "Teach me graph algorithms with lots of diagrams"
    assert payload["teachingStyle"] == "Visual"


async def test_the_free_tier_cap_is_checked_before_an_outline_is_generated(
    client: AsyncClient, auth_headers, monkeypatch
):
    """The limit must refuse at the outline step, not at the save step.

    It used to be checked only when saving, so a learner at their limit walked through four wizard steps,
    waited for a curriculum to be designed, reviewed it, pressed Create — and only then found out. That spent
    a model call on an outline that could never be saved, and spent their time on a decision already made.

    Monkeypatched rather than driven by creating courses: the cap is two per rolling month and the fixture
    learner's tier is not FREE, so the honest way to exercise the refusal is to make the guard itself refuse.
    This asserts the *ordering* — that generation is never reached — which is the thing that regressed.
    """
    from src.domains.knowledge.services import course_service
    from src.shared.exceptions import ForbiddenError

    generated = False

    async def refuse(_user):
        raise ForbiddenError("You can only create 2 courses per month on the free plan.")

    async def record_generation(*args, **kwargs):
        nonlocal generated
        generated = True
        return {}

    monkeypatch.setattr(course_service, "ensure_can_create_course", refuse)
    monkeypatch.setattr(
        "src.domains.personal_learning.services.llm_resilient.generate_content_json",
        record_generation,
    )

    response = await client.post(
        f"{BASE}/courses/outline",
        json={"title": "Anything", "brief": "A brief long enough to pass validation"},
        headers=auth_headers,
    )

    assert response.status_code == 403, response.text
    assert (
        generated is False
    ), "the outline was generated despite the learner being over their limit"


async def test_the_course_cap_returns_the_shared_upgrade_payload(
    client: AsyncClient, auth_headers, monkeypatch
):
    """The refusal has to be actionable, not just a sentence.

    It used to raise a plain `ForbiddenError`, so the only thing a client could do with it was print it. The
    quiz and document gates already answer with a typed payload carrying what the capability is worth, whether
    a trial is available and where to go — this now uses the same shape, so one component renders all three.

    `upgradeRequired` is the discriminant the client keys on, which is why the assertion is on that rather
    than on the wording.
    """
    from src.domains.personal_learning.services import feature_tier_service

    async def free_tier(_user_id):
        return ("free", False, None)

    async def at_the_limit(_where):
        return 99

    monkeypatch.setattr(feature_tier_service, "get_effective_tier", free_tier)
    monkeypatch.setattr(knowledge_repo, "count_courses", at_the_limit)

    response = await client.post(
        f"{BASE}/courses",
        json={"title": "One too many", "difficulty": "BEGINNER"},
        headers=auth_headers,
    )

    assert response.status_code == 403, response.text
    # FastAPI nests `HTTPException.detail`, which is the envelope `getApiError` already understands.
    detail = response.json()["detail"]
    assert detail["upgradeRequired"] is True
    assert detail["capability"] == "course_creation"
    assert detail["upgradeUrl"] == "/subscription"
    # Read from the capability matrix rather than written here, so the number and the copy cannot drift.
    assert "unlimited courses" in detail["upgradeValue"]
    assert isinstance(detail["trialAvailable"], bool)


async def test_a_plus_learner_is_not_capped(client: AsyncClient, auth_headers, monkeypatch):
    """The count is not even taken at Plus — an unlimited allowance should not cost a query."""
    from src.domains.personal_learning.services import feature_tier_service

    counted = False

    async def plus_tier(_user_id):
        return ("plus", False, None)

    async def record_count(_where):
        nonlocal counted
        counted = True
        return 99

    monkeypatch.setattr(feature_tier_service, "get_effective_tier", plus_tier)
    monkeypatch.setattr(knowledge_repo, "count_courses", record_count)

    response = await client.post(
        f"{BASE}/courses",
        json={"title": "Unlimited", "difficulty": "BEGINNER"},
        headers=auth_headers,
    )

    assert response.status_code == 201, response.text
    assert counted is False, "a Plus learner should not be counted against a free-tier allowance"
