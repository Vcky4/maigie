"""
System configuration service — DB-backed key-value store with in-memory cache.

Allows admins to update LLM model allowlists, fallback chains, and other
configuration without redeployment. Falls back to env vars / code defaults
when no DB entry exists.

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from src.core.database import db

logger = logging.getLogger(__name__)

# Cache TTL in seconds
_CACHE_TTL = 60

# In-memory cache: key -> (value, timestamp)
_cache: dict[str, tuple[str | None, float]] = {}


async def get_config(key: str, default: str | None = None) -> str | None:
    """Get a config value by key. Checks cache first, then DB."""
    now = time.time()
    if key in _cache:
        cached_value, cached_at = _cache[key]
        if now - cached_at < _CACHE_TTL:
            return cached_value if cached_value is not None else default

    try:
        record = await db.systemconfig.find_unique(where={"key": key})
        value = record.value if record else None
        _cache[key] = (value, now)
        return value if value is not None else default
    except Exception as e:
        logger.warning("Failed to read SystemConfig key=%s: %s", key, e)
        return default


async def get_config_json(key: str, default: Any = None) -> Any:
    """Get a config value and JSON-decode it."""
    raw = await get_config(key)
    if raw is None:
        return default
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return default


async def set_config(
    key: str, value: str, category: str = "general", label: str | None = None
) -> None:
    """Upsert a config value and invalidate cache."""
    try:
        await db.systemconfig.upsert(
            where={"key": key},
            data={
                "create": {"key": key, "value": value, "category": category, "label": label},
                "update": {"value": value, "category": category, "label": label},
            },
        )
        _cache[key] = (value, time.time())
        logger.info("SystemConfig updated: key=%s category=%s", key, category)
    except Exception as e:
        logger.error("Failed to set SystemConfig key=%s: %s", key, e)
        raise


async def get_all_by_category(category: str) -> dict[str, str]:
    """Get all config entries for a category."""
    try:
        records = await db.systemconfig.find_many(where={"category": category})
        return {r.key: r.value for r in records}
    except Exception as e:
        logger.warning("Failed to read SystemConfig category=%s: %s", category, e)
        return {}


def invalidate_cache(key: str | None = None) -> None:
    """Clear cache for a specific key or all keys."""
    if key is None:
        _cache.clear()
    else:
        _cache.pop(key, None)


# ---------------------------------------------------------------------------
# LLM-specific helpers
# ---------------------------------------------------------------------------

# These keys are used by the LLM subsystem
LLM_TIER_ALLOWLIST_FREE = "llm.tier_allowlist_free"
LLM_TIER_ALLOWLIST_PLUS = "llm.tier_allowlist_plus"
LLM_FALLBACK_CHAT_DEFAULT = "llm.fallback_chat_default"
LLM_FALLBACK_CHAT_TOOLS = "llm.fallback_chat_tools"
LLM_ENABLED_PROVIDERS = "llm.enabled_providers"
LLM_ROTATING_MODELS = "llm.rotating_models"


async def get_llm_config() -> dict[str, str | None]:
    """Get all LLM-related config values at once (batch read)."""
    keys = [
        LLM_TIER_ALLOWLIST_FREE,
        LLM_TIER_ALLOWLIST_PLUS,
        LLM_FALLBACK_CHAT_DEFAULT,
        LLM_FALLBACK_CHAT_TOOLS,
        LLM_ENABLED_PROVIDERS,
        LLM_ROTATING_MODELS,
    ]
    result: dict[str, str | None] = {}
    for key in keys:
        result[key] = await get_config(key)
    return result


async def seed_llm_defaults() -> None:
    """Seed LLM config defaults if they don't exist in DB yet.

    Called during app startup. Only inserts if the key doesn't exist,
    never overwrites existing admin-configured values.
    """
    from src.config import get_settings

    settings = get_settings()

    defaults = [
        (LLM_TIER_ALLOWLIST_FREE, settings.LLM_TIER_ALLOWLIST_FREE, "Free tier model allowlist"),
        (LLM_TIER_ALLOWLIST_PLUS, settings.LLM_TIER_ALLOWLIST_PLUS, "Plus tier model allowlist"),
        (LLM_FALLBACK_CHAT_DEFAULT, settings.FALLBACK_CHAT_DEFAULT, "Default chat fallback chain"),
        (LLM_FALLBACK_CHAT_TOOLS, settings.FALLBACK_CHAT_TOOLS, "Tools chat fallback chain"),
        (LLM_ENABLED_PROVIDERS, settings.LLM_ENABLED_PROVIDERS, "Enabled LLM providers"),
        (
            LLM_ROTATING_MODELS,
            settings.GEMINI_ROTATING_MODELS or "gemini-3.5-flash,gemini-3.1-flash-lite",
            "Gemini rotating models",
        ),
    ]

    for key, value, label in defaults:
        try:
            existing = await db.systemconfig.find_unique(where={"key": key})
            if existing is None:
                await db.systemconfig.create(
                    data={"key": key, "value": value, "category": "llm", "label": label}
                )
                logger.info("Seeded SystemConfig: %s", key)
        except Exception as e:
            logger.warning("Failed to seed SystemConfig key=%s: %s", key, e)
