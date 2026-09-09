"""Committed contracts for AI mutation handlers migrated off ActionService."""

from __future__ import annotations

import os

os.environ.setdefault("SKIP_DB_FIXTURE", "1")

from datetime import UTC, datetime, timedelta  # noqa: E402
from types import SimpleNamespace  # noqa: E402
from unittest.mock import AsyncMock, patch  # noqa: E402

from fastapi import HTTPException  # noqa: E402

from src.domains.intelligence.action.skills.handlers import handle_tool_call  # noqa: E402

USER_ID = "handler-test-user"


async def test_create_course_uses_the_atomic_outline_service_and_returns_persisted_id():
    progress = AsyncMock()
    course = SimpleNamespace(id="course-1", title="Physics")
    user = SimpleNamespace(id=USER_ID)
    modules = [{"title": "Mechanics", "topics": ["Motion", "Forces"]}]

    with (
        patch(
            "src.domains.identity.repository.IdentityRepository.find_by_id",
            AsyncMock(return_value=user),
        ),
        patch(
            "src.domains.knowledge.services.course_service.create_course_with_outline",
            AsyncMock(return_value=course),
        ) as create,
    ):
        result = await handle_tool_call(
            "create_course",
            {"title": "Physics", "difficulty": "INTERMEDIATE", "modules": modules},
            USER_ID,
            progress_callback=progress,
        )

    assert result == {
        "status": "success",
        "action": "create_course",
        "course_id": "course-1",
        "courseId": "course-1",
        "title": "Physics",
        "message": 'Created course "Physics".',
    }
    assert create.await_args.kwargs == {
        "user": user,
        "data": {
            "title": "Physics",
            "description": "",
            "difficulty": "INTERMEDIATE",
        },
        "modules": modules,
    }
    assert progress.await_count == 2


async def test_create_course_preserves_the_canonical_upgrade_refusal():
    refusal = {
        "upgradeRequired": True,
        "reason": "Monthly course limit reached.",
        "capability": "course_creation",
        "upgradeUrl": "/subscription",
        "trialAvailable": True,
    }
    with (
        patch(
            "src.domains.identity.repository.IdentityRepository.find_by_id",
            AsyncMock(return_value=SimpleNamespace(id=USER_ID)),
        ),
        patch(
            "src.domains.knowledge.services.course_service.create_course_with_outline",
            AsyncMock(side_effect=HTTPException(status_code=403, detail=refusal)),
        ),
    ):
        result = await handle_tool_call(
            "create_course",
            {"title": "Physics", "modules": [{"title": "M", "topics": ["T"]}]},
            USER_ID,
        )

    assert result["status"] == "error"
    assert result["upgrade_required"] is True
    assert result["upgrade"] == refusal
    assert result["message"] == refusal["reason"]


async def test_create_goal_validates_with_the_domain_model_and_returns_both_id_spellings():
    goal = SimpleNamespace(id="goal-1", title="Master calculus")
    create = AsyncMock(return_value=goal)
    with patch("src.domains.progress.services.goal_service.create_goal", create):
        result = await handle_tool_call(
            "create_goal",
            {
                "title": "Master calculus",
                "target_date": "2026-10-01T12:00:00Z",
                "course_id": "course-1",
            },
            USER_ID,
        )

    assert result["status"] == "success"
    assert result["goal_id"] == result["goalId"] == "goal-1"
    payload = create.await_args.kwargs["data"]
    assert payload["targetDate"] == datetime(2026, 10, 1, 12, tzinfo=UTC)
    assert payload["courseId"] == "course-1"


async def test_create_schedule_returns_the_compatibility_envelope_from_the_persisted_block():
    start = datetime(2026, 10, 1, 12, tzinfo=UTC)
    end = start + timedelta(hours=1)
    block = SimpleNamespace(
        id="block-1",
        title="Calculus practice",
        description=None,
        start_at=start,
        end_at=end,
        recurring_rule=None,
        course_id="course-1",
        topic_id=None,
        goal_id="goal-1",
    )
    create = AsyncMock(return_value=block)
    with patch("src.domains.progress.services.schedule_service.create_block", create):
        result = await handle_tool_call(
            "create_schedule",
            {
                "title": "Calculus practice",
                "start_at": start.isoformat(),
                "end_at": end.isoformat(),
                "course_id": "course-1",
                "goal_id": "goal-1",
            },
            USER_ID,
        )

    assert result["status"] == "success"
    assert result["schedule_id"] == result["scheduleId"] == "block-1"
    assert result["schedule"] is result["data"]
    assert result["data"]["startAt"] == start.isoformat()
    assert create.await_args.kwargs["data"]["startAt"] == start


