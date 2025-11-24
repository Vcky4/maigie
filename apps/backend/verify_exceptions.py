"""Verification script for exception handling system."""

import sys
from typing import Any

import httpx


def print_section(title: str) -> None:
    """Print a section header."""
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print('=' * 60)


def print_result(passed: bool, message: str) -> None:
    """Print a test result."""
    status = "✅ PASS" if passed else "❌ FAIL"
    print(f"{status}: {message}")


def test_response_format(response_data: dict[str, Any], expected_code: str) -> bool:
    """Verify response follows ErrorResponse format."""
    required_fields = ["status_code", "code", "message"]
    
    # Check all required fields exist
    for field in required_fields:
        if field not in response_data:
            print(f"  Missing required field: {field}")
            return False
    
    # Check code matches expected
    if response_data["code"] != expected_code:
        print(f"  Expected code '{expected_code}', got '{response_data['code']}'")
        return False
    
    # Check field types
    if not isinstance(response_data["status_code"], int):
        print(f"  status_code must be int, got {type(response_data['status_code'])}")
        return False
    
    if not isinstance(response_data["code"], str):
        print(f"  code must be str, got {type(response_data['code'])}")
        return False
    
    if not isinstance(response_data["message"], str):
        print(f"  message must be str, got {type(response_data['message'])}")
        return False
    
    return True


def verify_exception_handling(base_url: str = "http://localhost:8000") -> bool:
    """Verify the exception handling system."""
    all_tests_passed = True
    
    print_section("Exception Handling Verification")
    print(f"Testing against: {base_url}")
    
    # Test 1: Subscription Limit Error
    print_section("Test 1: Subscription Limit Error (403)")
    try:
        response = httpx.post(
            f"{base_url}/api/v1/examples/ai/voice-session",
            json={"session_type": "conversation"},
            headers={"X-User-Subscription": "basic"},
            timeout=10.0
        )
        
        if response.status_code == 403:
            data = response.json()
            passed = test_response_format(data, "SUBSCRIPTION_LIMIT_EXCEEDED")
            print_result(passed, "SubscriptionLimitError returns standardized error")
            if passed:
                print(f"  Response: {data}")
            all_tests_passed = all_tests_passed and passed
        else:
            print_result(False, f"Expected 403, got {response.status_code}")
            all_tests_passed = False
    except Exception as e:
        print_result(False, f"Request failed: {e}")
        all_tests_passed = False
    
    # Test 2: Resource Not Found Error
    print_section("Test 2: Resource Not Found Error (404)")
    try:
        response = httpx.get(
            f"{base_url}/api/v1/examples/ai/process/nonexistent",
            timeout=10.0
        )
        
        if response.status_code == 404:
            data = response.json()
            passed = test_response_format(data, "RESOURCE_NOT_FOUND")
            print_result(passed, "ResourceNotFoundError returns standardized error")
            if passed:
                print(f"  Response: {data}")
            all_tests_passed = all_tests_passed and passed
        else:
            print_result(False, f"Expected 404, got {response.status_code}")
            all_tests_passed = False
    except Exception as e:
        print_result(False, f"Request failed: {e}")
        all_tests_passed = False
    
    # Test 3: Validation Error
    print_section("Test 3: Validation Error (400)")
    try:
        response = httpx.post(
            f"{base_url}/api/v1/ai/chat",
            json={},  # Missing required 'message' field
            timeout=10.0
        )
        
        if response.status_code == 400:
            data = response.json()
            passed = test_response_format(data, "VALIDATION_ERROR")
            print_result(passed, "RequestValidationError returns standardized error")
            if passed:
                print(f"  Response: {data}")
            all_tests_passed = all_tests_passed and passed
        else:
            print_result(False, f"Expected 400, got {response.status_code}")
            all_tests_passed = False
    except Exception as e:
        print_result(False, f"Request failed: {e}")
        all_tests_passed = False
    
    # Test 4: Successful Request (Should NOT return error)
    print_section("Test 4: Valid Request (Should Succeed)")
    try:
        response = httpx.post(
            f"{base_url}/api/v1/ai/chat",
            json={"message": "Hello AI"},
            timeout=10.0
        )
        
        if response.status_code == 200:
            print_result(True, "Valid request does not trigger error handling")
            print(f"  Response: {response.json()}")
        else:
            print_result(False, f"Valid request returned {response.status_code}")
            all_tests_passed = False
    except Exception as e:
        print_result(False, f"Request failed: {e}")
        all_tests_passed = False
    
    # Test 5: Premium User Can Access Voice Session
    print_section("Test 5: Premium User Access (Should Succeed)")
    try:
        response = httpx.post(
            f"{base_url}/api/v1/examples/ai/voice-session",
            json={"session_type": "conversation"},
            headers={"X-User-Subscription": "premium"},
            timeout=10.0
        )
        
        if response.status_code != 403:
            print_result(True, "Premium user can access voice session")
            print(f"  Response: {response.json()}")
        else:
            print_result(False, "Premium user should not be blocked")
            all_tests_passed = False
    except Exception as e:
        print_result(False, f"Request failed: {e}")
        all_tests_passed = False
    
    # Final summary
    print_section("Summary")
    if all_tests_passed:
        print("✅ All exception handling tests PASSED")
        print("\nThe exception handling system is working correctly:")
        print("  • All errors follow the ErrorResponse format")
        print("  • Custom exceptions (SubscriptionLimitError, ResourceNotFoundError)")
        print("  • Validation errors are properly formatted")
        print("  • Valid requests succeed without errors")
        return True
    else:
        print("❌ Some tests FAILED")
        print("\nPlease check the failures above and ensure:")
        print("  • The server is running (python -m uvicorn src.main:app)")
        print("  • All exception handlers are registered")
        print("  • ErrorResponse model is properly imported")
        return False


if __name__ == "__main__":
    try:
        success = verify_exception_handling()
        sys.exit(0 if success else 1)
    except httpx.ConnectError:
        print("\n❌ ERROR: Cannot connect to the server")
        print("Please ensure the FastAPI server is running:")
        print("  cd apps/backend")
        print("  poetry run uvicorn src.main:app --reload")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        sys.exit(1)

