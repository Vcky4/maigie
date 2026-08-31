"""Adapter registry and dependency injection for multi-provider LLM routing.

Provides factory functions to instantiate provider adapters based on application
settings, and a ``get_llm_router()`` dependency that wires the LLMRouter with
all required services (FeatureFlagService, CircuitBreaker, CostTracker, adapters).

The registry is lazily initialized on first access and cached for the process
lifetime. Only providers listed in ``LLM_ENABLED_PROVIDERS`` are instantiated.

Ported from ``src/services/llm/adapter_registry.py`` at ``4953972^``, replacing a version whose
``get_llm_router`` raised ``UnmigratedSubsystemError`` unconditionally — which is why no chat turn
could produce a response.

Three departures from the original, each because it referred to something that no longer exists:

- **Configuration comes from ``Settings`` only.** The original read every value through
  ``src.services.system_config_service``'s module-level ``_cache`` — reaching into another module's
  private cache dict and TTL to do a synchronous read of DB-stored config, with ``Settings`` as the
  fallback. That module was not migrated. Rather than reconstruct it, this reads ``Settings``
  directly, which was already the fallback path. **The consequence is real and worth stating: LLM
  configuration is no longer changeable at runtime from the admin dashboard, so
  ``invalidate_llm_router()`` now only picks up a process restart or an in-memory override.** The
  admin surface for this is not mounted either, so nothing regresses today.
- **``CostTracker`` takes a session factory, not a Prisma client.** It also resolves the factory at
  call time, so building the router at import time no longer requires a connected database.
- **Adapter construction stays inside ``try/except``.** All four adapter modules exist as of step 6,
  so the blocks below are no longer guarding against a missing import — they guard against a
  constructor that raises, typically on a malformed key or a provider SDK that changes its
  signature. One provider failing to register must not take the other two down with it, and the
  router already treats an unregistered ``provider:model`` as a candidate to skip.

  Anthropic is a real example of that path today: it is absent from ``LLM_ENABLED_PROVIDERS`` *and*
  has no API key set, so nothing registers under ``anthropic:*`` even though it appears in both
  fallback chains. That is inert, not broken.
"""

from __future__ import annotations

import logging
from typing import Any

from src.config import get_settings
from src.domains.intelligence.reasoning.llm.base_adapter import BaseProviderAdapter
from src.domains.intelligence.reasoning.llm.circuit_breaker import CircuitBreaker
from src.domains.intelligence.reasoning.llm.cost_tracker import CostTracker
from src.domains.intelligence.reasoning.llm.feature_flags import FeatureFlagService
from src.domains.intelligence.reasoning.llm.registry import LlmTask
from src.domains.intelligence.reasoning.llm.router import LLMRouter

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Fallback chain parsing
# ---------------------------------------------------------------------------


def _parse_fallback_chain(chain_str: str) -> list[tuple[str, str]]:
    """Parse a comma-separated 'provider:model' string into a list of tuples.

    Example:
        "gemini:gemini-3.5-flash,openai:gpt-4o-mini" →
        [("gemini", "gemini-3.5-flash"), ("openai", "gpt-4o-mini")]
    """
    pairs: list[tuple[str, str]] = []
    for entry in chain_str.split(","):
        entry = entry.strip()
        if ":" not in entry:
            continue
        provider, model = entry.split(":", 1)
        provider = provider.strip()
        model = model.strip()
        if provider and model:
            pairs.append((provider, model))
    return pairs


