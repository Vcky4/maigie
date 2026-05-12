"""Tests for the FeatureFlagService."""

from __future__ import annotations

from typing import Any

import pytest

from src.services.llm.feature_flags import (
    FeatureFlagService,
    FeatureFlagStore,
    TIER_TO_ALLOWLIST_KEY,
)


# ---------------------------------------------------------------------------
# Helpers / Fixtures
# ---------------------------------------------------------------------------


class InMemoryFlagStore:
    """Simple in-memory implementation of FeatureFlagStore for testing."""

    def __init__(self, overrides: dict[tuple[str, str], bool] | None = None) -> None:
        self._overrides = overrides or {}

    async def get_user_override(self, user_id: str, provider: str) -> bool | None:
        return self._overrides.get((user_id, provider.lower()))

    async def get_all_flags(self) -> list[dict[str, Any]]:
        flags = []
        for (user_id, provider), enabled in self._overrides.items():
            flags.append(
                {
                    "provider": provider,
                    "model": None,
                    "tier": None,
                    "userId": user_id,
                    "enabled": enabled,
                }
            )
        return flags


# ---------------------------------------------------------------------------
# Tests: is_provider_enabled
# ---------------------------------------------------------------------------


class TestIsProviderEnabled:
    def test_enabled_provider(self):
        svc = FeatureFlagService(enabled_providers="gemini,openai")
        assert svc.is_provider_enabled("gemini") is True
        assert svc.is_provider_enabled("openai") is True

    def test_disabled_provider(self):
        svc = FeatureFlagService(enabled_providers="gemini")
        assert svc.is_provider_enabled("openai") is False
        assert svc.is_provider_enabled("anthropic") is False

    def test_case_insensitive(self):
        svc = FeatureFlagService(enabled_providers="Gemini,OPENAI")
        assert svc.is_provider_enabled("gemini") is True
        assert svc.is_provider_enabled("OpenAI") is True

    def test_empty_string(self):
        svc = FeatureFlagService(enabled_providers="")
        assert svc.is_provider_enabled("gemini") is False

    def test_whitespace_handling(self):
        svc = FeatureFlagService(enabled_providers=" gemini , openai ")
        assert svc.is_provider_enabled("gemini") is True
        assert svc.is_provider_enabled("openai") is True


# ---------------------------------------------------------------------------
# Tests: get_allowed_models
# ---------------------------------------------------------------------------


class TestGetAllowedModels:
    def test_returns_models_for_provider_and_tier(self):
        svc = FeatureFlagService(
            enabled_providers="gemini,openai",
            tier_allowlists={
                "free": "gemini:gemini-2.5-flash,gemini:gemini-2.0-flash-lite",
                "plus": "gemini:gemini-2.5-flash,openai:gpt-4o-mini",
            },
        )
        models = svc.get_allowed_models("gemini", "free")
        assert models == ["gemini-2.5-flash", "gemini-2.0-flash-lite"]

    def test_filters_by_provider(self):
        svc = FeatureFlagService(
            enabled_providers="gemini,openai",
            tier_allowlists={
                "plus": "gemini:gemini-2.5-flash,openai:gpt-4o-mini,anthropic:claude-sonnet",
            },
        )
        assert svc.get_allowed_models("openai", "plus") == ["gpt-4o-mini"]

    def test_unknown_tier_returns_empty(self):
        svc = FeatureFlagService(
            enabled_providers="gemini",
            tier_allowlists={"free": "gemini:gemini-2.5-flash"},
        )
        assert svc.get_allowed_models("gemini", "enterprise") == []

    def test_unknown_provider_returns_empty(self):
        svc = FeatureFlagService(
            enabled_providers="gemini",
            tier_allowlists={"free": "gemini:gemini-2.5-flash"},
        )
        assert svc.get_allowed_models("openai", "free") == []

    def test_case_insensitive_tier(self):
        svc = FeatureFlagService(
            enabled_providers="gemini",
            tier_allowlists={"FREE": "gemini:gemini-2.5-flash"},
        )
        assert svc.get_allowed_models("gemini", "free") == ["gemini-2.5-flash"]


# ---------------------------------------------------------------------------
# Tests: has_user_override
# ---------------------------------------------------------------------------


