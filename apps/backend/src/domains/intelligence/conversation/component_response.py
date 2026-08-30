"""Structured component responses produced by Ask Maigie tools."""

from typing import Any


def format_action_component_response(
    *,
    action_type: str,
    action_result: dict[str, Any],
    action_data: dict[str, Any] | None = None,
    user_id: str | None = None,
    db: Any = None,
) -> dict[str, Any]:
    """Return a client-renderable action for a successfully created note.

    The note id comes from the committed, ownership-scoped read-back in ``handle_create_note``. Keeping
    it in the component payload means the same destination survives both the live socket response and
    the persisted ``ChatMessage.componentData`` history path.

    Other action components remain intentionally unsupported until their contracts are migrated.
    """
    del action_data, user_id, db

    if action_type != "create_note" or action_result.get("status") != "success":
        return {}

    note_id = action_result.get("note_id") or action_result.get("noteId")
    if not note_id:
        return {}

    title = action_result.get("title")
    return {
        "type": "component",
        "component": "entity_action",
        "action": "open",
        "entityType": "note",
        "entityId": str(note_id),
        "title": str(title) if title else "Note",
        "label": "Open note",
    }


def format_list_component_response(*args, **kwargs) -> dict[str, Any]:
    """Format a list component response for the frontend."""
    return {}  # TODO: migrate implementation
