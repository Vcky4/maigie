"""Structured error hierarchy for multi-provider LLM adapters.

All provider-specific exceptions inherit from `LLMProviderError` so that callers — the router, the
circuit breaker, route handlers — can handle failures uniformly without depending on provider SDKs.
Adapters MUST NOT raise `HTTPException` directly; they translate SDK errors into a subclass here.

**This is a merge, not a straight port.** Two incompatible versions of this module existed. The
pre-migration one (`git show "4953972^:apps/backend/src/services/llm/errors.py"`) carried
`provider`, `model`, `status_code`, `category`, `message` and `retriable` — the shape the router,
the circuit breaker and `tests/test_end_to_end_routing.py` are all written against. The version
written during the migration reduced it to `(provider, message, status_code=None)`, losing
`category`, `model` and `retriable`.

The loss was not cosmetic. `websocket_handler` has always done this:

    except LLMProviderError as e:
        logger.error("... category=%s provider=%s model=%s msg=%s", e.category, e.provider, e.model, e.message)

`category`, `model` and `message` were all absent from the reduced class, so the handler that exists to
turn a provider failure into a readable message would itself have raised `AttributeError` — inside an
`except` block, which is the worst place for a second failure. It has never run only because
`get_llm_router()` raises before any adapter can produce one of these.

So the richer signature is canonical again, and the two things the migration version added are kept:
`LLMError` as a common base (so `except LLMError` catches provider and availability failures alike)
and `LLMUnavailableError`.

One addition to `ERROR_CATEGORIES`: `unsupported_capability`. `websocket_handler`'s
`_ERROR_CATEGORY_MESSAGES` maps it and the old frozenset did not contain it, so a capability mismatch
would have been classified `unknown` and reported as "An unexpected error occurred" — which is exactly
wrong for the one failure that is neither unexpected nor transient.
"""

from __future__ import annotations

#: Valid error categories that classify the nature of the failure.
ERROR_CATEGORIES = frozenset(
    {
        "rate_limit",
        "auth",
        "invalid_request",
        "server_error",
        "overloaded",
        "unsupported_capability",
        "unknown",
    }
)

#: Categories that are safe to retry with backoff.
#:
#: `auth` and `invalid_request` are excluded because retrying them burns quota to reach the same
#: answer. `unsupported_capability` is excluded because it is a routing mistake, not a transient
#: condition — the same provider will refuse the same request forever.
RETRIABLE_CATEGORIES = frozenset({"rate_limit", "server_error", "overloaded"})


class LLMError(Exception):
    """Base for every failure originating in the LLM layer.

    Kept from the migration-era module so `except LLMError` catches both a provider failure and a
    total-unavailability failure. Callers that care about the difference catch the subclass.
    """


class LLMProviderError(LLMError):
    """A provider returned an error or was unreachable.

    Attributes:
        provider: Provider identifier — "gemini", "openai", "anthropic".
        model: Model identifier that was being called.
        status_code: HTTP status from the provider, or None if unavailable.
        category: One of `ERROR_CATEGORIES`, classifying the failure.
        message: Human-readable description.
        retriable: Whether a retry could succeed. Defaults from `category` rather than to `False`,
            so a new adapter that forgets the argument still gets correct retry behaviour for a rate
            limit. Pass it explicitly to override.
    """

    def __init__(
        self,
        provider: str,
        model: str | None = None,
        status_code: int | None = None,
        category: str = "unknown",
        message: str = "",
        retriable: bool | None = None,
    ) -> None:
        self.provider = provider
        self.model = model
        self.status_code = status_code
        self.category = category if category in ERROR_CATEGORIES else "unknown"
        self.retriable = (
            self.category in RETRIABLE_CATEGORIES if retriable is None else bool(retriable)
        )
        super().__init__(message or f"[{provider}] {self.category}")

    @property
    def message(self) -> str:
        """Human-readable error message. An alias for `args[0]`, which callers already log."""
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


class LLMUnavailableError(LLMError):
    """Every provider failed, and the caller supplied no fallback.

    Distinct from `LLMProviderError`: that one names a provider that failed, this one says the router
    exhausted all of them. The difference matters to the caller — one is worth retrying against a
    different provider, the other is not worth retrying at all.
    """


class _ProviderScopedError(LLMProviderError):
    """Shared constructor for the per-provider subclasses. Sets `provider` from `PROVIDER`."""

    PROVIDER = "unknown"

    def __init__(
        self,
        model: str | None = None,
        status_code: int | None = None,
        category: str = "unknown",
        message: str = "",
        retriable: bool | None = None,
    ) -> None:
        super().__init__(
            provider=self.PROVIDER,
            model=model,
            status_code=status_code,
            category=category,
            message=message,
            retriable=retriable,
        )


class GeminiError(_ProviderScopedError):
    """Error raised by the Gemini adapter."""

    PROVIDER = "gemini"


class OpenAIError(_ProviderScopedError):
    """Error raised by the OpenAI adapter."""

    PROVIDER = "openai"


class AnthropicError(_ProviderScopedError):
    """Error raised by the Anthropic adapter."""

    PROVIDER = "anthropic"


__all__ = [
    "ERROR_CATEGORIES",
    "RETRIABLE_CATEGORIES",
    "AnthropicError",
    "GeminiError",
    "LLMError",
    "LLMProviderError",
    "LLMUnavailableError",
    "OpenAIError",
]
