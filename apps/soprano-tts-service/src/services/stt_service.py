"""
Kyutai STT Service wrapper.
Handles model initialization and audio transcription.
"""

import logging
from typing import Optional, Tuple

import numpy as np
import torch

try:
    from transformers import (
        KyutaiSpeechToTextForConditionalGeneration,
        KyutaiSpeechToTextProcessor,
    )
except ImportError:
    KyutaiSpeechToTextProcessor = None  # type: ignore
    KyutaiSpeechToTextForConditionalGeneration = None  # type: ignore

logger = logging.getLogger(__name__)


class STTService:
    """Service for managing Kyutai STT model."""

    def __init__(self):
        """Initialize the STT service."""
        self._processor: Optional[KyutaiSpeechToTextProcessor] = None
        self._model: Optional[KyutaiSpeechToTextForConditionalGeneration] = None
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

    def _get_models(
        self,
    ) -> Tuple[KyutaiSpeechToTextProcessor, KyutaiSpeechToTextForConditionalGeneration]:
        """Get or initialize Kyutai STT models."""
        if (
            KyutaiSpeechToTextProcessor is None
            or KyutaiSpeechToTextForConditionalGeneration is None
        ):
            raise RuntimeError(
                "Kyutai STT is not available. Please ensure transformers is installed."
            )
        if self._processor is None or self._model is None:
            try:
                device = self._get_device()
                # Use the low-latency model for real-time conversation
                model_id = (
                    "kyutai/stt-1b-en_fr"  # Low latency (500ms delay), supports English and French
                )

                logger.info(f"Loading Kyutai STT model: {model_id}")
                self._processor = KyutaiSpeechToTextProcessor.from_pretrained(model_id)
                self._model = KyutaiSpeechToTextForConditionalGeneration.from_pretrained(model_id)
                self._model = self._model.to(device)
                self._model.eval()  # Set to evaluation mode
                logger.info(f"Kyutai STT initialized with device: {device}")
            except Exception as e:
                logger.error(f"Failed to initialize Kyutai STT: {e}", exc_info=True)
                raise
        return self._processor, self._model

    def transcribe_audio(self, audio_data: bytes, sample_rate: int = 16000) -> str:
        """
        Transcribe audio to text using Kyutai STT.

        Args:
            audio_data: PCM audio bytes (16-bit, mono)
            sample_rate: Sample rate of the audio (default: 16000)

        Returns:
            Transcribed text
        """
        processor, model = self._get_models()
        device = self._get_device()

        # Convert PCM bytes to numpy array
        # Audio is 16-bit PCM
        audio_array = np.frombuffer(audio_data, dtype=np.int16).astype(np.float32)
        # Normalize to [-1, 1] range
        audio_array = audio_array / 32768.0

        # Prepare inputs for Kyutai STT
        inputs = processor(audio_array, sampling_rate=sample_rate, return_tensors="pt")

        # Move inputs to device
        inputs = {k: v.to(device) for k, v in inputs.items()}

        # Generate transcription
        with torch.no_grad():
            output_tokens = model.generate(**inputs)

        # Decode tokens to text
        transcription = processor.batch_decode(output_tokens, skip_special_tokens=True)[0]

        return transcription.strip()

    def is_healthy(self) -> bool:
        """Check if the STT service is healthy."""
        try:
            if (
                KyutaiSpeechToTextProcessor is None
                or KyutaiSpeechToTextForConditionalGeneration is None
            ):
                return False
            # Try to get models (will initialize if needed)
            self._get_models()
            return True
        except Exception as e:
            logger.error(f"STT health check failed: {e}", exc_info=True)
            return False