class TestHasUserOverride:
    def test_no_override_returns_none(self):
        svc = FeatureFlagService(enabled_providers="gemini")
        assert svc.has_user_override("user-1", "gemini") is None

    @pytest.mark.asyncio
    async def test_override_loaded_from_store(self):
        store = InMemoryFlagStore(overrides={("user-1", "openai"): True})
        svc = FeatureFlagService(enabled_providers="gemini,openai", store=store)
        await svc.reload()
        assert svc.has_user_override("user-1", "openai") is True

    @pytest.mark.asyncio
    async def test_revoked_override(self):
        store = InMemoryFlagStore(overrides={("user-2", "anthropic"): False})
        svc = FeatureFlagService(enabled_providers="gemini", store=store)
        await svc.reload()
        assert svc.has_user_override("user-2", "anthropic") is False


# ---------------------------------------------------------------------------
# Tests: is_model_allowed
# ---------------------------------------------------------------------------


class TestIsModelAllowed:
    def test_disabled_provider_always_false(self):
        svc = FeatureFlagService(
            enabled_providers="gemini",
            tier_allowlists={"free": "openai:gpt-4o-mini"},
        )
        # openai not enabled globally
        assert svc.is_model_allowed("openai", "gpt-4o-mini", "free", "user-1") is False

    def test_model_in_tier_allowlist(self):
        svc = FeatureFlagService(
            enabled_providers="gemini,openai",
            tier_allowlists={"plus": "openai:gpt-4o-mini,gemini:gemini-2.5-flash"},
        )
        assert svc.is_model_allowed("openai", "gpt-4o-mini", "plus", "user-1") is True

    def test_model_not_in_tier_allowlist(self):
        svc = FeatureFlagService(
            enabled_providers="gemini,openai",
            tier_allowlists={"free": "gemini:gemini-2.5-flash"},
        )
        assert svc.is_model_allowed("openai", "gpt-4o", "free", "user-1") is False

    @pytest.mark.asyncio
    async def test_user_override_grants_access(self):
        store = InMemoryFlagStore(overrides={("user-1", "openai"): True})
        svc = FeatureFlagService(
            enabled_providers="gemini,openai",
            tier_allowlists={"free": "gemini:gemini-2.5-flash"},
            store=store,
        )
        await svc.reload()
        # User has override for openai, even though model not in free tier
        assert svc.is_model_allowed("openai", "gpt-4o", "free", "user-1") is True

    @pytest.mark.asyncio
    async def test_user_override_does_not_bypass_global_disable(self):
        store = InMemoryFlagStore(overrides={("user-1", "anthropic"): True})
        svc = FeatureFlagService(
            enabled_providers="gemini",  # anthropic NOT enabled
            tier_allowlists={},
            store=store,
        )
        await svc.reload()
        # Provider not globally enabled, override doesn't help
        assert svc.is_model_allowed("anthropic", "claude-sonnet", "free", "user-1") is False


# ---------------------------------------------------------------------------
# Tests: reload
# ---------------------------------------------------------------------------


class TestReload:
    @pytest.mark.asyncio
    async def test_reload_without_store_is_noop(self):
        svc = FeatureFlagService(enabled_providers="gemini")
        # Should not raise
        await svc.reload()

    @pytest.mark.asyncio
    async def test_reload_populates_overrides(self):
        store = InMemoryFlagStore(
            overrides={
                ("user-a", "openai"): True,
                ("user-b", "gemini"): False,
            }
        )
        svc = FeatureFlagService(enabled_providers="gemini,openai", store=store)

        # Before reload, no overrides
        assert svc.has_user_override("user-a", "openai") is None

        await svc.reload()

        assert svc.has_user_override("user-a", "openai") is True
        assert svc.has_user_override("user-b", "gemini") is False

    @pytest.mark.asyncio
    async def test_reload_handles_store_error_gracefully(self):
        class FailingStore:
            async def get_user_override(self, user_id: str, provider: str) -> bool | None:
                return None

            async def get_all_flags(self) -> list[dict[str, Any]]:
                raise RuntimeError("DB connection failed")

        svc = FeatureFlagService(enabled_providers="gemini", store=FailingStore())
        # Should not raise, just log the error
        await svc.reload()


