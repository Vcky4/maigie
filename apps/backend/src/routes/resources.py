"""
Resource routes.

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, HTTPException, status

from src.dependencies import CurrentUser
from src.models.resources import (
    ResourceCreate,
    ResourceRecommendationRequest,
    ResourceRecommendationResponse,
    ResourceRecommendationItem,
)
from src.services.rag_service import rag_service
from src.services.user_memory_service import user_memory_service
from src.services.indexing_service import indexing_service
from src.core.database import db

router = APIRouter(prefix="/api/v1/resources", tags=["resources"])


@router.get("")
async def list_resources(current_user: CurrentUser):
    """List user's resources."""
    try:
        resources = await db.resource.find_many(
            where={"userId": current_user.id},
            order={"createdAt": "desc"},
        )

        return [
            {
                "id": r.id,
                "title": r.title,
                "url": r.url,
                "description": r.description,
                "type": r.type,
                "isRecommended": r.isRecommended,
                "recommendationScore": r.recommendationScore,
                "clickCount": r.clickCount,
                "createdAt": r.createdAt.isoformat(),
            }
            for r in resources
        ]
    except Exception as e:
        print(f"Error listing resources: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to list resources",
        )


@router.post("")
async def create_resource(
    data: ResourceCreate,
    current_user: CurrentUser,
    background_tasks: BackgroundTasks,
):
    """Create a new resource."""
    try:
        resource = await db.resource.create(
            data={
                "userId": current_user.id,
                "title": data.title,
                "url": data.url,
                "description": data.description,
                "type": data.type,
                "metadata": data.metadata,
            }
        )

        # Index the resource in the background
        background_tasks.add_task(indexing_service.index_resource, resource.id)

        # Record interaction
        await user_memory_service.record_interaction(
            user_id=current_user.id,
            interaction_type="RESOURCE_CREATE",
            entity_type="resource",
            entity_id=resource.id,
            importance=0.6,
        )

        return {
            "id": resource.id,
            "title": resource.title,
            "url": resource.url,
            "description": resource.description,
            "type": resource.type,
            "createdAt": resource.createdAt.isoformat(),
        }

    except Exception as e:
        print(f"Error creating resource: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create resource",
        )


@router.post("/recommend", response_model=ResourceRecommendationResponse)
async def recommend_resources(
    request: ResourceRecommendationRequest,
    current_user: CurrentUser,
):
    """
    Get AI-recommended resources using RAG and user personalization.

    This endpoint uses:
    1. RAG (Retrieval-Augmented Generation) to find relevant content from user's data
    2. User memory/interaction history for personalization
    3. LLM to generate contextual recommendations
    """
    try:
        # 1. Get user context for personalization
        user_context = await user_memory_service.get_user_context(current_user.id)

        # Merge with provided context
        if request.context:
            user_context.update(request.context)

        # 2. Generate recommendations using RAG
        recommendations = await rag_service.generate_recommendations(
            query=request.query,
            user_id=current_user.id,
            user_context=user_context,
            limit=request.limit,
        )

        # 3. Store recommendations as resources (optional - can be done async)
        # For now, we'll return the recommendations without storing them
        # The frontend can decide whether to save them

        # 4. Record interaction for memory
        await user_memory_service.record_interaction(
            user_id=current_user.id,
            interaction_type="RECOMMENDATION_REQUESTED",
            entity_type="chat",
            metadata={"query": request.query, "recommendationCount": len(recommendations)},
            importance=0.7,
        )

        # 5. Format response
        recommendation_items = [
            ResourceRecommendationItem(
                title=rec.get("title", "Untitled"),
                url=rec.get("url", ""),
                description=rec.get("description"),
                type=rec.get("type", "OTHER"),
                relevance=rec.get("relevance"),
                score=rec.get("score", 0.5),
            )
            for rec in recommendations
        ]

        return ResourceRecommendationResponse(
            recommendations=recommendation_items,
            query=request.query,
            personalized=True,
        )

    except Exception as e:
        print(f"Error generating recommendations: {e}")
        import traceback

        traceback.print_exc()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to generate recommendations",
        )


@router.post("/{resource_id}/interact")
async def record_resource_interaction(
    resource_id: str,
    interaction_type: str,
    current_user: CurrentUser,
):
    """
    Record a user interaction with a resource for personalization.

    Interaction types: RESOURCE_CLICK, RESOURCE_VIEW, RESOURCE_BOOKMARK, RESOURCE_FEEDBACK
    """
    try:
        # Verify resource exists and belongs to user
        resource = await db.resource.find_first(
            where={"id": resource_id, "userId": current_user.id}
        )

        if not resource:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Resource not found",
            )

        # Record interaction
        await user_memory_service.record_interaction(
            user_id=current_user.id,
            interaction_type=interaction_type,
            entity_type="resource",
            entity_id=resource_id,
            importance=0.8 if interaction_type == "RESOURCE_BOOKMARK" else 0.5,
        )

        # Update resource stats
        update_data = {}
        if interaction_type == "RESOURCE_CLICK":
            update_data["clickCount"] = {"increment": 1}
            update_data["lastAccessedAt"] = datetime.utcnow()
        elif interaction_type == "RESOURCE_BOOKMARK":
            update_data["bookmarkCount"] = {"increment": 1}

        if update_data:
            await db.resource.update(where={"id": resource_id}, data=update_data)

        return {"success": True, "message": "Interaction recorded"}

    except HTTPException:
        raise
    except Exception as e:
        print(f"Error recording interaction: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to record interaction",
        )
