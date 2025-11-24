"""Resource routes."""

from fastapi import APIRouter

router = APIRouter(prefix="/api/v1/resources", tags=["resources"])


@router.get("")
async def list_resources():
    """List resources."""
    # TODO: Implement list resources
    pass


@router.post("")
async def create_resource():
    """Create a new resource."""
    # TODO: Implement create resource
    pass


@router.post("/recommend")
async def recommend_resources():
    """Get AI-recommended resources."""
    # TODO: Implement AI recommendations
    pass
