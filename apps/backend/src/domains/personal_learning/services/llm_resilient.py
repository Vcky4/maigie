"""
Resilient LLM wrapper for the Personal Learning domain.

Supports per-user provider switching (Gemini, OpenAI, Anthropic).
Adds timeout, retry budget, and circuit breaker pattern around LLM calls.

Provider Selection:
- If user_id is provided, checks LearningProfile.preferred_llm_provider
- Falls back to system default (Gemini) if no preference or provider unavailable
- Supported values: "gemini", "openai", "anthropic"

Circuit Breaker States (per-provider):
- CLOSED: Normal operation; calls go through.
- OPEN: Too many failures; calls short-circuit with fallback immediately.
- HALF_OPEN: After cooldown, allow one test call. Success → CLOSED; failure → OPEN.
"""

import asyncio
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_FAILURE_THRESHOLD = 5  # Consecutive failures before opening circuit
_RECOVERY_TIMEOUT_S = 60  # Seconds before trying again after open
_DEFAULT_TIMEOUT_S = 30  # Per-call timeout in seconds
_MAX_RETRIES = 2  # Max retries per call (total attempts = MAX_RETRIES + 1)
_DEFAULT_PROVIDER = "gemini"

# Supported providers
SUPPORTED_PROVIDERS = ("gemini", "openai", "anthropic")

# ---------------------------------------------------------------------------
# Per-Provider Circuit Breaker State
# ---------------------------------------------------------------------------

_circuit_states: dict[str, dict[str, Any]] = {}


def _get_circuit(provider: str) -> dict[str, Any]:
    """Get or create circuit breaker state for a provider."""
    if provider not in _circuit_states:
        _circuit_states[provider] = {
            "failure_count": 0,
            "last_failure_time": 0.0,
            "state": "CLOSED",
        }
    return _circuit_states[provider]


def _is_circuit_open(provider: str) -> bool:
    """Check if the circuit breaker is open for a specific provider."""
    circuit = _get_circuit(provider)

    if circuit["state"] == "CLOSED":
        return False

    if circuit["state"] == "OPEN":
        elapsed = time.monotonic() - circuit["last_failure_time"]
        if elapsed >= _RECOVERY_TIMEOUT_S:
            circuit["state"] = "HALF_OPEN"
            logger.info(f"LLM circuit breaker [{provider}]: HALF_OPEN (testing recovery)")
            return False
        return True

    return False  # HALF_OPEN allows one call


def _record_success(provider: str) -> None:
    """Record a successful call — reset circuit breaker for provider."""
    circuit = _get_circuit(provider)
    circuit["failure_count"] = 0
    if circuit["state"] != "CLOSED":
        logger.info(f"LLM circuit breaker [{provider}]: CLOSED (recovered)")
    circuit["state"] = "CLOSED"


def _record_failure(provider: str) -> None:
    """Record a failed call — potentially open the circuit for provider."""
    circuit = _get_circuit(provider)
    circuit["failure_count"] += 1
    circuit["last_failure_time"] = time.monotonic()

    if circuit["failure_count"] >= _FAILURE_THRESHOLD:
        circuit["state"] = "OPEN"
        logger.warning(
            f"LLM circuit breaker [{provider}]: OPEN after {circuit['failure_count']} "
            f"consecutive failures. Will retry in {_RECOVERY_TIMEOUT_S}s."
        )
    elif circuit["state"] == "HALF_OPEN":
        circuit["state"] = "OPEN"
        logger.warning(f"LLM circuit breaker [{provider}]: OPEN (recovery test failed)")


# ---------------------------------------------------------------------------
# Provider Implementations
# ---------------------------------------------------------------------------


async def _call_gemini(prompt: str, *, max_tokens: int, temperature: float) -> str:
    """Call Gemini via the existing intelligence domain interface."""
    from src.domains.intelligence.reasoning.llm import generate_content as _gemini_generate

    return await _gemini_generate(prompt, max_tokens=max_tokens, temperature=temperature)