def _build_fallback_chains() -> dict[LlmTask, list[tuple[str, str]]]:
    """Build per-task fallback chains from application settings.

    Maps logical LlmTask values to ordered lists of (provider, model) pairs.
    Tasks without explicit chains inherit from the default chat chain.
    """
    settings = get_settings()

    chat_default_chain = _parse_fallback_chain(settings.FALLBACK_CHAT_DEFAULT)
    chat_tools_chain = _parse_fallback_chain(settings.FALLBACK_CHAT_TOOLS)

    # Most tasks use the default chat chain; tool-heavy tasks use the tools chain
    chains: dict[LlmTask, list[tuple[str, str]]] = {
        LlmTask.CHAT_DEFAULT: chat_default_chain,
        LlmTask.CHAT_TOOLS_SESSION: chat_tools_chain,
        LlmTask.CHAT_TOOLS_USAGE_FALLBACK: chat_default_chain,
        LlmTask.FACT_EXTRACTION_LITE: chat_default_chain,
        LlmTask.MINIMAL_RESPONSE: chat_default_chain,
        LlmTask.COURSE_OUTLINE: chat_tools_chain,
        LlmTask.STRUCTURED_COMPLETION: chat_tools_chain,
        LlmTask.MEMORY_JSON: chat_default_chain,
        LlmTask.EMBEDDING: [("gemini", "gemini-embedding-001")],
        LlmTask.EMAIL_PRIMARY: chat_default_chain,
        LlmTask.EMAIL_FALLBACK: chat_default_chain,
        LlmTask.VOICE_TRANSCRIPTION: chat_default_chain,
    }

    return chains


# ---------------------------------------------------------------------------
# Adapter instantiation
# ---------------------------------------------------------------------------


def _build_adapter_registry() -> dict[str, BaseProviderAdapter]:
    """Instantiate adapters for all enabled providers and their models.

    Only providers listed in ``LLM_ENABLED_PROVIDERS`` are instantiated.
    Returns a dict keyed by "provider:model" → adapter instance.
    """
    settings = get_settings()
    enabled = {p.strip().lower() for p in settings.LLM_ENABLED_PROVIDERS.split(",") if p.strip()}

    registry: dict[str, BaseProviderAdapter] = {}

    # --- Gemini adapters ---
    if "gemini" in enabled:
        try:
            from src.domains.intelligence.reasoning.llm.gemini_chat_tools import (
                GeminiChatToolsAdapter,
            )

            safety_settings: list[Any] = []
            gemini_models = [
                "gemini-3.5-flash",
                "gemini-3.1-flash-lite",
            ]
            for model_id in gemini_models:
                adapter = GeminiChatToolsAdapter(safety_settings=safety_settings, model_id=model_id)
                registry[f"gemini:{model_id}"] = adapter
            logger.info("Registered %d Gemini adapter(s)", len(gemini_models))
        except Exception as e:
            logger.warning("Failed to register Gemini adapters: %s", e)

        # Gemini embedding adapter — the only thing that makes LlmTask.EMBEDDING resolvable.
        try:
            from src.domains.intelligence.reasoning.llm.gemini_embedding import (
                GeminiEmbeddingAdapter,
            )

            embedding_adapter = GeminiEmbeddingAdapter(model_id="gemini-embedding-001")
            registry["gemini:gemini-embedding-001"] = embedding_adapter
            logger.info("Registered Gemini embedding adapter")
        except Exception as e:
            logger.warning("Failed to register Gemini embedding adapter: %s", e)

    # --- OpenAI adapters ---
    if "openai" in enabled and settings.OPENAI_API_KEY:
        try:
            from src.domains.intelligence.reasoning.llm.openai_chat_tools import (
                OpenAIChatToolsAdapter,
            )

            openai_models = ["gpt-4o-mini", "gpt-4o"]
            for model_id in openai_models:
                adapter = OpenAIChatToolsAdapter(
                    model=model_id,
                    api_key=settings.OPENAI_API_KEY,
                )
                registry[f"openai:{model_id}"] = adapter
            logger.info("Registered %d OpenAI adapter(s)", len(openai_models))
        except Exception as e:
            logger.warning("Failed to register OpenAI adapters: %s", e)

    # --- Anthropic adapters ---
    if "anthropic" in enabled and settings.ANTHROPIC_API_KEY:
        try:
            from src.domains.intelligence.reasoning.llm.anthropic_chat_tools import (
                AnthropicChatToolsAdapter,
            )

            anthropic_models = ["claude-sonnet-4-20250514", "claude-haiku-3-5"]
            for model_id in anthropic_models:
                adapter = AnthropicChatToolsAdapter(
                    model=model_id,
                    api_key=settings.ANTHROPIC_API_KEY,
                )
                registry[f"anthropic:{model_id}"] = adapter
            logger.info("Registered %d Anthropic adapter(s)", len(anthropic_models))
        except Exception as e:
            logger.warning("Failed to register Anthropic adapters: %s", e)

    if not registry:
        logger.warning("No LLM adapters registered! Enabled providers: %s", enabled)

    return registry


