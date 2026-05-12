"""Gemini embedding adapter for the multi-provider LLM system.

Implements the EmbeddingCapability protocol using the google-genai SDK's
embed_content API. Registered in the adapter registry for the
gemini-embedding-001 model.

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

from __future__ import annotations

import logging
from typing import Any

from src.services.llm.base_adapter import BaseProviderAdapter
from src.services.llm.capabilities import EmbeddingCapability
from src.services.llm.errors import GeminiError
from src.services.llm.gemini_sdk import new_gemini_client
from src.services.llm_registry import gemini_api_key

logger = logging.getLogger(__name__)


class GeminiEmbeddingAdapter(BaseProviderAdapter):
    """Gemini embedding adapter implementing EmbeddingCapability.

    Uses the google-genai SDK's embed_content method to generate
    text embeddings. Does NOT support chat/vision/structured output.
    """

    __slots__ = ("_model_id",)

    def __init__(self, model_id: str = "gemini-embedding-001") -> None:
        self._model_id = model_id

    # --- BaseProviderAdapter interface ---

    @property
    def provider_name(self) -> str:
        return "gemini"

    @property
    def model_id(self) -> str:
        return self._model_id

    def supported_capabilities(self) -> set[type]:
        return {EmbeddingCapability}

    async def get_chat_response_with_tools(
        self,
        history: list,
        user_message: str,
        context: dict | None = None,
        user_id: str | None = None,
        user_name: str | None = None,
        image_url: str | None = None,
        progress_callback: Any = None,
        stream_callback: Any = None,
    ) -> tuple[str, dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
        """Embedding adapter does not support chat. Raises an error."""
        raise GeminiError(
            model=self._model_id,
            status_code=None,
            category="invalid_request",
            message=(
                f"Model {self._model_id} is an embedding model and does not "
                "support chat. Use the embed() method instead."
            ),
            retriable=False,
        )

    # --- EmbeddingCapability interface ---

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Generate embeddings for a list of texts.

        Args:
            texts: List of text strings to embed (1-100 items, each ≤10,000 chars).

        Returns:
            List of embedding vectors (one per input text).

        Raises:
            GeminiError: On API failure.
        """
        if not texts:
            return []

        api_key = gemini_api_key()
        if not api_key:
            raise GeminiError(
                model=self._model_id,
                status_code=None,
                category="auth",
                message="Gemini API key not configured.",
                retriable=False,
            )

        try:
            client = new_gemini_client(api_key)
            response = client.models.embed_content(
                model=self._model_id,
                contents=texts if len(texts) > 1 else texts[0],
            )

            # Extract embedding vectors from response
            embeddings: list[list[float]] = []
            if hasattr(response, "embeddings") and response.embeddings:
                for emb in response.embeddings:
                    embeddings.append(list(emb.values))
            else:
                raise GeminiError(
                    model=self._model_id,
                    status_code=None,
                    category="server_error",
                    message="Empty embedding response from Gemini.",
                    retriable=True,
                )

            return embeddings

        except GeminiError:
            raise
        except Exception as e:
            logger.error("Gemini embedding error: %s", e)
            raise GeminiError(
                model=self._model_id,
                status_code=500,
                category="server_error",
                message=f"Gemini embedding error: {e}",
                retriable=True,
            ) from e
