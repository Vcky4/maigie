"""
Example endpoints demonstrating exception handling patterns.

These endpoints are for testing and demonstration purposes only.
They show how to properly use the custom exception system.

🧪 EXAMPLE ENDPOINTS - NOT FOR PRODUCTION USE

Copyright (C) 2025 Maigie

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU Affero General Public License as published
by the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU Affero General Public License for more details.

You should have received a copy of the GNU Affero General Public License
along with this program.  If not, see <https://www.gnu.org/licenses/>.
"""

import logging
from typing import Optional

from fastapi import APIRouter, Header
from pydantic import BaseModel

from ..utils.exceptions import (
    InternalServerError,
    ResourceNotFoundError,
    SubscriptionLimitError,
)

# Get logger for this module
logger = logging.getLogger(__name__)

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
    x_user_subscription: str | None = Header(None, alias="X-User-Subscription"),
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
            detail=f"User attempted Voice AI with subscription: {x_user_subscription or 'basic'}",
        )

    return {
        "message": "Voice session started (example response)",
        "session_type": request.session_type,
        "status": "active",
        "note": "This is an example endpoint for testing exception handling",
    }


@router.post("/ai/create-plan")
async def example_create_plan(
    request: PlanRequest, x_user_id: str | None = Header(None, alias="X-User-ID")
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
            detail="User profile must exist to create personalized plan",
        )

    return {
        "message": "Study plan created (example response)",
        "goal": request.goal,
        "duration_weeks": request.duration_weeks,
        "note": "This is an example endpoint for testing exception handling",
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
            detail="Course must exist before AI processing",
        )

    return {
        "message": "Course processing started (example response)",
        "course_id": course_id,
        "status": "processing",
        "note": "This is an example endpoint for testing exception handling",
    }


@router.post("/test/error-500")
async def test_internal_server_error():
    """
    🧪 **LOGGING TEST ENDPOINT** - Triggers InternalServerError (500)

    This endpoint deliberately raises an InternalServerError to demonstrate:
    - Structured JSON logging at ERROR level
    - Full traceback in log output
    - Sentry error tracking (if configured)
    - Generic error response to client

    **Expected Behavior:**
    1. Console shows JSON-formatted log with full traceback
    2. Sentry receives error event (if SENTRY_DSN is set)
    3. Client receives generic 500 error response

    **Example Usage:**
    ```bash
    curl -X POST http://localhost:8000/api/v1/examples/test/error-500
    ```

    **Expected Response:**
    ```json
    {
        "status_code": 500,
        "code": "INTERNAL_SERVER_ERROR",
        "message": "An internal server error occurred. Please try again later."
    }
    ```

    **Check Logs For:**
    - JSON-formatted error log
    - Full traceback information
    - Request context (path, method, user-agent)
    - Error code and status code

    Raises:
        InternalServerError: Always raised for testing
    """
    # Log some context before the error
    logger.info(
        "Testing InternalServerError logging",
        extra={"test_type": "error_500", "endpoint": "/api/v1/examples/test/error-500"},
    )

    # Raise InternalServerError to test logging
    raise InternalServerError(
        message="This is a test error for logging demonstration",
        detail="Simulated database connection failure",
    )


@router.post("/test/unhandled-exception")
async def test_unhandled_exception():
    """
    🧪 **LOGGING TEST ENDPOINT** - Triggers unhandled exception

    This endpoint deliberately raises an unhandled Python exception to demonstrate:
    - Structured JSON logging for unexpected errors
    - Full traceback in log output
    - Sentry error tracking (if configured)
    - Generic 500 error response to client

    **Expected Behavior:**
    1. Console shows JSON-formatted log with exception details
    2. Sentry receives error event (if SENTRY_DSN is set)
    3. Client receives generic 500 error response

    **Example Usage:**
    ```bash
    curl -X POST http://localhost:8000/api/v1/examples/test/unhandled-exception
    ```

    **Expected Response:**
    ```json
    {
        "status_code": 500,
        "code": "INTERNAL_SERVER_ERROR",
        "message": "An internal server error occurred. Please try again later."
    }
    ```

    **Check Logs For:**
    - JSON-formatted error log
    - Exception type (ZeroDivisionError)
    - Full traceback
    - Request context

    Raises:
        ZeroDivisionError: Simulated unhandled exception
    """
    # Log some context before the error
    logger.info(
        "Testing unhandled exception logging",
        extra={
            "test_type": "unhandled_exception",
            "endpoint": "/api/v1/examples/test/unhandled-exception",
        },
    )

    # Trigger unhandled exception
    result = 1 / 0  # This will raise ZeroDivisionError
    return {"result": result}  # This line is never reached


