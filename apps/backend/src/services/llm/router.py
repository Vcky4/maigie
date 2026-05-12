"""LLM Router / Orchestrator for multi-provider request routing.

Selects provider-model pairs based on a pipeline of constraints (capability,
feature flags, circuit breaker state, user preference, fallback chain) and
orchestrates retries with automatic fallback on retriable failures.

Selection pipeline order:
    1. Capability support — adapter must support the required capability for the task
    2. Feature flags — FeatureFlagService must allow the pair for the user
    3. Circuit breaker — pair must not be in OPEN state
    4. Model preference — user's preferred pair is attempted first if valid
    5. Fallback chain — remaining candidates in priority order

On retriable failure: record failure with CircuitBreaker, try next candidate (max 3 attempts).
On non-retriable failure: propagate immediately without recording failure.
On success: record success with CircuitBreaker, record cost with CostTracker.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Protocol

from src.services.llm.base_adapter import BaseProviderAdapter
from src.services.llm.capabilities import TASK_CAPABILITY_MAP
from src.services.llm.circuit_breaker import CircuitBreaker, CircuitState
from src.services.llm.errors import LLMProviderError
from src.services.llm.feature_flags import FeatureFlagService
from src.services.llm_registry import LlmTask

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3


# ---------------------------------------------------------------------------
# CostTracker protocol (decouples router from concrete implementation)
# ---------------------------------------------------------------------------


class CostTrackerProtocol(Protocol):
    """Minimal interface the router expects from a cost tracker."""

    async def record(
        self,
        provider: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        user_id: str,
        user_tier: str,
    ) -> Any: ...


# ---------------------------------------------------------------------------
# LLMRouter
# ---------------------------------------------------------------------------


class LLMRouter:
    """Selects provider-model pairs and orchestrates fallback.

    Args:
        feature_flags: Service controlling provider/model availability per tier/user.
        circuit_breaker: Per-provider-model resilience component.
        cost_tracker: Records per-request cost data.
        adapter_registry: Mapping of "provider:model" keys to adapter instances.
        fallback_chains: Per-task ordered lists of (provider, model) pairs.
        timeout_seconds: Maximum time allowed for the selection + execution pipeline.
    """

    def __init__(
        self,
        feature_flags: FeatureFlagService,
        circuit_breaker: CircuitBreaker,
        cost_tracker: CostTrackerProtocol,
        adapter_registry: dict[str, BaseProviderAdapter],
        fallback_chains: dict[LlmTask, list[tuple[str, str]]],
        timeout_seconds: float = 5.0,
    ) -> None:
        self._feature_flags = feature_flags
        self._circuit_breaker = circuit_breaker
        self._cost_tracker = cost_tracker
        self._adapter_registry = adapter_registry
        self._fallback_chains = fallback_chains
        self._timeout_seconds = timeout_seconds

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def route_request(
        self,
        task: LlmTask,
        user_id: str,
        user_tier: str,
        model_preference: tuple[str, str] | None,
        *,
        history: list,
        user_message: str,
        context: dict | None = None,
        user_name: str | None = None,
        image_url: str | None = None,
        progress_callback: Any = None,
        stream_callback: Any = None,
    ) -> tuple[str, dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
        """Route a request through the selection pipeline.

        Selection order:
            1. Filter by required capability for the task
            2. Filter by FeatureFlagService (tier + user overrides)
            3. Filter by CircuitBreaker state (exclude OPEN)
            4. Prefer user's ModelPreference if available and healthy
            5. Walk FallbackChain in priority order

        On retriable failure: record with CircuitBreaker, try next.
        On non-retriable failure: propagate immediately.
        On success: record success with CircuitBreaker, record cost.

        Returns:
            Tuple of (response_text, usage_dict, actions, query_rows).

        Raises:
            LLMProviderError: With category "overloaded" when no valid pair
                exists or selection exceeds timeout_seconds.
        """
        start_time = time.monotonic()

        # Build ordered candidate list
        candidates = self._select_candidates(task, user_id, user_tier)

        # Apply model preference: move preferred pair to front if it's in candidates
        if model_preference:
            pref_provider, pref_model = model_preference
            if (pref_provider, pref_model) in candidates:
                candidates.remove((pref_provider, pref_model))
                candidates.insert(0, (pref_provider, pref_model))

        if not candidates:
            raise LLMProviderError(
                provider="router",
                model="none",
                status_code=None,
                category="overloaded",
                message="No valid provider-model pair available for this request.",
                retriable=True,
            )

        # Attempt up to MAX_ATTEMPTS candidates
        last_error: LLMProviderError | None = None
        attempts = 0

        for provider, model in candidates:
            if attempts >= MAX_ATTEMPTS:
                break

            # Check timeout before each attempt
            elapsed = time.monotonic() - start_time
            if elapsed >= self._timeout_seconds:
                raise LLMProviderError(
                    provider="router",
                    model="none",
                    status_code=None,
                    category="overloaded",
                    message=(
                        f"Router selection exceeded {self._timeout_seconds}s timeout."
                    ),
                    retriable=True,
                )

            adapter_key = f"{provider}:{model}"
            adapter = self._adapter_registry.get(adapter_key)
            if adapter is None:
                logger.warning(
                    "No adapter registered for %s, skipping", adapter_key
                )
                continue

            attempts += 1

            try:
                # Calculate remaining time for this attempt
                remaining = self._timeout_seconds - (time.monotonic() - start_time)
                if remaining <= 0:
                    raise LLMProviderError(
                        provider="router",
                        model="none",
                        status_code=None,
                        category="overloaded",
                        message=(
                            f"Router selection exceeded {self._timeout_seconds}s timeout."
                        ),
                        retriable=True,
                    )

                # Execute the request with timeout
                result = await asyncio.wait_for(
                    adapter.get_chat_response_with_tools(
                        history=history,
                        user_message=user_message,
                        context=context,
                        user_id=user_id,
                        user_name=user_name,
                        image_url=image_url,
                        progress_callback=progress_callback,
                        stream_callback=stream_callback,
                    ),
                    timeout=remaining,
                )

                # Success: record with circuit breaker and cost tracker
                self._circuit_breaker.record_success(provider, model)

                # Extract token usage for cost tracking
                response_text, usage_dict, actions, queries = result
                input_tokens = usage_dict.get("input_tokens", 0) if usage_dict else 0
                output_tokens = usage_dict.get("output_tokens", 0) if usage_dict else 0

                try:
                    await self._cost_tracker.record(
                        provider=provider,
                        model=model,
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                        user_id=user_id,
                        user_tier=user_tier,
                    )
                except Exception:
                    # Cost tracking failure should not break the request
                    logger.exception(
                        "Failed to record cost for %s:%s", provider, model
                    )

                return result

            except asyncio.TimeoutError:
                # Timeout is treated as a retriable failure
                self._circuit_breaker.record_failure(provider, model)
                last_error = LLMProviderError(
                    provider=provider,
                    model=model,
                    status_code=None,
                    category="overloaded",
                    message=(
                        f"Request to {provider}:{model} timed out."
                    ),
                    retriable=True,
                )
                logger.warning(
                    "Timeout for %s:%s, attempting next candidate",
                    provider,
                    model,
                )

            except LLMProviderError as e:
                if e.retriable:
                    # Retriable: record failure and try next candidate
                    self._circuit_breaker.record_failure(provider, model)
                    last_error = e
                    logger.warning(
                        "Retriable error from %s:%s (category=%s), "
                        "attempting next candidate",
                        provider,
                        model,
                        e.category,
                    )
                else:
                    # Non-retriable: propagate immediately without recording
                    raise

        # All attempts exhausted
        if last_error:
            raise LLMProviderError(
                provider="router",
                model="none",
                status_code=None,
                category="overloaded",
                message=(
                    f"All provider-model pairs exhausted after {attempts} attempts. "
                    f"Last error: {last_error.message}"
                ),
                retriable=True,
            )

        # Should not reach here, but defensive
        raise LLMProviderError(
            provider="router",
            model="none",
            status_code=None,
            category="overloaded",
            message="No valid provider-model pair available for this request.",
            retriable=True,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_required_capability(self, task: LlmTask) -> type:
        """Map an LlmTask to the required capability protocol.

        Args:
            task: The logical LLM task being requested.

        Returns:
            The capability protocol class required for this task.

        Raises:
            LLMProviderError: If the task has no capability mapping.
        """
        capability = TASK_CAPABILITY_MAP.get(task)
        if capability is None:
            raise LLMProviderError(
                provider="router",
                model="none",
                status_code=None,
                category="invalid_request",
                message=f"No capability mapping found for task: {task}",
                retriable=False,
            )
        return capability

    def _select_candidates(
        self, task: LlmTask, user_id: str, user_tier: str
    ) -> list[tuple[str, str]]:
        """Return an ordered list of valid (provider, model) candidates.

        Applies the selection pipeline filters in order:
            1. Capability support — adapter must support the required capability
            2. Feature flags — pair must be allowed for the user's tier
            3. Circuit breaker — pair must not be in OPEN state

        The ordering follows the fallback chain priority for the given task.

        Args:
            task: The logical LLM task being requested.
            user_id: The requesting user's identifier.
            user_tier: The user's subscription tier.

        Returns:
            Ordered list of (provider, model) tuples that pass all filters.
        """
        required_capability = self._get_required_capability(task)

        # Get the fallback chain for this task (or empty list)
        chain = self._fallback_chains.get(task, [])

        candidates: list[tuple[str, str]] = []

        for provider, model in chain:
            adapter_key = f"{provider}:{model}"
            adapter = self._adapter_registry.get(adapter_key)

            # Filter 1: Adapter must exist and support the required capability
            if adapter is None:
                continue
            if required_capability not in adapter.supported_capabilities():
                continue

            # Filter 2: Feature flags must allow this pair for the user
            if not self._feature_flags.is_model_allowed(
                provider=provider,
                model=model,
                user_tier=user_tier,
                user_id=user_id,
            ):
                continue

            # Filter 3: Circuit breaker must not be OPEN
            if not self._circuit_breaker.should_allow(provider, model):
                continue

            candidates.append((provider, model))

        return candidates