# ---------------------------------------------------------------------------
# Tests: Parsing edge cases
# ---------------------------------------------------------------------------


class TestParsing:
    def test_invalid_allowlist_entry_without_colon(self):
        """Entries without ':' separator are skipped with a warning."""
        svc = FeatureFlagService(
            enabled_providers="gemini",
            tier_allowlists={"free": "gemini:gemini-2.5-flash,invalid-entry,openai:gpt-4o"},
        )
        models = svc.get_allowed_models("gemini", "free")
        assert models == ["gemini-2.5-flash"]

    def test_empty_allowlist_value(self):
        svc = FeatureFlagService(
            enabled_providers="gemini",
            tier_allowlists={"free": ""},
        )
        assert svc.get_allowed_models("gemini", "free") == []

    def test_whitespace_only_providers(self):
        svc = FeatureFlagService(enabled_providers="  ,  , ")
        assert svc.is_provider_enabled("gemini") is False


# ---------------------------------------------------------------------------
# Tests: Tier enforcement (Requirements 8.1 - 8.8)
# ---------------------------------------------------------------------------


class TestTierEnforcement:
    """Verify tier-based access enforcement per Requirements 8.1-8.8."""

    @pytest.fixture
    def full_service(self):
        """Service with all providers enabled and realistic tier allowlists."""
        return FeatureFlagService(
            enabled_providers="gemini,openai,anthropic",
            tier_allowlists={
                "free": "gemini:gemini-2.5-flash,gemini:gemini-2.0-flash-lite",
                "plus": "gemini:gemini-2.5-flash,gemini:gemini-2.0-flash-lite,openai:gpt-4o-mini",
                "circle": "gemini:gemini-2.5-flash,openai:gpt-4o,anthropic:claude-sonnet-4-20250514",
                "squad": "gemini:gemini-2.5-flash,openai:gpt-4o,anthropic:claude-sonnet-4-20250514",
            },
        )

    # --- Requirement 8.1: FREE tier only Gemini flash models ---

    def test_free_tier_allows_gemini_flash(self, full_service):
        """Req 8.1: FREE tier allows gemini-2.5-flash."""
        assert full_service.is_model_allowed(
            "gemini", "gemini-2.5-flash", "free", "user-1"
        ) is True

    def test_free_tier_allows_gemini_flash_lite(self, full_service):
        """Req 8.1: FREE tier allows gemini-2.0-flash-lite."""
        assert full_service.is_model_allowed(
            "gemini", "gemini-2.0-flash-lite", "free", "user-1"
        ) is True

    def test_free_tier_denies_openai(self, full_service):
        """Req 8.1: FREE tier denies OpenAI models."""
        assert full_service.is_model_allowed(
            "openai", "gpt-4o-mini", "free", "user-1"
        ) is False

    def test_free_tier_denies_anthropic(self, full_service):
        """Req 8.1: FREE tier denies Anthropic models."""
        assert full_service.is_model_allowed(
            "anthropic", "claude-sonnet-4-20250514", "free", "user-1"
        ) is False

    # --- Requirement 8.2: PREMIUM allows Gemini + OpenAI ---

    def test_premium_monthly_allows_gemini(self, full_service):
        """Req 8.2: PREMIUM_MONTHLY allows Gemini models."""
        assert full_service.is_model_allowed(
            "gemini", "gemini-2.5-flash", "premium_monthly", "user-1"
        ) is True

    def test_premium_yearly_allows_gemini(self, full_service):
        """Req 8.2: PREMIUM_YEARLY allows Gemini models."""
        assert full_service.is_model_allowed(
            "gemini", "gemini-2.5-flash", "premium_yearly", "user-1"
        ) is True

    def test_premium_allows_openai(self, full_service):
        """Req 8.2: PREMIUM allows OpenAI models."""
        assert full_service.is_model_allowed(
            "openai", "gpt-4o-mini", "premium_monthly", "user-1"
        ) is True

    def test_premium_denies_anthropic(self, full_service):
        """Req 8.2: PREMIUM denies Anthropic models."""
        assert full_service.is_model_allowed(
            "anthropic", "claude-sonnet-4-20250514", "premium_monthly", "user-1"
        ) is False

    # --- Requirement 8.3: STUDY_CIRCLE/SQUAD allows all providers ---

    def test_study_circle_allows_gemini(self, full_service):
        """Req 8.3: STUDY_CIRCLE allows Gemini."""
        assert full_service.is_model_allowed(
            "gemini", "gemini-2.5-flash", "study_circle_monthly", "user-1"
        ) is True

    def test_study_circle_allows_openai(self, full_service):
        """Req 8.3: STUDY_CIRCLE allows OpenAI."""
        assert full_service.is_model_allowed(
            "openai", "gpt-4o", "study_circle_monthly", "user-1"
        ) is True

    def test_study_circle_allows_anthropic(self, full_service):
        """Req 8.3: STUDY_CIRCLE allows Anthropic."""
        assert full_service.is_model_allowed(
            "anthropic", "claude-sonnet-4-20250514", "study_circle_monthly", "user-1"
        ) is True

    def test_squad_allows_all_providers(self, full_service):
        """Req 8.3: SQUAD allows all providers."""
        assert full_service.is_model_allowed(
            "gemini", "gemini-2.5-flash", "squad_monthly", "user-1"
        ) is True
        assert full_service.is_model_allowed(
            "openai", "gpt-4o", "squad_monthly", "user-1"
        ) is True
        assert full_service.is_model_allowed(
            "anthropic", "claude-sonnet-4-20250514", "squad_monthly", "user-1"
        ) is True

    # --- Requirement 8.4: Global disable overrides everything ---

    def test_global_disable_overrides_tier_allowlist(self):
        """Req 8.4: Globally disabled provider excluded for all tiers."""
        svc = FeatureFlagService(
            enabled_providers="gemini",  # openai NOT enabled
            tier_allowlists={
                "plus": "gemini:gemini-2.5-flash,openai:gpt-4o-mini",
            },
        )
        assert svc.is_model_allowed("openai", "gpt-4o-mini", "plus", "user-1") is False

    @pytest.mark.asyncio
    async def test_global_disable_overrides_user_grant(self):
        """Req 8.4: Globally disabled provider excluded even with user grant."""
        store = InMemoryFlagStore(overrides={("user-1", "anthropic"): True})
        svc = FeatureFlagService(
            enabled_providers="gemini,openai",  # anthropic NOT enabled
            tier_allowlists={
                "circle": "gemini:gemini-2.5-flash,openai:gpt-4o,anthropic:claude-sonnet-4-20250514",
            },
            store=store,
        )
        await svc.reload()
        assert svc.is_model_allowed(
            "anthropic", "claude-sonnet-4-20250514", "circle", "user-1"
        ) is False

    # --- Requirement 8.5: Per-user override grants access ---

    @pytest.mark.asyncio
    async def test_user_override_grants_access_beyond_tier(self):
        """Req 8.5: Per-user grant allows access even if tier excludes it."""
        store = InMemoryFlagStore(overrides={("user-1", "openai"): True})
        svc = FeatureFlagService(
            enabled_providers="gemini,openai,anthropic",
            tier_allowlists={
                "free": "gemini:gemini-2.5-flash,gemini:gemini-2.0-flash-lite",
            },
            store=store,
        )
        await svc.reload()
        # FREE tier doesn't include openai, but user has override
        assert svc.is_model_allowed("openai", "gpt-4o-mini", "free", "user-1") is True

    # --- Requirement 8.6: Per-user override revokes access ---

    @pytest.mark.asyncio
    async def test_user_override_revokes_access_despite_tier(self):
        """Req 8.6: Per-user revoke denies access even if tier includes it."""
        store = InMemoryFlagStore(overrides={("user-1", "openai"): False})
        svc = FeatureFlagService(
            enabled_providers="gemini,openai,anthropic",
            tier_allowlists={
                "plus": "gemini:gemini-2.5-flash,gemini:gemini-2.0-flash-lite,openai:gpt-4o-mini",
            },
            store=store,
        )
        await svc.reload()
        # PLUS tier includes openai, but user has revoke override
        assert svc.is_model_allowed("openai", "gpt-4o-mini", "plus", "user-1") is False

    # --- Requirement 8.7: Disallowed model rejected ---

    def test_disallowed_model_rejected(self, full_service):
        """Req 8.7: Model not in tier allowlist is rejected."""
        # gpt-4o is not in the plus tier allowlist
        assert full_service.is_model_allowed(
            "openai", "gpt-4o", "plus", "user-1"
        ) is False

    # --- Requirement 8.8: Precedence order ---

    @pytest.mark.asyncio
    async def test_precedence_global_beats_override(self):
        """Req 8.8: Global disable has highest precedence over user override."""
        store = InMemoryFlagStore(overrides={("user-1", "anthropic"): True})
        svc = FeatureFlagService(
            enabled_providers="gemini",  # anthropic disabled globally
            tier_allowlists={
                "squad": "gemini:gemini-2.5-flash,anthropic:claude-sonnet-4-20250514",
            },
            store=store,
        )
        await svc.reload()
        # Global disable wins over user grant
        assert svc.is_model_allowed(
            "anthropic", "claude-sonnet-4-20250514", "squad", "user-1"
        ) is False

    @pytest.mark.asyncio
    async def test_precedence_override_beats_tier(self):
        """Req 8.8: Per-user override has higher precedence than tier allowlist."""
        store = InMemoryFlagStore(overrides={("user-1", "openai"): False})
        svc = FeatureFlagService(
            enabled_providers="gemini,openai",
            tier_allowlists={
                "plus": "gemini:gemini-2.5-flash,openai:gpt-4o-mini",
            },
            store=store,
        )
        await svc.reload()
        # User revoke wins over tier allowlist
        assert svc.is_model_allowed("openai", "gpt-4o-mini", "plus", "user-1") is False

    @pytest.mark.asyncio
    async def test_precedence_tier_used_when_no_override(self):
        """Req 8.8: Tier allowlist used when no override exists."""
        svc = FeatureFlagService(
            enabled_providers="gemini,openai",
            tier_allowlists={
                "plus": "gemini:gemini-2.5-flash,openai:gpt-4o-mini",
            },
        )
        # No override, falls through to tier allowlist
        assert svc.is_model_allowed("openai", "gpt-4o-mini", "plus", "user-1") is True
        assert svc.is_model_allowed("openai", "gpt-4o", "plus", "user-1") is False


