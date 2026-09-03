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
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from src.domains.intelligence.reasoning.llm import GenerationUsage
from src.shared.exceptions import SubscriptionLimitError

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


# ---------------------------------------------------------------------------
# The model-quality paywall (Decision P, drift 23)
# ---------------------------------------------------------------------------

#: Operations that pick their model by tier. Everything else runs the standard model for everybody.
#:
#: **Why a set of names and not a table of unit costs.** Decision P defines the line as 500 units of
#: measured COGS, which reads like an invitation to write `{"quiz_generation": 780, ...}` and compare.
#: The commit before this one deleted exactly that table — `ESTIMATED_OPERATION_UNITS` — because the
#: numbers in it were estimates wearing the costume of measurements, and its ancestor priced a voice
#: minute two orders of magnitude low for the life of the product. The threshold is a real decision
#: about a real cost table; where it *lands* is a fact about six operations, and recording the
#: landing instead of re-deriving it every call means there is nowhere to put a wrong number.
#:
#: The six above the line, with the §6.5 estimates that put them there (in this comment, where they
#: cannot be arithmetic):
#:
#:   resource_recommendations  ~1 600     the most expensive operation in the product
#:   course_outline            ~1 020
#:   quiz_generation             ~780
#:   lesson_body                 ~780
#:   narrative_panel             ~770     three panels, one label
#:   document_generation         ~570
#:
#: **`course_outline` is not in Decision P's enumeration and belongs there.** That paragraph lists
#: quiz generation, lesson bodies, the narrative panels, resource recommendations, document
#: generation "and chat itself" — but Decision R's own table prices course generation at 1 020 units,
#: twice the threshold and dearer than four of the six that are listed. Included here on the
#: threshold rather than on the enumeration, because the threshold is the rule and the list was
#: meant to be its output.
#:
#: Everything absent is below the line and identical on both tiers: note summarise (~110), home
#: guidance (~140), discovery (~150), memory extraction (~100), the four flashcard paths (~160–200),
#: study plans and schedules (~225), note merges, study diagrams, voice session notes. Decision P's
#: argument for the threshold is that downgrading a 100-unit operation saves $0.008 and costs a
#: quality drop on something a learner sees constantly — the worst trade available.
#:
#: A consequence worth stating because it is load-bearing: `UNCHARGED_OPERATIONS` must never be
#: degraded, and a threshold gets that for free rather than needing a second exception list.
#: Onboarding auto-setup and memory extraction are both far below 500 units, so they are absent from
#: this set for the same reason as everything else, and there is no rule to keep in sync.
QUALITY_SPLIT_OPERATIONS = frozenset(
    {
        "resource_recommendations",
        "course_outline",
        "quiz_generation",
        "lesson_body",
        "narrative_panel",
        "document_generation",
    }
)