async def test_create_schedule_rejects_an_inverted_window_before_persistence():
    create = AsyncMock()
    with patch("src.domains.progress.services.schedule_service.create_block", create):
        result = await handle_tool_call(
            "create_schedule",
            {
                "title": "Impossible block",
                "start_at": "2026-10-01T13:00:00Z",
                "end_at": "2026-10-01T12:00:00Z",
            },
            USER_ID,
        )

    assert result == {"status": "error", "message": "end_at must be after start_at."}
    create.assert_not_awaited()


async def test_create_schedule_returns_validation_error_without_logging_an_exception():
    create = AsyncMock()
    with patch("src.domains.progress.services.schedule_service.create_block", create):
        result = await handle_tool_call(
            "create_schedule",
            {
                "title": "Malformed block",
                "start_at": "2026-09-09T01:49:00Z",
                "end_at": "tomorrow,",
            },
            USER_ID,
        )

    assert result["status"] == "error"
    assert result["error_type"] == "ValidationError"
    assert "endAt" in result["message"]
    create.assert_not_awaited()


async def test_retake_note_delegates_to_the_owner_scoped_note_service():
    note = SimpleNamespace(id="note-1", title="Vectors")
    retake = AsyncMock(return_value=note)
    with patch("src.domains.personal_learning.services.note_service.retake_note", retake):
        result = await handle_tool_call("retake_note", {"note_id": "note-1"}, USER_ID)

    retake.assert_awaited_once_with(user_id=USER_ID, note_id="note-1")
    assert result["status"] == "success"
    assert result["note_id"] == result["noteId"] == "note-1"


async def test_add_summary_returns_the_summary_persisted_by_the_note_service():
    note = SimpleNamespace(id="note-1", title="Vectors", summary="A concise summary")
    summarize = AsyncMock(return_value=note)
    with patch("src.domains.personal_learning.services.note_service.add_summary", summarize):
        result = await handle_tool_call("add_summary_to_note", {"note_id": "note-1"}, USER_ID)

    summarize.assert_awaited_once_with(user_id=USER_ID, note_id="note-1")
    assert result["status"] == "success"
    assert result["summary"] == "A concise summary"


async def test_add_tags_preserves_existing_tags_and_deduplicates_requested_tags():
    existing = SimpleNamespace(
        id="note-1",
        title="Vectors",
        tags=[SimpleNamespace(tag="LinearAlgebra")],
    )
    updated = SimpleNamespace(
        id="note-1",
        title="Vectors",
        tags=[SimpleNamespace(tag="LinearAlgebra"), SimpleNamespace(tag="Geometry")],
    )
    update = AsyncMock(return_value=updated)
    with (
        patch(
            "src.domains.personal_learning.services.note_service.get_note",
            AsyncMock(return_value=existing),
        ),
        patch("src.domains.personal_learning.services.note_service.update_note", update),
    ):
        result = await handle_tool_call(
            "add_tags_to_note",
            {"note_id": "note-1", "tags": ["Geometry", "Geometry", "LinearAlgebra"]},
            USER_ID,
        )

    update.assert_awaited_once_with(
        user_id=USER_ID,
        note_id="note-1",
        data={"tags": ["LinearAlgebra", "Geometry"]},
    )
    assert result["tags"] == ["LinearAlgebra", "Geometry"]


