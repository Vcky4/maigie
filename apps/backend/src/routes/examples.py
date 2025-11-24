"""
Example endpoints demonstrating exception handling patterns.

These endpoints are for testing and demonstration purposes only.
They show how to properly use the custom exception system.

🧪 EXAMPLE ENDPOINTS - NOT FOR PRODUCTION USE
"""

from typing import Optional

from fastapi import APIRouter, Header
from pydantic import BaseModel

from ..utils.exceptions import ResourceNotFoundError, SubscriptionLimitError

router = APIRouter(
    prefix="/api/v1/examples",
    tags=["examples"],
)


# ============================================================================
# Request Models
# ============================================================================

class VoiceSessionRequest(BaseModel):
    """Request model for voice session example."""
    session_type: str = "conversation"


class PlanRequest(BaseModel):
    """Request model for create plan example."""
    goal: str
    duration_weeks: int = 4


# ============================================================================
# Example Endpoints - Exception Handling Demonstrations
# ============================================================================

@router.post("/ai/voice-session")
async def example_voice_session(
    request: VoiceSessionRequest,
    x_user_subscription: Optional[str] = Header(None, alias="X-User-Subscription")
):
    """
    🧪 **EXAMPLE ENDPOINT** - Demonstrates SubscriptionLimitError
    
    This endpoint shows how to properly implement subscription tier checking
    and raise `SubscriptionLimitError` when a Basic user attempts to access
    a Premium feature.
    
    **How to Test:**
    - Send with header `X-User-Subscription: basic` → Returns 403 Forbidden
    - Send with header `X-User-Subscription: premium` → Returns 200 OK
    - Send without header → Returns 403 Forbidden (defaults to basic)
    
    **Example Usage:**
    ```bash
    # Should fail with 403
    curl -X POST http://localhost:8000/api/v1/examples/ai/voice-session \\
      -H "Content-Type: application/json" \\
      -H "X-User-Subscription: basic" \\
      -d '{"session_type": "conversation"}'
    
    # Should succeed with 200
    curl -X POST http://localhost:8000/api/v1/examples/ai/voice-session \\
      -H "Content-Type: application/json" \\
      -H "X-User-Subscription: premium" \\
      -d '{"session_type": "conversation"}'
    ```
    
    **Error Response Format:**
    ```json
    {
        "status_code": 403,
        "code": "SUBSCRIPTION_LIMIT_EXCEEDED",
        "message": "Voice AI sessions require a Premium subscription"
    }
    ```
    
    Args:
        request: Voice session configuration
        x_user_subscription: User subscription tier (demo purposes)
        
    Raises:
        SubscriptionLimitError: If user doesn't have Premium subscription
        
    Returns:
        Success message if user has Premium subscription
    """
    # Example: Check subscription tier
    # In production, this would check actual user data from database
    if x_user_subscription != "premium":
        raise SubscriptionLimitError(
            message="Voice AI sessions require a Premium subscription",
            detail=f"User attempted Voice AI with subscription: {x_user_subscription or 'basic'}"
        )
    
    return {
        "message": "Voice session started (example response)",
        "session_type": request.session_type,
        "status": "active",
        "note": "This is an example endpoint for testing exception handling"
    }