async def _call_openai(prompt: str, *, max_tokens: int, temperature: float) -> str:
    """Call OpenAI directly."""
    import openai

    from src.config import get_settings

    settings = get_settings()
    if not settings.OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY not configured")

    client = openai.AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
    completion = await client.chat.completions.create(
        model=settings.OPENAI_DEFAULT_MODEL,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=max_tokens,
        temperature=temperature,
    )
    text = completion.choices[0].message.content or ""
    if not text.strip():
        raise RuntimeError("OpenAI returned empty response")
    return text.strip()


async def _call_anthropic(prompt: str, *, max_tokens: int, temperature: float) -> str:
    """Call Anthropic directly."""
    import anthropic

    from src.config import get_settings

    settings = get_settings()
    if not settings.ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY not configured")

    client = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
    message = await client.messages.create(
        model=settings.ANTHROPIC_DEFAULT_MODEL,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
        temperature=temperature,
    )
    # Anthropic returns content blocks
    text = "".join(block.text for block in message.content if hasattr(block, "text"))
    if not text.strip():
        raise RuntimeError("Anthropic returned empty response")
    return text.strip()


_PROVIDER_CALLABLES = {
    "gemini": _call_gemini,
    "openai": _call_openai,
    "anthropic": _call_anthropic,
}


# ---------------------------------------------------------------------------
# Provider Resolution
# ---------------------------------------------------------------------------


async def _resolve_provider(user_id: str | None) -> str:
    """Resolve the preferred LLM provider for a user.

    Returns the provider name (gemini/openai/anthropic).
    Falls back to system default if no preference set or user_id is None.
    """
    if not user_id:
        logger.debug("No user_id provided, using default provider")
        return _DEFAULT_PROVIDER

    from ..repository import personal_learning_repo as repo

    profile = await repo.get_profile_by_user(user_id)
    if profile and profile.preferred_llm_provider:
        provider = profile.preferred_llm_provider.lower().strip()
        if provider in SUPPORTED_PROVIDERS:
            logger.info(f"User {user_id} preferred LLM provider: {provider}")
            return provider
        logger.warning(f"Unknown LLM provider '{provider}' for user {user_id}, using default")
    else:
        logger.debug(f"No LLM preference for user {user_id}, using default: {_DEFAULT_PROVIDER}")

    return _DEFAULT_PROVIDER


def _get_fallback_providers(primary: str) -> list[str]:
    """Get fallback provider order if primary fails."""
    # Try other providers in a reasonable order
    fallback_order = ["gemini", "openai", "anthropic"]
    return [p for p in fallback_order if p != primary]


# ---------------------------------------------------------------------------
# Public Interface
# ---------------------------------------------------------------------------


class LLMUnavailableError(Exception):
    """Raised when the LLM is unavailable (all providers failed) and no fallback provided."""

    pass


