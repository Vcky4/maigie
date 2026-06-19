"""
Admin LLM Configuration endpoints.

Allows admins to view and update LLM model configuration (tier allowlists,
fallback chains, enabled providers) without redeployment.

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

import logging

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from ..dependencies import AdminUser
from ..services import system_config_service
from ..services.system_config_service import (
    LLM_ENABLED_PROVIDERS,
    LLM_FALLBACK_CHAT_DEFAULT,
    LLM_FALLBACK_CHAT_TOOLS,
    LLM_ROTATING_MODELS,
    LLM_TIER_ALLOWLIST_FREE,
    LLM_TIER_ALLOWLIST_PLUS,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/admin/llm-config", tags=["admin-llm-config"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class LlmConfigResponse(BaseModel):
    tier_allowlist_free: str = Field(
        description="Comma-separated provider:model pairs for free tier"
    )
    tier_allowlist_plus: str = Field(
        description="Comma-separated provider:model pairs for plus tier"
    )
    fallback_chat_default: str = Field(description="Default chat fallback chain")
    fallback_chat_tools: str = Field(description="Tools chat fallback chain")
    enabled_providers: str = Field(description="Comma-separated enabled providers")
    rotating_models: str = Field(description="Comma-separated rotating model IDs")


class LlmConfigUpdateRequest(BaseModel):
    tier_allowlist_free: str | None = None
    tier_allowlist_plus: str | None = None
    fallback_chat_default: str | None = None
    fallback_chat_tools: str | None = None
    enabled_providers: str | None = None
    rotating_models: str | None = None


class ConfigEntry(BaseModel):
    key: str
    value: str
    category: str
    label: str | None = None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("", response_model=LlmConfigResponse)
async def get_llm_config(admin_user: AdminUser):
    """Get current LLM configuration values."""
    from src.config import get_settings

    settings = get_settings()

    config = await system_config_service.get_llm_config()

    return LlmConfigResponse(
        tier_allowlist_free=config.get(LLM_TIER_ALLOWLIST_FREE) or settings.LLM_TIER_ALLOWLIST_FREE,
        tier_allowlist_plus=config.get(LLM_TIER_ALLOWLIST_PLUS) or settings.LLM_TIER_ALLOWLIST_PLUS,
        fallback_chat_default=config.get(LLM_FALLBACK_CHAT_DEFAULT)
        or settings.FALLBACK_CHAT_DEFAULT,
        fallback_chat_tools=config.get(LLM_FALLBACK_CHAT_TOOLS) or settings.FALLBACK_CHAT_TOOLS,
        enabled_providers=config.get(LLM_ENABLED_PROVIDERS) or settings.LLM_ENABLED_PROVIDERS,
        rotating_models=config.get(LLM_ROTATING_MODELS)
        or settings.GEMINI_ROTATING_MODELS
        or "gemini-3.5-flash,gemini-3.1-flash-lite",
    )


@router.put("", response_model=LlmConfigResponse)
async def update_llm_config(body: LlmConfigUpdateRequest, admin_user: AdminUser):
    """Update LLM configuration values. Only provided fields are updated."""
    updates = {
        LLM_TIER_ALLOWLIST_FREE: (body.tier_allowlist_free, "Free tier model allowlist"),
        LLM_TIER_ALLOWLIST_PLUS: (body.tier_allowlist_plus, "Plus tier model allowlist"),
        LLM_FALLBACK_CHAT_DEFAULT: (body.fallback_chat_default, "Default chat fallback chain"),
        LLM_FALLBACK_CHAT_TOOLS: (body.fallback_chat_tools, "Tools chat fallback chain"),
        LLM_ENABLED_PROVIDERS: (body.enabled_providers, "Enabled LLM providers"),
        LLM_ROTATING_MODELS: (body.rotating_models, "Gemini rotating models"),
    }

    for key, (value, label) in updates.items():
        if value is not None:
            # Basic validation: ensure format is reasonable
            value = value.strip()
            if not value:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Value for {key} cannot be empty",
                )
            await system_config_service.set_config(key, value, category="llm", label=label)

    # Invalidate the LLM router singleton so it rebuilds with new config on next request
    from src.services.llm.adapter_registry import invalidate_llm_router

    invalidate_llm_router()

    logger.info("LLM config updated by admin %s", admin_user.id)

    # Return the updated config
    return await get_llm_config(admin_user)


@router.get("/all", response_model=list[ConfigEntry])
async def get_all_system_configs(admin_user: AdminUser):
    """Get all system config entries (all categories)."""
    from src.core.database import db

    records = await db.systemconfig.find_many(order={"category": "asc"})
    return [
        ConfigEntry(key=r.key, value=r.value, category=r.category, label=r.label) for r in records
    ]
