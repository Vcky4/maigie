"""
Pydantic schemas for the Model Selection REST API.

Defines request/response models for the /api/v1/models endpoints.
"""

from pydantic import BaseModel, Field


# Valid capability values that users can set preferences for
VALID_CAPABILITIES = frozenset({"chat", "vision", "structured_output", "embedding"})


class ModelInfo(BaseModel):
    """Information about an available LLM model."""

    model_id: str = Field(..., description="The model identifier (e.g. 'gpt-4o-mini')")
    provider: str = Field(..., description="The provider name (e.g. 'openai')")
    display_name: str = Field(..., description="Human-readable model name")
    capabilities: list[str] = Field(
        ...,
        description="List of supported capabilities: chat, vision, structured_output, embedding",
    )


class SetPreferenceRequest(BaseModel):
    """Request body for setting a model preference."""

    capability: str = Field(
        ...,
        description="The capability to set preference for: chat, vision, structured_output, embedding",
    )
    model_id: str = Field(..., description="The model identifier to prefer for this capability")


class PreferenceResponse(BaseModel):
    """Response after successfully setting a model preference."""

    capability: str = Field(..., description="The capability the preference was set for")
    model_id: str = Field(..., description="The preferred model identifier")
    provider: str = Field(..., description="The provider of the preferred model")
