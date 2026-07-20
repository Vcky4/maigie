"""Stub — implementation pending migration from services/llm/adapter_registry."""

from typing import Any


def get_feature_flag_service():
    """Get the feature flag service instance."""
    from src.domains.intelligence.reasoning.llm.feature_flags import FeatureFlagService

    return FeatureFlagService()


def get_llm_router():
    """Get the LLM router instance."""
    return None  # TODO: migrate implementation
