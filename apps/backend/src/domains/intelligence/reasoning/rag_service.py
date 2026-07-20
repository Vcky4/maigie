"""Stub — implementation pending migration from services/rag_service."""

from typing import Any


class RagService:
    """Retrieval-Augmented Generation service."""

    async def search(self, query: str, user_id: str, **kwargs) -> list[dict[str, Any]]:
        """Search for relevant documents."""
        return []  # TODO: migrate implementation

    async def get_context(self, query: str, user_id: str, **kwargs) -> str:
        """Get RAG context for a query."""
        return ""  # TODO: migrate implementation


rag_service = RagService()