async def test_create_note_drops_an_unavailable_model_course_before_persistence():
    from src.shared.exceptions import NotFoundError

    created = SimpleNamespace(id="note-1")
    persisted = SimpleNamespace(id="note-1", title="Upper Limb Bones")
    create = AsyncMock(return_value=created)

    with (
        patch(
            "src.domains.knowledge.services.course_service.check_course_ownership",
            AsyncMock(side_effect=NotFoundError("Course", "anatomy_bone_extremities_12345")),
        ) as check_course,
        patch("src.domains.personal_learning.services.note_service.create_note", create),
        patch(
            "src.domains.personal_learning.services.note_service.get_note",
            AsyncMock(return_value=persisted),
        ),
    ):
        result = await handle_tool_call(
            "create_note",
            {
                "title": "Upper Limb Bones",
                "content": "Flashcards",
                "course_id": "anatomy_bone_extremities_12345",
            },
            USER_ID,
        )

    check_course.assert_awaited_once_with("anatomy_bone_extremities_12345", USER_ID)
    assert "courseId" not in create.await_args.kwargs["data"]
    assert result["status"] == "success"
    assert result["note_id"] == "note-1"


async def test_create_note_context_topic_wins_and_supplies_its_owned_course():
    topic = SimpleNamespace(id="topic-owned")
    module = SimpleNamespace(id="module-owned")
    course = SimpleNamespace(id="course-owned")
    created = SimpleNamespace(id="note-1")
    persisted = SimpleNamespace(id="note-1", title="Owned topic note")
    create = AsyncMock(return_value=created)
    check_course = AsyncMock()

    with (
        patch(
            "src.domains.knowledge.services.course_service.check_topic_ownership",
            AsyncMock(return_value=(topic, module, course)),
        ) as check_topic,
        patch(
            "src.domains.knowledge.services.course_service.check_course_ownership",
            check_course,
        ),
        patch("src.domains.personal_learning.services.note_service.create_note", create),
        patch(
            "src.domains.personal_learning.services.note_service.get_note",
            AsyncMock(return_value=persisted),
        ),
    ):
        result = await handle_tool_call(
            "create_note",
            {
                "title": "Owned topic note",
                "content": "Content",
                "topic_id": "model-topic",
                "course_id": "model-course",
            },
            USER_ID,
            context={"topicId": "topic-owned", "courseId": "context-course"},
        )

    check_topic.assert_awaited_once_with("topic-owned", USER_ID)
    check_course.assert_not_awaited()
    assert create.await_args.kwargs["data"]["topicId"] == "topic-owned"
    assert create.await_args.kwargs["data"]["courseId"] == "course-owned"
    assert result["status"] == "success"


async def test_create_note_owned_model_topic_replaces_a_mismatched_model_course():
    topic = SimpleNamespace(id="topic-owned")
    course = SimpleNamespace(id="course-owned")
    create = AsyncMock(return_value=SimpleNamespace(id="note-1"))

    with (
        patch(
            "src.domains.knowledge.services.course_service.check_topic_ownership",
            AsyncMock(return_value=(topic, SimpleNamespace(id="module-1"), course)),
        ),
        patch("src.domains.personal_learning.services.note_service.create_note", create),
        patch(
            "src.domains.personal_learning.services.note_service.get_note",
            AsyncMock(return_value=SimpleNamespace(id="note-1", title="Topic note")),
        ),
    ):
        result = await handle_tool_call(
            "create_note",
            {
                "title": "Topic note",
                "content": "Content",
                "topic_id": "topic-owned",
                "course_id": "wrong-course",
            },
            USER_ID,
        )

    assert create.await_args.kwargs["data"]["topicId"] == "topic-owned"
    assert create.await_args.kwargs["data"]["courseId"] == "course-owned"
    assert result["status"] == "success"


async def test_create_note_rejects_unowned_context_topic_without_writing():
    from src.shared.exceptions import ForbiddenError

    create = AsyncMock()
    with (
        patch(
            "src.domains.knowledge.services.course_service.check_topic_ownership",
            AsyncMock(side_effect=ForbiddenError("You do not own this topic")),
        ),
        patch("src.domains.personal_learning.services.note_service.create_note", create),
    ):
        result = await handle_tool_call(
            "create_note",
            {"title": "Unsafe note", "content": "Content"},
            USER_ID,
            context={"topicId": "other-users-topic"},
        )

    assert result == {"status": "error", "message": "Topic not found or access denied."}
    create.assert_not_awaited()