@router.post("/ai/create-plan")
async def example_create_plan(
    request: PlanRequest,
    x_user_id: Optional[str] = Header(None, alias="X-User-ID")
):
    """
    🧪 **EXAMPLE ENDPOINT** - Demonstrates ResourceNotFoundError (User)
    
    This endpoint shows how to properly check if a resource exists and
    raise `ResourceNotFoundError` when it doesn't.
    
    **How to Test:**
    - Send with header `X-User-ID: unknown` → Returns 404 Not Found
    - Send with any other ID → Returns 200 OK
    - Send without header → Returns 200 OK
    
    **Example Usage:**
    ```bash
    # Should fail with 404
    curl -X POST http://localhost:8000/api/v1/examples/ai/create-plan \\
      -H "Content-Type: application/json" \\
      -H "X-User-ID: unknown" \\
      -d '{"goal": "Learn Python", "duration_weeks": 8}'
    
    # Should succeed with 200
    curl -X POST http://localhost:8000/api/v1/examples/ai/create-plan \\
      -H "Content-Type: application/json" \\
      -H "X-User-ID: user123" \\
      -d '{"goal": "Learn Python", "duration_weeks": 8}'
    ```
    
    **Error Response Format:**
    ```json
    {
        "status_code": 404,
        "code": "RESOURCE_NOT_FOUND",
        "message": "User with ID 'unknown' not found"
    }
    ```
    
    Args:
        request: Plan creation parameters
        x_user_id: User ID from header (demo purposes)
        
    Raises:
        ResourceNotFoundError: If user profile not found
        
    Returns:
        Success message with plan details
    """
    # Example: Check if user exists
    # In production, this would query the database
    if x_user_id == "unknown":
        raise ResourceNotFoundError(
            resource_type="User",
            resource_id=x_user_id,
            detail="User profile must exist to create personalized plan"
        )
    
    return {
        "message": "Study plan created (example response)",
        "goal": request.goal,
        "duration_weeks": request.duration_weeks,
        "note": "This is an example endpoint for testing exception handling"
    }


@router.get("/ai/process/{course_id}")
async def example_process_course(course_id: str):
    """
    🧪 **EXAMPLE ENDPOINT** - Demonstrates ResourceNotFoundError (Course)
    
    This endpoint shows how to properly validate path parameters and
    raise `ResourceNotFoundError` when a resource doesn't exist.
    
    **How to Test:**
    - Use `course_id=nonexistent` → Returns 404 Not Found
    - Use any other course ID → Returns 200 OK
    
    **Example Usage:**
    ```bash
    # Should fail with 404
    curl http://localhost:8000/api/v1/examples/ai/process/nonexistent
    
    # Should succeed with 200
    curl http://localhost:8000/api/v1/examples/ai/process/course123
    ```
    
    **Error Response Format:**
    ```json
    {
        "status_code": 404,
        "code": "RESOURCE_NOT_FOUND",
        "message": "Course with ID 'nonexistent' not found"
    }
    ```
    
    Args:
        course_id: ID of the course to process
        
    Raises:
        ResourceNotFoundError: If course doesn't exist
        
    Returns:
        Success message with processing status
    """
    # Example: Check if course exists
    # In production, this would query the database
    if course_id == "nonexistent":
        raise ResourceNotFoundError(
            resource_type="Course",
            resource_id=course_id,
            detail="Course must exist before AI processing"
        )
    
    return {
        "message": "Course processing started (example response)",
        "course_id": course_id,
        "status": "processing",
        "note": "This is an example endpoint for testing exception handling"
    }


@router.get("/info")
async def examples_info():
    """
    Get information about available example endpoints.
    
    Returns a list of all example endpoints and their purposes.
    """
    return {
        "title": "Exception Handling Examples",
        "description": "These endpoints demonstrate the custom exception handling system",
        "endpoints": [
            {
                "path": "/api/v1/examples/ai/voice-session",
                "method": "POST",
                "demonstrates": "SubscriptionLimitError (403 Forbidden)",
                "test_with": "Header: X-User-Subscription: basic"
            },
            {
                "path": "/api/v1/examples/ai/create-plan",
                "method": "POST",
                "demonstrates": "ResourceNotFoundError (404 Not Found)",
                "test_with": "Header: X-User-ID: unknown"
            },
            {
                "path": "/api/v1/examples/ai/process/{course_id}",
                "method": "GET",
                "demonstrates": "ResourceNotFoundError (404 Not Found)",
                "test_with": "Path param: course_id=nonexistent"
            }
        ],
        "documentation": {
            "guide": "/docs",
            "error_codes": {
                "SUBSCRIPTION_LIMIT_EXCEEDED": "User's subscription doesn't allow this feature",
                "RESOURCE_NOT_FOUND": "Requested resource doesn't exist",
                "VALIDATION_ERROR": "Request data validation failed",
                "INTERNAL_SERVER_ERROR": "Unexpected server error"
            }
        }
    }

