"""
Live Voice Service using Kyutai STT, Gemini Chat, and Soprano TTS.
Handles real-time voice conversations via WebSocket streaming.
"""

import asyncio
import io
import logging
import os
import uuid
from typing import Callable, Optional

import numpy as np
import torch
import google.generativeai as genai
from transformers import (
    KyutaiSpeechToTextForConditionalGeneration,
    KyutaiSpeechToTextProcessor,
)

from src.services.tts_client import get_tts_client

logger = logging.getLogger(__name__)

# Configure Gemini API
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))


class LiveVoiceConversationService:
    """
    Service for managing real-time voice conversations.
    Uses Kyutai STT for speech-to-text, Gemini for chat, and Soprano for text-to-speech.
    Streams everything via WebSocket for real-time feel.
    """

    def __init__(self):
        """Initialize the Live Voice service."""
        self.active_sessions: dict[str, dict] = {}
        # TTS client for communicating with Soprano TTS service
        self._tts_client = None
        # Kyutai STT models (lazy loading)
        self._stt_processor: Optional[KyutaiSpeechToTextProcessor] = None
        self._stt_model: Optional[KyutaiSpeechToTextForConditionalGeneration] = None
        # Gemini model for chat (will be initialized per session with system instruction)
        self._chat_models: dict[str, genai.GenerativeModel] = {}
        # Device for STT models
        self._device = None

    def _get_device(self) -> str:
        """Get the device (cuda/cpu) for models with detailed GPU information."""
        if self._device is None:
            try:
                if torch.cuda.is_available():
                    self._device = "cuda"
                    gpu_count = torch.cuda.device_count()
                    gpu_name = torch.cuda.get_device_name(0) if gpu_count > 0 else "Unknown"
                    gpu_memory = (
                        f"{torch.cuda.get_device_properties(0).total_memory / 1024**3:.1f} GB"
                        if gpu_count > 0
                        else "Unknown"
                    )
                    logger.info(
                        f"GPU detected: Using CUDA device (GPU: {gpu_name}, "
                        f"Memory: {gpu_memory}, Count: {gpu_count})"
                    )
                else:
                    self._device = "cpu"
                    logger.info("No GPU detected: Using CPU device")
            except Exception as e:
                self._device = "cpu"
                logger.warning(f"GPU detection failed ({e}): Falling back to CPU")
        return self._device

    def _get_stt_models(
        self,
    ) -> tuple[KyutaiSpeechToTextProcessor, KyutaiSpeechToTextForConditionalGeneration]:
        """Get or initialize Kyutai STT models."""
        if self._stt_processor is None or self._stt_model is None:
            try:
                device = self._get_device()
                # Use the low-latency model for real-time conversation
                model_id = (
                    "kyutai/stt-1b-en_fr"  # Low latency (500ms delay), supports English and French
                )

                logger.info(f"Loading Kyutai STT model: {model_id}")
                self._stt_processor = KyutaiSpeechToTextProcessor.from_pretrained(model_id)
                self._stt_model = KyutaiSpeechToTextForConditionalGeneration.from_pretrained(
                    model_id
                )
                self._stt_model = self._stt_model.to(device)
                self._stt_model.eval()  # Set to evaluation mode
                logger.info(f"Kyutai STT initialized with device: {device}")
            except Exception as e:
                logger.error(f"Failed to initialize Kyutai STT: {e}", exc_info=True)
                raise
        return self._stt_processor, self._stt_model

    def _get_tts_client(self):
        """Get or initialize TTS gRPC client."""
        if self._tts_client is None:
            try:
                self._tts_client = get_tts_client()
                logger.info("TTS gRPC client initialized")
            except Exception as e:
                logger.error(f"Failed to initialize TTS client: {e}", exc_info=True)
                raise
        return self._tts_client

    def _transcribe_audio_sync(
        self,
        processor: KyutaiSpeechToTextProcessor,
        model: KyutaiSpeechToTextForConditionalGeneration,
        audio_array: np.ndarray,
        device: str,
    ) -> str:
        """Synchronous transcription function to run in executor."""
        try:
            # Prepare inputs for Kyutai STT
            # The processor expects raw audio waveform (numpy array)
            inputs = processor(audio_array, sampling_rate=16000, return_tensors="pt")

            # Move inputs to device
            inputs = {k: v.to(device) for k, v in inputs.items()}

            # Generate transcription
            with torch.no_grad():
                output_tokens = model.generate(**inputs)

            # Decode tokens to text
            transcription = processor.batch_decode(output_tokens, skip_special_tokens=True)[0]

            return transcription
        except Exception as e:
            logger.error(f"Error in Kyutai STT transcription: {e}", exc_info=True)
            return ""

    async def start_conversation(
        self,
        user_id: str,
        session_id: Optional[str] = None,
        on_user_message: Optional[Callable[[str], None]] = None,
        on_assistant_message: Optional[Callable[[str], None]] = None,
        on_assistant_message_complete: Optional[Callable[[str], None]] = None,
        on_transcription: Optional[Callable[[str], None]] = None,
        on_audio: Optional[Callable[[bytes], None]] = None,
        on_session_closed: Optional[Callable[[str], None]] = None,
        system_instruction: Optional[str] = None,
    ) -> dict:
        """
        Start a new live voice conversation session.

        Args:
            user_id: User ID for the conversation
            session_id: Unique session ID (auto-generated if not provided)
            on_user_message: Callback when user speaks (transcribed text)
            on_assistant_message: Callback when assistant responds (text chunks for streaming)
            on_assistant_message_complete: Callback when assistant message is complete (full text)
            on_transcription: Callback for transcription updates
            on_audio: Callback for audio chunks (TTS output)
            on_session_closed: Callback when session closes
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
            "Be conversational, helpful, and encouraging. "
            "Keep responses concise and natural for voice conversation."
        )

        system_instruction_text = system_instruction or default_instruction

        # Initialize Gemini chat model for this session
        chat_model = genai.GenerativeModel(
            model_name="models/gemini-flash-latest",
            system_instruction=system_instruction_text,
        )
        self._chat_models[session_id] = chat_model

        # Store session info
        session_info = {
            "user_id": user_id,
            "session_id": session_id,
            "on_user_message": on_user_message,
            "on_assistant_message": on_assistant_message,
            "on_assistant_message_complete": on_assistant_message_complete,
            "on_transcription": on_transcription,
            "on_audio": on_audio,
            "on_session_closed": on_session_closed,
            "chat_model": chat_model,
            "conversation_history": [],  # Store conversation history for context
            "audio_buffer": bytearray(),  # Buffer for accumulating audio chunks
            "is_processing": False,  # Flag to prevent concurrent processing
            "task": None,
        }

        self.active_sessions[session_id] = session_info

        logger.info(f"Started Live Voice conversation session {session_id} for user {user_id}")

        return {"session_id": session_id, "status": "started"}

    async def process_audio_chunk(
        self,
        session_id: str,
        audio_data: bytes,
        is_final: bool = False,
    ) -> bool:
        """
        Process an audio chunk from the user.

        Args:
            session_id: Session ID
            audio_data: Raw audio bytes (PCM, 16-bit, 16kHz, mono)
            is_final: Whether this is the final chunk of a speech segment

        Returns:
            True if audio was processed, False otherwise
        """
        if session_id not in self.active_sessions:
            logger.warning(f"Session {session_id} not found")
            return False

        session_info = self.active_sessions[session_id]

        # Prevent concurrent processing
        if session_info["is_processing"]:
            return True  # Still accept the chunk

        try:
            # Accumulate audio in buffer
            session_info["audio_buffer"].extend(audio_data)

            # If this is a final chunk or buffer is large enough, process it
            buffer_size = len(session_info["audio_buffer"])
            # Process when buffer reaches ~2 seconds of audio (32000 bytes for 16kHz mono 16-bit)
            # or when is_final is True
            if is_final or buffer_size >= 32000:
                # Process the accumulated audio
                await self._process_audio_buffer(session_id)
                session_info["audio_buffer"] = bytearray()  # Clear buffer

            return True
        except Exception as e:
            logger.error(
                f"Error processing audio chunk for session {session_id}: {e}", exc_info=True
            )
            return False

    async def _process_audio_buffer(self, session_id: str):
        """Process accumulated audio buffer: transcribe, get response, generate TTS."""
        session_info = self.active_sessions.get(session_id)
        if not session_info:
            return

        session_info["is_processing"] = True

        try:
            audio_buffer = bytes(session_info["audio_buffer"])
            if len(audio_buffer) == 0:
                return

            # Step 1: Transcribe audio using Kyutai STT
            logger.info(
                f"Transcribing audio for session {session_id}, size: {len(audio_buffer)} bytes"
            )

            # Convert PCM bytes to numpy array
            # Audio is 16-bit PCM, 16kHz, mono
            audio_array = np.frombuffer(audio_buffer, dtype=np.int16).astype(np.float32)
            # Normalize to [-1, 1] range
            audio_array = audio_array / 32768.0

            # Get STT models
            processor, stt_model = self._get_stt_models()
            device = self._get_device()

            # Process audio with Kyutai STT
            # Run in a thread pool to avoid blocking the event loop
            loop = asyncio.get_event_loop()
            transcribed_text = await loop.run_in_executor(
                None,
                self._transcribe_audio_sync,
                processor,
                stt_model,
                audio_array,
                device,
            )

            if not transcribed_text or not transcribed_text.strip():
                logger.debug(f"No transcription for session {session_id}")
                return

            transcribed_text = transcribed_text.strip()
            logger.info(f"Transcribed text for session {session_id}: {transcribed_text}")

            # Call transcription callback
            on_transcription = session_info.get("on_transcription")
            if on_transcription:
                if asyncio.iscoroutinefunction(on_transcription):
                    await on_transcription(transcribed_text)
                else:
                    on_transcription(transcribed_text)

            # Call user message callback
            on_user_message = session_info.get("on_user_message")
            if on_user_message:
                if asyncio.iscoroutinefunction(on_user_message):
                    await on_user_message(transcribed_text)
                else:
                    on_user_message(transcribed_text)

            # Step 2: Get AI response using Gemini chat
            chat_model = session_info["chat_model"]
            conversation_history = session_info["conversation_history"]

            # Format history for Gemini
            formatted_history = []
            for msg in conversation_history[-10:]:  # Last 10 messages for context
                role = "user" if msg["role"] == "user" else "model"
                formatted_history.append({"role": role, "parts": [msg["content"]]})

            # Add current user message
            formatted_history.append({"role": "user", "parts": [transcribed_text]})

            # Generate response with streaming
            logger.info(f"Generating AI response for session {session_id}")
            response_text = ""
            response_parts = []

            # Use streaming for real-time feel
            response_stream = await chat_model.generate_content_async(
                formatted_history,
                stream=True,
            )

            on_assistant_message = session_info.get("on_assistant_message")
            on_audio = session_info.get("on_audio")

            # Process streamed response
            async for chunk in response_stream:
                if chunk.text:
                    chunk_text = chunk.text
                    response_text += chunk_text
                    response_parts.append(chunk_text)

                    # Send text chunk to client immediately for real-time feel
                    if on_assistant_message:
                        if asyncio.iscoroutinefunction(on_assistant_message):
                            await on_assistant_message(chunk_text)
                        else:
                            on_assistant_message(chunk_text)

            if not response_text:
                logger.warning(f"Empty response from AI for session {session_id}")
                return

            logger.info(f"AI response for session {session_id}: {response_text[:100]}...")

            # Update conversation history
            session_info["conversation_history"].append(
                {"role": "user", "content": transcribed_text}
            )
            session_info["conversation_history"].append(
                {"role": "assistant", "content": response_text}
            )

            # Call completion callback with full message for database saving
            on_assistant_message_complete = session_info.get("on_assistant_message_complete")
            if on_assistant_message_complete:
                if asyncio.iscoroutinefunction(on_assistant_message_complete):
                    await on_assistant_message_complete(response_text)
                else:
                    on_assistant_message_complete(response_text)

            # Step 3: Generate TTS audio using Soprano TTS service via gRPC
            logger.info(f"Generating TTS audio for session {session_id}")
            try:
                tts_client = self._get_tts_client()

                # Generate audio using gRPC client (streams audio chunks)
                async for audio_chunk in tts_client.generate_speech(text=response_text):
                    if on_audio:
                        if asyncio.iscoroutinefunction(on_audio):
                            await on_audio(audio_chunk)
                        else:
                            on_audio(audio_chunk)
                    # Small delay to simulate streaming
                    await asyncio.sleep(0.01)

                logger.info(f"Streamed TTS audio for session {session_id}")

            except Exception as e:
                logger.error(
                    f"Error generating TTS audio for session {session_id}: {e}", exc_info=True
                )
                # Continue even if TTS fails - text response was already sent

        except Exception as e:
            logger.error(
                f"Error processing audio buffer for session {session_id}: {e}", exc_info=True
            )
        finally:
            session_info["is_processing"] = False

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

        # Notify session closure
        on_session_closed = session_info.get("on_session_closed")
        if on_session_closed:
            try:
                if asyncio.iscoroutinefunction(on_session_closed):
                    await on_session_closed(session_id)
                else:
                    on_session_closed(session_id)
            except Exception as e:
                logger.error(f"Error calling on_session_closed for session {session_id}: {e}")

        # Clean up
        self.active_sessions.pop(session_id, None)
        self._chat_models.pop(session_id, None)

        logger.info(f"Stopped conversation session {session_id}")
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
_live_voice_service: Optional[LiveVoiceConversationService] = None


def get_live_voice_service() -> LiveVoiceConversationService:
    """Get or create the global Live Voice service instance."""
    global _live_voice_service
    if _live_voice_service is None:
        _live_voice_service = LiveVoiceConversationService()
    return _live_voice_service
