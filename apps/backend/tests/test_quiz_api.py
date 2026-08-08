"""HTTP integration tests for the quiz lifecycle.

These prove at the wire level what ``test_quiz_engine_scoring.py`` proves at the
service level: the answer key does not reach the client before the learner
answers, and a question cannot be answered from someone else's session.

They require a database. Without ``DATABASE_URL`` the ``db_lifecycle`` fixture
skips them, so they are CI-safe but do not run in a bare checkout.

Question generation is mocked. Hitting a real LLM would make these slow,
non-deterministic, and dependent on a provider key.
"""

import uuid
from datetime import UTC, datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient

PREP_PATH = "/api/v1/learning/preparations"
QUIZ_PATH = "/api/v1/learning/quizzes"

# What the mocked model returns. Two questions, distinct keys and explanations so
# a leak of either is unambiguous in an assertion.
GENERATED = [
    {
        "topicNumber": 1,
        "questionText": "What does a p-value measure?",
        "questionType": "MULTIPLE_CHOICE",
        "options": ["evidence against the null", "the effect size", "the sample size", "the mean"],
        "correctAnswer": "evidence against the null",
        "explanation": "FIRST_EXPLANATION_CANARY",
    },
    {
        "topicNumber": 1,
        "questionText": "What is a type I error?",
        "questionType": "MULTIPLE_CHOICE",
        "options": ["a false positive", "a false negative", "a bias", "a variance"],
        "correctAnswer": "a false positive",
        "explanation": "SECOND_EXPLANATION_CANARY",
    },
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _create_preparation(client: AsyncClient, headers: dict) -> str:
    target = (datetime.now(UTC) + timedelta(days=30)).isoformat()
    response = await client.post(
        PREP_PATH,
        json={
            "subject": f"Statistics {uuid.uuid4().hex[:6]}",
            "type": "EXAM",
            "targetDate": target,
        },
        headers=headers,
    )
    if response.status_code != 201:
        pytest.skip(f"Could not create preparation: {response.status_code} - {response.text[:200]}")
    return response.json()["id"]


async def _seed_topic(prep_id: str, title: str = "Hypothesis Testing") -> str:
    """Insert a topic directly.

    The only route that creates topics runs LLM extraction, which is not what
    these tests are about.
    """
    from src.domains.personal_learning.repository import personal_learning_repo as repo

    topic = await repo.create_prep_topic(
        {
            "prepId": prep_id,
            "title": title,
            "description": "Null and alternative hypotheses.",
            "estimatedMinutes": 30,
            "orderIndex": 0,
        }
    )
    return topic.id


def _mock_generation(payload=None):
    """Patch the generation call where the quiz engine imports it from."""
    return patch(
        "src.domains.personal_learning.services.llm_resilient.generate_content_json",
        new_callable=AsyncMock,
        return_value=GENERATED if payload is None else payload,
    )


async def _start_quiz(client: AsyncClient, headers: dict, prep_id: str, payload=None):
    with _mock_generation(payload):
        return await client.post(
            f"{PREP_PATH}/{prep_id}/quizzes",
            json={"mode": "FULL_PRACTICE", "questionCount": 2},
            headers=headers,
        )


async def _second_user(client: AsyncClient) -> dict:
    """Register, activate, and log in a second learner, for scoping tests."""
    email = f"other_{uuid.uuid4()}@example.com"
    password = "StrongPassword123!"

    signup = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": password, "name": "Other Learner"},
    )
    if signup.status_code not in (200, 201):
        pytest.skip(f"Second-user signup failed: {signup.status_code}")

    from sqlalchemy import update as sa_update

    from src.domains.identity.db_models import User
    from src.shared.database.session import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        await session.execute(sa_update(User).where(User.email == email).values(is_active=True))
        await session.commit()

    login = await client.post(
        "/api/v1/auth/login/json", json={"email": email, "password": password}
    )
    if login.status_code != 200:
        pytest.skip(f"Second-user login failed: {login.status_code}")
    body = login.json()
    token = body.get("access_token") or body.get("accessToken")
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_quiz_start_withholds_the_answer_key(client: AsyncClient, auth_headers):
    """The Phase 4 exit gate, at the wire level."""
    prep_id = await _create_preparation(client, auth_headers)
    await _seed_topic(prep_id)

    response = await _start_quiz(client, auth_headers, prep_id)
    assert response.status_code == 201, response.text

    session = response.json()
    assert session["status"] == "IN_PROGRESS"
    assert session["totalQuestions"] == 2
    assert len(session["questions"]) == 2

    for question in session["questions"]:
        assert question["correctAnswer"] is None
        assert question["explanation"] is None
        # The learner still gets everything they need in order to answer.
        assert question["questionText"]
        assert len(question["options"]) == 4

    # The explanations exist nowhere but the key, so they are the leak canary.
    assert "FIRST_EXPLANATION_CANARY" not in response.text
    assert "SECOND_EXPLANATION_CANARY" not in response.text

    await client.delete(f"{PREP_PATH}/{prep_id}", headers=auth_headers)