async def model_for_operation(*, user_id: str | None, operation: str) -> str:
    """Which Gemini model this operation runs on for this learner.

    **Public because one generation path cannot come through this module.**
    `generate_grounded_content` attaches the search tool, which this wrapper's retry-across-providers
    shape cannot host — OpenAI and Anthropic are not substitutes for a Gemini-grounded search — so
    `resource_service`'s step 1 calls it directly. That is the single most expensive operation in the
    product (~1 600 units), so leaving it on the Plus model for everybody would mean the split missed
    the biggest thing it exists to cover. Exporting the decision is how it gets covered without
    `resource_service` resolving an entitlement of its own, which is the "two places deciding what a
    learner is entitled to" that drift 23 is a record of.

    Below the threshold, both tiers get the standard model — so most calls never resolve an
    entitlement at all, and the paywall costs nothing on the paths it does not apply to.

    Above it, the tier decides. It comes from `entitlement_service.resolve`, the one resolver
    (Decision B), which collapses subscription, pass and trial into `"free"` / `"plus"` — so a
    trialling learner gets the Plus model here exactly as they get Plus quiz modes, which is the
    pairing whose absence was drift 11. `resolve` is memoised per request, so above-threshold calls
    in a request that was already gated add no read at all.

    Asked of `entitlement_service` directly rather than through
    `feature_flags.effective_tier_for_request`, which is what chat uses. That function's job is to
    choose between personal and space scope, and this path is personal-only by Decision F — passing
    it `PERSONAL_SCOPE` would be asking a scope question with the answer already written down, and
    it would mean importing `adapter_registry` to reach the service singleton, which constructs the
    provider adapters as a side effect of a tier lookup.

    **No `user_id` means the standard model.** A system-initiated generation has no entitlement to
    read, and the safe reading of "nobody in particular" is the cheap model — matching
    `entitlement_service.FREE_ENTITLEMENT` and the fail-as-free posture everywhere else in the gate.
    The opposite default would mean an unattributed call silently costs 6× and is charged to no one.

    **Fails to standard, not to premium.** A resolver that cannot answer must not hand out the dear
    model; unlike `_refuse_if_exhausted`, which fails open because refusing a paying learner is
    worse than one unbilled call, the wrong answer here is not an outage but a cost, so it fails to
    the cheap side.
    """
    from src.domains.intelligence.reasoning.llm.registry import (
        LlmTask,
        default_model_for,
    )

    standard = default_model_for(LlmTask.GENERATION_STANDARD)
    if user_id is None or operation not in QUALITY_SPLIT_OPERATIONS:
        return standard

    try:
        from src.domains.billing.services import entitlement_service

        tier = (await entitlement_service.resolve(user_id)).tier
    except Exception:
        logger.exception(
            "quality: tier resolution failed for user=%s operation=%s — using standard model",
            user_id,
            operation,
        )
        return standard

    if tier == "plus":
        return default_model_for(LlmTask.GENERATION_PREMIUM)
    return standard


async def _refuse_if_exhausted(*, user_id: str | None, operation: str) -> None:
    """Refuse a learner whose window is already spent, before a provider is called.

    **The counterpart to measured metering, and the reason it is safe.** A measured operation cannot
    be checked against its own cost in advance — the cost is not known until the generation has
    happened — so the pre-flight question is not "can they afford this?" but "do they have anything
    left at all?". Refusing at zero is what stops an exhausted learner from generating indefinitely
    while `record_units` dutifully logs an ever-growing overshoot.

    A window can still be exceeded by the cost of one operation in flight. That is bounded, it
    self-corrects on the next call, and it is the price of measuring cost rather than inventing it.

    Raises `SubscriptionLimitError`, which existing handlers already turn into a `402` carrying the
    reset time. Unlike `_meter`, this **is** allowed to raise: it runs before any money is spent, and
    refusing is the entire point of it.
    """
    if user_id is None or operation in UNCHARGED_OPERATIONS:
        return
    try:
        from src.domains.billing.services.credit_consumption_service import (
            has_headroom,
            has_proactive_headroom,
        )
        from src.domains.identity.repository import IdentityRepository

        user = await IdentityRepository().find_by_id(user_id)
        if user is None:
            return
        available, message = await has_headroom(user)
        # A proactive generation has two bounds to clear, and the sub-cap is the tighter one. Checked
        # second so an exhausted learner still gets the window message: "out of allowance" is the
        # truer statement of their position than "the background budget is spent".
        if available and _PROACTIVE.get():
            available, message = await has_proactive_headroom(user)
    except Exception:
        # A gate that cannot read the meter must not become an outage. Fail **open**: the learner
        # gets their generation and `record_units` records the spend. The opposite choice would turn
        # a transient database blip into a product-wide refusal, which is a far worse failure than
        # one unbilled operation.
        logger.exception(
            "usage: headroom check failed for user=%s operation=%s — allowing",
            user_id,
            operation,
        )
        return

    if not available:
        logger.info(
            "usage: refused user=%s operation=%s — window exhausted before generation",
            user_id,
            operation,
        )
        raise SubscriptionLimitError(
            message=message or "You've used this session's allowance. It refills automatically.",
            detail=f"operation={operation}, limit=window_exhausted",
        )


