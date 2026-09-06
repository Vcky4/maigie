"""Tests for LLM context helpers."""

from src.domains.intelligence.reasoning.llm.context import (
    build_enhanced_chat_user_message,
    map_tool_to_action_type,
    tool_has_side_effect,
)


def test_map_add_summary_alias():
    assert map_tool_to_action_type("add_summary_to_note") == "add_summary"


def test_build_enhanced_includes_user_message_and_date():
    text = build_enhanced_chat_user_message("Hello", None)
    assert "User Message: Hello" in text
    assert "Current Date & Time:" in text


def test_build_enhanced_course_topic():
    text = build_enhanced_chat_user_message(
        "Q",
        {"courseTitle": "Bio", "topicTitle": "Cells", "topicContent": "x" * 400},
    )
    assert "Bio" in text
    assert "Cells" in text
    assert "..." in text  # truncated topic


def test_owner_scoped_personalization_and_entities_reach_the_provider_prompt_bounded():
    text = build_enhanced_chat_user_message(
        "What should I study?",
        {
            "learnerProfile": {
                "purpose": "Exam readiness",
                "subjects": ["Physics", "Math"],
                "goals": "Understand entropy",
                "explanationStyle": "examples first",
            },
            "learningRhythm": {
                "avgSessionMinutes": 25,
                "consistencyScore": 88,
                "bestDayOfWeek": "Tuesday",
            },
            "dueReviewCount": 7,
            "examPrep": {"id": "prep_1", "subject": "Physics"},
            "studyPlan": {"id": "plan_1", "title": "Finals"},
            "goal": {"id": "goal_1", "title": "Thermodynamics"},
            "reflection": {"id": "reflection_1", "summary": "x" * 2_000},
            "space": {
                "id": "space_1",
                "name": "Physics Cohort",
                "description": "Thermodynamics study group",
                "role": "MEMBER",
                "membershipVerified": True,
            },
        },
    )
    for expected in (
        "Exam readiness",
        "Physics, Math",
        "average session 25 minutes",
        "Flashcards due for review: 7",
        "Exam preparation (owner-scoped, id prep_1)",
        "Study plan (owner-scoped, id plan_1)",
        "Goal (owner-scoped, id goal_1)",
        "Reflection (owner-scoped, id reflection_1)",
        "Verified learning space",
        "Physics Cohort",
        "learner role: MEMBER",
    ):
        assert expected in text
    assert "x" * 601 not in text


def test_retry_safety_classifies_every_exposed_non_create_mutation():
    for tool_name in (
        "delete_course",
        "save_user_fact",
        "email_user",
        "regenerate_schedule",
        "complete_review",
        "update_course_outline",
        "complete_topic_and_continue",
    ):
        assert tool_has_side_effect(tool_name), tool_name
    assert not tool_has_side_effect("get_user_notes")
