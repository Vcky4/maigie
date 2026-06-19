"""Adapter registry and dependency injection for multi-provider LLM routing.

Provides factory functions to instantiate provider adapters based on application
settings, and a ``get_llm_router()`` dependency that wires the LLMRouter with
all required services (FeatureFlagService, CircuitBreaker, CostTracker, adapters).

The registry is lazily initialized on first access and cached for the process
lifetime. Only providers listed in ``LLM_ENABLED_PROVIDERS`` are instantiated.
"""

from __future__ import annotations

import logging
from typing import Any

from src.config import get_settings
from src.services.llm.base_adapter import BaseProviderAdapter
from src.services.llm.circuit_breaker import CircuitBreaker
from src.services.llm.cost_tracker import CostTracker
from src.services.llm.feature_flags import FeatureFlagService
from src.services.llm.router import LLMRouter
from src.services.llm_registry import LlmTask

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
    """Build per-task fallback chains from DB config (with env/settings fallback).

    Maps logical LlmTask values to ordered lists of (provider, model) pairs.
    Tasks without explicit chains inherit from the default chat chain.
    """
    settings = get_settings()

    # Read from system_config_service cache (populated at startup by seed_llm_defaults).
    from src.services.system_config_service import (
        LLM_FALLBACK_CHAT_DEFAULT as _KEY_DEFAULT,
        LLM_FALLBACK_CHAT_TOOLS as _KEY_TOOLS,
        _cache,
        _CACHE_TTL,
    )
    import time as _time

    def _read_cached(key: str, fallback: str) -> str:
        now = _time.time()
        if key in _cache:
            val, ts = _cache[key]
            if now - ts < _CACHE_TTL and val is not None:
                return val
        return fallback

    chat_default_chain = _parse_fallback_chain(
        _read_cached(_KEY_DEFAULT, settings.FALLBACK_CHAT_DEFAULT)
    )
    chat_tools_chain = _parse_fallback_chain(_read_cached(_KEY_TOOLS, settings.FALLBACK_CHAT_TOOLS))

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
            from src.services.llm.gemini_chat_tools import GeminiChatToolsAdapter

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

        # Gemini embedding adapter
        try:
            from src.services.llm.gemini_embedding import GeminiEmbeddingAdapter

            embedding_adapter = GeminiEmbeddingAdapter(model_id="gemini-embedding-001")
            registry["gemini:gemini-embedding-001"] = embedding_adapter
            logger.info("Registered Gemini embedding adapter")
        except Exception as e:
            logger.warning("Failed to register Gemini embedding adapter: %s", e)

    # --- OpenAI adapters ---
    if "openai" in enabled and settings.OPENAI_API_KEY:
        try:
            from src.services.llm.openai_chat_tools import OpenAIChatToolsAdapter

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
            from src.services.llm.anthropic_chat_tools import AnthropicChatToolsAdapter

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

    Creates the instance from DB-stored config (via system_config_service cache)
    on first call, falling back to application settings. Subsequent calls
    return the same instance so that per-user overrides loaded via reload()
    are visible to all consumers (router, model selection API, etc.).
    """
    global _feature_flag_service_instance
    if _feature_flag_service_instance is not None:
        return _feature_flag_service_instance

    settings = get_settings()

    # Read from system_config_service cache (populated at startup by seed_llm_defaults).
    # Falls back to settings (env vars / code defaults) if cache is empty.
    from src.services.system_config_service import (
        LLM_ENABLED_PROVIDERS as _KEY_PROVIDERS,
        LLM_TIER_ALLOWLIST_FREE as _KEY_FREE,
        LLM_TIER_ALLOWLIST_PLUS as _KEY_PLUS,
        _cache,
        _CACHE_TTL,
    )
    import time as _time

    def _read_cached(key: str, fallback: str) -> str:
        """Read from in-memory cache synchronously, fallback to settings."""
        now = _time.time()
        if key in _cache:
            val, ts = _cache[key]
            if now - ts < _CACHE_TTL and val is not None:
                return val
        return fallback

    tier_allowlists = {
        "free": _read_cached(_KEY_FREE, settings.LLM_TIER_ALLOWLIST_FREE),
        "plus": _read_cached(_KEY_PLUS, settings.LLM_TIER_ALLOWLIST_PLUS),
    }

    _feature_flag_service_instance = FeatureFlagService(
        enabled_providers=_read_cached(_KEY_PROVIDERS, settings.LLM_ENABLED_PROVIDERS),
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
    """Create a CostTracker instance with the default pricing table.

    The DB client is injected lazily — the CostTracker will use the
    application's shared Prisma client from core.database.
    """
    from src.core.database import db as prisma_db
    from src.services.llm.cost_tracker import PROVIDER_PRICING

    return CostTracker(pricing_table=PROVIDER_PRICING, db=prisma_db)


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

    Called when admin updates LLM configuration via the dashboard.
    The next call to ``get_llm_router()`` or ``get_feature_flag_service()``
    will rebuild from the latest DB/env configuration.
    """
    global _llm_router_instance, _feature_flag_service_instance
    _llm_router_instance = None
    _feature_flag_service_instance = None
    logger.info("LLM router and feature flag singletons invalidated (will rebuild on next use)")
