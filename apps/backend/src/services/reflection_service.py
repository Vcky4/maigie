"""
Self-Correction / Reflection Service.

Evaluates the outcome of AI actions, detects failures, and logs
success/failure patterns for continuous improvement.

Copyright (C) 2025 Maigie
Licensed under the Business Source License 1.1 (BUSL-1.1).
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


async def evaluate_action_outcome(
    action_type: str,
    action_data: dict[str, Any],
    action_result: dict[str, Any],
    user_message: str | None = None,
) -> dict[str, Any]:
    """
    Evaluate whether a tool call produced the expected outcome.

    Returns:
        dict with:
        - success: bool
        - issues: list of detected issues
        - correction_hint: optional suggestion for the LLM
    """
    evaluation = {
        "success": True,
        "issues": [],
        "correction_hint": None,
    }

    status = action_result.get("status", "")

    # 1. Check for explicit failure
    if status == "error" or status == "failed":
        evaluation["success"] = False
        error_msg = action_result.get("message", "Unknown error")
        evaluation["issues"].append(f"Action '{action_type}' failed: {error_msg}")

        # Generate correction hints based on error type
        if "already exists" in error_msg.lower() or "duplicate" in error_msg.lower():
            evaluation["correction_hint"] = (
                "A similar entity already exists. Use get_user_courses or get_user_goals "
                "to find existing items instead of creating duplicates."
            )
        elif "not found" in error_msg.lower():
            evaluation["correction_hint"] = (
                "The referenced entity was not found. Use query tools to find the correct ID first."
            )
        elif "limit" in error_msg.lower() or "quota" in error_msg.lower():
            evaluation["correction_hint"] = (
                "The user has reached a usage limit. Inform them about the limit "
                "and suggest alternatives or upgrading."
            )

        return evaluation

    # 2. Validate action-specific outcomes
    if action_type == "create_course":
        if not action_result.get("course_id"):
            evaluation["success"] = False
            evaluation["issues"].append("Course created but no course_id returned.")

    elif action_type == "create_schedule":
        schedule_data = action_result.get("data", {})
        if schedule_data.get("startAt") and schedule_data.get("endAt"):
            # Check if schedule is in the past
            from datetime import datetime, UTC

            try:
                start = schedule_data["startAt"]
                if isinstance(start, str):
                    # Try to parse ISO format
                    from dateutil.parser import parse

                    start_dt = parse(start)
                    if start_dt.replace(tzinfo=None) < datetime.now(UTC).replace(tzinfo=None):
                        evaluation["issues"].append(
                            "Schedule block was created in the past. User may want a future date."
                        )
                        evaluation["correction_hint"] = (
                            "The schedule was set in the past. Ask the user to confirm the date "
                            "or create it for a future date instead."
                        )
            except Exception:
                pass

    elif action_type == "create_goal":
        if not action_result.get("goal_id"):
            evaluation["success"] = False
            evaluation["issues"].append("Goal created but no goal_id returned.")

    elif action_type == "recommend_resources":
        resources = action_result.get("resources", [])
        if not resources:
            evaluation["issues"].append(
                "No resources were found. Consider broadening the search query."
            )
            evaluation["correction_hint"] = (
                "The resource search returned no results. Try rephrasing the query "
                "or searching for a broader topic."
            )

    # 3. Check for suspicious patterns
    if action_type.startswith("create_") and evaluation["success"]:
        # Log successful creation
        logger.info(
            "Action %s succeeded for %s: %s",
            action_type,
            action_data.get("title", "untitled"),
            action_result.get("message", "ok"),
        )

    return evaluation


def build_reflection_context(evaluations: list[dict[str, Any]]) -> str:
    """
    Build a context string from action evaluations for the LLM.

    This is appended to the prompt when the AI needs to self-correct.
    """
    if not evaluations:
        return ""

    issues = []
    corrections = []

    for ev in evaluations:
        if not ev.get("success", True):
            issues.extend(ev.get("issues", []))
            if ev.get("correction_hint"):
                corrections.append(ev["correction_hint"])
        elif ev.get("issues"):
            issues.extend(ev["issues"])

    if not issues and not corrections:
        return ""

    parts = []
    if issues:
        parts.append(
            "⚠️ Issues detected with previous actions:\n" + "\n".join(f"- {i}" for i in issues)
        )
    if corrections:
        parts.append("💡 Suggestions:\n" + "\n".join(f"- {c}" for c in corrections))

    return "\n".join(parts)
