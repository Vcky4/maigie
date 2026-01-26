"""
gRPC client for Soprano TTS service.
Handles communication with the separate Soprano TTS container.
"""

import asyncio
import logging
import os
from typing import AsyncIterator, Optional, TYPE_CHECKING, Any

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


class TTSClient:
    """
    gRPC client for communicating with Soprano TTS service.
    Handles connection pooling, retries, and error handling.
    """

    def __init__(self, service_url: Optional[str] = None):
        """
        Initialize the TTS client.

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
                "gRPC proto files not generated. TTS client will fail on first use. "
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
                    self._stub = tts_pb2_grpc.TTSServiceStub(self._channel)
                    logger.info(f"Connected to Soprano TTS service at {self.service_url}")
                except Exception as e:
                    logger.error(f"Failed to connect to Soprano TTS service: {e}", exc_info=True)
                    raise

    async def generate_speech(self, text: str, voice: Optional[str] = None) -> AsyncIterator[bytes]:
        """
        Generate speech from text, streaming audio chunks.

        Args:
            text: Text to convert to speech
            voice: Optional voice name (not currently used by Soprano)

        Yields:
            Audio chunks as bytes

        Raises:
            grpc.RpcError: If gRPC call fails
        """
        await self._ensure_connection()

        if self._stub is None:
            raise RuntimeError("TTS service stub not initialized")

        try:
            # Create request
            request = tts_pb2.GenerateSpeechRequest(text=text)
            if voice:
                request.voice = voice

            # Call gRPC service and stream audio chunks
            async for chunk in self._stub.GenerateSpeech(request):
                yield chunk.audio_data

        except grpc.RpcError as e:
            logger.error(f"gRPC error generating speech: {e}", exc_info=True)
            raise
        except Exception as e:
            logger.error(f"Unexpected error generating speech: {e}", exc_info=True)
            raise

    async def generate_speech_complete(self, text: str, voice: Optional[str] = None) -> bytes:
        """
        Generate complete speech from text, returning all audio bytes.

        Args:
            text: Text to convert to speech
            voice: Optional voice name

        Returns:
            Complete audio bytes

        Raises:
            grpc.RpcError: If gRPC call fails
        """
        audio_chunks = []
        async for chunk in self.generate_speech(text, voice):
            audio_chunks.append(chunk)
        return b"".join(audio_chunks)

    async def health_check(self) -> bool:
        """
        Check if the TTS service is healthy.

        Returns:
            True if service is healthy, False otherwise
        """
        try:
            await self._ensure_connection()

            if self._stub is None:
                return False

            request = tts_pb2.HealthCheckRequest()
            response = await self._stub.HealthCheck(request)
            return response.healthy
        except Exception as e:
            logger.error(f"Health check failed: {e}", exc_info=True)
            return False

    async def close(self):
        """Close the gRPC channel."""
        if self._channel:
            await self._channel.close()
            self._channel = None
            self._stub = None
            logger.info("Closed connection to Soprano TTS service")


# Global instance
_tts_client: Optional[TTSClient] = None


def get_tts_client() -> TTSClient:
    """Get or create the global TTS client instance."""
    global _tts_client
    if _tts_client is None:
        _tts_client = TTSClient()
    return _tts_client
