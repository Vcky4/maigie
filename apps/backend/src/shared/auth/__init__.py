"""Authentication and authorization infrastructure."""

from .dependencies import (
    CurrentUser,
    OptionalCurrentUser,
    SpaceMemberUser,
    StaffUser,
    SuperAdminUser,
    get_current_user,
    get_current_user_optional,
    get_staff_user,
    get_super_admin_user,
    require_space_membership,
)
from .jwt import (
    create_access_token,
    create_refresh_token,
    create_verification_token,
    decode_access_token,
    generate_otp,
    get_password_hash,
    verify_password,
)

__all__ = [
    # Dependencies (type aliases for route signatures)
    "CurrentUser",
    "OptionalCurrentUser",
    "SpaceMemberUser",
    "StaffUser",
    "SuperAdminUser",
    # Dependency functions
    "get_current_user",
    "get_current_user_optional",
    "get_staff_user",
    "get_super_admin_user",
    "require_space_membership",
    # JWT
    "create_access_token",
    "create_refresh_token",
    "create_verification_token",
    "decode_access_token",
    # Password
    "get_password_hash",
    "verify_password",
    # OTP
    "generate_otp",
]
