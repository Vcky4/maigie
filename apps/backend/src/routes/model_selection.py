"""
Model Selection REST API endpoints.

Allows authenticated users to view available models based on their tier
and set model preferences per capability.
"""

import logging

from fastapi import APIRouter, HTTPException, status

from src.dependencies import CurrentUser, DBDep
from src.schemas.model_selection import (
    ModelInfo,
    PreferenceResponse,
    SetPreferenceRequest,
    VALID_CAPABILITIES,
)
from src.services.llm.adapter_registry import get_feature_flag_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/models", tags=["models"])


# ---------------------------------------------------------------------------
# Model display name mapping
# ---------------------------------------------------------------------------

MODEL_DISPLAY_NAMES: dict[str, str] = {
    # Gemini
    "gemini-2.5-flash": "Gemini 2.5 Flash",
    "gemini-2.0-flash": "Gemini 2.0 Flash",
    "gemini-2.0-flash-lite": "Gemini 2.0 Flash Lite",
    "gemini-3-flash-preview": "Gemini 3 Flash Preview",
    "gemini-embedding-001": "Gemini Embedding",
    # OpenAI
    "gpt-4o-mini": "GPT-4o Mini",
    "gpt-4o": "GPT-4o",
    # Anthropic
    "claude-sonnet-4-20250514": "Claude Sonnet 4",
    "claude-haiku-3-5": "Claude Haiku 3.5",
}

# ---------------------------------------------------------------------------
# Model capabilities mapping
# ---------------------------------------------------------------------------

MODEL_CAPABILITIES: dict[str, list[str]] = {
    # Gemini models
    "gemini-2.5-flash": ["chat", "vision", "structured_output"],
    "gemini-2.0-flash": ["chat", "vision", "structured_output"],
    "gemini-2.0-flash-lite": ["chat", "structured_output"],
    "gemini-3-flash-preview": ["chat", "vision", "structured_output"],
    "gemini-embedding-001": ["embedding"],
    # OpenAI models
    "gpt-4o-mini": ["chat", "vision", "structured_output"],
    "gpt-4o": ["chat", "vision", "structured_output"],
    # Anthropic models
    "claude-sonnet-4-20250514": ["chat", "vision", "structured_output"],
    "claude-haiku-3-5": ["chat", "vision", "structured_output"],
}


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/available", response_model=list[ModelInfo])
async def get_available_models(
    current_user: CurrentUser,
) -> list[ModelInfo]:
    """Return models available to the authenticated user based on their tier.

    The returned list is filtered by the user's subscription tier and any
    per-user feature flag overrides.
    """
    user_tier = str(current_user.tier) if current_user.tier else "FREE"
    user_id = current_user.id

    feature_flags = get_feature_flag_service()
    available_pairs = feature_flags.get_available_models_for_user(user_id, user_tier)

    models: list[ModelInfo] = []
    for provider, model_id in available_pairs:
        display_name = MODEL_DISPLAY_NAMES.get(model_id, model_id)
        capabilities = MODEL_CAPABILITIES.get(model_id, ["chat"])

        models.append(
            ModelInfo(
                model_id=model_id,
                provider=provider,
                display_name=display_name,
                capabilities=capabilities,
            )
        )

    return models


@router.put("/preference", response_model=PreferenceResponse)
async def set_model_preference(
    body: SetPreferenceRequest,
    current_user: CurrentUser,
    db: DBDep,
) -> PreferenceResponse:
    """Set the user's preferred model for a capability.

    Validates that:
    - The capability is valid (chat, vision, structured_output, embedding)
    - The model exists in the system
    - The model is allowed for the user's tier
    """
    user_tier = str(current_user.tier) if current_user.tier else "FREE"
    user_id = current_user.id

    # Validate capability
    if body.capability not in VALID_CAPABILITIES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Invalid capability '{body.capability}'. "
                f"Valid options are: {', '.join(sorted(VALID_CAPABILITIES))}"
            ),
        )

    # Check if model exists in the system
    if body.model_id not in MODEL_CAPABILITIES:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Model '{body.model_id}' not found in the system.",
        )

    # Determine the provider for this model
    feature_flags = get_feature_flag_service()
    available_pairs = feature_flags.get_available_models_for_user(user_id, user_tier)

    # Find the provider for the requested model
    provider_for_model: str | None = None
    for provider, model_id in available_pairs:
        if model_id == body.model_id:
            provider_for_model = provider
            break

    # If model not in available pairs, check if it exists but is disallowed
    if provider_for_model is None:
        # Model exists (we checked above) but is not allowed for this tier
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"Model '{body.model_id}' is not available for your current tier "
                f"({user_tier}). Upgrade your subscription to access this model."
            ),
        )

    # Upsert the preference in the database
    await db.modelpreference.upsert(
        where={
            "userId_capability": {
                "userId": user_id,
                "capability": body.capability,
            }
        },
        data={
            "create": {
                "userId": user_id,
                "capability": body.capability,
                "provider": provider_for_model,
                "modelId": body.model_id,
            },
            "update": {
                "provider": provider_for_model,
                "modelId": body.model_id,
            },
        },
    )

    logger.info(
        "Model preference set",
        extra={
            "user_id": user_id,
            "capability": body.capability,
            "provider": provider_for_model,
            "model_id": body.model_id,
        },
    )

    return PreferenceResponse(
        capability=body.capability,
        model_id=body.model_id,
        provider=provider_for_model,
    )
