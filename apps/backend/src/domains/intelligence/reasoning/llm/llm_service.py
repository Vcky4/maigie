"""Stub — implementation pending migration from services/llm_service."""

from typing import Any


class LlmService:
    """LLM service for generating content via various providers."""

    async def generate_course_outline(
        self, topic: str, difficulty: str, user_message: str | None = None
    ) -> dict[str, Any]:
        """Generate a course outline via LLM."""
        return {}  # TODO: migrate implementation

    async def generate(self, prompt: str, **kwargs) -> str:
        """Generate text from a prompt."""
        return ""  # TODO: migrate implementation


llm_service = LlmService()
