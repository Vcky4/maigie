"""
Resilient LLM wrapper for the Personal Learning domain.

Adds timeout, retry budget, and circuit breaker pattern around LLM calls.
All personal learning services should import generate_content from here
instead of directly from the intelligence domain.

Circuit Breaker States:
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
# Circuit Breaker State
# ---------------------------------------------------------------------------

_FAILURE_THRESHOLD = 5  # Consecutive failures before opening circuit
_RECOVERY_TIMEOUT_S = 60  # Seconds before trying again after open
_DEFAULT_TIMEOUT_S = 30  # Per-call timeout in seconds
_MAX_RETRIES = 2  # Max retries per call (total attempts = MAX_RETRIES + 1)

_failure_count: int = 0
_last_failure_time: float = 0.0
_circuit_state: str = "CLOSED"  # CLOSED | OPEN | HALF_OPEN


def _is_circuit_open() -> bool:
    """Check if the circuit breaker is open (blocking calls)."""
    global _circuit_state, _failure_count, _last_failure_time

    if _circuit_state == "CLOSED":
        return False

    if _circuit_state == "OPEN":
        # Check if cooldown period has passed
        elapsed = time.monotonic() - _last_failure_time
        if elapsed >= _RECOVERY_TIMEOUT_S:
            _circuit_state = "HALF_OPEN"
            logger.info("LLM circuit breaker: HALF_OPEN (testing recovery)")
            return False  # Allow one test call
        return True  # Still blocking

    # HALF_OPEN: allow the call
    return False


def _record_success() -> None:
    """Record a successful call — reset circuit breaker."""
    global _failure_count, _circuit_state
    _failure_count = 0
    if _circuit_state != "CLOSED":
        logger.info("LLM circuit breaker: CLOSED (recovered)")
    _circuit_state = "CLOSED"


def _record_failure() -> None:
    """Record a failed call — potentially open the circuit."""
    global _failure_count, _circuit_state, _last_failure_time

    _failure_count += 1
    _last_failure_time = time.monotonic()

    if _failure_count >= _FAILURE_THRESHOLD:
        _circuit_state = "OPEN"
        logger.warning(
            f"LLM circuit breaker: OPEN after {_failure_count} consecutive failures. "
            f"Will retry in {_RECOVERY_TIMEOUT_S}s."
        )
    elif _circuit_state == "HALF_OPEN":
        # Test call failed — go back to OPEN
        _circuit_state = "OPEN"
        logger.warning("LLM circuit breaker: OPEN (recovery test failed)")


# ---------------------------------------------------------------------------
# Public Interface
# ---------------------------------------------------------------------------


class LLMUnavailableError(Exception):
    """Raised when the LLM is unavailable (circuit open) and no fallback provided."""

    pass


async def generate_content(
    prompt: str,
    *,
    max_tokens: int = 2048,
    temperature: float = 0.7,
    timeout_s: float = _DEFAULT_TIMEOUT_S,
    max_retries: int = _MAX_RETRIES,
    fallback: str | None = None,
) -> str:
    """Generate text content with resilience guarantees.

    Features:
    - Per-call timeout (default 30s)
    - Retry with exponential backoff (default 2 retries)
    - Circuit breaker (opens after 5 consecutive failures, 60s cooldown)
    - Optional fallback string returned when LLM is unavailable

    Args:
        prompt: The prompt to send.
        max_tokens: Max output tokens.
        temperature: Creativity parameter.
        timeout_s: Timeout per attempt in seconds.
        max_retries: Max retry attempts (0 = no retries).
        fallback: String to return if all attempts fail and circuit is open.
                  If None and all attempts fail, raises LLMUnavailableError.

    Returns:
        Generated text, or fallback string if LLM unavailable.

    Raises:
        LLMUnavailableError: If LLM unavailable and no fallback provided.
    """
    # Circuit breaker check
    if _is_circuit_open():
        logger.debug("LLM circuit open — returning fallback or raising")
        if fallback is not None:
            return fallback
        raise LLMUnavailableError("LLM circuit breaker is OPEN. Try again later.")

    from src.domains.intelligence.reasoning.llm import (
        generate_content as _raw_generate,
    )

    last_error: Exception | None = None

    for attempt in range(max_retries + 1):
        try:
            result = await asyncio.wait_for(
                _raw_generate(prompt, max_tokens=max_tokens, temperature=temperature),
                timeout=timeout_s,
            )
            _record_success()
            return result

        except asyncio.TimeoutError:
            last_error = asyncio.TimeoutError(
                f"LLM call timed out after {timeout_s}s (attempt {attempt + 1})"
            )
            logger.warning(f"LLM timeout (attempt {attempt + 1}/{max_retries + 1})")

        except Exception as e:
            last_error = e
            logger.warning(
                f"LLM call failed (attempt {attempt + 1}/{max_retries + 1}): {type(e).__name__}: {e}"
            )

        # Exponential backoff between retries (0.5s, 1s, 2s, ...)
        if attempt < max_retries:
            await asyncio.sleep(0.5 * (2**attempt))

    # All attempts exhausted
    _record_failure()

    if fallback is not None:
        logger.info("LLM unavailable — using fallback response")
        return fallback

    raise LLMUnavailableError(f"LLM unavailable after {max_retries + 1} attempts: {last_error}")


async def generate_content_json(
    prompt: str,
    *,
    max_tokens: int = 2048,
    temperature: float = 0.7,
    timeout_s: float = _DEFAULT_TIMEOUT_S,
    max_retries: int = _MAX_RETRIES,
    fallback: Any = None,
) -> Any:
    """Generate content and parse as JSON. Returns fallback on failure.

    Convenience wrapper for the common pattern of:
    1. Call LLM
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
            # Attempt repair: truncated arrays are common with LLM output limits
            repaired = _try_repair_json(cleaned)
            if repaired is not None:
                return repaired
            raise
    except (LLMUnavailableError, json.JSONDecodeError, Exception) as e:
        logger.warning(f"generate_content_json failed: {type(e).__name__}: {e}")
        if fallback is not None:
            return fallback
        raise


