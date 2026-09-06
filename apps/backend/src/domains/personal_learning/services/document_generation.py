"""Stub — implementation pending migration from services/document_generation_service."""

from typing import Any


class DocumentGenerationService:
    """Service for generating PDF/DOCX documents."""

    async def generate_document(
        self, format: str, title: str, content: str, **kwargs
    ) -> dict[str, Any]:
        """Generate a document in the specified format."""
        return {}  # TODO: migrate implementation


document_generation_service = DocumentGenerationService()