# ---------------------------------------------------------------------------
# Tests: set_user_override and remove_user_override
# ---------------------------------------------------------------------------


class TestUserOverrideManagement:
    """Tests for programmatic per-user override management."""

    def test_set_user_override_grant(self):
        """Setting a grant override allows access."""
        svc = FeatureFlagService(
            enabled_providers="gemini,openai",
            tier_allowlists={"free": "gemini:gemini-2.5-flash"},
        )
        svc.set_user_override("user-1", "openai", grant=True)
        assert svc.has_user_override("user-1", "openai") is True
        assert svc.is_model_allowed("openai", "gpt-4o-mini", "free", "user-1") is True

    def test_set_user_override_revoke(self):
        """Setting a revoke override denies access even if tier allows."""
        svc = FeatureFlagService(
            enabled_providers="gemini,openai",
            tier_allowlists={"plus": "gemini:gemini-2.5-flash,openai:gpt-4o-mini"},
        )
        svc.set_user_override("user-1", "openai", grant=False)
        assert svc.has_user_override("user-1", "openai") is False
        assert svc.is_model_allowed("openai", "gpt-4o-mini", "plus", "user-1") is False

    def test_remove_user_override(self):
        """Removing an override falls back to tier allowlist."""
        svc = FeatureFlagService(
            enabled_providers="gemini,openai",
            tier_allowlists={"plus": "gemini:gemini-2.5-flash,openai:gpt-4o-mini"},
        )
        # First revoke access
        svc.set_user_override("user-1", "openai", grant=False)
        assert svc.is_model_allowed("openai", "gpt-4o-mini", "plus", "user-1") is False

        # Then remove the override — should fall back to tier allowlist
        svc.remove_user_override("user-1", "openai")
        assert svc.has_user_override("user-1", "openai") is None
        assert svc.is_model_allowed("openai", "gpt-4o-mini", "plus", "user-1") is True

    def test_remove_nonexistent_override_is_noop(self):
        """Removing a non-existent override does not raise."""
        svc = FeatureFlagService(enabled_providers="gemini")
        svc.remove_user_override("user-1", "openai")  # Should not raise

    def test_revoke_override_does_not_bypass_global_disable(self):
        """A grant override cannot bypass a globally disabled provider."""
        svc = FeatureFlagService(
            enabled_providers="gemini",  # openai NOT enabled
            tier_allowlists={"free": "gemini:gemini-2.5-flash"},
        )
        svc.set_user_override("user-1", "openai", grant=True)
        # Global disable still wins
        assert svc.is_model_allowed("openai", "gpt-4o-mini", "free", "user-1") is False


