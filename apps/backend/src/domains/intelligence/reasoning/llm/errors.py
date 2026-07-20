"""LLM provider error hierarchy."""

from __future__ import annotations


class LLMError(Exception):
    """Base error for LLM operations."""

    pass


class LLMProviderError(LLMError):
    """An LLM provider returned an error or was unreachable."""

    def __init__(self, provider: str, message: str, *, status_code: int | None = None):
        self.provider = provider
        self.status_code = status_code
        super().__init__(f"[{provider}] {message}")


class LLMUnavailableError(LLMError):
    """All LLM providers are unavailable after retries."""

    pass


class GeminiError(LLMProviderError):
    """Gemini-specific error."""

    def __init__(self, message: str, *, status_code: int | None = None):
        super().__init__("gemini", message, status_code=status_code)


class OpenAIError(LLMProviderError):
    """OpenAI-specific error."""

    def __init__(self, message: str, *, status_code: int | None = None):
        super().__init__("openai", message, status_code=status_code)


class AnthropicError(LLMProviderError):
    """Anthropic-specific error."""

    def __init__(self, message: str, *, status_code: int | None = None):
        super().__init__("anthropic", message, status_code=status_code)
