"""Tests for comprehensive exception handling system."""

import pytest
from fastapi.testclient import TestClient

from src.main import app

client = TestClient(app)


class TestSubscriptionLimitError:
    """Test subscription limit error handling."""
    
    def test_voice_session_without_premium_subscription(self):
        """Test that basic users get 403 when accessing voice sessions."""
        response = client.post(
            "/api/v1/examples/ai/voice-session",
            json={"session_type": "conversation"},
            headers={"X-User-Subscription": "basic"}
        )
        
        assert response.status_code == 403
        data = response.json()
        
        # Verify standardized error response format
        assert "status_code" in data
        assert "code" in data
        assert "message" in data
        
        assert data["status_code"] == 403
        assert data["code"] == "SUBSCRIPTION_LIMIT_EXCEEDED"
        assert "Premium subscription" in data["message"]
    
    def test_voice_session_with_premium_subscription(self):
        """Test that premium users can access voice sessions."""
        response = client.post(
            "/api/v1/examples/ai/voice-session",
            json={"session_type": "conversation"},
            headers={"X-User-Subscription": "premium"}
        )
        
        # Should succeed (200) or be unimplemented but not forbidden
        assert response.status_code != 403
    
    def test_voice_session_without_subscription_header(self):
        """Test default behavior when subscription header is missing."""
        response = client.post(
            "/api/v1/examples/ai/voice-session",
            json={"session_type": "conversation"}
        )
        
        # Should fail with subscription limit error
        assert response.status_code == 403
        data = response.json()
        assert data["code"] == "SUBSCRIPTION_LIMIT_EXCEEDED"


class TestResourceNotFoundError:
    """Test resource not found error handling."""
    
    def test_user_not_found_in_create_plan(self):
        """Test ResourceNotFoundError when user doesn't exist."""
        response = client.post(
            "/api/v1/examples/ai/create-plan",
            json={"goal": "Learn Python", "duration_weeks": 8},
            headers={"X-User-ID": "unknown"}
        )
        
        assert response.status_code == 404
        data = response.json()
        
        # Verify standardized error response format
        assert data["status_code"] == 404
        assert data["code"] == "RESOURCE_NOT_FOUND"
        assert "User" in data["message"]
        assert "unknown" in data["message"]
    
    def test_course_not_found_in_process(self):
        """Test ResourceNotFoundError when course doesn't exist."""
        response = client.get("/api/v1/examples/ai/process/nonexistent")
        
        assert response.status_code == 404
        data = response.json()
        
        assert data["status_code"] == 404
        assert data["code"] == "RESOURCE_NOT_FOUND"
        assert "Course" in data["message"]
        assert "nonexistent" in data["message"]
    
    def test_valid_course_id(self):
        """Test that valid course IDs don't throw ResourceNotFoundError."""
        response = client.get("/api/v1/examples/ai/process/valid-course-123")
        
        # Should not return 404
        assert response.status_code != 404


class TestValidationError:
    """Test request validation error handling."""
    
    def test_invalid_chat_request(self):
        """Test validation error with missing required field."""
        response = client.post(
            "/api/v1/ai/chat",
            json={}  # Missing required 'message' field
        )
        
        assert response.status_code == 400
        data = response.json()
        
        # Verify standardized error response format
        assert data["status_code"] == 400
        assert data["code"] == "VALIDATION_ERROR"
        assert "message" in data["message"].lower()
    
    def test_invalid_plan_duration(self):
        """Test validation error with invalid data type."""
        response = client.post(
            "/api/v1/ai/create-plan",
            json={
                "goal": "Learn Python",
                "duration_weeks": "invalid"  # Should be int
            }
        )
        
        assert response.status_code == 400
        data = response.json()
        assert data["code"] == "VALIDATION_ERROR"
    
    def test_valid_request(self):
        """Test that valid requests don't throw validation errors."""
        response = client.post(
            "/api/v1/ai/chat",
            json={"message": "Hello AI"}
        )
        
        # Should not return validation error
        assert response.status_code != 400


class TestErrorResponseFormat:
    """Test that all errors follow the standardized format."""
    
    def test_error_response_has_required_fields(self):
        """Test that error responses have all required fields."""
        response = client.post(
            "/api/v1/examples/ai/voice-session",
            json={"session_type": "conversation"}
        )
        
        data = response.json()
        
        # Required fields
        assert "status_code" in data
        assert "code" in data
        assert "message" in data
        
        # All fields should be correct types
        assert isinstance(data["status_code"], int)
        assert isinstance(data["code"], str)
        assert isinstance(data["message"], str)
        
        # Optional detail field (may or may not be present)
        if "detail" in data:
            assert isinstance(data["detail"], str)
    
    def test_multiple_error_types_same_format(self):
        """Test that different error types use the same response format."""
        # Get 403 error
        response_403 = client.post(
            "/api/v1/examples/ai/voice-session",
            json={"session_type": "conversation"}
        )
        
        # Get 404 error
        response_404 = client.get("/api/v1/examples/ai/process/nonexistent")
        
        # Get 400 error
        response_400 = client.post("/api/v1/ai/chat", json={})
        
        # All should have the same structure
        for response in [response_403, response_404, response_400]:
            data = response.json()
            assert "status_code" in data
            assert "code" in data
            assert "message" in data


class TestInternalErrorsNotLeaked:
    """Test that internal errors don't leak implementation details."""
    
    def test_unhandled_exception_returns_generic_error(self):
        """
        Test that unhandled exceptions return generic 500 errors.
        
        Note: This test would require triggering an actual unhandled exception
        in the application. In a real scenario, you might mock a function to
        raise an exception and verify the handler catches it properly.
        """
        # This is a placeholder test - in practice, you'd need to trigger
        # an actual unhandled exception somehow
        pass
    
    def test_error_detail_field_handling(self):
        """Test that detail field is handled appropriately."""
        response = client.post(
            "/api/v1/examples/ai/voice-session",
            json={"session_type": "conversation"}
        )
        
        data = response.json()
        
        # In production, detail should be None or omitted
        # In development, it might contain information
        # The key is it shouldn't leak sensitive data
        if "detail" in data and data["detail"] is not None:
            # Detail should not contain sensitive information
            assert "password" not in data["detail"].lower()
            assert "secret" not in data["detail"].lower()
            assert "token" not in data["detail"].lower()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