@router.post("/test/structured-logging")
async def test_structured_logging():
    """
    🧪 **LOGGING TEST ENDPOINT** - Demonstrates structured logging

    This endpoint demonstrates structured logging with rich context:
    - Multiple log levels
    - Custom fields via 'extra' parameter
    - JSON-formatted output

    **Expected Behavior:**
    1. Multiple JSON log entries with different levels
    2. Custom fields included in JSON output
    3. Successful response returned to client

    **Example Usage:**
    ```bash
    curl -X POST http://localhost:8000/api/v1/examples/test/structured-logging
    ```

    **Expected Response:**
    ```json
    {
        "message": "Structured logging test completed",
        "logs_generated": 4,
        "note": "Check console for JSON-formatted log entries"
    }
    ```

    **Check Logs For:**
    - DEBUG level log with query timing
    - INFO level log with user action
    - WARNING level log with performance issue
    - All logs in JSON format with custom fields

    Returns:
        Success response with test results
    """
    # Simulate various logging scenarios

    # 1. Debug log with database query context
    logger.debug(
        "Database query executed",
        extra={
            "query": "SELECT * FROM courses WHERE user_id = ?",
            "duration_ms": 45,
            "rows_returned": 12,
            "test": True,
        },
    )

    # 2. Info log with user action
    logger.info(
        "User action completed successfully",
        extra={
            "user_id": "test-user-123",
            "action": "view_examples",
            "resource_id": "examples-test",
            "duration_ms": 125,
            "test": True,
        },
    )

    # 3. Warning log with performance issue
    logger.warning(
        "Slow operation detected",
        extra={
            "operation": "data_processing",
            "duration_ms": 2500,
            "threshold_ms": 1000,
            "test": True,
        },
    )

    # 4. Info log with API call context
    logger.info(
        "External API call completed",
        extra={
            "api": "openai",
            "endpoint": "/v1/completions",
            "status_code": 200,
            "duration_ms": 850,
            "test": True,
        },
    )

    return {
        "message": "Structured logging test completed",
        "logs_generated": 4,
        "log_levels": ["DEBUG", "INFO", "WARNING", "INFO"],
        "note": "Check console for JSON-formatted log entries",
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
                "test_with": "Header: X-User-Subscription: basic",
            },
            {
                "path": "/api/v1/examples/ai/create-plan",
                "method": "POST",
                "demonstrates": "ResourceNotFoundError (404 Not Found)",
                "test_with": "Header: X-User-ID: unknown",
            },
            {
                "path": "/api/v1/examples/ai/process/{course_id}",
                "method": "GET",
                "demonstrates": "ResourceNotFoundError (404 Not Found)",
                "test_with": "Path param: course_id=nonexistent",
            },
            {
                "path": "/api/v1/examples/test/error-500",
                "method": "POST",
                "demonstrates": "InternalServerError (500) with structured logging",
                "test_with": "No parameters needed",
            },
            {
                "path": "/api/v1/examples/test/unhandled-exception",
                "method": "POST",
                "demonstrates": "Unhandled exception with structured logging",
                "test_with": "No parameters needed",
            },
            {
                "path": "/api/v1/examples/test/structured-logging",
                "method": "POST",
                "demonstrates": "Structured logging with custom fields",
                "test_with": "No parameters needed",
            },
        ],
        "documentation": {
            "guide": "/docs",
            "logging_guide": "See LOGGING_AND_ERROR_TRACKING.md",
            "error_codes": {
                "SUBSCRIPTION_LIMIT_EXCEEDED": "User's subscription doesn't allow this feature",
                "RESOURCE_NOT_FOUND": "Requested resource doesn't exist",
                "VALIDATION_ERROR": "Request data validation failed",
                "INTERNAL_SERVER_ERROR": "Unexpected server error",
            },
        },
    }
