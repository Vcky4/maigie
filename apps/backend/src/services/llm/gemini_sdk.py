"""
Google GenAI (`google-genai`) import boundary.

All Gemini client construction should go through :func:`new_gemini_client` so multi-provider
work can swap implementations without scattering ``Client(...)`` calls.
"""

from __future__ import annotations

import warnings
from typing import Any

_genai: Any = None
_types: Any = None

try:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        from google import genai as _genai_mod
        from google.genai import types as _types_mod

    _genai = _genai_mod
    _types = _types_mod
except Exception:  # pragma: no cover - optional dependency / import errors
    _genai = None
    _types = None

# Public aliases (match historical ``llm_service`` names)
genai = _genai
types = _types


def new_gemini_client(api_key: str | None) -> Any:
    """Return a new ``google.genai.Client`` (raises if SDK unavailable)."""
    if genai is None:
        raise RuntimeError(
            "google-genai is not installed or failed to import. "
            "Install the `google-genai` package to enable Gemini."
        )
    return genai.Client(api_key=api_key)
