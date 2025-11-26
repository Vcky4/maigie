from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from src.dependencies import CurrentUser
from src.core.database import db
from src.models.auth import UserResponse

router = APIRouter()

# Schema for updating preferences
class PreferencesUpdate(BaseModel):
    theme: str | None = None
    language: str | None = None
    notifications: bool | None = None

@router.put("/preferences", response_model=UserResponse)
async def update_preferences(
    preferences: PreferencesUpdate,
    current_user: CurrentUser
):
    """
    Update the current user's preferences.
    """
    # Using Prisma's 'update' with a nested write to UserPreferences
    updated_user = await db.user.update(
        where={"id": current_user.id},
        data={
            "preferences": {
                "upsert": {
                    "create": {
                        "theme": preferences.theme or "light",
                        "language": preferences.language or "en",
                        "notifications": preferences.notifications if preferences.notifications is not None else True
                    },
                    "update": {
                        # Only update fields that were sent (exclude None)
                        **preferences.model_dump(exclude_unset=True)
                    }
                }
            }
        },
        include={"preferences": True}
    )
    
    return updated_user