# ---------------------------------------------------------------------------
# Feature flag service factory (singleton)
# ---------------------------------------------------------------------------

_feature_flag_service_instance: FeatureFlagService | None = None


def _build_feature_flag_service() -> FeatureFlagService:
    """Return the shared FeatureFlagService singleton.

    Created from application settings on first call. Subsequent calls return the same instance so
    that per-user overrides set via ``set_user_override`` are visible to all consumers (router,
    model selection API, etc.).
    """
    global _feature_flag_service_instance

    if _feature_flag_service_instance is not None:
        return _feature_flag_service_instance

    settings = get_settings()

    tier_allowlists = {
        "free": settings.LLM_TIER_ALLOWLIST_FREE,
        "plus": settings.LLM_TIER_ALLOWLIST_PLUS,
    }

    _feature_flag_service_instance = FeatureFlagService(
        enabled_providers=settings.LLM_ENABLED_PROVIDERS,
        tier_allowlists=tier_allowlists,
    )
    return _feature_flag_service_instance


def get_feature_flag_service() -> FeatureFlagService:
    """Public accessor for the shared FeatureFlagService singleton.

    Use this from route handlers and other modules that need access to
    the same feature flag state as the LLM router.
    """
    return _build_feature_flag_service()


# ---------------------------------------------------------------------------
# Circuit breaker factory
# ---------------------------------------------------------------------------


def _build_circuit_breaker() -> CircuitBreaker:
    """Create a CircuitBreaker from application settings."""
    settings = get_settings()
    return CircuitBreaker(
        failure_threshold=settings.CIRCUIT_BREAKER_FAILURE_THRESHOLD,
        cooldown_seconds=settings.CIRCUIT_BREAKER_COOLDOWN_SECONDS,
        rolling_window_seconds=settings.CIRCUIT_BREAKER_ROLLING_WINDOW_SECONDS,
    )


# ---------------------------------------------------------------------------
# Cost tracker factory
# ---------------------------------------------------------------------------


def _build_cost_tracker() -> CostTracker:
    """Create a CostTracker with the default pricing table.

    The session factory is left unset so the tracker resolves it at call time; the router is built
    before the database is necessarily connected.
    """
    from src.domains.intelligence.reasoning.llm.cost_tracker import PROVIDER_PRICING

    return CostTracker(pricing_table=PROVIDER_PRICING)


# ---------------------------------------------------------------------------
# Router singleton
# ---------------------------------------------------------------------------

_llm_router_instance: LLMRouter | None = None


