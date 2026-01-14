"""
Resource routes.

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

import logging
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, HTTPException, status

from src.core.database import db
from src.dependencies import CurrentUser
from src.models.resources import (
    ResourceCreate,
    ResourceRecommendationItem,
    ResourceRecommendationRequest,
    ResourceRecommendationResponse,
)
from src.services.indexing_service import indexing_service
from src.services.rag_service import rag_service
from src.services.user_memory_service import user_memory_service

logger = logging.getLogger(__name__)

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
        logger.error(
            "Error listing resources",
            extra={
                "user_id": current_user.id,
                "error_type": type(e).__name__,
                "error": str(e),
            },
            exc_info=True,
        )
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
        # Validate courseId if provided
        if data.courseId:
            course = await db.course.find_first(
                where={"id": data.courseId, "userId": current_user.id}
            )
            if not course:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Course not found: {data.courseId}",
                )

        # Validate topicId if provided
        if data.topicId:
            topic = await db.topic.find_unique(where={"id": data.topicId})
            if not topic:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Topic not found: {data.topicId}",
                )
            # If courseId is also provided, verify topic belongs to that course
            if data.courseId:
                topic_with_module = await db.topic.find_unique(
                    where={"id": data.topicId},
                    include={"module": {"include": {"course": True}}},
                )
                if (
                    topic_with_module
                    and topic_with_module.module
                    and topic_with_module.module.course
                    and topic_with_module.module.course.id != data.courseId
                ):
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="Topic does not belong to the specified course",
                    )

        # Build resource data, only including fields that are set
        resource_data = {
            "userId": current_user.id,
            "title": data.title,
            "url": data.url,
            "type": data.type,
            "isRecommended": data.isRecommended,
        }

        # Add optional fields only if they are provided
        if data.description is not None:
            resource_data["description"] = data.description

        # Handle metadata - Prisma Json fields need explicit None or Json object
        if data.metadata is not None:
            from prisma import Json

            resource_data["metadata"] = Json(data.metadata)
        # If metadata is None, don't include it (Prisma will use default)

        if data.recommendationScore is not None:
            resource_data["recommendationScore"] = data.recommendationScore

        if data.courseId is not None:
            resource_data["courseId"] = data.courseId

        if data.topicId is not None:
            resource_data["topicId"] = data.topicId

        resource = await db.resource.create(data=resource_data)

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

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "Error creating resource",
            extra={
                "user_id": current_user.id,
                "title": data.title,
                "url": data.url,
                "course_id": data.courseId,
                "topic_id": data.topicId,
                "error_type": type(e).__name__,
                "error": str(e),
            },
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create resource: {str(e)}",
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
        logger.error(
            "Error generating recommendations",
            extra={
                "user_id": current_user.id,
                "query": request.query,
                "limit": request.limit,
                "error_type": type(e).__name__,
                "error": str(e),
            },
            exc_info=True,
        )
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
        logger.error(
            "Error recording interaction",
            extra={
                "user_id": current_user.id,
                "resource_id": resource_id,
                "interaction_type": interaction_type,
                "error_type": type(e).__name__,
                "error": str(e),
            },
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to record interaction",
        )
