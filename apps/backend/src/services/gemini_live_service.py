"""
Gemini Live API Service using direct WebSocket connections.
Handles real-time voice conversations with Gemini Live API.
"""

import asyncio
import logging
import os
import uuid
from typing import Callable, Optional

from google.genai import Client, types

logger = logging.getLogger(__name__)


class GeminiLiveConversationService:
    """
    Service for managing real-time voice conversations using Gemini Live API.
    Uses direct WebSocket connections to Google's Gemini Live API.
    """

    def __init__(self, api_key: str):
        """
        Initialize the Gemini Live service.

        Args:
            api_key: Google Gemini API key
        """
        self.api_key = api_key
        self.client = Client(api_key=api_key)
        self.active_sessions: dict[str, dict] = {}  # Store active session info

    async def start_conversation(
        self,
        user_id: str,
        session_id: Optional[str] = None,
        on_user_message: Optional[Callable[[str], None]] = None,
        on_assistant_message: Optional[Callable[[str], None]] = None,
        on_transcription: Optional[Callable[[str], None]] = None,
        on_audio: Optional[Callable[[bytes], None]] = None,
        system_instruction: Optional[str] = None,
    ) -> dict:
        """
        Start a new Gemini Live conversation session.

        Args:
            user_id: User ID for the conversation
            session_id: Unique session ID (auto-generated if not provided)
            on_user_message: Callback when user speaks (transcribed text)
            on_assistant_message: Callback when assistant responds (text)
            on_transcription: Callback for transcription updates
            system_instruction: Custom system instruction for the AI

        Returns:
            Dictionary with session_id and status
        """
        if session_id is None:
            session_id = str(uuid.uuid4())

        if session_id in self.active_sessions:
            logger.warning(f"Session {session_id} already exists, stopping previous session")
            await self.stop_conversation(session_id)

        # Default system instruction
        default_instruction = (
            "You are Maigie, an intelligent study companion. "
            "Your goal is to help students organize learning, generate courses, "
            "manage schedules, create notes, and summarize content. "
            "Be conversational, helpful, and encouraging."
        )

        # Create Live API configuration
        system_instruction_text = system_instruction or default_instruction

        # Create Live API session
        # Note: connect() returns an async context manager, so we need to enter it explicitly
        try:
            # Use a valid Gemini Live model
            # Live API models typically don't use "models/" prefix
            # Try different model names in order of preference
            model_names = [
                "gemini-2.5-flash-native-audio-preview-12-2025",
                "gemini-live-2.5-flash-native-audio",
                "gemini-live-2.5-flash",
            ]

            context_manager = None
            last_error = None
            successful_model = None
            session = None

            # Try different configurations for each model
            for model_name in model_names:
                if session is not None:
                    break

                # Determine if this is a native-audio model
                is_native_audio = "native-audio" in model_name.lower()

                # Try configurations in order of preference
                configs_to_try = []

                if is_native_audio:
                    # Native audio models require AUDIO modality
                    # Try AUDIO-only first, then AUDIO+TEXT
                    configs_to_try = [
                        {
                            "name": "AUDIO-only with speech_config",
                            "config": types.LiveConnectConfig(
                                system_instruction=types.Content(
                                    parts=[types.Part(text=system_instruction_text)]
                                ),
                                response_modalities=["AUDIO"],
                                speech_config=types.SpeechConfig(
                                    voice_config=types.VoiceConfig(
                                        prebuilt_voice_config=types.PrebuiltVoiceConfig(
                                            voice_name="Aoede"
                                        )
                                    ),
                                    language_code="en-US",
                                ),
                            ),
                        },
                        {
                            "name": "AUDIO-only without speech_config",
                            "config": types.LiveConnectConfig(
                                system_instruction=types.Content(
                                    parts=[types.Part(text=system_instruction_text)]
                                ),
                                response_modalities=["AUDIO"],
                            ),
                        },
                        {
                            "name": "AUDIO+TEXT with speech_config",
                            "config": types.LiveConnectConfig(
                                system_instruction=types.Content(
                                    parts=[types.Part(text=system_instruction_text)]
                                ),
                                response_modalities=["AUDIO", "TEXT"],
                                speech_config=types.SpeechConfig(
                                    voice_config=types.VoiceConfig(
                                        prebuilt_voice_config=types.PrebuiltVoiceConfig(
                                            voice_name="Aoede"
                                        )
                                    ),
                                    language_code="en-US",
                                ),
                            ),
                        },
                    ]
                else:
                    # Non-native-audio models can use TEXT or AUDIO+TEXT
                    configs_to_try = [
                        {
                            "name": "AUDIO+TEXT with speech_config",
                            "config": types.LiveConnectConfig(
                                system_instruction=types.Content(
                                    parts=[types.Part(text=system_instruction_text)]
                                ),
                                response_modalities=["AUDIO", "TEXT"],
                                speech_config=types.SpeechConfig(
                                    voice_config=types.VoiceConfig(
                                        prebuilt_voice_config=types.PrebuiltVoiceConfig(
                                            voice_name="Aoede"
                                        )
                                    ),
                                    language_code="en-US",
                                ),
                            ),
                        },
                        {
                            "name": "TEXT-only",
                            "config": types.LiveConnectConfig(
                                system_instruction=types.Content(
                                    parts=[types.Part(text=system_instruction_text)]
                                ),
                                response_modalities=["TEXT"],
                            ),
                        },
                    ]

                for config_attempt in configs_to_try:
                    try:
                        logger.info(
                            f"Attempting {model_name} with config: {config_attempt['name']}"
                        )
                        context_manager = self.client.aio.live.connect(
                            model=model_name,
                            config=config_attempt["config"],
                        )
                        logger.info(
                            f"Successfully created context manager with {model_name} using {config_attempt['name']}"
                        )

                        # Try to enter the context manager
                        session = await context_manager.__aenter__()
                        successful_model = model_name
                        logger.info(
                            f"Successfully connected with {model_name} using {config_attempt['name']}"
                        )
                        break
                    except Exception as e:
                        logger.warning(f"Failed {model_name} with {config_attempt['name']}: {e}")
                        last_error = e
                        context_manager = None
                        continue

                if session is not None:
                    break

            if session is None:
                raise Exception(
                    f"Failed to connect with any model/config. Last error: {last_error}"
                )

            # Store session info
            session_info = {
                "session": session,
                "context_manager": context_manager,  # Store context manager for cleanup
                "user_id": user_id,
                "session_id": session_id,
                "on_user_message": on_user_message,
                "on_assistant_message": on_assistant_message,
                "on_transcription": on_transcription,
                "on_audio": on_audio,
                "task": None,
            }

            # Start listening for responses in background
            task = asyncio.create_task(
                self._handle_responses(
                    session,
                    session_id,
                    on_user_message,
                    on_assistant_message,
                    on_transcription,
                    on_audio,
                )
            )
            session_info["task"] = task

            self.active_sessions[session_id] = session_info

            logger.info(f"Started Gemini Live conversation session {session_id} for user {user_id}")

            # Send initial greeting to trigger AI response
            try:
                greeting = "Hello! I'm Maigie, your study companion. How can I help you today?"
                await self.send_text(session_id, greeting, turn_complete=True)
                logger.info(f"Sent initial greeting for session {session_id}")
            except Exception as e:
                logger.warning(f"Failed to send initial greeting for session {session_id}: {e}")

            return {"session_id": session_id, "status": "started"}

        except Exception as e:
            logger.error(f"Error starting Gemini Live session: {e}", exc_info=True)
            raise

    async def _handle_responses(
        self,
        session,
        session_id: str,
        on_user_message: Optional[Callable[[str], None]],
        on_assistant_message: Optional[Callable[[str], None]],
        on_transcription: Optional[Callable[[str], None]],
        on_audio: Optional[Callable[[bytes], None]] = None,
    ):
        """Handle responses from Gemini Live API."""
        try:
            async for message in session.receive():
                try:
                    # Get current callbacks from session_info (they may be updated dynamically)
                    session_info = self.active_sessions.get(session_id)
                    current_on_audio = session_info.get("on_audio") if session_info else on_audio
                    current_on_assistant = (
                        session_info.get("on_assistant_message")
                        if session_info
                        else on_assistant_message
                    )

                    # Handle server content (assistant responses)
                    if hasattr(message, "server_content") and message.server_content:
                        for content in message.server_content:
                            if hasattr(content, "model_turn") and content.model_turn:
                                model_turn = content.model_turn

                                # Handle parts (text and audio)
                                if hasattr(model_turn, "parts") and model_turn.parts:
                                    for part in model_turn.parts:
                                        # Handle text responses
                                        if hasattr(part, "text") and part.text:
                                            text = part.text
                                            callback = current_on_assistant or on_assistant_message
                                            if callback:
                                                if asyncio.iscoroutinefunction(callback):
                                                    await callback(text)
                                                else:
                                                    callback(text)

                                        # Handle audio responses - check inline_data first
                                        if hasattr(part, "inline_data") and part.inline_data:
                                            audio_data = part.inline_data.data
                                            if audio_data:
                                                callback = current_on_audio or on_audio
                                                if callback:
                                                    logger.info(
                                                        f"Received audio response for session {session_id}, size: {len(audio_data)} bytes"
                                                    )
                                                    if asyncio.iscoroutinefunction(callback):
                                                        await callback(audio_data)
                                                    else:
                                                        callback(audio_data)

                                        # Also check for audio in other possible formats
                                        if hasattr(part, "audio") and part.audio:
                                            audio_data = (
                                                part.audio.data
                                                if hasattr(part.audio, "data")
                                                else None
                                            )
                                            if audio_data:
                                                callback = current_on_audio or on_audio
                                                if callback:
                                                    logger.info(
                                                        f"Received audio response (alt format) for session {session_id}, size: {len(audio_data)} bytes"
                                                    )
                                                    if asyncio.iscoroutinefunction(callback):
                                                        await callback(audio_data)
                                                    else:
                                                        callback(audio_data)

                    # Handle user content (transcriptions)
                    if hasattr(message, "user_content") and message.user_content:
                        for content in message.user_content:
                            if hasattr(content, "parts") and content.parts:
                                for part in content.parts:
                                    if hasattr(part, "text") and part.text:
                                        text = part.text
                                        if on_transcription:
                                            if asyncio.iscoroutinefunction(on_transcription):
                                                await on_transcription(text)
                                            else:
                                                on_transcription(text)

                                        if on_user_message:
                                            if asyncio.iscoroutinefunction(on_user_message):
                                                await on_user_message(text)
                                            else:
                                                on_user_message(text)

                except Exception as e:
                    logger.error(
                        f"Error processing response in session {session_id}: {e}", exc_info=True
                    )

        except asyncio.CancelledError:
            logger.info(f"Response handler cancelled for session {session_id}")
        except Exception as e:
            logger.error(f"Error in response handler for session {session_id}: {e}", exc_info=True)
        finally:
            # Clean up session if it's still in active_sessions
            if session_id in self.active_sessions:
                session_info = self.active_sessions[session_id]
                context_manager = session_info.get("context_manager")
                try:
                    if context_manager:
                        await context_manager.__aexit__(None, None, None)
                    elif session:
                        await session.close()
                except Exception as e:
                    logger.warning(f"Error closing session {session_id}: {e}")

    async def send_audio(self, session_id: str, audio_data: bytes) -> bool:
        """
        Send audio data to the Gemini Live session.

        Args:
            session_id: Session ID
            audio_data: Raw audio bytes (PCM, 16-bit, 16kHz, mono)

        Returns:
            True if audio was sent, False otherwise
        """
        if session_id not in self.active_sessions:
            logger.warning(f"Session {session_id} not found")
            return False

        try:
            session_info = self.active_sessions[session_id]
            session = session_info["session"]

            # Send audio to Gemini Live API
            # Use Blob with correct MIME type including sample rate
            await session.send_realtime_input(
                audio=types.Blob(
                    data=audio_data,
                    mime_type="audio/pcm;rate=16000",
                ),
            )

            return True
        except Exception as e:
            logger.error(f"Error sending audio to session {session_id}: {e}", exc_info=True)
            return False

    async def send_text(self, session_id: str, text: str, turn_complete: bool = True) -> bool:
        """
        Send text message to the Gemini Live session.

        Args:
            session_id: Session ID
            text: Text message to send
            turn_complete: Whether this is the end of the turn

        Returns:
            True if text was sent, False otherwise
        """
        if session_id not in self.active_sessions:
            logger.warning(f"Session {session_id} not found")
            return False

        try:
            session_info = self.active_sessions[session_id]
            session = session_info["session"]

            # Send text to Gemini Live API
            await session.send_client_content(
                turns=[types.Content(parts=[types.Part(text=text)])],
                turn_complete=turn_complete,
            )

            return True
        except Exception as e:
            logger.error(f"Error sending text to session {session_id}: {e}", exc_info=True)
            return False

    async def stop_conversation(self, session_id: str) -> bool:
        """
        Stop an active conversation session.

        Args:
            session_id: Session ID to stop

        Returns:
            True if session was stopped, False if not found
        """
        if session_id not in self.active_sessions:
            logger.warning(f"Session {session_id} not found")
            return False

        session_info = self.active_sessions[session_id]
        try:
            session = session_info.get("session")
            task = session_info.get("task")

            # Cancel the response handler task
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

            # Close the session
            if session:
                try:
                    await session.close()
                except Exception as e:
                    logger.warning(f"Error closing session: {e}")

            # Remove session from active_sessions (handle race condition)
            self.active_sessions.pop(session_id, None)
            logger.info(f"Stopped conversation session {session_id}")
            return True
        except Exception as e:
            logger.error(f"Error stopping session {session_id}: {e}", exc_info=True)
            # Ensure session is removed even if there was an error
            self.active_sessions.pop(session_id, None)
            return False

    def is_session_active(self, session_id: str) -> bool:
        """Check if a session is currently active."""
        return session_id in self.active_sessions

    def get_active_sessions(self) -> list[str]:
        """Get list of active session IDs."""
        return list(self.active_sessions.keys())

    def get_session_info(self, session_id: str) -> Optional[dict]:
        """Get information about a session."""
        return self.active_sessions.get(session_id)


# Global instance
_gemini_live_service: Optional[GeminiLiveConversationService] = None


def get_gemini_live_service() -> GeminiLiveConversationService:
    """Get or create the global Gemini Live service instance."""
    global _gemini_live_service
    if _gemini_live_service is None:
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise ValueError("GEMINI_API_KEY environment variable is required")
        _gemini_live_service = GeminiLiveConversationService(api_key=api_key)
    return _gemini_live_service
