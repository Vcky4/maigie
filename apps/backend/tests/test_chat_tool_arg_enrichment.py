"""Unit tests for chat tool arg repair (no DB when only testing pure helpers)."""

import pytest

from src.domains.intelligence.action.tool_arg_enrichment import (
    enrich_tool_args_for_llm as enrich,
)
from src.domains.intelligence.action.tool_arg_enrichment import (
    merge_successful_tool_result_into_created_ids,
)


@pytest.mark.asyncio
async def test_enrich_resolves_dollar_course_id():
    out = await enrich(
        "create_goal",
        {"title": "G", "description": "", "course_id": "$course_id"},
        context=None,
        created_ids={"course_id": "c-real", "courseId": "c-real"},
        user_id="u1",
    )
    assert out["course_id"] == "c-real"


@pytest.mark.asyncio
async def test_enrich_circle_id_from_context():
    out = await enrich(
        "recommend_resources",
        {"query": "videos"},
        context={"circleId": "circ-1"},
        created_ids=None,
        user_id="u1",
    )
    assert out["circle_id"] == "circ-1"


def test_merge_create_course():
    d: dict = {}
    merge_successful_tool_result_into_created_ids(
        d,
        "create_course",
        {"status": "success", "course_id": "c-new"},
    )
    assert d["course_id"] == "c-new"
    assert d["courseId"] == "c-new"


def test_merge_ignores_error_status():
    d: dict = {}
    merge_successful_tool_result_into_created_ids(
        d,
        "create_course",
        {"status": "error", "course_id": "c-x"},
    )
    assert d == {}


def test_merge_create_schedule_nested():
    d: dict = {}
    merge_successful_tool_result_into_created_ids(
        d,
        "create_schedule",
        {"status": "success", "schedule": {"id": "s-1", "title": "Study"}},
    )
    assert d["schedule_id"] == "s-1"


@pytest.mark.asyncio
async def test_enrich_repairs_one_terminal_comma_on_valid_schedule_datetimes():
    args = {
        "title": "Study",
        "start_at": "2026-09-09T01:49:00Z,",
        "end_at": "2026-09-09T02:49:00Z,",
    }

    out = await enrich(
        "create_schedule",
        args,
        context=None,
        created_ids=None,
        user_id="u1",
    )

    assert out["start_at"] == "2026-09-09T01:49:00Z"
    assert out["end_at"] == "2026-09-09T02:49:00Z"
    assert args["end_at"] == "2026-09-09T02:49:00Z,"


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["tomorrow,", "2026-09-09T02:49:00Z,,"])
async def test_enrich_leaves_unrepairable_schedule_datetimes_for_validation(value):
    out = await enrich(
        "create_schedule",
        {"title": "Study", "start_at": value, "end_at": value},
        context=None,
        created_ids=None,
        user_id="u1",
    )

    assert out["start_at"] == value
    assert out["end_at"] == value