def get_llm_router() -> LLMRouter:
    """Return the process-wide LLMRouter instance.

    Lazily initializes all dependencies (adapters, feature flags, circuit
    breaker, cost tracker) on first call and caches the result.

    This is the primary dependency for route handlers that need multi-provider
    LLM routing.
    """
    global _llm_router_instance

    if _llm_router_instance is not None:
        return _llm_router_instance

    settings = get_settings()

    feature_flags = _build_feature_flag_service()
    circuit_breaker = _build_circuit_breaker()
    cost_tracker = _build_cost_tracker()
    adapter_registry = _build_adapter_registry()
    fallback_chains = _build_fallback_chains()

    router = LLMRouter(
        feature_flags=feature_flags,
        circuit_breaker=circuit_breaker,
        cost_tracker=cost_tracker,
        adapter_registry=adapter_registry,
        fallback_chains=fallback_chains,
        timeout_seconds=settings.LLM_ROUTER_TIMEOUT_SECONDS,
        adapter_timeout_seconds=settings.LLM_ADAPTER_TIMEOUT_SECONDS,
    )

    logger.info(
        "LLMRouter initialized with %d adapters, enabled providers: %s",
        len(adapter_registry),
        settings.LLM_ENABLED_PROVIDERS,
    )

    _log_unroutable_tasks(adapter_registry, fallback_chains, feature_flags)

    _llm_router_instance = router
    return router


def _log_unroutable_tasks(
    adapter_registry: dict[str, BaseProviderAdapter],
    fallback_chains: dict[LlmTask, list[tuple[str, str]]],
    feature_flags: FeatureFlagService,
) -> None:
    """Log, at startup, any task whose whole fallback chain is unroutable.

    **This exists because the failure it catches is invisible until a learner hits it, and then
    lies about itself.** The fallback chains and tier allowlists are configurable
    (``FALLBACK_CHAT_*``, ``LLM_TIER_ALLOWLIST_*``) while the adapter registry above hardcodes its
    model ids, so the two drift. When they do, ``_select_candidates`` filters every pair out and the
    router raises ``LLMProviderError(category="overloaded")`` — which reaches the learner as *"All AI
    services are currently busy, please try again"*. Retrying never helps, because nothing is busy:
    the chain names a model no adapter was registered for.

    That is exactly what happened on 2026-08-31. The registry had ``gemini-3.5-flash`` and
    ``gemini-3.1-flash-lite``; a stale ``.env`` pinned the chains and allowlists to
    ``gemini-2.5-flash`` and ``gemini-2.0-flash-lite`` (the latter no longer exists upstream at all),
    and OpenAI/Anthropic had no API keys, so there was nothing left to route to. Every Ask Maigie
    turn failed and reported overload.

    So this walks the same filters the router does and says which pairs are unusable and why, once,
    at build time. It only logs — a missing provider is a deployment condition, not a reason to
    refuse to boot, and raising here would take down surfaces that never call the LLM.
    """
    for task, chain in fallback_chains.items():
        reasons: list[str] = []
        routable = False

        for provider, model in chain:
            pair = f"{provider}:{model}"
            if adapter_registry.get(pair) is None:
                reasons.append(f"{pair}: no adapter registered")
                continue
            # Checked against the most restrictive tier: a pair allowed for no tier at all is a
            # configuration error, while one allowed only for `plus` is a deliberate paid gate.
            if not feature_flags.is_model_allowed(
                provider=provider,
                model=model,
                user_tier="plus",
                user_id="__startup_check__",
            ):
                reasons.append(f"{pair}: not in any tier allowlist")
                continue
            routable = True

        if not routable:
            logger.error(
                "LLM task %s is unroutable — every pair in its fallback chain was rejected (%s). "
                "Turns for this task will fail as 'overloaded' even though no provider is busy. "
                "Check FALLBACK_* and LLM_TIER_ALLOWLIST_* against the registered adapters: %s",
                getattr(task, "value", task),
                "; ".join(reasons) or "chain is empty",
                sorted(adapter_registry) or "none",
            )


def invalidate_llm_router() -> None:
    """Reset the LLM router and feature flag singletons.

    The next call to ``get_llm_router()`` or ``get_feature_flag_service()`` rebuilds from the
    current ``Settings``. Note that settings are themselves cached, so this picks up in-memory
    changes rather than a new environment; see the module docstring.
    """
    global _llm_router_instance, _feature_flag_service_instance
    _llm_router_instance = None
    _feature_flag_service_instance = None
    logger.info("LLM router and feature flag singletons invalidated (will rebuild on next use)")
