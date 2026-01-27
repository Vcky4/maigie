"""
gRPC client for Voice Service STT functionality.
Handles communication with the separate Voice service container for speech-to-text.
"""

import asyncio
import logging
import os
from typing import Optional, TYPE_CHECKING, Any

import grpc

# Import generated proto files (will be generated during build)
if TYPE_CHECKING:
    from src.proto import tts_pb2, tts_pb2_grpc
else:
    try:
        from src.proto import tts_pb2, tts_pb2_grpc
    except ImportError:
        # Fallback if proto files not generated yet
        tts_pb2 = None  # type: ignore
        tts_pb2_grpc = None  # type: ignore

logger = logging.getLogger(__name__)


class STTClient:
    """
    gRPC client for communicating with Voice Service STT functionality.
    Handles connection pooling, retries, and error handling.
    """

    def __init__(self, service_url: Optional[str] = None):
        """
        Initialize the STT client.

        Args:
            service_url: gRPC service URL (default: from SOPRANO_TTS_SERVICE_URL env var)
        """
        self.service_url = service_url or os.getenv(
            "SOPRANO_TTS_SERVICE_URL", "soprano-tts-service:50051"
        )
        self._channel: Optional[grpc.aio.Channel] = None
        self._stub: Optional[Any] = None  # type: ignore
        self._lock = asyncio.Lock()

        # Validate proto files are available (lazy check on first use)
        if tts_pb2 is None or tts_pb2_grpc is None:
            logger.warning(
                "gRPC proto files not generated. STT client will fail on first use. "
                "Run: python -m grpc_tools.protoc --proto_path=src/proto "
                "--python_out=src/proto --grpc_python_out=src/proto src/proto/tts.proto"
            )

    async def _ensure_connection(self):
        """Ensure gRPC channel and stub are initialized."""
        if tts_pb2 is None or tts_pb2_grpc is None:
            raise RuntimeError(
                "gRPC proto files not generated. "
                "Run: python -m grpc_tools.protoc --proto_path=src/proto "
                "--python_out=src/proto --grpc_python_out=src/proto src/proto/tts.proto"
            )

        async with self._lock:
            if self._channel is None or self._stub is None:
                try:
                    self._channel = grpc.aio.insecure_channel(self.service_url)
                    self._stub = tts_pb2_grpc.VoiceServiceStub(self._channel)
                    logger.info(f"Connected to Voice Service at {self.service_url}")
                except Exception as e:
                    logger.error(f"Failed to connect to Voice Service: {e}", exc_info=True)
                    raise

    async def transcribe_audio(self, audio_data: bytes, sample_rate: int = 16000) -> str:
        """
        Transcribe audio to text.

        Args:
            audio_data: PCM audio bytes (16-bit, mono)
            sample_rate: Sample rate of the audio (default: 16000)

        Returns:
            Transcribed text

        Raises:
            grpc.RpcError: If gRPC call fails
        """
        await self._ensure_connection()

        if self._stub is None:
            raise RuntimeError("STT service stub not initialized")

        try:
            # Create request
            request = tts_pb2.TranscribeAudioRequest(audio_data=audio_data, sample_rate=sample_rate)

            # Call gRPC service
            response = await self._stub.TranscribeAudio(request)
            return response.text

        except grpc.RpcError as e:
            logger.error(f"gRPC error transcribing audio: {e}", exc_info=True)
            raise
        except Exception as e:
            logger.error(f"Unexpected error transcribing audio: {e}", exc_info=True)
            raise

    async def close(self):
        """Close the gRPC channel."""
        if self._channel:
            await self._channel.close()
            self._channel = None
            self._stub = None
            logger.info("Closed connection to Voice Service")


# Global instance
_stt_client: Optional[STTClient] = None


def get_stt_client() -> STTClient:
    """Get or create the global STT client instance."""
    global _stt_client
    if _stt_client is None:
        _stt_client = STTClient()
    return _stt_client