async def test_answering_returns_that_questions_key_only(client: AsyncClient, auth_headers):
    prep_id = await _create_preparation(client, auth_headers)
    await _seed_topic(prep_id)
    start = await _start_quiz(client, auth_headers, prep_id)
    session = start.json()
    quiz_id = session["id"]
    first, second = session["questions"][0], session["questions"][1]

    answered = await client.post(
        f"{QUIZ_PATH}/{quiz_id}/answer",
        json={"questionId": first["id"], "userAnswer": first["options"][0]},
        headers=auth_headers,
    )
    assert answered.status_code == 200, answered.text
    result = answered.json()
    assert result["correctAnswer"] is not None
    assert result["alreadyAnswered"] is False

    # Re-reading the session now teaches on the answered question only.
    reread = await client.get(f"{QUIZ_PATH}/{quiz_id}", headers=auth_headers)
    assert reread.status_code == 200
    by_id = {q["id"]: q for q in reread.json()["questions"]}
    assert by_id[first["id"]]["correctAnswer"] is not None
    assert by_id[first["id"]]["explanation"] is not None
    assert by_id[second["id"]]["correctAnswer"] is None
    assert by_id[second["id"]]["explanation"] is None

    await client.delete(f"{PREP_PATH}/{prep_id}", headers=auth_headers)


async def test_resubmitting_replays_without_rescoring(client: AsyncClient, auth_headers):
    prep_id = await _create_preparation(client, auth_headers)
    await _seed_topic(prep_id)
    session = (await _start_quiz(client, auth_headers, prep_id)).json()
    quiz_id = session["id"]
    question = session["questions"][0]

    # Answer wrongly on purpose, which discloses the correct answer.
    wrong = next(o for o in question["options"] if o != "evidence against the null")
    first = await client.post(
        f"{QUIZ_PATH}/{quiz_id}/answer",
        json={"questionId": question["id"], "userAnswer": wrong},
        headers=auth_headers,
    )
    assert first.json()["isCorrect"] is False
    disclosed = first.json()["correctAnswer"]

    # Send the disclosed answer back. It must not become correct.
    second = await client.post(
        f"{QUIZ_PATH}/{quiz_id}/answer",
        json={"questionId": question["id"], "userAnswer": disclosed},
        headers=auth_headers,
    )
    assert second.status_code == 200
    assert second.json()["alreadyAnswered"] is True
    assert second.json()["isCorrect"] is False

    summary = await client.post(f"{QUIZ_PATH}/{quiz_id}/complete", headers=auth_headers)
    assert summary.status_code == 200
    assert summary.json()["correctCount"] == 0

    await client.delete(f"{PREP_PATH}/{prep_id}", headers=auth_headers)


