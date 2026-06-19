"""Feature flag service for multi-provider LLM rollout control.

Controls which providers and models are available globally, per user tier,
and per individual user. Supports loading configuration from environment
variables (comma-separated format) and an optional database table for
dynamic updates without redeployment.

Configuration format (environment variables):
    LLM_ENABLED_PROVIDERS: comma-separated list of globally enabled providers
        e.g. "gemini,openai"
    LLM_TIER_ALLOWLIST_FREE: comma-separated "provider:model" pairs
        e.g. "gemini:gemini-3.5-flash,gemini:gemini-3.1-flash-lite"
    LLM_TIER_ALLOWLIST_PLUS: comma-separated "provider:model" pairs

Precedence order for access decisions:
    1. Global provider enable/disable (highest priority)
    2. Per-user override (grant or revoke)
    3. Tier allowlist (lowest priority)

Effective-tier resolution per Usage_Scope (Circle Reimagining):
    Personal_Tier and Seat_Tier are resolved to a single ``EffectiveTier``
    value of ``"free"`` or ``"plus"`` via :py:meth:`effective_tier_for_request`.
    Downstream tier gates (model allowlist, caps, upload limits) only see
    these two outcomes; the legacy ``circle`` / ``squad`` branches are gone.
"""

from __future__ import annotations

import logging
from typing import Any, Literal, Protocol

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Effective tier and usage scope types
# ---------------------------------------------------------------------------

# Two-valued effective tier used by every downstream LLM gate. The legacy
# "circle" and "squad" outcomes were removed by the Circle Reimagining
# feature; Circle-scoped capabilities are derived from Seat_Tier instead.
EffectiveTier = Literal["free", "plus"]

# Scope under which an AI request is executed. ``"personal"`` resolves
# against the User's Personal_Tier; ``"circle:{circle_id}"`` resolves
# against the User's Seat_Tier in that Circle. The two scopes are
# mutually isolated (Requirements 7.2, 7.3, 7.4).
UsageScope = str  # "personal" | "circle:{circle_id}"

PERSONAL_SCOPE: UsageScope = "personal"


def circle_scope(circle_id: str) -> UsageScope:
    """Build a Circle-scoped Usage_Scope value for a given Circle id."""
    return f"circle:{circle_id}"


def parse_scope(scope: UsageScope) -> tuple[str, str | None]:
    """Split a Usage_Scope into ``(kind, circle_id)`` where kind is
    ``"personal"`` or ``"circle"``.

    Raises ValueError for malformed scope strings.
    """
    if scope == PERSONAL_SCOPE:
        return ("personal", None)
    if scope.startswith("circle:") and len(scope) > len("circle:"):
        return ("circle", scope[len("circle:") :])
    raise ValueError(f"Invalid usage scope: {scope!r}")


# ---------------------------------------------------------------------------
# Seat tier resolution (abstraction point for seat_service)
# ---------------------------------------------------------------------------
#
# Until Task 3.1 ``seat_service`` lands, the effective-tier resolver reads
# ``CircleMember.seatTier`` directly via Prisma. The lookup is funneled
# through :py:func:`read_seat_tier_for_user` so the upcoming
# ``seat_service.get_seat_tier`` can replace this helper without touching
# any caller.
#
# Contract:
#   - Returns the stored Seat_Tier enum value as a string
#     (``"PLUS_SEAT"`` or ``"FREE_SEAT"``).
#   - Non-members and lookup failures resolve to ``"FREE_SEAT"`` so the
#     request is gated as Free without raising — authorization errors
#     belong to the per-route membership check, not the tier resolver.


async def read_seat_tier_for_user(user_id: str, circle_id: str) -> str:
    """Return ``CircleMember.seatTier`` for ``(user_id, circle_id)``.

    Direct Prisma read; Task 3.1 swaps this for ``seat_service.get_seat_tier``.
    Returns ``"FREE_SEAT"`` for non-members or any lookup failure.
    """
    from src.core.database import db as prisma_db

    try:
        member = await prisma_db.circlemember.find_unique(
            where={"circleId_userId": {"circleId": circle_id, "userId": user_id}}
        )
    except Exception:
        logger.exception(
            "Failed to read CircleMember.seatTier for user_id=%s circle_id=%s",
            user_id,
            circle_id,
        )
        return "FREE_SEAT"
    if member is None or getattr(member, "seatTier", None) is None:
        return "FREE_SEAT"
    return str(member.seatTier)