#: Marks the current task as proactive, so its spend lands in the sub-budget too (Decision M).
#:
#: **A context variable rather than a parameter, and the reason is the call depth.** A Celery task
#: reaches a provider through `discovery_service` or `reflection_service`, each of which calls the
#: chokepoint several frames down. Threading a `proactive` flag would mean widening every service
#: signature between the task and the meter, and every one of those parameters would exist only to be
#: passed on — which is a lot of places for one to be forgotten, and forgetting it fails silently by
#: charging the month without the sub-budget.
_PROACTIVE: ContextVar[bool] = ContextVar("maigie_proactive_generation", default=False)


@contextmanager
def proactive_scope():
    """Mark everything generated inside this block as proactive.

    Wrapped per learner rather than around a whole task, so one learner's budget cannot leak into the
    next one's accounting if a batch raises midway.
    """
    token = _PROACTIVE.set(True)
    try:
        yield
    finally:
        _PROACTIVE.reset(token)


def is_proactive() -> bool:
    return _PROACTIVE.get()


async def meter_usage(
    *,
    user_id: str | None,
    operation: str,
    usage: GenerationUsage | None,
    attempt_of: str = "direct",
) -> None:
    """Charge one provider call to the learner's window.

    **Public because one call site cannot use the chokepoint.**
    `intelligence.reasoning.llm.generate_grounded_content` attaches the search tool, which the
    retry-across-providers shape cannot host, so `resource_service` calls it directly — and at ~1 600
    units it is the most expensive operation in the product. It charges through here rather than
    reimplementing, so the exemption list, the unmeasured-reply warning and the never-raises
    guarantee are shared rather than duplicated. Duplicating them is how one path ends up exempting
    something the other charges.

    `attempt_of` defaults to `"direct"` for those callers: there is no provider chain to name, and
    labelling it as such keeps the warning line readable when a direct call comes back unmeasured.

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
        await record_units(user_id, units, operation=operation, proactive=_PROACTIVE.get())
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

#: Hard ceiling on billable provider calls for one logical operation, across every provider tried.
#:
#: `_MAX_RETRIES` is per provider and says nothing about the sum, so three enabled providers meant a
#: worst case of nine charged calls for one generation. Four is three attempts on the primary plus one
#: on a fallback: it keeps the retry that actually recovers transient blanks — the failure this
#: module's empty-reply handling exists for, which recovers on the second attempt — while refusing to
#: fund a full walk of the chain. **Since retries are charged, this is a price and not only a
#: timeout.**
_MAX_TOTAL_ATTEMPTS = 4
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
    prompt: str,
    *,
    max_tokens: int,
    temperature: float,
    thinking: int | None = None,
    model: str | None = None,
) -> ProviderReply:
    """Call Gemini via the existing intelligence domain interface.

    `thinking` is Gemini-only. OpenAI and Anthropic take no equivalent here, so a caller that bounds
    reasoning gets that bound on the primary provider and the provider default on a fallback. Worth
    knowing rather than hiding: a fallback is more expensive than the call it replaced, in tokens as
    well as in latency.

    `model` is likewise Gemini-only, and the same caveat applies with money attached: **the quality
    paywall exists on the Gemini path alone.** A free learner whose Gemini attempts all fail falls
    through to `OPENAI_DEFAULT_MODEL`, which no allowlist has been consulted about and which may cost
    more than the Plus model the split was avoiding. Building a second tier map for the fallback
    providers would be two places deciding what a learner is entitled to, which is the mistake
    drift 23 records; the honest bound is that a fallback is rare, and the fix if it stops being
    rare is a cheaper fallback model rather than a second allowlist.
    """
    from src.domains.intelligence.reasoning.llm import generate_content_with_usage

    text, usage = await generate_content_with_usage(
        prompt,
        max_tokens=max_tokens,
        temperature=temperature,
        thinking=thinking,
        model=model,
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


def enabled_providers() -> tuple[str, ...]:
    """The providers this module may call, honouring `LLM_ENABLED_PROVIDERS`.

    **`LLM_ENABLED_PROVIDERS` did not reach this module, and "turn a provider off" therefore only
    turned it off for chat.** It is read by `adapter_registry`, `feature_flags` and `router` — the
    chat path — while this module hardcoded `["gemini", "openai", "anthropic"]` for fallbacks and
    validated a learner's stored preference against `SUPPORTED_PROVIDERS`. So disabling OpenAI in
    production would have left it serving all 27 generation surfaces as a fallback, and serving them
    as the *primary* for any learner whose `preferred_llm_provider` was `"openai"`.

    The only thing that actually stopped a disabled provider was an unset API key, which works by
    accident: `_call_openai` raises `RuntimeError("... not configured")` and the attempt loop treats
    that as "skip to the next provider". Depending on a missing credential to enforce a policy means
    the policy is silently re-enabled by anyone who sets the credential.

    `router.py` already states the intended rule — "turning a provider off must turn it off
    everywhere" — and this is the half of "everywhere" that was missing.

    **An empty or unparseable list is treated as no opinion rather than as "call nothing".** A
    configuration that disables every provider is a mistake, not an instruction, and honouring it
    literally would take every AI surface in the product down at once. It falls back to the default
    provider and logs at `error`, which is the outcome that is loud without being an outage.
    """
    from src.config import get_settings

    configured = {
        name.strip().lower()
        for name in (get_settings().LLM_ENABLED_PROVIDERS or "").split(",")
        if name.strip()
    }
    allowed = tuple(name for name in SUPPORTED_PROVIDERS if name in configured)
    if allowed:
        return allowed

    logger.error(
        "LLM_ENABLED_PROVIDERS names no provider this module can call (%r); falling back to %s. "
        "Disabling every provider is a misconfiguration rather than an instruction.",
        get_settings().LLM_ENABLED_PROVIDERS,
        _DEFAULT_PROVIDER,
    )
    return (_DEFAULT_PROVIDER,)


async def _resolve_provider(user_id: str | None) -> str:
    """Resolve the preferred LLM provider for a user.

    Returns the provider name (gemini/openai/anthropic).
    Falls back to the system default if there is no preference, the preference names a provider that
    is not enabled, or `user_id` is None.
    """
    allowed = enabled_providers()
    # The configured default is not guaranteed to be enabled, so the fallback for "no preference" is
    # the default when it is callable and the first enabled provider when it is not. Returning a
    # disabled default would put the whole product on a provider the operator switched off.
    default = _DEFAULT_PROVIDER if _DEFAULT_PROVIDER in allowed else allowed[0]

    # **One provider means there is nothing to resolve.** Reading `LearningProfile` to choose between
    # one option is a database round trip on every generation in the product that cannot change the
    # answer, and today Gemini is the only enabled provider. The read exists to honour a learner's
    # preference, and a preference is only meaningful when there is an alternative.
    #
    # This is a short-circuit rather than a deletion because the preference is a real feature the
    # moment a second key is provisioned — and it is deliberately keyed on the *enabled* set rather
    # than on `SUPPORTED_PROVIDERS`, so it re-enables itself along with the provider.
    if len(allowed) == 1:
        return default

    if not user_id:
        logger.debug("No user_id provided, using default provider")
        return default

    from ..repository import personal_learning_repo as repo

    profile = await repo.get_profile_by_user(user_id)
    if profile and profile.preferred_llm_provider:
        provider = profile.preferred_llm_provider.lower().strip()
        if provider in allowed:
            logger.info(f"User {user_id} preferred LLM provider: {provider}")
            return provider
        if provider in SUPPORTED_PROVIDERS:
            # Distinguished from an unknown name on purpose: this is a real provider the operator has
            # switched off, and the learner's stored preference is now unhonourable. Worth a distinct
            # log line, because the remedy is a settings screen that stops offering it rather than a
            # data fix — the same mismatch as drift 22 on the web model picker.
            logger.info(
                "User %s prefers %s, which is not in LLM_ENABLED_PROVIDERS; using %s",
                user_id,
                provider,
                default,
            )
        else:
            logger.warning(f"Unknown LLM provider '{provider}' for user {user_id}, using default")
    else:
        logger.debug(f"No LLM preference for user {user_id}, using default: {default}")

    return default


def _get_fallback_providers(primary: str) -> list[str]:
    """Get fallback provider order if primary fails, enabled providers only."""
    return [name for name in enabled_providers() if name != primary]


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
    # Refuse an already-exhausted learner before spending anything. **Once per logical operation,
    # not once per attempt** — a retry must not re-gate, or an operation that started legitimately
    # could be refused halfway through its own retry chain and leave the learner with nothing after
    # we had already paid for two provider calls.
    await _refuse_if_exhausted(user_id=user_id, operation=operation)

    # Resolve preferred provider
    primary_provider = await _resolve_provider(user_id)
    # Resolved once per logical operation, for the same reason the gate is: a retry must not be able
    # to change which model the learner is on halfway through, or one operation could bill two
    # different rates and the log would show the last one.
    model = await model_for_operation(user_id=user_id, operation=operation)
    logger.info(
        f"LLM request: user_id={user_id}, resolved_provider={primary_provider}, "
        f"operation={operation}, model={model}, "
        f"prompt_length={len(prompt)}, max_tokens={max_tokens}"
    )

    # Build provider attempt order: primary first, then fallbacks
    providers_to_try = [primary_provider] + _get_fallback_providers(primary_provider)

    last_error: Exception | None = None

    # **A ceiling on the total, not just on each provider.** `max_retries` is per provider, so with
    # three enabled the worst case was three attempts × three providers = nine billable calls for one
    # logical operation, and every one of them is charged (Decision L). That was a reliability
    # decision made before retries cost money.
    #
    # Dormant today because `LLM_ENABLED_PROVIDERS` is Gemini alone, which is exactly why it is worth
    # fixing now: the bug reappears the moment a second provider is enabled, and it reappears as a
    # bill rather than as an error. `_MAX_TOTAL_ATTEMPTS` bounds the spend regardless of how many
    # providers exist.
    attempts_left = _MAX_TOTAL_ATTEMPTS

    for provider in providers_to_try:
        if attempts_left <= 0:
            logger.warning(
                "LLM giving up after %d total attempts across providers (operation=%s)",
                _MAX_TOTAL_ATTEMPTS,
                operation,
            )
            break

        # Check circuit breaker for this provider
        if _is_circuit_open(provider):
            logger.debug(f"LLM circuit open for [{provider}] — skipping")
            continue

        # Check if provider has an API key configured
        call_fn = _PROVIDER_CALLABLES.get(provider)
        if not call_fn:
            continue

        # Try this provider with retries, bounded by what is left of the total budget.
        for attempt in range(min(max_retries + 1, attempts_left)):
            attempts_left -= 1
            try:
                # `thinking` and `model` reach Gemini only. The other two callables do not accept
                # either, and passing them here rather than widening those signatures keeps both
                # parameters where they mean something. See `_call_gemini` for what that costs on a
                # fallback.
                extra = {"thinking": thinking, "model": model} if provider == "gemini" else {}
                reply = await asyncio.wait_for(
                    call_fn(prompt, max_tokens=max_tokens, temperature=temperature, **extra),
                    timeout=timeout_s,
                )
                result = reply.text
                # Charged before the empty-reply check below, because **an empty reply still
                # consumed tokens.** The provider answered, billed us, and produced nothing usable,
                # which is exactly the case where charging on delivery instead of on spend would
                # leave real cost invisible. Decision L on retries is the same argument.
                await meter_usage(
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

    except SubscriptionLimitError:
        # **Never swallowed into a fallback.** A refusal is not a generation failure — it is a
        # deliberate answer that the learner is out of allowance, and it has to reach them as a `402`
        # with a reset time. Returning `fallback` here would silently hand a caller an empty object,
        # so a learner who had run out would see an empty quiz rather than being told why. This
        # `except` is above the broad one for that reason alone; it does nothing else.
        raise
    except (LLMUnavailableError, json.JSONDecodeError, Exception) as e:
        logger.warning(f"generate_content_json failed: {type(e).__name__}: {e}")
        if fallback is not None:
            return fallback
        raise
