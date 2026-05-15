"""
Skills API — exposes available skills metadata to the frontend.
"""

from fastapi import APIRouter

router = APIRouter(prefix="/api/v1/skills", tags=["skills"])


# Static skill suggestions for the chat input (lightweight, no auth needed)
SKILL_SUGGESTIONS = [
    {
        "id": "courses",
        "name": "Courses",
        "icon": "book-open",
        "prompt": "Show my courses",
        "color": "indigo",
    },
    {
        "id": "goals",
        "name": "Goals",
        "icon": "target",
        "prompt": "Show my goals",
        "color": "orange",
    },
    {
        "id": "scheduling",
        "name": "Schedule",
        "icon": "calendar",
        "prompt": "What's on my schedule?",
        "color": "purple",
    },
    {
        "id": "notes",
        "name": "Notes",
        "icon": "file-text",
        "prompt": "Show my notes",
        "color": "blue",
    },
    {
        "id": "resources",
        "name": "Find Resources",
        "icon": "search",
        "prompt": "Find resources for my current topic",
        "color": "emerald",
    },
    {
        "id": "planning",
        "name": "Study Plan",
        "icon": "map",
        "prompt": "Create a study plan",
        "color": "rose",
    },
    {
        "id": "memory",
        "name": "My Profile",
        "icon": "user",
        "prompt": "What do you know about me?",
        "color": "amber",
    },
    {
        "id": "insights",
        "name": "Insights",
        "icon": "bar-chart",
        "prompt": "Show my learning insights",
        "color": "cyan",
    },
]


@router.get("/suggestions")
async def get_skill_suggestions():
    """Return skill suggestion chips for the chat input UI."""
    return {"skills": SKILL_SUGGESTIONS}