# ---------------------------------------------------------------------------
# Tier name mapping: maps database tier values to allowlist config keys
# ---------------------------------------------------------------------------

# Maps user tier enum values (as stored in the database) to the allowlist
# config keys used in LLM_TIER_ALLOWLIST_* settings. Only ``free`` and
# ``plus`` outcomes exist in the reimagined ladder. Legacy STUDY_CIRCLE_*
# and SQUAD_* enum values still appear in the database for historical
# billing records, but they map to ``plus`` here so any pre-migration
# user retains paid-tier capabilities until the migration runner converts
# them.
TIER_TO_ALLOWLIST_KEY: dict[str, str] = {
    "free": "free",
    "premium_monthly": "plus",
    "premium_yearly": "plus",
    # Maigie Plus plan aliases (used in subscription routes)
    "plus": "plus",
    "plus_monthly": "plus",
    "plus_yearly": "plus",
    "maigie_plus_monthly": "plus",
    "maigie_plus_yearly": "plus",
    # Pre-migration legacy tiers map to ``plus`` so paid users keep
    # paid-tier model access until the migration runs.
    "study_circle_monthly": "plus",
    "study_circle_yearly": "plus",
    "squad_monthly": "plus",
    "squad_yearly": "plus",
}


# ---------------------------------------------------------------------------
# Database interface (injectable for testing / decoupling from Prisma)
# ---------------------------------------------------------------------------


