"""Authentication routes."""

from fastapi import APIRouter

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


@router.post("/register")
async def register():
    """Register a new user."""
    # TODO: Implement registration
    pass


@router.post("/login")
async def login():
    """Login user."""
    # TODO: Implement login
    pass


@router.post("/refresh")
async def refresh():
    """Refresh access token."""
    # TODO: Implement token refresh
    pass


@router.get("/me")
async def get_current_user():
    """Get current authenticated user."""
    # TODO: Implement get current user
    pass

