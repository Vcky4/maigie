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


# --- 3. Unit Test: WebSocket Continue Flow ---
@pytest.mark.asyncio
async def test_websocket_continue_after_message_processing():
    """
    Verify that after processing a message and sending responses,
    the WebSocket loop continues to the next message.
    """
    from unittest.mock import AsyncMock, MagicMock, patch
    from fastapi import WebSocket

    # Mock WebSocket
    mock_websocket = MagicMock(spec=WebSocket)
    mock_websocket.receive_text = AsyncMock(side_effect=["Hello", "World", KeyboardInterrupt])

    # Mock user
    mock_user = MagicMock()
    mock_user.id = "user_123"

    # Mock database
    mock_session = MagicMock()
    mock_session.id = "session_123"
    mock_session.isActive = True

    with (
        patch("src.routes.chat.db") as mock_db,
        patch("src.routes.chat.manager") as mock_manager,
        patch("src.routes.chat.llm_service") as mock_llm_service_patch,
        patch("src.routes.chat.get_current_user_ws", return_value=mock_user),
    ):

        # Setup database mocks
        mock_db.chatsession.find_first = AsyncMock(return_value=mock_session)
        mock_db.chatmessage.create = AsyncMock(return_value=MagicMock(id="msg_123"))
        mock_db.user.find_unique = AsyncMock(return_value=MagicMock(tier="FREE", credits=1000))
        mock_db.chatsession.find_first.return_value = mock_session

        # Setup LLM service mock - llm_service is imported directly, patch the instance
        mock_llm_instance = MagicMock()
        mock_llm_instance.get_chat_response_with_tools = AsyncMock(
            return_value=("Test response", {"input_tokens": 10, "output_tokens": 5}, [], [])
        )
        # Patch the imported llm_service instance in the chat module
        import src.routes.chat as chat_module

        chat_module.llm_service = mock_llm_instance

        # Setup manager mocks
        mock_manager.connect = AsyncMock()
        mock_manager.send_personal_message = AsyncMock()
        mock_manager.send_json = AsyncMock()
        mock_manager.disconnect = MagicMock()

        # Import and call the WebSocket endpoint
        from src.routes.chat import websocket_endpoint

        try:
            await websocket_endpoint(mock_websocket, mock_user)
        except KeyboardInterrupt:
            # Expected - this simulates the loop continuing
            pass

        # Verify that multiple messages were processed (continue worked)
        assert mock_websocket.receive_text.call_count >= 2
        assert mock_manager.send_personal_message.call_count >= 2
