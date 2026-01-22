"""
Gemini Live API Service using Pipecat.
Handles real-time voice conversations with WebRTC support.
"""

import asyncio
import logging
import os
import uuid
from typing import Callable, Optional

logger = logging.getLogger(__name__)

try:
    from pipecat.frames.frames import (
        AudioRawFrame,
        Frame,
        LLMMessagesFrame,
        TextFrame,
        TranscriptionFrame,
    )
    from pipecat.frames.frame_processor import FrameDirection, FrameProcessor
    from pipecat.pipeline.pipeline import Pipeline
    from pipecat.pipeline.runner import PipelineRunner
    from pipecat.pipeline.task import PipelineTask
    from pipecat.processors.aggregators.llm_response import LLMUserResponseAggregator
    from pipecat.services.google import GeminiLiveService
    from pipecat.transports.services.daily import DailyParams, DailyTransport

    PIPECAT_AVAILABLE = True

    class CallbackFrameProcessor(FrameProcessor):
        """Frame processor that calls callbacks for specific frame types."""

        def __init__(
            self,
            on_user_message: Optional[Callable[[str], None]] = None,
            on_assistant_message: Optional[Callable[[str], None]] = None,
            on_transcription: Optional[Callable[[str], None]] = None,
            user_id: Optional[str] = None,
        ):
            super().__init__()
            self.on_user_message = on_user_message
            self.on_assistant_message = on_assistant_message
            self.on_transcription = on_transcription
            self.user_id = user_id

        async def process_frame(self, frame: Frame, direction: FrameDirection):
            """Process frames and call appropriate callbacks."""
            await super().process_frame(frame, direction)

            try:
                if isinstance(frame, TranscriptionFrame):
                    # User speech transcribed
                    if self.on_transcription:
                        if asyncio.iscoroutinefunction(self.on_transcription):
                            await self.on_transcription(frame.text)
                        else:
                            self.on_transcription(frame.text)

                    # Check if this is from the user
                    if (
                        self.on_user_message
                        and hasattr(frame, "user_id")
                        and frame.user_id == self.user_id
                    ):
                        if asyncio.iscoroutinefunction(self.on_user_message):
                            await self.on_user_message(frame.text)
                        else:
                            self.on_user_message(frame.text)

                elif isinstance(frame, TextFrame):
                    # Assistant response text
                    if self.on_assistant_message:
                        if asyncio.iscoroutinefunction(self.on_assistant_message):
                            await self.on_assistant_message(frame.text)
                        else:
                            self.on_assistant_message(frame.text)

            except Exception as e:
                logger.error(f"Error in callback frame processor: {e}", exc_info=True)

            # Always forward frames downstream
            await self.push_frame(frame, direction)

except ImportError:
    PIPECAT_AVAILABLE = False
    logger.warning("Pipecat not available. Install with: pip install 'pipecat-ai[webrtc,google]'")
    # Define a placeholder class when pipecat is not available
    CallbackFrameProcessor = None  # type: ignore


class GeminiLiveConversationService:
    """
    Service for managing real-time voice conversations using Gemini Live API via Pipecat.
    Supports Daily.co WebRTC transport for real-time audio streaming.
    """

    def __init__(self, api_key: str):
        """
        Initialize the Gemini Live service.

        Args:
            api_key: Google Gemini API key
        """
        if not PIPECAT_AVAILABLE:
            raise ImportError(
                "Pipecat is not installed. Install with: pip install 'pipecat-ai[webrtc,google]'"
            )
        self.api_key = api_key
        self.active_sessions: dict[str, dict] = (
            {}
        )  # Store session info including task and transport

    async def start_conversation(
        self,
        room_url: str,
        token: str,
        user_id: str,
        session_id: Optional[str] = None,
        on_user_message: Optional[Callable[[str], None]] = None,
        on_assistant_message: Optional[Callable[[str], None]] = None,
        on_transcription: Optional[Callable[[str], None]] = None,
        system_instruction: Optional[str] = None,
    ) -> dict:
        """
        Start a new Gemini Live conversation session.

        Args:
            room_url: Daily.co room URL for WebRTC connection
            token: Daily.co room token
            user_id: User ID for the conversation
            session_id: Unique session ID (auto-generated if not provided)
            on_user_message: Callback when user speaks (transcribed text)
            on_assistant_message: Callback when assistant responds (text)
            on_transcription: Callback for transcription updates
            system_instruction: Custom system instruction for the AI

        Returns:
            Dictionary with session_id and task info
        """
        if session_id is None:
            session_id = str(uuid.uuid4())

        if session_id in self.active_sessions:
            logger.warning(f"Session {session_id} already exists, stopping previous session")
            await self.stop_conversation(session_id)

        # Create Daily transport for WebRTC
        transport = DailyTransport(
            room_url=room_url,
            token=token,
            bot_name="Maigie AI Assistant",
            mic_enabled=True,
            mic_sample_rate=16000,
            camera_enabled=False,
        )

        # Create Gemini Live service with system instruction
        default_instruction = (
            "You are Maigie, an intelligent study companion. "
            "Your goal is to help students organize learning, generate courses, "
            "manage schedules, create notes, and summarize content. "
            "Be conversational, helpful, and encouraging."
        )

        gemini_service = GeminiLiveService(
            api_key=self.api_key,
            system_instruction=system_instruction or default_instruction,
        )

        # Create callback frame processor
        callback_processor = CallbackFrameProcessor(
            on_user_message=on_user_message,
            on_assistant_message=on_assistant_message,
            on_transcription=on_transcription,
            user_id=user_id,
        )

        # Build pipeline with callback processor
        # Place callback processor after gemini_service to catch both transcriptions and responses
        pipeline = Pipeline(
            [
                transport.input(),
                gemini_service,
                callback_processor,  # Process frames and call callbacks
                transport.output(),
            ]
        )

        # Create pipeline task
        task = PipelineTask(pipeline)
        runner = PipelineRunner()

        # Store session info
        session_info = {
            "task": task,
            "transport": transport,
            "runner": runner,
            "user_id": user_id,
            "session_id": session_id,
            "on_user_message": on_user_message,
            "on_assistant_message": on_assistant_message,
            "on_transcription": on_transcription,
        }
        self.active_sessions[session_id] = session_info

        # Start the pipeline in background
        asyncio.create_task(runner.run(task))

        logger.info(f"Started Gemini Live conversation session {session_id} for user {user_id}")

        return {"session_id": session_id, "status": "started"}

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
            task = session_info["task"]
            transport = session_info.get("transport")

            # Cancel the task
            await task.cancel()

            # Clean up transport if available
            if transport:
                try:
                    await transport.cleanup()
                except Exception as e:
                    logger.warning(f"Error cleaning up transport: {e}")

            del self.active_sessions[session_id]
            logger.info(f"Stopped conversation session {session_id}")
            return True
        except Exception as e:
            logger.error(f"Error stopping session {session_id}: {e}")
            return False

    async def send_text_message(self, session_id: str, message: str) -> bool:
        """
        Send a text message to the conversation (for testing or manual input).

        Args:
            session_id: Session ID
            message: Text message to send

        Returns:
            True if message was sent, False otherwise
        """
        if session_id not in self.active_sessions:
            return False

        # This would require access to the pipeline's input
        # For now, this is a placeholder for future implementation
        logger.info(f"Sending text message to session {session_id}: {message}")
        return True

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
