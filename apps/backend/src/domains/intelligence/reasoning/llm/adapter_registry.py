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
- **Only the Gemini adapter exists.** ``openai_chat_tools``, ``anthropic_chat_tools`` and
  ``gemini_embedding`` are step 6 of the migration. Each block stays wrapped in ``try/except`` so a
  missing adapter logs and is skipped instead of taking the whole registry down — and the router
  already treats an unregistered ``provider:model`` as a candidate to skip. So OpenAI being listed
  in ``LLM_ENABLED_PROVIDERS`` and in the fallback chains is currently inert rather than broken.
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

    # --- Gemini embedding, OpenAI and Anthropic adapters ---
    #
    # Not registered: `gemini_embedding`, `openai_chat_tools` and `anthropic_chat_tools` are step 6
    # of the migration and do not exist yet. Recover them with
    # `git show "4953972^:apps/backend/src/services/llm/<name>.py"`.
    #
    # The original wrote each block as a function-local import inside `try/except Exception`, which
    # degrades correctly but is the pattern `tests/test_local_imports.py` exists to forbid — a
    # function-local import of a module that is not there is invisible until the line runs, and this
    # migration has already turned up two of those (`process_chat_message` and
    # `send_message_with_context`, both absent, both behind a broad `except`). Blocks that cannot
    # work are therefore left out rather than left in, and step 6 restores them alongside the modules.
    #
    # Behaviour is unchanged by their absence: nothing was ever registered under these keys, and
    # `LLMRouter._select_candidates` skips a `provider:model` with no adapter. So `openai` appearing
    # in `LLM_ENABLED_PROVIDERS` and in both fallback chains is inert today, not broken.

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

    _llm_router_instance = router
    return router


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
