"""Unit tests for chat_helpers (no database)."""

import os

os.environ.setdefault("SKIP_DB_FIXTURE", "1")

from src.routes.chat_helpers import (
    MAIGIE_MENTION_PATTERN,
    _extract_suggestion,
    _guess_image_media_type,
    _map_db_role_to_client,
    _strip_maigie_mention,
)


def test_strip_maigie_mention():
    assert _strip_maigie_mention("@Maigie help me") == "help me"
    assert _strip_maigie_mention("no mention here") == "no mention here"


def test_maigie_mention_pattern_detects():
    assert MAIGIE_MENTION_PATTERN.search("hi @maigie there")


def test_map_db_role_to_client():
    assert _map_db_role_to_client("USER") == "user"
    assert _map_db_role_to_client("ASSISTANT") == "assistant"
    assert _map_db_role_to_client("OTHER") == "system"


def test_extract_suggestion_splits():
    text = "Here is your plan.\n\nWould you like me to add deadlines?"
    main, sug = _extract_suggestion(text)
    assert "Would you like me" in (sug or "")
    assert "Here is your plan" in main


def test_guess_image_media_type():
    assert _guess_image_media_type("chat-images/x.png", "") == "image/png"
    assert _guess_image_media_type("x.bin", "image/webp") == "image/webp"
