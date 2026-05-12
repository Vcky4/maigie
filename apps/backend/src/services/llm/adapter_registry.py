"""Adapter registry and dependency injection for multi-provider LLM routing.

Provides factory functions to instantiate provider adapters based on application
settings, and a ``get_llm_router()`` dependency that wires the LLMRouter with
all required services (FeatureFlagService, CircuitBreaker, CostTracker, adapters).

The registry is lazily initialized on first access and cached for the process
lifetime. Only providers listed in ``LLM_ENABLED_PROVIDERS`` are instantiated.
"""

from __future__ import annotations

import logging
from functools import lru_cache
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
        "gemini:gemini-2.5-flash,openai:gpt-4o-mini" →
        [("gemini", "gemini-2.5-flash"), ("openai", "gpt-4o-mini")]
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
    """Build per-task fallback chains from settings.

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
                "gemini-2.5-flash",
                "gemini-2.0-flash",
                "gemini-2.0-flash-lite",
                "gemini-3-flash-preview",
            ]
            for model_id in gemini_models:
                adapter = GeminiChatToolsAdapter(
                    safety_settings=safety_settings, model_id=model_id
                )
                registry[f"gemini:{model_id}"] = adapter
            logger.info("Registered %d Gemini adapter(s)", len(gemini_models))
        except Exception as e:
            logger.warning("Failed to register Gemini adapters: %s", e)

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
        logger.warning(
            "No LLM adapters registered! Enabled providers: %s", enabled
        )

    return registry


# ---------------------------------------------------------------------------
# Feature flag service factory (singleton)
# ---------------------------------------------------------------------------

_feature_flag_service_instance: FeatureFlagService | None = None


def _build_feature_flag_service() -> FeatureFlagService:
    """Return the shared FeatureFlagService singleton.

    Creates the instance from application settings on first call. Subsequent
    calls return the same instance so that per-user overrides loaded via
    reload() are visible to all consumers (router, model selection API, etc.).
    """
    global _feature_flag_service_instance
    if _feature_flag_service_instance is not None:
        return _feature_flag_service_instance

    settings = get_settings()

    tier_allowlists = {
        "free": settings.LLM_TIER_ALLOWLIST_FREE,
        "plus": settings.LLM_TIER_ALLOWLIST_PLUS,
        "circle": settings.LLM_TIER_ALLOWLIST_CIRCLE,
        "squad": settings.LLM_TIER_ALLOWLIST_SQUAD,
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


@lru_cache(maxsize=1)
def get_llm_router() -> LLMRouter:
    """Return the process-wide LLMRouter instance.

    Lazily initializes all dependencies (adapters, feature flags, circuit
    breaker, cost tracker) on first call and caches the result.

    This is the primary dependency for route handlers that need multi-provider
    LLM routing.
    """
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
        timeout_seconds=5.0,
    )

    logger.info(
        "LLMRouter initialized with %d adapters, enabled providers: %s",
        len(adapter_registry),
        settings.LLM_ENABLED_PROVIDERS,
    )

    return router
