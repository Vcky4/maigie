"""
Schedule routes.

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

from fastapi import APIRouter

router = APIRouter(prefix="/api/v1/schedule", tags=["schedule"])


@router.get("")
async def get_schedule():
    """Get schedule for a date."""
    # TODO: Implement get schedule
    pass


@router.post("")
async def create_schedule_block():
    """Create a schedule block."""
    # TODO: Implement create schedule block
    pass