class FeatureFlagStore(Protocol):
    """Async interface for reading feature flag overrides from a database.

    Implementations can use Prisma, SQLAlchemy, or any other data layer.
    The service works without a store (env-only mode) when None is passed.
    """

    async def get_user_override(self, user_id: str, provider: str) -> bool | None:
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
                (e.g. "free", "plus"). The legacy ``circle`` and ``squad``
                allowlist keys were removed by Circle Reimagining; Circle
                capabilities are now resolved per-Seat_Tier and map back
                to ``free`` / ``plus``.
            store: Optional async database store for per-user overrides and
                dynamic flag management. Pass None for env-only mode.
        """
        self._store = store

        # Parse initial configuration
        self._enabled_providers: set[str] = self._parse_enabled_providers(enabled_providers)
        self._tier_allowlists: dict[str, list[tuple[str, str]]] = self._parse_tier_allowlists(
            tier_allowlists or {}
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
                "PREMIUM_MONTHLY", "plus"). Legacy STUDY_CIRCLE_* / SQUAD_*
                values are accepted and resolved to ``plus`` for the
                migration window via :py:data:`TIER_TO_ALLOWLIST_KEY`.

        Returns:
            List of model identifiers allowed for this provider+tier combo.
            Returns an empty list if the tier has no allowlist or the
            provider has no entries in that tier.
        """
        tier_key = self._resolve_tier_key(user_tier)
        allowlist = self._tier_allowlists.get(tier_key, [])
        return [model for p, model in allowlist if p == provider.lower()]

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
                "PREMIUM_MONTHLY", "plus"). Legacy STUDY_CIRCLE_* / SQUAD_*
                values are accepted and resolved to ``plus`` for the
                migration window via :py:data:`TIER_TO_ALLOWLIST_KEY`.
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
        if (provider_lower, model.lower()) in [(p, m.lower()) for p, m in allowlist]:
            return True

        # Not allowed by any rule
        return False

    def set_user_override(self, user_id: str, provider: str, *, grant: bool) -> None:
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

    def get_available_models_for_user(self, user_id: str, user_tier: str) -> list[tuple[str, str]]:
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

    async def effective_tier_for_request(
        self,
        user_id: str,
        scope: UsageScope,
        *,
        personal_tier: str | None = None,
        seat_tier: str | None = None,
    ) -> EffectiveTier:
        """Resolve the effective tier for an AI request under a given scope.

        For ``scope == "personal"``, returns ``"plus"`` if the user's
        Personal_Tier maps to PLUS (any of PREMIUM_*, PLUS_*, STUDY_CIRCLE_*,
        SQUAD_* — the legacy paid tiers retain paid capabilities until
        migration runs), else ``"free"``.

        For ``scope == "circle:{circle_id}"``, returns ``"plus"`` if the
        user's Seat_Tier in that Circle is ``PLUS_SEAT``, else ``"free"``.
        Independent of Personal_Tier (Requirements 7.2, 7.3, 7.4).

        Pre-resolved values may be passed via ``personal_tier`` /
        ``seat_tier`` to avoid redundant DB reads when the caller already
        loaded them. Otherwise the resolver performs a direct Prisma read
        against ``User.tier`` or ``CircleMember.seatTier`` via the shared
        Prisma client.

        Args:
            user_id: The requesting User's id.
            scope: Either ``"personal"`` or ``"circle:{circle_id}"``.
            personal_tier: Optional pre-loaded ``User.tier`` string.
            seat_tier: Optional pre-loaded ``CircleMember.seatTier`` string.

        Returns:
            ``"plus"`` if the user's effective tier under this scope is
            paid, else ``"free"``.
        """
        kind, circle_id = parse_scope(scope)

        if kind == "personal":
            tier_value = personal_tier
            if tier_value is None:
                tier_value = await self._fetch_personal_tier(user_id)
            return self._personal_tier_to_effective(tier_value)

        # Circle scope
        assert circle_id is not None  # parse_scope guarantees this
        seat_value = seat_tier
        if seat_value is None:
            seat_value = await self._fetch_seat_tier(user_id, circle_id)
        return "plus" if str(seat_value).upper() == "PLUS_SEAT" else "free"

    @staticmethod
    def _personal_tier_to_effective(tier_value: str | None) -> EffectiveTier:
        """Map a stored Personal_Tier enum value to an EffectiveTier.

        FREE → ``"free"``. Any non-FREE value (PREMIUM_*, PLUS_*,
        STUDY_CIRCLE_*, SQUAD_*) → ``"plus"`` so that paid users retain
        paid capabilities through the Circle Reimagining migration window.
        """
        if not tier_value:
            return "free"
        return "free" if str(tier_value).upper() == "FREE" else "plus"

    @staticmethod
    async def _fetch_personal_tier(user_id: str) -> str | None:
        """Read User.tier directly via the shared Prisma client.

        Returns ``None`` if the user is not found, which the caller maps
        to ``"free"``.
        """
        from src.core.database import db as prisma_db

        try:
            user = await prisma_db.user.find_unique(where={"id": user_id})
        except Exception:
            logger.exception("Failed to read User.tier for user_id=%s", user_id)
            return None
        if user is None:
            return None
        return str(user.tier) if getattr(user, "tier", None) else None

    @staticmethod
    async def _fetch_seat_tier(user_id: str, circle_id: str) -> str:
        """Resolve ``CircleMember.seatTier`` via ``seat_service``.

        Delegates to :py:func:`seat_service.get_seat_tier` which reads
        ``CircleMember.seatTier`` and returns ``"FREE_SEAT"`` for
        non-members or lookup failures.
        """
        from src.services.seat_service import get_seat_tier

        return await get_seat_tier(user_id, circle_id)

    async def reload(self) -> None:
        """Re-read configuration from environment and optionally from database.

        This refreshes the in-memory user override cache from the database
        store (if one is configured). Environment variable configuration
        is not re-read here since it's typically set at startup; call
        __init__ again or use a factory to pick up env changes.
        """
        if self._store is None:
            logger.debug("FeatureFlagService reload skipped: no database store configured")
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
        return {p.strip().lower() for p in value.split(",") if p.strip()}

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