async def test_question_from_another_learners_session_is_not_answerable(
    client: AsyncClient, auth_headers
):
    """The answer-key IDOR, end to end.

    The intruder holds a valid session of their own, so the session ownership
    check passes. Only the question-to-session scoping stops them reading the
    victim's answer key.
    """
    victim_prep = await _create_preparation(client, auth_headers)
    await _seed_topic(victim_prep)
    victim_session = (await _start_quiz(client, auth_headers, victim_prep)).json()
    victim_question_id = victim_session["questions"][0]["id"]

    intruder_headers = await _second_user(client)
    intruder_prep = await _create_preparation(client, intruder_headers)
    await _seed_topic(intruder_prep)
    intruder_session = (await _start_quiz(client, intruder_headers, intruder_prep)).json()

    attack = await client.post(
        f"{QUIZ_PATH}/{intruder_session['id']}/answer",
        json={"questionId": victim_question_id, "userAnswer": "anything"},
        headers=intruder_headers,
    )

    assert attack.status_code == 404
    assert "FIRST_EXPLANATION_CANARY" not in attack.text
    assert "evidence against the null" not in attack.text

    # Reading the victim's session directly is also refused.
    direct = await client.get(f"{QUIZ_PATH}/{victim_session['id']}", headers=intruder_headers)
    assert direct.status_code == 404

    await client.delete(f"{PREP_PATH}/{victim_prep}", headers=auth_headers)
    await client.delete(f"{PREP_PATH}/{intruder_prep}", headers=intruder_headers)


async def test_completed_session_reveals_every_answer(client: AsyncClient, auth_headers):
    prep_id = await _create_preparation(client, auth_headers)
    await _seed_topic(prep_id)
    session = (await _start_quiz(client, auth_headers, prep_id)).json()
    quiz_id = session["id"]
    first = session["questions"][0]

    await client.post(
        f"{QUIZ_PATH}/{quiz_id}/answer",
        json={"questionId": first["id"], "userAnswer": first["options"][0]},
        headers=auth_headers,
    )
    complete = await client.post(
        f"{QUIZ_PATH}/{quiz_id}/complete", json={"durationSeconds": 60}, headers=auth_headers
    )
    assert complete.status_code == 200

    review = await client.get(f"{QUIZ_PATH}/{quiz_id}", headers=auth_headers)
    assert review.json()["status"] == "COMPLETED"
    # Including the question that was never answered, so review is complete.
    for question in review.json()["questions"]:
        assert question["correctAnswer"] is not None

    await client.delete(f"{PREP_PATH}/{prep_id}", headers=auth_headers)


async def test_answering_a_completed_session_is_rejected(client: AsyncClient, auth_headers):
    prep_id = await _create_preparation(client, auth_headers)
    await _seed_topic(prep_id)
    session = (await _start_quiz(client, auth_headers, prep_id)).json()
    quiz_id = session["id"]
    question = session["questions"][0]

    await client.post(f"{QUIZ_PATH}/{quiz_id}/complete", headers=auth_headers)
    rejected = await client.post(
        f"{QUIZ_PATH}/{quiz_id}/answer",
        json={"questionId": question["id"], "userAnswer": question["options"][0]},
        headers=auth_headers,
    )

    assert rejected.status_code == 409

    await client.delete(f"{PREP_PATH}/{prep_id}", headers=auth_headers)


async def test_quiz_without_topics_is_actionable_not_a_500(client: AsyncClient, auth_headers):
    """Defect 4: this used to be a generic 500 that hid the next step."""
    prep_id = await _create_preparation(client, auth_headers)

    response = await _start_quiz(client, auth_headers, prep_id)

    assert response.status_code == 409
    assert response.status_code != 500

    await client.delete(f"{PREP_PATH}/{prep_id}", headers=auth_headers)


async def test_unusable_generation_fails_rather_than_returning_an_empty_quiz(
    client: AsyncClient, auth_headers
):
    """Decision F. Every candidate here is unscorable, so none may be persisted."""
    prep_id = await _create_preparation(client, auth_headers)
    await _seed_topic(prep_id)

    unusable = [
        # Correct answer is not one of the options.
        {
            "topicNumber": 1,
            "questionText": "Unanswerable",
            "questionType": "MULTIPLE_CHOICE",
            "options": ["a", "b", "c", "d"],
            "correctAnswer": "z",
        },
        # No answer key at all.
        {
            "topicNumber": 1,
            "questionText": "Keyless",
            "questionType": "MULTIPLE_CHOICE",
            "options": ["a", "b"],
        },
    ]
    response = await _start_quiz(client, auth_headers, prep_id, payload=unusable)

    assert response.status_code == 503, response.text
    assert response.status_code != 201

    await client.delete(f"{PREP_PATH}/{prep_id}", headers=auth_headers)


async def test_unauthenticated_requests_are_refused(client: AsyncClient):
    response = await client.get(f"{QUIZ_PATH}/some-quiz-id")
    assert response.status_code in (401, 403)
