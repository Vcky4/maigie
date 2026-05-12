"""Feature flag service for multi-provider LLM rollout control.

Controls which providers and models are available globally, per user tier,
and per individual user. Supports loading configuration from environment
variables (comma-separated format) and an optional database table for
dynamic updates without redeployment.

Configuration format (environment variables):
    LLM_ENABLED_PROVIDERS: comma-separated list of globally enabled providers
        e.g. "gemini,openai"
    LLM_TIER_ALLOWLIST_FREE: comma-separated "provider:model" pairs
        e.g. "gemini:gemini-2.5-flash,gemini:gemini-2.0-flash-lite"
    LLM_TIER_ALLOWLIST_PLUS: comma-separated "provider:model" pairs
    LLM_TIER_ALLOWLIST_CIRCLE: comma-separated "provider:model" pairs
    LLM_TIER_ALLOWLIST_SQUAD: comma-separated "provider:model" pairs

Precedence order for access decisions:
    1. Global provider enable/disable (highest priority)
    2. Per-user override (grant or revoke)
    3. Tier allowlist (lowest priority)
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tier name mapping: maps database tier values to allowlist config keys
# ---------------------------------------------------------------------------

# Maps user tier enum values (as stored in the database) to the allowlist
# config keys used in LLM_TIER_ALLOWLIST_* settings.
TIER_TO_ALLOWLIST_KEY: dict[str, str] = {
    "free": "free",
    "premium_monthly": "plus",
    "premium_yearly": "plus",
    "study_circle_monthly": "circle",
    "study_circle_yearly": "circle",
    "squad_monthly": "squad",
    "squad_yearly": "squad",
    # Direct keys (already normalized) for backward compatibility
    "plus": "plus",
    "circle": "circle",
    "squad": "squad",
    # Maigie Plus plan aliases (used in subscription routes)
    "maigie_plus_monthly": "plus",
    "maigie_plus_yearly": "plus",
}


# ---------------------------------------------------------------------------
# Database interface (injectable for testing / decoupling from Prisma)
# ---------------------------------------------------------------------------


class FeatureFlagStore(Protocol):
    """Async interface for reading feature flag overrides from a database.

    Implementations can use Prisma, SQLAlchemy, or any other data layer.
    The service works without a store (env-only mode) when None is passed.
    """

    async def get_user_override(
        self, user_id: str, provider: str
    ) -> bool | None:
        """Return True/False if an explicit override exists, None otherwise."""
        ...

    async def get_all_flags(self) -> list[dict[str, Any]]:
        """Return all feature flag rows for bulk reload.

        Each dict should contain at minimum:
            provider (str), enabled (bool),
            model (str | None), tier (str | None), userId (str | None)
        """
        ...


# ---------------------------------------------------------------------------
# FeatureFlagService
# ---------------------------------------------------------------------------


class FeatureFlagService:
    """Controls provider availability per tier and per user.

    The service loads its initial state from environment-variable-style
    configuration strings and can optionally refresh from a database store
    for dynamic updates.
    """

    def __init__(
        self,
        enabled_providers: str = "",
        tier_allowlists: dict[str, str] | None = None,
        store: FeatureFlagStore | None = None,
    ) -> None:
        """Initialize the feature flag service.

        Args:
            enabled_providers: Comma-separated list of globally enabled
                provider names (e.g. "gemini,openai").
            tier_allowlists: Mapping of tier name → comma-separated
                "provider:model" pairs. Keys should be lowercase tier names
                (e.g. "free", "plus", "circle", "squad").
            store: Optional async database store for per-user overrides and
                dynamic flag management. Pass None for env-only mode.
        """
        self._store = store

        # Parse initial configuration
        self._enabled_providers: set[str] = self._parse_enabled_providers(
            enabled_providers
        )
        self._tier_allowlists: dict[str, list[tuple[str, str]]] = (
            self._parse_tier_allowlists(tier_allowlists or {})
        )

        # Cache for database-sourced user overrides (populated on reload)
        self._user_overrides: dict[tuple[str, str], bool] = {}

        logger.info(
            "FeatureFlagService initialized",
            extra={
                "enabled_providers": sorted(self._enabled_providers),
                "tiers_configured": sorted(self._tier_allowlists.keys()),
            },
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def is_provider_enabled(self, provider: str) -> bool:
        """Check if a provider is globally enabled.

        Args:
            provider: Provider identifier (e.g. "gemini", "openai").

        Returns:
            True if the provider is in the global enabled list.
        """
        return provider.lower() in self._enabled_providers

    def get_allowed_models(self, provider: str, user_tier: str) -> list[str]:
        """Get the list of models allowed for a provider and tier.

        Args:
            provider: Provider identifier.
            user_tier: User's subscription tier (e.g. "FREE",
                "PREMIUM_MONTHLY", "plus", "circle", etc.).

        Returns:
            List of model identifiers allowed for this provider+tier combo.
            Returns an empty list if the tier has no allowlist or the
            provider has no entries in that tier.
        """
        tier_key = self._resolve_tier_key(user_tier)
        allowlist = self._tier_allowlists.get(tier_key, [])
        return [
            model
            for p, model in allowlist
            if p == provider.lower()
        ]

    def has_user_override(self, user_id: str, provider: str) -> bool | None:
        """Check if a per-user override exists for a provider.

        This checks the in-memory cache populated by reload(). For real-time
        lookups against the database, use the async variant or call reload()
        first.

        Args:
            user_id: The user's unique identifier.
            provider: Provider identifier.

        Returns:
            True if the user has an explicit grant, False if explicitly
            revoked, None if no override exists.
        """
        return self._user_overrides.get((user_id, provider.lower()))

    def is_model_allowed(
        self,
        provider: str,
        model: str,
        user_tier: str,
        user_id: str,
    ) -> bool:
        """Determine if a specific model is allowed for a user.

        Precedence order (highest to lowest):
            1. Global provider enable/disable — if provider is globally
               disabled, access is DENIED regardless of overrides or tier.
            2. Per-user override — if an explicit override exists:
               - grant (True): access is ALLOWED (even if tier excludes it)
               - revoke (False): access is DENIED (even if tier includes it)
            3. Tier allowlist — if (provider, model) is in the user's tier
               allowlist, access is ALLOWED; otherwise DENIED.

        Args:
            provider: Provider identifier.
            model: Model identifier.
            user_tier: User's subscription tier (e.g. "FREE",
                "PREMIUM_MONTHLY", "plus", "circle", etc.).
            user_id: User's unique identifier.

        Returns:
            True if the user is allowed to use this provider+model.
        """
        provider_lower = provider.lower()

        # Step 1: Global provider enable/disable (highest precedence)
        if not self.is_provider_enabled(provider_lower):
            return False

        # Step 2: Per-user override (grant OR revoke)
        override = self.has_user_override(user_id, provider_lower)
        if override is True:
            # Per-user grant: allow access even if tier doesn't include it
            return True
        if override is False:
            # Per-user revoke: deny access even if tier includes it
            return False

        # Step 3: Tier allowlist (lowest precedence)
        tier_key = self._resolve_tier_key(user_tier)
        allowlist = self._tier_allowlists.get(tier_key, [])
        if (provider_lower, model.lower()) in [
            (p, m.lower()) for p, m in allowlist
        ]:
            return True

        # Not allowed by any rule
        return False

    def set_user_override(
        self, user_id: str, provider: str, *, grant: bool
    ) -> None:
        """Set a per-user override for a provider.

        This updates the in-memory cache immediately. For persistence,
        the caller should also write to the database store.

        Args:
            user_id: The user's unique identifier.
            provider: Provider identifier (e.g. "openai", "anthropic").
            grant: True to grant access (even if tier excludes it),
                   False to revoke access (even if tier includes it).
        """
        self._user_overrides[(user_id, provider.lower())] = grant
        logger.info(
            "User override set",
            extra={
                "user_id": user_id,
                "provider": provider.lower(),
                "grant": grant,
            },
        )

    def grant_user_access(self, user_id: str, provider: str) -> None:
        """Grant a user access to a provider, overriding tier restrictions.

        This allows the user to access any model from the specified provider
        even if their tier normally excludes it. The global provider
        enable/disable still takes precedence — a grant cannot bypass a
        globally disabled provider.

        Args:
            user_id: The user's unique identifier.
            provider: Provider identifier (e.g. "openai", "anthropic").
        """
        self.set_user_override(user_id, provider, grant=True)

    def revoke_user_access(self, user_id: str, provider: str) -> None:
        """Revoke a user's access to a provider, overriding tier allowlist.

        This denies the user access to all models from the specified provider
        even if their tier normally includes it. This is useful for
        enforcement actions (e.g. abuse prevention) where a specific user
        should be blocked from a provider regardless of their subscription.

        Args:
            user_id: The user's unique identifier.
            provider: Provider identifier (e.g. "openai", "anthropic").
        """
        self.set_user_override(user_id, provider, grant=False)

    def is_user_revoked(self, user_id: str, provider: str) -> bool:
        """Check if a user has been explicitly revoked from a provider.

        Args:
            user_id: The user's unique identifier.
            provider: Provider identifier.

        Returns:
            True if the user has an explicit revocation override for this
            provider, False otherwise (including when no override exists
            or when the override is a grant).
        """
        return self._user_overrides.get((user_id, provider.lower())) is False

    def remove_user_override(self, user_id: str, provider: str) -> None:
        """Remove a per-user override, falling back to tier allowlist rules.

        This removes both grant and revoke overrides. After removal, the
        user's access is determined solely by the tier allowlist.

        Args:
            user_id: The user's unique identifier.
            provider: Provider identifier.
        """
        key = (user_id, provider.lower())
        if key in self._user_overrides:
            del self._user_overrides[key]
            logger.info(
                "User override removed",
                extra={"user_id": user_id, "provider": provider.lower()},
            )

    def get_available_models_for_user(
        self, user_id: str, user_tier: str
    ) -> list[tuple[str, str]]:
        """Return all (provider, model) pairs available to a user.

        Evaluates global enable/disable, per-user overrides, and tier
        allowlists to produce the complete set of models the user can access.

        Args:
            user_id: The user's unique identifier.
            user_tier: User's subscription tier.

        Returns:
            List of (provider, model) tuples the user is allowed to use.
        """
        tier_key = self._resolve_tier_key(user_tier)
        allowlist = self._tier_allowlists.get(tier_key, [])
        available: list[tuple[str, str]] = []

        # Collect all known (provider, model) pairs from all tier allowlists
        all_models: set[tuple[str, str]] = set()
        for pairs in self._tier_allowlists.values():
            all_models.update(pairs)

        for provider, model in all_models:
            if self.is_model_allowed(provider, model, user_tier, user_id):
                available.append((provider, model))

        return sorted(available)

    async def reload(self) -> None:
        """Re-read configuration from environment and optionally from database.

        This refreshes the in-memory user override cache from the database
        store (if one is configured). Environment variable configuration
        is not re-read here since it's typically set at startup; call
        __init__ again or use a factory to pick up env changes.
        """
        if self._store is None:
            logger.debug(
                "FeatureFlagService reload skipped: no database store configured"
            )
            return

        try:
            flags = await self._store.get_all_flags()
            new_overrides: dict[tuple[str, str], bool] = {}

            for flag in flags:
                user_id = flag.get("userId")
                provider = flag.get("provider", "")
                enabled = flag.get("enabled", True)

                # Per-user overrides have a userId set
                if user_id:
                    new_overrides[(user_id, provider.lower())] = enabled

            self._user_overrides = new_overrides
            logger.info(
                "FeatureFlagService reloaded from database",
                extra={"user_overrides_count": len(new_overrides)},
            )
        except Exception:
            logger.exception("Failed to reload feature flags from database")

    # ------------------------------------------------------------------
    # Parsing helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_tier_key(user_tier: str) -> str:
        """Resolve a user tier value to the corresponding allowlist config key.

        Maps database tier enum values (e.g. "PREMIUM_MONTHLY") to the
        allowlist keys used in configuration (e.g. "plus"). If the tier
        is already a valid allowlist key (e.g. "free", "plus"), it is
        returned as-is.

        Args:
            user_tier: User's subscription tier value.

        Returns:
            The normalized allowlist key for the tier.
        """
        return TIER_TO_ALLOWLIST_KEY.get(user_tier.lower(), user_tier.lower())

    @staticmethod
    def _parse_enabled_providers(value: str) -> set[str]:
        """Parse comma-separated provider list into a set of lowercase names."""
        if not value or not value.strip():
            return set()
        return {
            p.strip().lower()
            for p in value.split(",")
            if p.strip()
        }

    @staticmethod
    def _parse_tier_allowlists(
        raw: dict[str, str],
    ) -> dict[str, list[tuple[str, str]]]:
        """Parse tier allowlist mappings.

        Args:
            raw: Mapping of tier name → comma-separated "provider:model" pairs.

        Returns:
            Mapping of tier name → list of (provider, model) tuples.
        """
        result: dict[str, list[tuple[str, str]]] = {}
        for tier, value in raw.items():
            tier_key = tier.lower()
            pairs: list[tuple[str, str]] = []
            if value and value.strip():
                for entry in value.split(","):
                    entry = entry.strip()
                    if ":" in entry:
                        provider, model = entry.split(":", 1)
                        provider = provider.strip().lower()
                        model = model.strip()
                        if provider and model:
                            pairs.append((provider, model))
                    else:
                        logger.warning(
                            "Invalid tier allowlist entry (missing ':'): %r",
                            entry,
                        )
            result[tier_key] = pairs
        return result
