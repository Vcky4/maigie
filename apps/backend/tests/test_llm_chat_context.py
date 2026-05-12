"""Tests for llm_chat_context helpers."""

from src.services.llm_chat_context import (
    build_enhanced_chat_user_message,
    map_gemini_tool_to_action_type,
)


def test_map_add_summary_alias():
    assert map_gemini_tool_to_action_type("add_summary_to_note") == "add_summary"


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