def _try_repair_json(text: str) -> Any:
    """Attempt to repair truncated JSON produced by an LLM.

    Strategy: walk back from EOF to the last complete `}` or `]`,
    trim any trailing comma, then close any still-open brackets in the
    correct nesting order.

    Examples that recover:
      - Array of objects truncated mid-string:
          [{"a": 1}, {"b": 2}, {"c": "trun...  →  [{"a": 1}, {"b": 2}]
      - Object with a truncated nested array:
          {"m": "hi", "items": [{"t": "a"}, {"t": "tr...  →  {"m": "hi", "items": [{"t": "a"}]}
      - Trailing comma before EOF:
          {"a": 1, "b": 2,  →  {"a": 1, "b": 2}
    """
    import json

    stripped = text.strip()
    if not stripped:
        return None

    # Fast path: brackets already balanced — the failure was something else.
    if stripped.count("{") == stripped.count("}") and stripped.count("[") == stripped.count("]"):
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            pass

    def _close_openers(candidate: str) -> str | None:
        """Walk the candidate to figure out the still-open bracket stack, then
        close them in reverse order. Return None if the walker gets confused
        (e.g. we're mid-string)."""
        stack: list[str] = []
        in_string = False
        escape = False
        for ch in candidate:
            if in_string:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_string = False
                continue
            if ch == '"':
                in_string = True
            elif ch in "{[":
                stack.append(ch)
            elif ch == "}":
                if not stack or stack[-1] != "{":
                    return None
                stack.pop()
            elif ch == "]":
                if not stack or stack[-1] != "[":
                    return None
                stack.pop()
        # If we ended mid-string the candidate is not a clean boundary.
        if in_string:
            return None
        closers = "".join("}" if opener == "{" else "]" for opener in reversed(stack))
        return candidate + closers

    # First, try just closing whatever is open on the whole string —
    # handles cases like `{"a": 1, "b": 2,` where there's no closing brace.
    whole = stripped.rstrip().rstrip(",").rstrip()
    attempt = _close_openers(whole)
    if attempt is not None:
        try:
            return json.loads(attempt)
        except json.JSONDecodeError:
            pass

    # Then try progressively earlier boundary points (last `}` or `]`).
    tried: set[int] = set()
    for boundary_char in ("}", "]"):
        idx = stripped.rfind(boundary_char)
        while idx > 0:
            if idx in tried:
                idx = stripped.rfind(boundary_char, 0, idx)
                continue
            tried.add(idx)
            candidate = stripped[: idx + 1].rstrip().rstrip(",").rstrip()
            attempt = _close_openers(candidate)
            if attempt is not None:
                try:
                    return json.loads(attempt)
                except json.JSONDecodeError:
                    pass
            idx = stripped.rfind(boundary_char, 0, idx)

    return None
