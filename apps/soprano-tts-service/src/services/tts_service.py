"""
Soprano TTS Service wrapper.
Handles model initialization and audio generation.
"""

import logging
import os
from typing import Optional

import numpy as np
import torch

try:
    from soprano_tts import Soprano
except ImportError:
    Soprano = None  # type: ignore

logger = logging.getLogger(__name__)


class TTSService:
    """Service for managing Soprano TTS model."""

    def __init__(self):
        """Initialize the TTS service."""
        self._model: Optional[Soprano] = None
        self._device: Optional[str] = None

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

    def _get_model(self) -> Soprano:
        """Get or initialize Soprano TTS model."""
        if Soprano is None:
            raise RuntimeError(
                "Soprano TTS is not available. " "Please ensure soprano-tts is installed."
            )
        if self._model is None:
            try:
                device = self._get_device()
                # Try to use lmdeploy backend first (faster), fallback to transformers if not available
                try:
                    import lmdeploy

                    backend = "auto"  # Will use lmdeploy if available
                    logger.info("Using lmdeploy backend for Soprano TTS (faster)")
                except ImportError:
                    backend = "transformers"  # Fallback to transformers backend
                    logger.info(
                        "lmdeploy not available, using transformers backend for Soprano TTS (slower but compatible)"
                    )

                # Soprano accepts backend parameter - try with backend, fallback to default if not supported
                try:
                    self._model = Soprano(device=device, backend=backend)
                except TypeError:
                    # If backend parameter not supported, use default initialization
                    self._model = Soprano(device=device)
                logger.info(f"Soprano TTS initialized with device: {device}")
            except Exception as e:
                logger.error(f"Failed to initialize Soprano TTS: {e}", exc_info=True)
                raise
        return self._model

    def generate_audio(self, text: str, voice: Optional[str] = None) -> bytes:
        """
        Generate audio from text using Soprano TTS.

        Args:
            text: Text to convert to speech
            voice: Optional voice name (not currently used by Soprano)

        Returns:
            Audio bytes in 16-bit PCM format (16kHz, mono)
        """
        model = self._get_model()

        # Generate audio using Soprano
        audio_output = model.generate(text=text)

        # Convert to bytes
        if isinstance(audio_output, bytes):
            audio_bytes = audio_output
        elif hasattr(audio_output, "tobytes"):
            audio_bytes = audio_output.tobytes()
        elif hasattr(audio_output, "numpy"):
            audio_array = np.array(audio_output)
            # Convert to 16-bit PCM if needed
            if audio_array.dtype != np.int16:
                # Normalize to [-1, 1] range and convert to int16
                audio_array = np.clip(audio_array, -1.0, 1.0)
                audio_array = (audio_array * 32767).astype(np.int16)
            audio_bytes = audio_array.tobytes()
        else:
            # Try to convert to bytes directly
            audio_bytes = bytes(audio_output)

        return audio_bytes

    def is_healthy(self) -> bool:
        """Check if the TTS service is healthy."""
        try:
            if Soprano is None:
                return False
            # Try to get model (will initialize if needed)
            self._get_model()
            return True
        except Exception as e:
            logger.error(f"Health check failed: {e}", exc_info=True)
            return False
