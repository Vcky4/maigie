"""
Backward-compatibility bridge for code that imports from gemini_tools.

This module provides the same `get_all_tools()` and `get_study_tools()` functions
that the legacy `gemini_tools.py` exposed, but backed by the new skill registry.

Existing adapter code can switch imports from:
    from src.services.gemini_tools import get_all_tools
to:
    from src.services.skills.compat import get_all_tools

Or simply update gemini_tools.py to delegate here (which we do).
"""

from __future__ import annotations

from typing import Any

from src.services.skills.registry import skill_registry


def get_all_tools() -> list[dict[str, Any]]:
    """Return all tools in the legacy Gemini functionDeclarations format.

    Drop-in replacement for `gemini_tools.get_all_tools()`.
    """
    return skill_registry.get_all_tools_legacy_format()


def get_study_tools() -> list[dict[str, Any]]:
    """Return study tools in the legacy format.

    Drop-in replacement for `gemini_tools.get_study_tools()`.
    """
    return skill_registry.get_study_tools_legacy_format()
