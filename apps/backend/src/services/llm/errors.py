"""Structured error hierarchy for multi-provider LLM adapters.

All provider-specific exceptions inherit from `LLMProviderError` so that
callers (router, circuit breaker, route handlers) can handle failures
uniformly without depending on provider SDKs.

Adapters MUST NOT raise `HTTPException` directly — they translate SDK
errors into the appropriate subclass here.
"""

from __future__ import annotations

# Valid error categories that classify the nature of the failure.
ERROR_CATEGORIES = frozenset(
    {
        "rate_limit",
        "auth",
        "invalid_request",
        "server_error",
        "overloaded",
        "unknown",
    }
)

# Categories that are safe to retry with backoff.
RETRIABLE_CATEGORIES = frozenset({"rate_limit", "server_error", "overloaded"})


class LLMProviderError(Exception):
    """Base error for all LLM provider failures.

    Attributes:
        provider: Provider identifier (e.g. "gemini", "openai", "anthropic").
        model: Model identifier that was being called.
        status_code: HTTP status code from the provider, or None if unavailable.
        category: One of the ERROR_CATEGORIES classifying the failure type.
        message: Human-readable description of the error.
        retriable: Whether this error can be retried (rate_limit, server_error,
            overloaded are retriable by default).
    """

    def __init__(
        self,
        provider: str,
        model: str,
        status_code: int | None,
        category: str,
        message: str,
        retriable: bool = False,
    ) -> None:
        self.provider = provider
        self.model = model
        self.status_code = status_code
        self.category = category
        self.retriable = retriable

        super().__init__(message)

    @property
    def message(self) -> str:
        """Human-readable error message (alias for Exception args[0])."""
        return self.args[0] if self.args else ""

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}("
            f"provider={self.provider!r}, "
            f"model={self.model!r}, "
            f"status_code={self.status_code!r}, "
            f"category={self.category!r}, "
            f"message={self.message!r}, "
            f"retriable={self.retriable!r})"
        )


class GeminiError(LLMProviderError):
    """Error raised by the Gemini adapter."""

    def __init__(
        self,
        model: str,
        status_code: int | None,
        category: str,
        message: str,
        retriable: bool = False,
    ) -> None:
        super().__init__(
            provider="gemini",
            model=model,
            status_code=status_code,
            category=category,
            message=message,
            retriable=retriable,
        )


class OpenAIError(LLMProviderError):
    """Error raised by the OpenAI adapter."""

    def __init__(
        self,
        model: str,
        status_code: int | None,
        category: str,
        message: str,
        retriable: bool = False,
    ) -> None:
        super().__init__(
            provider="openai",
            model=model,
            status_code=status_code,
            category=category,
            message=message,
            retriable=retriable,
        )


class AnthropicError(LLMProviderError):
    """Error raised by the Anthropic adapter."""

    def __init__(
        self,
        model: str,
        status_code: int | None,
        category: str,
        message: str,
        retriable: bool = False,
    ) -> None:
        super().__init__(
            provider="anthropic",
            model=model,
            status_code=status_code,
            category=category,
            message=message,
            retriable=retriable,
        )
