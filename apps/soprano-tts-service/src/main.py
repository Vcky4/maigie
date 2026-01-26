"""
gRPC server for Soprano TTS service.
"""

import asyncio
import logging
import os
import sys
from concurrent import futures

import grpc

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from src.proto import tts_pb2, tts_pb2_grpc
from src.services.tts_service import TTSService

logger = logging.getLogger(__name__)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)


class TTSServiceServicer(tts_pb2_grpc.TTSServiceServicer):
    """gRPC servicer for TTS service."""

    def __init__(self):
        """Initialize the servicer."""
        self.tts_service = TTSService()

    async def GenerateSpeech(
        self, request: tts_pb2.GenerateSpeechRequest, context: grpc.aio.ServicerContext
    ) -> tts_pb2.AudioChunk:
        """
        Generate speech from text, streaming audio chunks.

        Args:
            request: GenerateSpeechRequest with text and optional voice
            context: gRPC context

        Yields:
            AudioChunk messages with audio data
        """
        try:
            logger.info(f"Generating speech for text: {request.text[:50]}...")

            # Generate audio using Soprano
            audio_bytes = self.tts_service.generate_audio(
                text=request.text, voice=request.voice if request.HasField("voice") else None
            )

            # Stream audio in chunks for real-time playback
            chunk_size = 8192  # ~0.25 seconds of audio at 16kHz mono 16-bit
            total_chunks = (len(audio_bytes) + chunk_size - 1) // chunk_size

            for i in range(0, len(audio_bytes), chunk_size):
                chunk = audio_bytes[i : i + chunk_size]
                is_final = i + chunk_size >= len(audio_bytes)

                yield tts_pb2.AudioChunk(audio_data=chunk, is_final=is_final)

                # Small delay to simulate streaming
                await asyncio.sleep(0.01)

            logger.info(f"Streamed TTS audio, total size: {len(audio_bytes)} bytes")

        except Exception as e:
            logger.error(f"Error generating speech: {e}", exc_info=True)
            await context.set_code(grpc.StatusCode.INTERNAL)
            await context.set_details(f"Error generating speech: {str(e)}")
            raise

    async def HealthCheck(
        self, request: tts_pb2.HealthCheckRequest, context: grpc.aio.ServicerContext
    ) -> tts_pb2.HealthCheckResponse:
        """
        Health check endpoint.

        Args:
            request: HealthCheckRequest
            context: gRPC context

        Returns:
            HealthCheckResponse with health status
        """
        try:
            is_healthy = self.tts_service.is_healthy()
            if is_healthy:
                return tts_pb2.HealthCheckResponse(healthy=True, message="Service is healthy")
            else:
                return tts_pb2.HealthCheckResponse(healthy=False, message="Service is not healthy")
        except Exception as e:
            logger.error(f"Health check error: {e}", exc_info=True)
            return tts_pb2.HealthCheckResponse(
                healthy=False, message=f"Health check failed: {str(e)}"
            )


async def serve():
    """Start the gRPC server."""
    port = os.getenv("PORT", "50051")
    server = grpc.aio.server(futures.ThreadPoolExecutor(max_workers=10))

    # Add servicer
    tts_pb2_grpc.add_TTSServiceServicer_to_server(TTSServiceServicer(), server)

    # Listen on port
    listen_addr = f"0.0.0.0:{port}"
    server.add_insecure_port(listen_addr)

    logger.info(f"Starting Soprano TTS gRPC server on {listen_addr}")
    await server.start()

    try:
        await server.wait_for_termination()
    except KeyboardInterrupt:
        logger.info("Shutting down server...")
        await server.stop(5)


if __name__ == "__main__":
    asyncio.run(serve())
