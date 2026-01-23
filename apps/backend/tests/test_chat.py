from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from src.main import app
from src.services.action_service import action_service


# --- 1. Unit Test: Action Detection Logic ---
@pytest.mark.asyncio
async def test_action_execution_flow():
    """
    Verify that the ActionService correctly creates a course
    when valid data is passed.
    """
    # FIX: Patch the entire 'db' object in action_service, not individual methods
    with patch("src.services.action_service.db") as mock_db:
        # Setup the async mocks for the chain
        mock_db.course.create = AsyncMock()
        mock_db.module.create = AsyncMock()
        mock_db.topic.create = AsyncMock()

        # Setup mock return values
        mock_db.course.create.return_value.id = "course_123"
        mock_db.course.create.return_value.title = "Test Python Course"
        mock_db.module.create.return_value.id = "mod_123"

        # valid action payload
        action_data = {
            "title": "Test Python Course",
            "description": "A test course",
            "modules": [{"title": "Module 1", "topics": ["Topic A", "Topic B"]}],
        }

        # Run the service
        result = await action_service.execute_action("create_course", action_data, "user_123")

        # Assertions
        assert result["status"] == "success"
        assert result["course_id"] == "course_123"

        # Verify DB was called
        mock_db.course.create.assert_called_once()
        assert mock_db.module.create.call_count == 1  # 1 module
        assert mock_db.topic.create.call_count == 2  # 2 topics


# --- 2. API Test: Voice Endpoint ---
@pytest.mark.asyncio
async def test_voice_endpoint_security():
    """
    Ensure the /voice endpoint rejects requests without a token.
    """
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # Create a dummy file
        files = {"file": ("test.mp3", b"dummy content", "audio/mpeg")}

        # Call without token
        response = await ac.post("/api/v1/chat/voice", files=files)

        # FIX: Your main.py converts 422 (Validation Error) to 400 (Bad Request)
        assert response.status_code == 400


@pytest.mark.asyncio
async def test_voice_endpoint_success():
    """
    Mock the Gemini Voice Service and test a successful upload.
    """
    # Mock the voice service so we don't hit Google API
    with patch(
        "src.routes.chat.voice_service.transcribe_audio", new_callable=AsyncMock
    ) as mock_transcribe:
        mock_transcribe.return_value = "This is a mocked transcription."

        # Mock Auth to bypass token check
        mock_user = MagicMock()
        mock_user.id = "user_123"

        with patch("src.routes.chat.get_current_user_ws", return_value=mock_user):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                files = {"file": ("test.mp3", b"audio_data", "audio/mpeg")}

                # Call with a fake token
                response = await ac.post("/api/v1/chat/voice?token=fake_token", files=files)

                assert response.status_code == 200
                assert response.json() == {"text": "This is a mocked transcription."}