# ---------------------------------------------------------------------------
# Tests: get_available_models_for_user
# ---------------------------------------------------------------------------


class TestGetAvailableModelsForUser:
    """Tests for the get_available_models_for_user method."""

    def test_free_tier_returns_only_gemini_flash(self):
        svc = FeatureFlagService(
            enabled_providers="gemini,openai,anthropic",
            tier_allowlists={
                "free": "gemini:gemini-2.5-flash,gemini:gemini-2.0-flash-lite",
                "plus": "gemini:gemini-2.5-flash,openai:gpt-4o-mini",
                "circle": "gemini:gemini-2.5-flash,openai:gpt-4o,anthropic:claude-sonnet-4-20250514",
            },
        )
        models = svc.get_available_models_for_user("user-1", "free")
        assert ("gemini", "gemini-2.5-flash") in models
        assert ("gemini", "gemini-2.0-flash-lite") in models
        # No OpenAI or Anthropic
        assert all(p == "gemini" for p, _ in models)

    def test_premium_tier_returns_gemini_and_openai(self):
        svc = FeatureFlagService(
            enabled_providers="gemini,openai,anthropic",
            tier_allowlists={
                "free": "gemini:gemini-2.5-flash,gemini:gemini-2.0-flash-lite",
                "plus": "gemini:gemini-2.5-flash,gemini:gemini-2.0-flash-lite,openai:gpt-4o-mini",
                "circle": "gemini:gemini-2.5-flash,openai:gpt-4o,anthropic:claude-sonnet-4-20250514",
            },
        )
        models = svc.get_available_models_for_user("user-1", "premium_monthly")
        providers = {p for p, _ in models}
        assert "gemini" in providers
        assert "openai" in providers
        assert "anthropic" not in providers

    def test_circle_tier_returns_all_providers(self):
        svc = FeatureFlagService(
            enabled_providers="gemini,openai,anthropic",
            tier_allowlists={
                "free": "gemini:gemini-2.5-flash",
                "circle": "gemini:gemini-2.5-flash,openai:gpt-4o,anthropic:claude-sonnet-4-20250514",
            },
        )
        models = svc.get_available_models_for_user("user-1", "study_circle_monthly")
        providers = {p for p, _ in models}
        assert "gemini" in providers
        assert "openai" in providers
        assert "anthropic" in providers

    def test_global_disable_excludes_provider(self):
        svc = FeatureFlagService(
            enabled_providers="gemini,openai",  # anthropic NOT enabled
            tier_allowlists={
                "circle": "gemini:gemini-2.5-flash,openai:gpt-4o,anthropic:claude-sonnet-4-20250514",
            },
        )
        models = svc.get_available_models_for_user("user-1", "study_circle_monthly")
        providers = {p for p, _ in models}
        assert "anthropic" not in providers

    def test_user_override_grant_adds_model(self):
        svc = FeatureFlagService(
            enabled_providers="gemini,openai,anthropic",
            tier_allowlists={
                "free": "gemini:gemini-2.5-flash",
                "circle": "gemini:gemini-2.5-flash,openai:gpt-4o,anthropic:claude-sonnet-4-20250514",
            },
        )
        svc.set_user_override("user-1", "openai", grant=True)
        models = svc.get_available_models_for_user("user-1", "free")
        providers = {p for p, _ in models}
        # User has openai grant, so openai models from any tier should be available
        assert "openai" in providers
