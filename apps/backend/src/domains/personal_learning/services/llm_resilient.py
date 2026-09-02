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
import json
import logging
import time
from dataclasses import dataclass
from typing import Any

from src.domains.intelligence.reasoning.llm import GenerationUsage

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Metering (Decision L)
# ---------------------------------------------------------------------------

#: Operations that are never charged, on principle rather than on cost (§6.6).
#:
#: **Onboarding** is where "free should create real success" is either honoured or not; charging a
#: learner before they have learned anything is the one place a meter is self-defeating.
#: **Memory extraction** is what makes Maigie feel like it knows you, and a learner cannot be asked
#: to pay for the product remembering them.
#:
#: Matched against the `operation` label a caller passes. An unlabelled call is charged — the
#: default has to be "charge", or exemption becomes the thing that happens by forgetting.
UNCHARGED_OPERATIONS = frozenset(
    {
        "onboarding_auto_setup",
        "memory_extraction",
        "memory_summarisation",
    }
)


async def _meter(
    *,
    user_id: str | None,
    operation: str,
    usage: GenerationUsage | None,
    attempt_of: str,
) -> None:
    """Charge one provider call to the learner's window.

    **Called per attempt, not per operation, and that is deliberate** (Decision L). One logical
    generation can bill up to nine provider calls — three attempts across three providers, with an
    empty reply counted as a failure — and every one of them costs real money. Metering inside the
    loop counts what was spent rather than what was delivered.

    It will look unfair the first time a learner's allowance goes on our own instability, and the
    honest response is to shorten the retry chain rather than to hide the charge.

    **Never raises, and that is enforced here rather than assumed of the caller.** `record_units`
    swallows its own database failures, but this function also prices the call — `units_for_tokens`
    reaches the rate card, which can be handed a model name it has never seen — and an exception
    escaping into the attempt loop is indistinguishable from a provider failure. It would be caught
    by the loop's `except Exception`, counted as a failed attempt, retried, and could exhaust the
    provider chain: **an accounting error would present as an outage.** A test pins this, because it
    is the failure mode nobody would look for.
    """
    if user_id is None:
        # A system-initiated generation with nobody to charge. Background tasks that should be
        # attributed pass `user_id`; the ones that legitimately have no learner (warmups, health
        # probes) are rare and small.
        return
    if operation in UNCHARGED_OPERATIONS:
        logger.debug("usage: %s exempt from charging (user=%s)", operation, user_id)
        return
    if usage is None:
        # Loudly, because this is the gap Decision L exists to close: a provider path that returns
        # no token counts is an unmetered surface, and the only way that gets fixed is if it is
        # visible in the logs rather than silently charged as zero.
        logger.warning(
            "usage: unmetered provider reply for user=%s operation=%s provider=%s",
            user_id,
            operation,
            attempt_of,
        )
        return

    try:
        from src.domains.billing.services.credit_consumption_service import (
            record_units,
            units_for_tokens,
        )

        units = units_for_tokens(usage.input_tokens, usage.billable_output_tokens, usage.model)
        await record_units(user_id, units, operation=operation)
    except Exception:
        logger.exception(
            "usage: metering failed for user=%s operation=%s model=%s — generation kept",
            user_id,
            operation,
            usage.model,
        )


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


@dataclass(frozen=True)
class ProviderReply:
    """A provider's text plus what it consumed, so the attempt loop can charge for it.

    Usage is optional because only Gemini reports it reliably through this path today. A `None` is
    an *unmeasured* call rather than a free one — `_meter` logs the gap instead of charging zero
    silently, because a provider that quietly costs nothing is how an unmetered surface returns.
    """

    text: str
    usage: GenerationUsage | None = None


async def _call_gemini(
    prompt: str, *, max_tokens: int, temperature: float, thinking: int | None = None
) -> ProviderReply:
    """Call Gemini via the existing intelligence domain interface.

    `thinking` is Gemini-only. OpenAI and Anthropic take no equivalent here, so a caller that bounds
    reasoning gets that bound on the primary provider and the provider default on a fallback. Worth
    knowing rather than hiding: a fallback is more expensive than the call it replaced, in tokens as
    well as in latency.
    """
    from src.domains.intelligence.reasoning.llm import generate_content_with_usage

    text, usage = await generate_content_with_usage(
        prompt, max_tokens=max_tokens, temperature=temperature, thinking=thinking
    )
    return ProviderReply(text=text, usage=usage)