async def generate_content(
    prompt: str,
    *,
    max_tokens: int = 2048,
    temperature: float = 0.7,
    timeout_s: float = _DEFAULT_TIMEOUT_S,
    max_retries: int = _MAX_RETRIES,
    fallback: str | None = None,
    user_id: str | None = None,
) -> str:
    """Generate text content with resilience and per-user provider routing.

    Features:
    - Per-user provider selection (from LearningProfile.preferred_llm_provider)
    - Per-call timeout (default 30s)
    - Retry with exponential backoff (default 2 retries)
    - Per-provider circuit breaker (opens after 5 failures, 60s cooldown)
    - Automatic fallback to other providers if primary is down
    - Optional fallback string returned when all providers unavailable

    Args:
        prompt: The prompt to send.
        max_tokens: Max output tokens.
        temperature: Creativity parameter.
        timeout_s: Timeout per attempt in seconds.
        max_retries: Max retry attempts per provider.
        fallback: String to return if all providers fail.
        user_id: User ID to resolve provider preference (None = system default).

    Returns:
        Generated text, or fallback string if all providers unavailable.

    Raises:
        LLMUnavailableError: If all providers unavailable and no fallback provided.
    """
    # Resolve preferred provider
    primary_provider = await _resolve_provider(user_id)
    logger.info(
        f"LLM request: user_id={user_id}, resolved_provider={primary_provider}, "
        f"prompt_length={len(prompt)}, max_tokens={max_tokens}"
    )

    # Build provider attempt order: primary first, then fallbacks
    providers_to_try = [primary_provider] + _get_fallback_providers(primary_provider)

    last_error: Exception | None = None

    for provider in providers_to_try:
        # Check circuit breaker for this provider
        if _is_circuit_open(provider):
            logger.debug(f"LLM circuit open for [{provider}] — skipping")
            continue

        # Check if provider has an API key configured
        call_fn = _PROVIDER_CALLABLES.get(provider)
        if not call_fn:
            continue

        # Try this provider with retries
        for attempt in range(max_retries + 1):
            try:
                result = await asyncio.wait_for(
                    call_fn(prompt, max_tokens=max_tokens, temperature=temperature),
                    timeout=timeout_s,
                )
                _record_success(provider)
                logger.info(f"LLM [{provider}] succeeded: response_length={len(result)}")
                return result

            except TimeoutError:
                last_error = TimeoutError(
                    f"[{provider}] timed out after {timeout_s}s (attempt {attempt + 1})"
                )
                logger.warning(
                    f"LLM [{provider}] timeout (attempt {attempt + 1}/{max_retries + 1})"
                )

            except RuntimeError as e:
                # Provider not configured (no API key) — skip to next provider immediately
                if "not configured" in str(e):
                    logger.debug(f"LLM [{provider}] not configured, skipping")
                    last_error = e
                    break  # Don't retry, move to next provider
                last_error = e
                logger.warning(
                    f"LLM [{provider}] failed (attempt {attempt + 1}/{max_retries + 1}): {e}"
                )

            except Exception as e:
                last_error = e
                logger.warning(
                    f"LLM [{provider}] failed (attempt {attempt + 1}/{max_retries + 1}): "
                    f"{type(e).__name__}: {e}"
                )

            # Exponential backoff between retries
            if attempt < max_retries:
                await asyncio.sleep(0.5 * (2**attempt))
        else:
            # All retries exhausted for this provider — record failure
            _record_failure(provider)

    # All providers exhausted
    if fallback is not None:
        logger.info("All LLM providers unavailable — using fallback response")
        return fallback

    raise LLMUnavailableError(
        f"All LLM providers unavailable after trying {providers_to_try}: {last_error}"
    )


async def generate_content_json(
    prompt: str,
    *,
    max_tokens: int = 2048,
    temperature: float = 0.7,
    timeout_s: float = _DEFAULT_TIMEOUT_S,
    max_retries: int = _MAX_RETRIES,
    fallback: Any = None,
    user_id: str | None = None,
) -> Any:
    """Generate content and parse as JSON. Returns fallback on failure.

    Convenience wrapper for the common pattern of:
    1. Call LLM (with per-user provider routing)
    2. Parse response as JSON
    3. Fall back to a default on any failure (LLM down, bad JSON, etc.)
    """
    import json

    try:
        response = await generate_content(
            prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            timeout_s=timeout_s,
            max_retries=max_retries,
            fallback=None,  # We handle fallback ourselves after JSON parse
            user_id=user_id,
        )
        # Strip markdown fences if present
        cleaned = response.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned[3:]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        cleaned = cleaned.strip()
        if cleaned.startswith("json"):
            cleaned = cleaned[4:].strip()

        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            # Attempt repair: find first [ or { and last ] or }
            first_bracket = -1
            for i, c in enumerate(cleaned):
                if c in "[{":
                    first_bracket = i
                    break
            if first_bracket >= 0:
                last_bracket = max(
                    cleaned.rfind("]"),
                    cleaned.rfind("}"),
                )
                if last_bracket > first_bracket:
                    trimmed = cleaned[first_bracket : last_bracket + 1]
                    try:
                        return json.loads(trimmed)
                    except json.JSONDecodeError:
                        pass
            raise

    except (LLMUnavailableError, json.JSONDecodeError, Exception) as e:
        logger.warning(f"generate_content_json failed: {type(e).__name__}: {e}")
        if fallback is not None:
            return fallback
        raise
