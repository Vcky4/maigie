"""Access points for the LLM routing layer.

``get_llm_router`` returned ``None``, and its callers immediately call
``route_request`` on the result, so the chat path failed with
``AttributeError: 'NoneType' object has no attribute 'route_request'`` well away from
the actual cause. It now raises a message that names the missing subsystem.

The multi-provider routing layer is the largest piece of the migration still
outstanding. The pre-migration ``src/services/llm`` package held 23 modules, including
``router.py`` (route_request, provider selection, fallback), ``circuit_breaker.py``,
``feature_flags.py``, ``stream_normalizer.py``, ``tool_normalizer.py``,
``cost_tracker.py`` and the Gemini, OpenAI and Anthropic tool-calling adapters. Nine of
the test files that are currently skipped at collection are tests for those modules.
"""

from src.shared.infrastructure.unmigrated import raise_unmigrated

_LLM_ORIGIN = 'git show "4953972^:apps/backend/src/services/llm/router.py"'


def get_feature_flag_service():
    """Get the feature flag service instance."""
    from src.domains.intelligence.reasoning.llm.feature_flags import FeatureFlagService

    return FeatureFlagService()


def get_llm_router():
    """Get the LLM router.

    Raises:
        UnmigratedSubsystemError: always, until the routing layer is migrated.
    """
    raise_unmigrated(
        subsystem="The LLM routing layer (adapter registry and provider router)",
        origin=_LLM_ORIGIN,
        consequence=(
            "Chat request routing, provider fallback and multi-provider tool calling "
            "are unavailable, so no chat response can be produced."
        ),
    )