async def _call_openai(prompt: str, *, max_tokens: int, temperature: float) -> ProviderReply:
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
    usage = getattr(completion, "usage", None)
    return ProviderReply(
        text=text.strip(),
        usage=(
            GenerationUsage(
                model=settings.OPENAI_DEFAULT_MODEL,
                input_tokens=getattr(usage, "prompt_tokens", 0) or 0,
                output_tokens=getattr(usage, "completion_tokens", 0) or 0,
            )
            if usage is not None
            else None
        ),
    )


async def _call_anthropic(prompt: str, *, max_tokens: int, temperature: float) -> ProviderReply:
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
    usage = getattr(message, "usage", None)
    return ProviderReply(
        text=text.strip(),
        usage=(
            GenerationUsage(
                model=settings.ANTHROPIC_DEFAULT_MODEL,
                input_tokens=getattr(usage, "input_tokens", 0) or 0,
                output_tokens=getattr(usage, "output_tokens", 0) or 0,
            )
            if usage is not None
            else None
        ),
    )


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
    thinking: int | None = None,
    operation: str = "unknown",
) -> str:
    """Generate text content with resilience and per-user provider routing.

    `thinking` bounds hidden reasoning tokens on the Gemini path — see
    `intelligence.reasoning.llm.THINKING_OFF` / `_BOUNDED` / `_DYNAMIC`. Ignored by the OpenAI and
    Anthropic fallbacks, which take no equivalent parameter here.

    `operation` labels the call for the meter and for per-operation measurement. It defaults to
    `"unknown"` rather than being required because 26 call sites reach this function and a required
    argument would have meant changing all of them in the commit that introduced metering; the
    labels arrive per call site, and `"unknown"` is visible in the logs until they do. **Passing
    `user_id` is what makes a call chargeable at all** — a call without one is not charged, which is
    correct for genuinely system-initiated work and a bug anywhere else.

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
                # `thinking` reaches Gemini only. The other two callables do not accept it, and
                # inspecting the signature here rather than widening theirs keeps the parameter
                # where it means something.
                extra = {"thinking": thinking} if provider == "gemini" else {}
                reply = await asyncio.wait_for(
                    call_fn(prompt, max_tokens=max_tokens, temperature=temperature, **extra),
                    timeout=timeout_s,
                )
                result = reply.text
                # Charged before the empty-reply check below, because **an empty reply still
                # consumed tokens.** The provider answered, billed us, and produced nothing usable,
                # which is exactly the case where charging on delivery instead of on spend would
                # leave real cost invisible. Decision L on retries is the same argument.
                await _meter(
                    user_id=user_id,
                    operation=operation,
                    usage=reply.usage,
                    attempt_of=provider,
                )
                # An empty reply is a failed attempt, not a successful one.
                #
                # This was the root cause of a `500` on the study-diagram route. A provider answered with an
                # empty string, which this returned as a success — so it was never retried, never fell
                # through to the next provider, and `_record_success` even credited the circuit breaker for
                # it. The caller then handed "" to a JSON parser and got `Expecting value: line 1 column 1`.
                #
                # A retry was all that failure needed: asked again moments later, the same provider produced
                # the diagram correctly. Treating it as an exception puts it through the machinery that
                # already exists for exactly this — backoff, then the next provider — rather than adding a
                # second recovery path beside it.
                #
                # No generation in this product has a use for an empty string: every caller either parses it
                # or displays it. A caller that supplied a `fallback` still gets it if every provider comes
                # back empty, so the only behaviour that changes is that a transient blank is now retried.
                if not (result or "").strip():
                    raise ValueError("provider returned an empty response")
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


def _repair_json(cleaned: str) -> Any | None:
    """Recover a usable object from a nearly-valid JSON reply, or `None` if there is nothing to recover.

    Two failure modes, and the second is the common one.

    **Surrounding prose.** The model wraps the object in a sentence. Handled by trimming to the outermost
    brackets, which is what this function used to do and all it used to do.

    **Truncation.** The reply hit the token limit mid-value, so it ends in the middle of a string with a
    dozen structures still open. The old repair made this *worse*: trimming to the last `}` or `]` cuts at
    whatever nested object happened to close last, producing a fragment that is still invalid. A generated
    lesson failed exactly this way — `Unterminated string starting at line 56 column 16` — and the route
    answered `500`.

    Truncation is repaired by closing what is open: terminate the dangling string, drop a trailing comma or
    key, and emit the missing brackets in reverse order of opening. The result is the prefix the model did
    finish, which for a list of sections is most of them — and the parsers downstream already drop
    incomplete entries, so a half-written final section is discarded rather than rendered.

    Returns `None` rather than raising, so the caller decides between a fallback and an error. Repair is
    best-effort by nature and a failure here is not exceptional.
    """
    # Trim to the outermost brackets first: prose before or after the object defeats everything else.
    start = next((i for i, char in enumerate(cleaned) if char in "[{"), -1)
    if start < 0:
        return None

    candidate = cleaned[start:]

    # The old behaviour, kept because it is the right repair when the reply is complete but wrapped.
    last = max(candidate.rfind("]"), candidate.rfind("}"))
    if last > 0:
        try:
            return json.loads(candidate[: last + 1])
        except json.JSONDecodeError:
            pass

    # Truncation. Walk the text tracking structure, ignoring anything inside a string, so a `}` in prose
    # does not read as a closing brace.
    stack: list[str] = []
    in_string = False
    escaped = False
    for char in candidate:
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char in "[{":
            stack.append(char)
        elif char in "]}":
            if stack:
                stack.pop()

    if not stack and not in_string:
        return None

    patched = candidate
    # Close the dangling string. An escape character immediately before the cut would escape the quote
    # being added, so it goes too.
    if in_string:
        if escaped:
            patched = patched[:-1]
        patched += '"'

    # A trailing comma, or a key with no value, cannot be closed into anything valid — drop back to the
    # last complete entry.
    patched = patched.rstrip()
    while patched and patched[-1] in ",:":
        patched = patched[:-1].rstrip()
        if patched.endswith('"'):
            # A dangling key: remove it, leaving the object to be closed.
            key_start = patched.rfind('"', 0, len(patched) - 1)
            if key_start > 0 and patched[:key_start].rstrip().endswith(("{", ",")):
                patched = patched[:key_start].rstrip().rstrip(",")

    patched += "".join("]" if opener == "[" else "}" for opener in reversed(stack))

    try:
        return json.loads(patched)
    except json.JSONDecodeError:
        return None


async def generate_content_json(
    prompt: str,
    *,
    max_tokens: int = 2048,
    temperature: float = 0.7,
    timeout_s: float = _DEFAULT_TIMEOUT_S,
    max_retries: int = _MAX_RETRIES,
    fallback: Any = None,
    user_id: str | None = None,
    thinking: int | None = None,
    operation: str = "unknown",
) -> Any:
    """Generate content and parse as JSON. Returns fallback on failure.

    Convenience wrapper for the common pattern of:
    1. Call LLM (with per-user provider routing)
    2. Parse response as JSON
    3. Fall back to a default on any failure (LLM down, bad JSON, etc.)

    **`fallback` is what is returned on failure, and `None` means "no fallback — raise".** The two readings
    of `fallback=None` are not the same and the ambiguity has already cost a `500`: two routes passed it
    meaning "give me `None` and I will handle it", and got an unhandled `JSONDecodeError` instead. There is
    no way to ask for `None` on failure; a caller that wants that catches the exception.
    """
    try:
        response = await generate_content(
            prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            timeout_s=timeout_s,
            max_retries=max_retries,
            fallback=None,  # We handle fallback ourselves after JSON parse
            user_id=user_id,
            thinking=thinking,
            operation=operation,
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

        # An empty reply is not malformed JSON, and saying so matters when something goes wrong in
        # production. `json.loads("")` reports `Expecting value: line 1 column 1 (char 0)`, which reads as a
        # parsing problem and sends whoever is debugging it looking at the prompt or the repair logic — when
        # what actually happened is that the provider returned nothing at all. Raised rather than returned so
        # the fallback handling below is unchanged; only the diagnosis improves.
        if not cleaned:
            raise ValueError("The model returned an empty response, so there was no JSON to parse")

        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            repaired = _repair_json(cleaned)
            if repaired is not None:
                return repaired
            raise

    except (LLMUnavailableError, json.JSONDecodeError, Exception) as e:
        logger.warning(f"generate_content_json failed: {type(e).__name__}: {e}")
        if fallback is not None:
            return fallback
        raise
